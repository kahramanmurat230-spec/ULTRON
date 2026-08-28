"""ULTRON V15 backend — aiohttp REST + WebSocket. Real data only."""
import asyncio
import json
import os
import re
import time
from collections import deque
from pathlib import Path

import aiohttp
from aiohttp import web

from agent import Agent
from app.code_intel.analyzer import CodeIntel
from app.core.intent import VISION_PROMPT
from app.security.audit import AuditLog
from auth import Auth
from bridge import UltronBridge
from codegen import CodeGen
from app.core import doctor as doctor_mod
from app.core.backup_engine import BackupEngine
from health import build_health, log_health
from memory import MemorySystem
from notifier import Notifier
from telemetry import Telemetry
from tools import ToolRegistry
import test_runner

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
WORKSPACE = os.environ.get("ULTRON_WORKSPACE", os.path.dirname(BASE))
OLLAMA_HOST = os.environ.get("ULTRON_OLLAMA_HOST", "http://127.0.0.1:11434")
PERSISTENT = os.environ.get("ULTRON_PERSISTENT_MEMORY", "1") != "0"
VERSION = "15.0.0"


class Hub:
    def __init__(self) -> None:
        self.clients: set[web.WebSocketResponse] = set()
        self.activity: deque[dict] = deque(maxlen=30)
        self.notifications: deque[dict] = deque(maxlen=20)
        self.telemetry = Telemetry()
        self.ai_status: dict = {"connected": False, "host": OLLAMA_HOST, "models": [],
                                "primary": None, "vision": None, "embedding": None, "checked_at": 0}
        self.memory = MemorySystem(DATA_DIR, PERSISTENT)
        self.tools = ToolRegistry(WORKSPACE, self.broadcast_tools, self.on_activity, lambda: self.ai_status)
        self.agent = Agent(self.broadcast, self.tools, self.memory, self.telemetry,
                           lambda: self.ai_status, self.on_activity)
        self.bridge: UltronBridge | None = None
        self.pending_task: dict | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.last_command_ts: float = 0.0
        self.audit = AuditLog()
        self.notifier = Notifier(lambda text, level: self._sched_notify(text, level))
        self.codegen = CodeGen(Path(os.path.dirname(BASE)), self.audit, self.notifier.notify)
        self.code_intel = CodeIntel(os.path.dirname(BASE))
        self.auth = Auth(Path(DATA_DIR) / "auth" / "sessions.json", os.environ.get("ULTRON_AUTH", "0") == "1")
        from app.personal.user_dna import MasterRules, UserDNA
        from app.personal.workspace_sentinel import WorkspaceSentinel
        from app.security.voiceprint_guard import VoiceprintGuard
        self.rules = MasterRules()
        self.voiceprint = VoiceprintGuard()
        self.dna = UserDNA()
        self.sentinel = WorkspaceSentinel()

    def _sched_notify(self, text: str, level: str) -> None:
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.notify(text, level), self.loop)

    def bridge_available_llm(self) -> bool:
        return bool(self.bridge and self.bridge.available and self.ai_status.get("connected"))

    def tools_list(self) -> list[dict]:
        base = self.tools.list_status()
        return base + (self.bridge.capabilities() if self.bridge else [])

    async def broadcast_tools(self, _payload: dict | None = None) -> None:
        await self.broadcast({"type": "tools", "data": self.tools_list()})

    async def broadcast_task(self) -> None:
        await self.broadcast({"type": "task", "data": self.pending_task})

    # ---------- broadcast / events ----------
    async def broadcast(self, payload: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def on_activity(self, text: str, kind: str = "info") -> None:
        self.activity.appendleft({"ts": time.time(), "text": text, "kind": kind})
        await self.broadcast({"type": "activity", "data": list(self.activity)})

    async def notify(self, text: str, level: str = "info") -> None:
        self.notifications.appendleft({"ts": time.time(), "text": text, "level": level})
        await self.broadcast({"type": "notifications", "data": list(self.notifications)})

    # ---------- live probes ----------
    async def check_ollama(self) -> dict:
        try:
            timeout = aiohttp.ClientTimeout(total=2.5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(OLLAMA_HOST + "/api/tags") as resp:
                    data = await resp.json()
            models = [m.get("name", "?") for m in data.get("models", [])]
        except Exception:
            new = {"connected": False, "host": OLLAMA_HOST, "models": [],
                   "primary": None, "vision": None, "embedding": None, "checked_at": time.time()}
        else:
            primary = os.environ.get("ULTRON_MODEL") or (models[0] if models else None)
            vision = next((m for m in models if any(k in m.lower() for k in ("llava", "vision"))), None)
            embedding = next((m for m in models if "embed" in m.lower()), None)
            new = {"connected": True, "host": OLLAMA_HOST, "models": models,
                   "primary": primary, "vision": vision, "embedding": embedding, "checked_at": time.time()}
        changed = new["connected"] != self.ai_status.get("connected")
        self.ai_status = new
        self.tools.refresh_sync()
        return {"changed": changed, "status": new}

    def snapshot_all(self) -> dict:
        return {
            "system": self.telemetry.snapshot(),
            "ai": self.ai_status,
            "tools": self.tools_list(),
            "memory": self.memory.status(),
            "activity": list(self.activity),
            "notifications": list(self.notifications),
            "agent": {"state": self.agent.state},
            "config": self.config(),
        }

    def config(self) -> dict:
        return {
            "version": VERSION,
            "ollama_host": OLLAMA_HOST,
            "primary_model": self.ai_status.get("primary"),
            "persistent_memory": PERSISTENT,
            "workspace": WORKSPACE,
        }


hub = Hub()


# ---------------- HTTP routes ----------------
async def api_system(_req: web.Request) -> web.Response:
    return web.json_response(hub.telemetry.snapshot())


async def api_ai(_req: web.Request) -> web.Response:
    res = await hub.check_ollama()
    return web.json_response(res["status"])


async def api_tools(_req: web.Request) -> web.Response:
    return web.json_response(hub.tools_list())


async def api_memory(_req: web.Request) -> web.Response:
    status = hub.memory.status()
    if hub.bridge and hub.bridge.available:
        try:
            status["v16_total"] = hub.bridge.runtime.memory.count()
        except Exception:
            status["v16_total"] = None
    return web.json_response(status)


async def api_audit(_req: web.Request) -> web.Response:
    if hub.bridge and hub.bridge.available:
        return web.json_response(hub.bridge.runtime.audit.recent(30))
    return web.json_response([])


async def api_voice_metrics(_req: web.Request) -> web.Response:
    if hub.bridge:
        return web.json_response(hub.bridge.voice_stack.get_metrics())
    return web.json_response({})


async def api_voice_metrics_history(req: web.Request) -> web.Response:
    try:
        hours = float(req.query.get("hours", "24"))
    except ValueError:
        hours = 24.0
    if hub.bridge:
        return web.json_response(hub.bridge.metrics_store.percentiles(hours))
    return web.json_response({"count": 0})


async def api_system_health(_req: web.Request) -> web.Response:
    h = dict(getattr(hub, "health", {}))
    h["ollama"] = "connected" if hub.ai_status.get("connected") else "offline"
    return web.json_response(h)


# ---------------- Phase-4: sovereign / master / voiceprint / workspace ----------------
async def api_voiceprint_enroll(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    pcm = body.get("pcm_b64", "")
    if not pcm:
        return web.json_response({"ok": False, "error": "pcm_b64 required"}, status=400)
    from app.security.voiceprint_guard import b64_to_pcm
    res = hub.voiceprint.enroll(b64_to_pcm(pcm))
    hub.audit.write("VOICEPRINT_ENROLL", f"dims={res.get('dims')} engine={res.get('engine')}")
    return web.json_response(res)


async def api_voiceprint_verify(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    from app.security.voiceprint_guard import b64_to_pcm
    res = hub.voiceprint.verify(b64_to_pcm(body.get("pcm_b64", "")))
    if not res.get("ok") and not res.get("bypassed"):
        hub.audit.write("VOICEPRINT_REJECT", f"sim={res.get('similarity')} line={res.get('reject_line', '')[:40]}")
    return web.json_response(res)


async def api_master_profile(_req: web.Request) -> web.Response:
    return web.json_response({
        "rules": hub.rules.get(),
        "tampered_restored": hub.rules.tampered,
        "voiceprint": {"enabled": hub.voiceprint.enabled,
                       "threshold": hub.voiceprint.threshold,
                       "enrolled": hub.voiceprint._load() is not None},
        "dna_rows": len(hub.dna.recent(30)),
        "workspace": hub.sentinel.state(),
    })


async def api_master_insights(_req: web.Request) -> web.Response:
    return web.json_response({"insights": hub.dna.insights(7)})


async def api_workspace_state(_req: web.Request) -> web.Response:
    return web.json_response(hub.sentinel.state())


# ---------------- Phase-6: Master HUD ----------------
# ---------------- Neural TTS (edge-tts) ----------------
async def api_tts_speak(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    text = str(body.get("text", "")).strip()
    rt = _rt()
    tts = rt.tts if rt else None
    if tts is None:
        from app.voice.tts import TextToSpeech
        tts = TextToSpeech()
    try:
        audio, fmt = await tts.synthesize(text)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": str(e)}, status=503)
    import base64 as _b64
    return web.json_response({"ok": True, "audio_b64": _b64.b64encode(audio).decode("ascii"),
                              "format": fmt, "engine": tts.backend(),
                              "voice": tts.voice, "rate": tts.rate, "pitch": tts.pitch})


async def api_tts_status(_req: web.Request) -> web.Response:
    rt = _rt()
    tts = rt.tts if rt else None
    if tts is None:
        from app.voice.tts import TextToSpeech
        tts = TextToSpeech()
    return web.json_response({"engine": tts.backend(), "voice": tts.voice,
                              "rate": tts.rate, "pitch": tts.pitch})


# ---------------- Phase-12.2: UI theme relay (PC->mobile sync) ----------------
async def api_ui_theme_get(_req: web.Request) -> web.Response:
    return web.json_response(getattr(hub, "ui_theme", {"name": "CRIMSON", "ts": 0}))


async def api_ui_theme_post(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    name = str(body.get("name", "CRIMSON"))
    if name not in ("CRIMSON", "CYAN", "PURPLE", "HYBRID"):
        return web.json_response({"ok": False, "error": "unknown theme"}, status=400)
    hub.ui_theme = {"name": name, "ts": time.time()}
    return web.json_response({"ok": True, **hub.ui_theme})


# ---------------- Phase-11: presence ----------------
async def api_presence_ping(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        body = {}
    snap = hub.telemetry.snapshot()
    briefing = (f"Kısa brifing: CPU %{snap['cpu']['percent']}, RAM %{snap['ram']['percent']}, "
                f"Ollama {'çevrimiçi' if hub.ai_status.get('connected') else 'uykuda'}.")
    res = hub.presence.ping(str(body.get("source", "manual")),
                            str(body.get("device_id", "local")), briefing)
    hub.audit.write("PRESENCE_PING", f"{body.get('source')} welcomed={res['welcomed']}")
    return web.json_response(res)


async def api_presence_status(_req: web.Request) -> web.Response:
    return web.json_response(hub.presence.status())


# ---------------- Phase-10: IoT Nexus ----------------
async def api_iot_devices(_req: web.Request) -> web.Response:
    return web.json_response(hub.iot.list())


async def api_iot_control(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = hub.iot.control(str(body.get("device_id", "")),
                          str(body.get("action", "toggle")),
                          body.get("value"))
    if res.get("ok"):
        hub.audit.write("IOT_CONTROL", f"{body.get('device_id')} {body.get('action')}")
    return web.json_response(res)


async def api_iot_scene(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = hub.scenes.activate(str(body.get("scene_name", "")))
    hub.audit.write("IOT_SCENE", str(body.get("scene_name")))
    return web.json_response(res)


async def api_iot_discover(_req: web.Request) -> web.Response:
    return web.json_response(hub.iot.discover())


# ---------------- Phase-9: dual-brain mesh ----------------
def _mesh_auth_ok(req: web.Request) -> bool:
    token = req.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return bool(token) and token in hub.auth.sessions


async def api_mesh_handshake(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = hub.mesh_registry.handshake(
        str(body.get("node_id", "unknown")), str(body.get("role", "")),
        str(body.get("version", "0")), _mesh_auth_ok(req))
    return web.json_response(res)


async def api_mesh_push(req: web.Request) -> web.Response:
    if not _mesh_auth_ok(req):
        return web.json_response({"ok": False, "error": "unauthorized node"}, status=401)
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = hub.mesh_sync.push(body)
    hub.audit.write("MESH_PUSH", str(res)[:100])
    return web.json_response({"ok": True, **res})


async def api_mesh_pull(req: web.Request) -> web.Response:
    if not _mesh_auth_ok(req):
        return web.json_response({"ok": False, "error": "unauthorized node"}, status=401)
    try:
        since = float(req.query.get("since_ts", "0"))
    except ValueError:
        since = 0.0
    return web.json_response(hub.mesh_sync.pull(since))


async def api_mesh_nodes(_req: web.Request) -> web.Response:
    return web.json_response({"nodes": hub.mesh_registry.status(),
                              "pc_online": hub.mesh_registry.pc_online(),
                              "mobile_online": hub.mesh_registry.mobile_online()})


# ---------------- Phase-8: doctor + backup ----------------
async def api_system_doctor(_req: web.Request) -> web.Response:
    ctx = {
        "ollama_host": OLLAMA_HOST,
        "db_paths": [str(p) for p in Path(DATA_DIR).rglob("*.db")],
        "allow_busy_ports": True,
        "rules_path": os.path.join(BASE, "config", "security", "master_rules.json"),
    }
    res = doctor_mod.run_doctor(
        ctx, persona=(hub.bridge.runtime.agent.persona
                      if (hub.bridge and hub.bridge.available) else None))
    hub.last_doctor = res
    return web.json_response(res)


async def api_backup_create(_req: web.Request) -> web.Response:
    res = hub.backup.create()
    hub.audit.write("BACKUP_CREATE", f"{res['backup_id']} sha={res['sha256'][:16]}")
    return web.json_response({"ok": True, **res})


async def api_backup_list(_req: web.Request) -> web.Response:
    return web.json_response(hub.backup.list())


async def api_backup_restore(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    try:
        res = hub.backup.restore(str(body.get("backup_id", "")))
    except ValueError as e:
        hub.audit.write("BACKUP_RESTORE_REJECT", str(e)[:60])
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    hub.audit.write("BACKUP_RESTORE", res["backup_id"])
    return web.json_response({"ok": True, **res})


# ---------------- Phase-7: emotion + memory evolution ----------------
def _rt():
    return hub.bridge.runtime if (hub.bridge and hub.bridge.available) else None


async def api_emotion_analyze(req: web.Request) -> web.Response:
    rt = _rt()
    if not rt:
        return web.json_response({"ok": False, "error": "runtime unavailable"}, status=503)
    try:
        body = await req.json()
    except Exception:
        body = {}
    from app.emotion.emotion_engine import analyze as em_analyze
    import base64 as _b64
    pcm = _b64.b64decode(body["audio_b64"]) if body.get("audio_b64") else None
    res = em_analyze(pcm=pcm, text=body.get("text"))
    rt.emotion_log.add(res["state"], res["confidence"], res["source"])
    hint = rt.adaptive.adapt(res["state"], res["confidence"])
    return web.json_response({**res, "adapted_hint": hint})


async def api_emotion_history(req: web.Request) -> web.Response:
    rt = _rt()
    if not rt:
        return web.json_response({"rows": [], "trend": {}})
    try:
        hours = float(req.query.get("hours", "24"))
    except ValueError:
        hours = 24.0
    return web.json_response(rt.emotion_log.history(hours))


async def api_memory_stats(_req: web.Request) -> web.Response:
    rt = _rt()
    if not rt:
        return web.json_response({"ok": False, "error": "runtime unavailable"}, status=503)
    return web.json_response(rt.semv2.stats())


async def api_memory_decay_run(_req: web.Request) -> web.Response:
    rt = _rt()
    if not rt:
        return web.json_response({"ok": False, "error": "runtime unavailable"}, status=503)
    n = rt.semv2.decay()
    hub.audit.write("MEMORY_DECAY", f"archived={n}")
    return web.json_response({"ok": True, "archived": n})


async def api_memory_export(_req: web.Request) -> web.Response:
    rt = _rt()
    if not rt:
        return web.json_response({"ok": False, "error": "runtime unavailable"}, status=503)
    from app.memory import memory_io
    res = memory_io.export_local(rt.memory, hub.dna, hub.rules)
    hub.audit.write("MEMORY_EXPORT", res["sha256"][:16])
    return web.json_response({"ok": True, **res})


async def api_memory_import(req: web.Request) -> web.Response:
    rt = _rt()
    if not rt:
        return web.json_response({"ok": False, "error": "runtime unavailable"}, status=503)
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    from app.memory import memory_io
    try:
        res = memory_io.import_snapshot(rt.memory, hub.dna, body)
    except ValueError as e:
        hub.audit.write("MEMORY_IMPORT_REJECT", str(e)[:60])
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    hub.audit.write("MEMORY_IMPORT", str(res))
    return web.json_response({"ok": True, **res})


async def api_hud_overview(_req: web.Request) -> web.Response:
    return web.json_response(hub.hud.overview())


async def api_hud_audit_recent(req: web.Request) -> web.Response:
    try:
        limit = int(req.query.get("limit", "20"))
    except ValueError:
        limit = 20
    return web.json_response(hub.hud.audit_recent(limit))


async def api_hud_persona_trends(_req: web.Request) -> web.Response:
    return web.json_response(hub.hud.persona_trends(50))


async def api_hud_self_diagnostic(_req: web.Request) -> web.Response:
    rt = _rt()
    if rt:
        res = rt.self_diagnostic()
    else:
        res = hub.hud.self_diagnostic()
    hub.audit.write("SELF_DIAGNOSTIC", res.get("report", "")[:120])
    return web.json_response(res)


async def api_vision_toggle(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = hub.vision_control.toggle(bool(body.get("enabled", False)))
    hub.audit.write("VISION_TOGGLE", res["status"])
    return web.json_response({"ok": True, **res})


async def api_vision_status(_req: web.Request) -> web.Response:
    return web.json_response(hub.vision_control.status())


async def api_vision_scan_now(_req: web.Request) -> web.Response:
    return web.json_response(hub.vision_control.scan_now())


async def api_sovereign_audit(_req: web.Request) -> web.Response:
    from app.security import sovereign_privacy as sov
    import importlib.util as _iu
    from health import tts_backend_name
    a = sov.audit(OLLAMA_HOST, tts_backend_name(),
                  _iu.find_spec("faster_whisper") is not None,
                  hub.ai_status.get("connected", False))
    a["sovereign_status"] = sov.sovereign_status(a)
    return web.json_response(a)


async def api_config_reload(_req: web.Request) -> web.Response:
    if not (hub.bridge and hub.bridge.available):
        return web.json_response({"ok": False, "error": "runtime unavailable"}, status=503)
    st = hub.bridge.runtime.reload_config()
    if getattr(hub, "health", None):
        hub.health["persona_guard"]["mode"] = st.get("persona_guard_mode", "reframe")
    await hub.on_activity("Config hot-reload applied", "info")
    return web.json_response({"ok": True, "persona_guard_mode": st.get("persona_guard_mode", "reframe")})


async def api_voice_live(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    on = bool(body.get("on"))
    if not (hub.bridge and hub.bridge.available):
        return web.json_response({"ok": False, "error": "V16 runtime unavailable"})
    import importlib.util as _iu
    if not (_iu.find_spec("sounddevice") and _iu.find_spec("faster_whisper")):
        return web.json_response({"ok": False, "error": "sounddevice + faster-whisper not installed"})
    if on:
        hub.bridge.runtime.start_live_voice()
    else:
        hub.bridge.runtime.stop_live_voice()
    await hub.on_activity(f"Live voice {'started' if on else 'stopped'}", "info")
    return web.json_response({"ok": True})


async def api_activity(_req: web.Request) -> web.Response:
    return web.json_response(list(hub.activity))


async def api_notifications(_req: web.Request) -> web.Response:
    return web.json_response(list(hub.notifications))


async def api_config(_req: web.Request) -> web.Response:
    return web.json_response(hub.config())


async def api_agent_state(_req: web.Request) -> web.Response:
    return web.json_response({"state": hub.agent.state})


async def api_command(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    text = str(body.get("text", "")).strip()
    approved = bool(body.get("approved", False))
    hub.last_command_ts = time.time()
    if not text:
        return web.json_response({"ok": False, "error": "empty command"}, status=400)
    if hub.agent.busy:
        return web.json_response({"ok": False, "error": "agent busy"}, status=409)
    asyncio.create_task(hub.agent.run(text, approved=approved))
    return web.json_response({"ok": True})


async def api_task_pending(_req: web.Request) -> web.Response:
    return web.json_response(hub.pending_task or {})


async def api_task_approve(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        body = {}
    pid = str(body.get("id", ""))
    task = hub.pending_task
    if not task or task.get("id") != pid:
        return web.json_response({"ok": False, "error": "no such pending task"}, status=404)
    text = task["text"]
    hub.pending_task = None
    await hub.broadcast_task()
    await hub.on_activity(f"Task {pid} APPROVED", "success")
    asyncio.create_task(hub.agent.run(text, approved=True))
    return web.json_response({"ok": True})


async def api_task_reject(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        body = {}
    pid = str(body.get("id", ""))
    if hub.pending_task and hub.pending_task.get("id") == pid:
        hub.pending_task = None
        await hub.broadcast_task()
        await hub.agent.set_state("IDLE", f"Task {pid} rejected.")
        await hub.on_activity(f"Task {pid} REJECTED", "warn")
        return web.json_response({"ok": True})
    return web.json_response({"ok": False, "error": "no such pending task"}, status=404)


async def api_voice_report(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False}, status=400)
    hub.tools.report_voice(bool(body.get("available")), str(body.get("detail", "")))
    await hub.broadcast_tools()
    return web.json_response({"ok": True})


async def api_action_screenshot(_req: web.Request) -> web.Response:
    res = await hub.tools.execute("screen")
    return web.json_response(res)


async def api_action_browser(_req: web.Request) -> web.Response:
    res = await hub.tools.execute("browser", "https://www.google.com")
    return web.json_response(res)


async def api_action_system_check(_req: web.Request) -> web.Response:
    snap = hub.telemetry.snapshot()
    await hub.on_activity("System status checked", "info")
    return web.json_response({"ok": True, "output": snap})


async def api_action_clear_memory(_req: web.Request) -> web.Response:
    hub.memory.clear_all()
    await hub.broadcast({"type": "memory", "data": hub.memory.status()})
    await hub.on_activity("Memory cleared", "warn")
    return web.json_response({"ok": True})


# ---------------- code intelligence / codegen / tests ----------------
async def api_codeintel_analyze(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        body = {}
    target = str(body.get("target", "all"))
    rep = await asyncio.to_thread(hub.code_intel.analyze, target)
    await hub.on_activity(
        f"Code analysis [{target}]: {rep['summary']['files']} files, {len(rep['issues'])} issues", "info")
    return web.json_response(rep)


async def api_codeintel_explain(req: web.Request) -> web.Response:
    return web.json_response(await asyncio.to_thread(hub.code_intel.explain_file, req.query.get("file", "")))


async def api_codegen_propose(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    goal = str(body.get("goal", "")).strip()
    if not goal:
        return web.json_response({"ok": False, "error": "empty goal"}, status=400)
    brain = None
    if hub.bridge and hub.bridge.available and hub.ai_status.get("connected"):
        brain = hub.bridge.runtime.brain
    res = await hub.codegen.propose(goal, brain=brain)
    if res.get("ok"):
        await hub.agent.set_state("WAITING_APPROVAL",
                                  f"Patch {res['proposal']['id']} hazır — onay bekleniyor")
        await hub.broadcast({"type": "patch", "data": res["proposal"]})
        hub.notifier.notify("approval", f"Onay bekleyen patch: {res['proposal']['id']}", "warn", force=True)
    return web.json_response(res)


async def _test_emit(name: str, status: str, detail: str) -> None:
    await hub.broadcast({"type": "tests", "data": {"name": name, "status": status, "detail": detail[:200]}})


async def api_codegen_apply(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    pid = str(body.get("id", ""))
    res = await hub.codegen.apply(pid, run_tests=lambda quick=True: test_runner.run_all(_test_emit, quick=quick))
    if res.get("ok"):
        await hub.agent.set_state("DONE", f"Patch {pid} uygulandı, testler geçti.")
    else:
        await hub.agent.set_state("ERROR", f"Patch {pid}: {res.get('error')} (rollback: {res.get('rolled_back')})")
    await hub.broadcast({"type": "patch", "data": None})
    return web.json_response(res)


async def api_codegen_reject(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = hub.codegen.reject(str(body.get("id", "")))
    if not hub.codegen.pending():
        await hub.agent.set_state("IDLE")
    await hub.broadcast({"type": "patch", "data": None})
    return web.json_response(res)


async def api_codegen_pending(_req: web.Request) -> web.Response:
    return web.json_response(hub.codegen.pending())


async def api_tests_run(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        body = {}
    quick = bool(body.get("quick", False))
    results = await test_runner.run_all(_test_emit, quick=quick)
    failed = [r["name"] for r in results if not r["ok"]]
    await hub.on_activity(f"Test suite: {len(results) - len(failed)}/{len(results)} passed",
                          "success" if not failed else "error")
    if failed:
        hub.notifier.notify("tests", f"Testler başarısız: {', '.join(failed)}", "error", force=True)
    return web.json_response({"results": results, "failed": failed})


# ---------------- memory v16 management ----------------
def _v16_mem():
    if hub.bridge and hub.bridge.available:
        return hub.bridge.runtime.memory
    return None


async def api_memory_v16(req: web.Request) -> web.Response:
    mem = _v16_mem()
    if not mem:
        return web.json_response({"ok": False, "error": "V16 runtime unavailable"}, status=503)
    limit = int(req.query.get("limit", "50"))
    return web.json_response({"rows": mem.recent_full(limit), "kinds": mem.kind_counts()})


async def api_memory_v16_add(req: web.Request) -> web.Response:
    mem = _v16_mem()
    if not mem:
        return web.json_response({"ok": False, "error": "V16 runtime unavailable"}, status=503)
    body = await req.json()
    kind = str(body.get("kind", "FACT")).upper()[:16]
    text = str(body.get("text", "")).strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty text"}, status=400)
    rid = mem.add(kind, text[:500])
    await hub.broadcast({"type": "memory", "data": hub.memory.status()})
    await hub.on_activity(f"Memory +{kind}: {text[:50]}", "info")
    return web.json_response({"ok": True, "id": rid})


async def api_memory_v16_delete(req: web.Request) -> web.Response:
    mem = _v16_mem()
    if not mem:
        return web.json_response({"ok": False, "error": "V16 runtime unavailable"}, status=503)
    body = await req.json()
    ok = mem.delete(body.get("id"))
    await hub.broadcast({"type": "memory", "data": hub.memory.status()})
    return web.json_response({"ok": ok})


async def api_memory_v16_clear(_req: web.Request) -> web.Response:
    mem = _v16_mem()
    if not mem:
        return web.json_response({"ok": False, "error": "V16 runtime unavailable"}, status=503)
    mem.clear()
    await hub.broadcast({"type": "memory", "data": hub.memory.status()})
    await hub.on_activity("V16 long-term memory cleared", "warn")
    return web.json_response({"ok": True})


async def api_memory_v16_search(req: web.Request) -> web.Response:
    mem = _v16_mem()
    if not mem:
        return web.json_response({"ok": False, "error": "V16 runtime unavailable"}, status=503)
    q = req.query.get("q", "")
    hits = hub.bridge.runtime.semantic_memory.search(q, limit=10)
    return web.json_response([{"score": round(s, 2), "kind": k, "content": c} for s, k, c, _ in hits])


# ---------------- diagnostics (V17.1, temporary) ----------------
async def api_debug_runtime(_req: web.Request) -> web.Response:
    import inspect
    import agent as v15agent
    import app.agent.agent as v16agent
    from app.core import runtime as rtmod
    from app.vision import vision_llm as vmod
    rt = hub.bridge.runtime if (hub.bridge and hub.bridge.available) else None
    return web.json_response({
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "server_file": os.path.abspath(__file__),
        "v15_agent_file": os.path.abspath(v15agent.__file__),
        "v16_agent_file": os.path.abspath(v16agent.__file__),
        "runtime_file": os.path.abspath(rtmod.__file__),
        "vision_llm_file": os.path.abspath(vmod.__file__),
        "settings_path": os.path.abspath(os.path.join(BASE, "config", "settings.json")),
        "configured_vision_model": (rt.settings.get("vision", {}).get("model") if rt else None),
        "installed_models": hub.ai_status.get("models"),
        "bridge_available": bool(hub.bridge and hub.bridge.available),
        "v15_agent_vision_hook": getattr(hub.agent, "vision_check", None) is not None,
        "v15_agent_fallback": getattr(hub.agent, "fallback", None) is not None,
        "v16_agent_vision_branch_loaded": "vision_pattern" in inspect.getsource(v16agent.Agent._direct),
        "v16_runtime_agent_has_vision_llm": (hasattr(rt.agent, "vision_llm") and rt.agent.vision_llm is not None) if rt else None,
    })


async def api_debug_vision_selftest(req: web.Request) -> web.Response:
    """One real pipeline run with a full trace. Stops at the exact failing stage."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    trace: dict = {"pid": os.getpid(), "cwd": os.getcwd()}
    text = str(body.get("text", "Ekranımı analiz et ve ekranda ne gördüğünü anlat."))
    trace["1_detected_vision_intent"] = bool(hub.bridge and hub.bridge.is_vision_flow(text))
    trace["2_handler"] = "bridge.run_vision / Agent._direct._vision_pipeline (shared regex)"
    if not (hub.bridge and hub.bridge.available):
        trace["stop"] = "RUNTIME UNAVAILABLE"
        return web.json_response(trace)
    rt = hub.bridge.runtime
    m = re.search(r"(\S+\.(?:png|jpe?g))", text, re.I)
    path = str(body.get("path") or (m.group(1) if m else "") or "")
    if not path:
        try:
            path = await asyncio.to_thread(
                lambda: rt.executor.execute([("capture_screen", {})], approved=True)[0])
        except Exception as exc:  # noqa: BLE001
            trace["stop"] = "3_SCREENSHOT"
            trace["error"] = str(exc)
            return web.json_response(trace)
    trace["3_screenshot_path"] = path
    exists = os.path.exists(path)
    trace["4_screenshot_exists"] = exists
    trace["4_screenshot_size"] = os.path.getsize(path) if exists else None
    if not exists or os.path.getsize(path) == 0:
        trace["stop"] = "4_PNG_VERIFY"
        return web.json_response(trace)
    models = hub.ai_status.get("models") or []
    model = rt.vision_llm.resolve_model(models)
    trace["5_selected_model"] = model
    trace["6_endpoint"] = rt.brain.base_url.rstrip("/") + "/api/chat"
    if model is None:
        trace["stop"] = "5_NO_VISION_MODEL"
        trace["installed_models"] = models
        return web.json_response(trace)
    try:
        analysis = await asyncio.to_thread(rt.vision_llm.analyze, path, VISION_PROMPT, models)
    except Exception as exc:  # noqa: BLE001
        trace["stop"] = "7_OLLAMA_CALL"
        trace["7_image_attached"] = True
        trace["10_ollama_http_status"] = rt.brain.last_http_status
        trace["error"] = str(exc)
        return web.json_response(trace)
    trace["7_image_attached"] = True
    trace["8_image_bytes"] = os.path.getsize(path)
    trace["9_request_model"] = model
    trace["10_ollama_http_status"] = rt.brain.last_http_status
    trace["11_response_model"] = rt.brain.last_response_model
    trace["12_raw_response_head"] = analysis[:200]
    trace["13_final_response"] = f"EKRAN ANALİZİ ({model}): {analysis}"[:400]
    return web.json_response(trace)


# ---------------- mobile vision preview (additive) ----------------
async def api_vision_last(_req: web.Request) -> web.Response:
    d = Path(BASE) / "data" / "logs"
    files = []
    if d.exists():
        files = sorted(d.glob("screen_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return web.json_response({"exists": False})
    p = files[0]
    return web.json_response({"exists": True, "name": p.name, "size": p.stat().st_size, "ts": p.stat().st_mtime})


async def api_vision_preview(req: web.Request) -> web.Response:
    name = req.query.get("name", "")
    if not re.fullmatch(r"screen_[\w.\-]+\.png", name):
        return web.json_response({"ok": False, "error": "bad name"}, status=400)
    p = Path(BASE) / "data" / "logs" / name
    if not p.exists():
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.FileResponse(p)


# ---------------- shared-brain auth ----------------
async def api_auth_handshake(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        body = {}
    try:
        result = hub.auth.handshake(str(body.get("device", "client")), str(body.get("pairing_secret", "")))
    except PermissionError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=401)
    return web.json_response(result)


async def api_auth_devices(_req: web.Request) -> web.Response:
    return web.json_response({"enabled": hub.auth.enabled(), "devices": hub.auth.devices()})


# ---------------- WebSocket ----------------
async def ws_handler(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(req)
    hub.clients.add(ws)
    try:
        await ws.send_json({"type": "hello", **hub.snapshot_all()})
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                if data.get("type") == "voice_report":
                    hub.tools.report_voice(bool(data.get("available")), str(data.get("detail", "")))
                    await hub.broadcast_tools()
                elif data.get("type") == "mic":
                    active = bool(data.get("active"))
                    if active and hub.agent.state == "IDLE":
                        await hub.agent.set_state("LISTENING", "Microphone active")
                    elif not active and hub.agent.state == "LISTENING":
                        await hub.agent.set_state("IDLE")
                elif data.get("type") == "tts" and hub.bridge:
                    # client TTS activity feeds the barge-in detector
                    if bool(data.get("active")):
                        hub.bridge.voice_stack.tts_start()
                    else:
                        hub.bridge.voice_stack.tts_stop()
            elif msg.type == web.WSMsgType.ERROR:
                break
    finally:
        hub.clients.discard(ws)
    return ws


# ---------------- background loops ----------------
async def telemetry_loop(_app: web.Application) -> None:
    while True:
        await asyncio.sleep(2)
        await hub.broadcast({"type": "system", "data": hub.telemetry.snapshot()})
        await hub.broadcast({"type": "memory", "data": hub.memory.status()})


async def ollama_loop(_app: web.Application) -> None:
    while True:
        res = await hub.check_ollama()
        await hub.broadcast({"type": "ai", "data": res["status"]})
        await hub.broadcast_tools()
        if res["changed"]:
            if res["status"]["connected"]:
                hub.notifier.notify("ollama", f"Ollama connected · {OLLAMA_HOST}", "success", force=True)
            else:
                hub.notifier.notify("ollama", "Ollama connection lost", "error", force=True)
                if hub.bridge:  # CRITICAL severity → spoken (policy cooldown applies)
                    hub.bridge.policy.handle("ollama", "Ollama connection lost", "error")
        await asyncio.sleep(10)


async def on_startup(app: web.Application) -> None:
    await hub.telemetry.start()
    first = await hub.check_ollama()
    hub.ai_status = first["status"]
    # V16 runtime behind the V15.1 API/WS layer
    hub.loop = asyncio.get_running_loop()
    hub.bridge = UltronBridge(hub, os.path.join(BASE, "config", "settings.json"), asyncio.get_running_loop())
    if hub.bridge.available:
        hub.agent.fallback = hub.bridge.run  # GENERAL_CONVERSATION → Ollama
        hub.agent.vision_check = hub.bridge.is_vision_flow  # screenshot→LLaVA pipeline
        hub.agent.composite = hub.bridge.run_orchestrated  # Agent 2.0 planner
        hub.bridge.start_proactive()
        await hub.notify("V16 runtime online (agent/memory/audit/proactive).", "success")
    else:
        await hub.notify(f"V16 runtime unavailable: {hub.bridge.error}", "error")
    await hub.on_activity("ULTRON backend online", "success")
    await hub.notify("System is running smoothly.", "success")
    if not first["status"]["connected"]:
        await hub.notify(f"Ollama offline at {OLLAMA_HOST}", "warn")
    # V2.1 startup health check
    hub.health = build_health(
        stack=(hub.bridge.voice_stack if hub.bridge else None),
        runtime=(hub.bridge.runtime if hub.bridge and hub.bridge.available else None),
        ollama_connected=first["status"]["connected"],
        persona_mode=(hub.bridge.runtime.settings.get("persona_guard_mode", "reframe")
                      if hub.bridge and hub.bridge.available else "reframe"),
        db_paths=[os.path.join(BASE, "data", "metrics", "voice_metrics.db"),
                  os.path.join(BASE, "data", "memory", "ultron.db"),
                  os.path.join(BASE, "data", "metrics", "persona_drift.db")])
    # Phase-4: voiceprint config + sovereign audit + sentinel loop
    if hub.bridge and hub.bridge.available:
        st0 = hub.bridge.runtime.settings
        hub.voiceprint.enabled = bool(st0.get("voiceprint_lock_enabled", False))
        hub.voiceprint.threshold = float(st0.get("voiceprint_threshold", 0.75))
    from app.security import sovereign_privacy as sov
    import importlib.util as _iu
    from health import tts_backend_name
    sova = sov.audit(OLLAMA_HOST, tts_backend_name(),
                     _iu.find_spec("faster_whisper") is not None, first["status"]["connected"])
    hub.health["sovereign"] = sova
    hub.health["sovereign_status"] = sov.sovereign_status(sova)
    log_health(hub.health)
    # Phase-8: backup engine + boot doctor
    hub.backup = BackupEngine(
        root=os.path.join(BASE, "backups"),
        sources=[
            ("memory/ultron.db", os.path.join(BASE, "data", "memory", "ultron.db")),
            ("dna/user_dna.db", os.path.join(BASE, "data", "dna", "user_dna.db")),
            ("metrics/voice_metrics.db", os.path.join(BASE, "data", "metrics", "voice_metrics.db")),
            ("metrics/persona_drift.db", os.path.join(BASE, "data", "metrics", "persona_drift.db")),
            ("master_rules.json", os.path.join(BASE, "config", "security", "master_rules.json")),
            ("settings.json", os.path.join(BASE, "config", "settings.json")),
        ])
    hub.last_doctor = doctor_mod.run_doctor(
        {"ollama_host": OLLAMA_HOST,
         "db_paths": [os.path.join(BASE, "data", "memory", "ultron.db")],
         "rules_path": os.path.join(BASE, "config", "security", "master_rules.json")},
        persona=(hub.bridge.runtime.agent.persona if (hub.bridge and hub.bridge.available) else None))
    print(f"[ULTRON-DOCTOR] overall={hub.last_doctor['overall']} :: {hub.last_doctor['summary']}", flush=True)
    # Phase-9: dual-brain mesh
    from app.mesh.dual_node_engine import NodeRegistry
    from app.mesh.mesh_sync import MeshSync
    hub.mesh_registry = NodeRegistry()
    hub.mesh_sync = MeshSync(hub.bridge.runtime.memory, hub.dna, hub.rules)
    # Phase-10: IoT Nexus + scenes
    from app.iot.iot_nexus import IoTNexus
    from app.iot.scenes_engine import ScenesEngine
    hub.iot = IoTNexus(db_path=os.path.join(BASE, "data", "iot", "iot_devices.db"))
    hub.scenes = ScenesEngine(hub.iot, sentinel=hub.sentinel,
                              persona=(hub.bridge.runtime.agent.persona
                                       if (hub.bridge and hub.bridge.available) else None))
    # Phase-11: room presence & welcome
    from app.presence.room_presence import RoomPresence

    def _presence_speak(text: str) -> None:
        asyncio.run_coroutine_threadsafe(
            hub.broadcast({"type": "proactive_speech", "text": text}), hub.loop)

    hub.presence = RoomPresence(scenes=hub.scenes, speak_cb=_presence_speak,
                                persona=(hub.bridge.runtime.agent.persona
                                         if (hub.bridge and hub.bridge.available) else None))
    from app.personal.workspace_sentinel import BREAK_LINE
    import threading as _th
    hub.sentinel.on_break = (lambda m: asyncio.run_coroutine_threadsafe(
        hub.notify(BREAK_LINE.format(min=m), "warn"), hub.loop))
    _th.Thread(target=_sentinel_loop, daemon=True).start()
    # Phase-5: smart screen watcher (default OFF, privacy-first)
    from app.vision.smart_screen_watcher import SmartScreenWatcher, DEFAULT_TARGETS, DEFAULT_BLACKLIST
    from app.vision.visual_context_engine import VisualContextEngine
    from app.vision.vision_control import VisionControl
    stv = hub.bridge.runtime.settings if (hub.bridge and hub.bridge.available) else {}
    hub.watcher = SmartScreenWatcher(
        title_fn=lambda: hub.sentinel.title,
        enabled=bool(stv.get("vision_enabled", False)),
        targets=stv.get("vision_targets", DEFAULT_TARGETS),
        blacklist=stv.get("vision_blacklist", DEFAULT_BLACKLIST))
    hub.vision_engine = VisualContextEngine(
        policy=(hub.bridge.policy if hub.bridge else None),
        persona=(hub.bridge.runtime.agent.persona if (hub.bridge and hub.bridge.available) else None))
    hub.watcher.on_change = hub.vision_engine.on_change
    hub.vision_control = VisionControl(hub.watcher, hub.vision_engine,
                                       enabled_default=bool(stv.get("vision_enabled", False)))
    _th.Thread(target=hub.watcher.loop,
               kwargs={"idle_fn": lambda: hub.sentinel.mode == "IDLE_MODE"},
               daemon=True).start()
    # Phase-6: Master HUD collector
    from app.observability.master_hud import MasterHUDCollector
    from app.security import sovereign_privacy as _sov
    hub.hud = MasterHUDCollector(
        health_fn=lambda: hub.health,
        metrics_store=(hub.bridge.metrics_store if hub.bridge else None),
        persona=(hub.bridge.runtime.agent.persona if (hub.bridge and hub.bridge.available) else None),
        voiceprint=hub.voiceprint,
        sentinel=hub.sentinel,
        vision_engine=getattr(hub, "vision_engine", None),
        dna=hub.dna,
        memory=(hub.bridge.runtime.memory if (hub.bridge and hub.bridge.available) else None),
        audit=hub.audit,
        sovereign_fn=lambda: _sov.audit(OLLAMA_HOST, None, False,
                                        hub.ai_status.get("connected", False)),
        emotion_fn=lambda: (hub.bridge.runtime.emotion_log.history(24)
                            if (hub.bridge and hub.bridge.available) else {}),
        memory_health_fn=lambda: (hub.bridge.runtime.semv2.stats()
                                  if (hub.bridge and hub.bridge.available) else {}),
        doctor_fn=lambda: hub.last_doctor,
        backup_fn=lambda: (hub.backup.list()[-1] if hub.backup.list() else None),
        mesh_fn=lambda: {"nodes": hub.mesh_registry.status(),
                         "pc_online": hub.mesh_registry.pc_online(),
                         "mobile_online": hub.mesh_registry.mobile_online(),
                         "last_sync": hub.mesh_sync.last_sync_ts},
        iot_fn=lambda: {
            "devices": len(hub.iot.list()),
            "active": sum(1 for d in hub.iot.list() if d["state"] == "on"),
            "last_scene": hub.scenes.last_scene},
        presence_fn=lambda: hub.presence.status())
    app["loops"] = [asyncio.create_task(telemetry_loop(app)), asyncio.create_task(ollama_loop(app))]


def _sentinel_loop() -> None:
    import time as _t
    while True:
        try:
            hub.sentinel.sample()
        except Exception:
            pass
        _t.sleep(30)


async def on_cleanup(app: web.Application) -> None:
    for t in app.get("loops", []):
        t.cancel()
    if hub.bridge:
        hub.bridge.stop()
    await hub.telemetry.stop()


@web.middleware
async def auth_middleware(req: web.Request, handler):
    if hub.auth.required and (req.path.startswith("/api/") or req.path == "/ws"):
        if req.path != "/api/auth/handshake":
            token = req.headers.get("Authorization", "").replace("Bearer ", "").strip()
            if not token and req.path == "/ws":
                token = req.query.get("token", "").strip()  # browsers can't set WS headers
            if not hub.auth.valid(token or None):
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return await handler(req)


@web.middleware
async def cors_middleware(req: web.Request, handler):
    # Secure default: no wildcard. Same-origin clients (vite proxy) need nothing.
    # Cross-origin access only via explicit ULTRON_CORS_ORIGINS comma-list.
    if req.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(req)
    allowed = os.environ.get("ULTRON_CORS_ORIGINS", "")
    origin = req.headers.get("Origin", "")
    if allowed and origin and origin in [o.strip() for o in allowed.split(",") if o.strip()]:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


def main() -> None:
    os.chdir(BASE)  # V16 relative data paths (data/memory, data/logs, data/vault)
    app = web.Application(middlewares=[auth_middleware, cors_middleware])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/api/system", api_system)
    app.router.add_get("/api/ai", api_ai)
    app.router.add_get("/api/tools", api_tools)
    app.router.add_get("/api/memory", api_memory)
    app.router.add_get("/api/audit", api_audit)
    app.router.add_post("/api/voice/live", api_voice_live)
    app.router.add_get("/api/voice/metrics", api_voice_metrics)
    app.router.add_get("/api/voice/metrics/history", api_voice_metrics_history)
    app.router.add_get("/api/system/health", api_system_health)
    app.router.add_post("/api/config/reload", api_config_reload)
    app.router.add_post("/api/security/voiceprint/enroll", api_voiceprint_enroll)
    app.router.add_post("/api/security/voiceprint/verify", api_voiceprint_verify)
    app.router.add_get("/api/master/profile", api_master_profile)
    app.router.add_get("/api/master/dna/insights", api_master_insights)
    app.router.add_get("/api/workspace/state", api_workspace_state)
    app.router.add_get("/api/sovereign/audit", api_sovereign_audit)
    app.router.add_post("/api/vision/toggle", api_vision_toggle)
    app.router.add_get("/api/vision/status", api_vision_status)
    app.router.add_post("/api/vision/scan_now", api_vision_scan_now)
    app.router.add_get("/api/hud/overview", api_hud_overview)
    app.router.add_get("/api/hud/audit/recent", api_hud_audit_recent)
    app.router.add_get("/api/hud/persona/trends", api_hud_persona_trends)
    app.router.add_post("/api/hud/self_diagnostic", api_hud_self_diagnostic)
    app.router.add_get("/api/system/doctor", api_system_doctor)
    app.router.add_post("/api/mesh/handshake", api_mesh_handshake)
    app.router.add_post("/api/mesh/sync/push", api_mesh_push)
    app.router.add_get("/api/mesh/sync/pull", api_mesh_pull)
    app.router.add_get("/api/mesh/nodes", api_mesh_nodes)
    app.router.add_get("/api/iot/devices", api_iot_devices)
    app.router.add_post("/api/iot/device/control", api_iot_control)
    app.router.add_post("/api/iot/scene/activate", api_iot_scene)
    app.router.add_post("/api/iot/discover", api_iot_discover)
    app.router.add_post("/api/presence/ping", api_presence_ping)
    app.router.add_get("/api/presence/status", api_presence_status)
    app.router.add_get("/api/ui/theme", api_ui_theme_get)
    app.router.add_post("/api/ui/theme", api_ui_theme_post)
    app.router.add_post("/api/tts/speak", api_tts_speak)
    app.router.add_get("/api/tts/status", api_tts_status)
    app.router.add_post("/api/system/backup/create", api_backup_create)
    app.router.add_get("/api/system/backup/list", api_backup_list)
    app.router.add_post("/api/system/backup/restore", api_backup_restore)
    app.router.add_post("/api/emotion/analyze", api_emotion_analyze)
    app.router.add_get("/api/emotion/history", api_emotion_history)
    app.router.add_get("/api/memory/stats", api_memory_stats)
    app.router.add_post("/api/memory/decay/run", api_memory_decay_run)
    app.router.add_get("/api/memory/export", api_memory_export)
    app.router.add_post("/api/memory/import", api_memory_import)
    app.router.add_get("/api/activity", api_activity)
    app.router.add_get("/api/notifications", api_notifications)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/api/agent", api_agent_state)
    app.router.add_post("/api/agent/command", api_command)
    app.router.add_post("/api/tools/voice", api_voice_report)
    app.router.add_post("/api/actions/screenshot", api_action_screenshot)
    app.router.add_post("/api/actions/browser", api_action_browser)
    app.router.add_post("/api/actions/system-check", api_action_system_check)
    app.router.add_post("/api/actions/clear-memory", api_action_clear_memory)
    app.router.add_post("/api/codeintel/analyze", api_codeintel_analyze)
    app.router.add_get("/api/codeintel/explain", api_codeintel_explain)
    app.router.add_post("/api/codegen/propose", api_codegen_propose)
    app.router.add_post("/api/codegen/apply", api_codegen_apply)
    app.router.add_post("/api/codegen/reject", api_codegen_reject)
    app.router.add_get("/api/codegen/pending", api_codegen_pending)
    app.router.add_post("/api/tests/run", api_tests_run)
    app.router.add_get("/api/memory/v16", api_memory_v16)
    app.router.add_post("/api/memory/v16/add", api_memory_v16_add)
    app.router.add_post("/api/memory/v16/delete", api_memory_v16_delete)
    app.router.add_post("/api/memory/v16/clear", api_memory_v16_clear)
    app.router.add_get("/api/memory/v16/search", api_memory_v16_search)
    app.router.add_get("/api/task/pending", api_task_pending)
    app.router.add_post("/api/task/approve", api_task_approve)
    app.router.add_post("/api/task/reject", api_task_reject)
    app.router.add_post("/api/auth/handshake", api_auth_handshake)
    app.router.add_get("/api/auth/devices", api_auth_devices)
    app.router.add_get("/api/debug/runtime", api_debug_runtime)
    app.router.add_post("/api/debug/vision-selftest", api_debug_vision_selftest)
    app.router.add_get("/api/vision/last", api_vision_last)
    app.router.add_get("/api/vision/preview", api_vision_preview)
    app.router.add_get("/ws", ws_handler)
    port = int(os.environ.get("ULTRON_PORT", "8000"))
    bind_host = os.environ.get("ULTRON_BIND_HOST", "127.0.0.1")
    if bind_host not in {"127.0.0.1", "localhost", "::1"} and not hub.auth.required:
        raise RuntimeError("ULTRON_BIND_HOST is non-local but ULTRON_AUTH is disabled. Enable ULTRON_AUTH=1 before exposing ULTRON to a network.")
    web.run_app(app, host=bind_host, port=port, print=lambda *_: None)


if __name__ == "__main__":
    main()
