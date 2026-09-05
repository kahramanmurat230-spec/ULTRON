"""V15.1 <-> V16 integration bridge.

Adapts the V16 UltronRuntime behind the existing V15.1 aiohttp/WebSocket layer:
- tier-2 command routing (GENERAL_CONVERSATION -> Ollama via V16 Agent)
- unified agent state events over the existing WS contract
- real capability checks for the TOOLS panel
- proactive monitor -> real UI notifications
- per-request approval flag feeding PermissionManager / self_repair gate
"""
import asyncio
import importlib.util
import os
import re
import sys
import time
import uuid

import test_runner
from app.agent.orchestrator import parse_plan, risk_report, should_retry, ALTERNATIVES
from app.core.intent import vision_pattern, VISION_PROMPT
from app.core.runtime import UltronRuntime
from app.proactive.proactive_voice_policy import ProactiveVoicePolicy
from app.vision.targeting import build_prompt, parse_coords, click_target
from app.voice.metrics_store import MetricsStore
from app.voice.voice_stack_v2 import VoiceStackV2


def _vlog(msg: str) -> None:
    if os.environ.get("ULTRON_VISION_DEBUG", "1") != "0":
        print(f"[ULTRON-VISION] {msg}", flush=True)


def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def shutil_which(cmd: str):
    import shutil
    return shutil.which(cmd)


class UltronBridge:
    def __init__(self, hub, settings_path: str, loop: asyncio.AbstractEventLoop) -> None:
        self.hub = hub
        self.loop = loop
        self.runtime: UltronRuntime | None = None
        self.error: str | None = None
        try:
            self.runtime = UltronRuntime(settings_path)
            # route proactive events into the real UI notification stream
            original = self.runtime._proactive_event
            self.runtime.proactive.callback = lambda msg: self._proactive_ui(msg, original)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
        # V2 voice stack (VAD/barge-in/latency) + severity-gated proactive voice
        self.metrics_store = MetricsStore()
        self.voice_stack = VoiceStackV2(on_interrupt=self._barge_in, store=self.metrics_store)
        if self.runtime:
            from app.security import sovereign_privacy as sov
            sov.configure(self.runtime.settings)
        self.policy = ProactiveVoicePolicy(
            speak_cb=self._speak_proactive,
            is_user_idle_cb=lambda: (self.hub.agent.state == "IDLE")
            and (time.time() - getattr(self.hub, "last_command_ts", 0) > 30),
        )

    def _barge_in(self) -> None:
        asyncio.run_coroutine_threadsafe(
            self.hub.broadcast({"type": "barge_in"}), self.loop)

    def _speak_proactive(self, text: str) -> None:
        # clients speak via their own TTS; backend TTS best-effort
        asyncio.run_coroutine_threadsafe(
            self.hub.broadcast({"type": "proactive_speech", "text": text}), self.loop)
        if self.runtime:
            asyncio.run_coroutine_threadsafe(
                asyncio.to_thread(self._try_backend_tts, text), self.loop)

    def _try_backend_tts(self, text: str) -> None:
        try:
            self.runtime.tts.speak(text)
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self.runtime is not None

    # ------------------------------------------------------------ proactive
    def _proactive_ui(self, message: str, original) -> None:
        try:
            original(message)
        except Exception:
            pass
        # Memory 2.0: real system events become SYSTEM knowledge (never user fabrications)
        try:
            if self.runtime:
                self.runtime.memory.add("SYSTEM", message[:300])
        except Exception:
            pass
        key = "ram" if "RAM" in message else "cpu" if "CPU" in message else "proactive"
        # CODING/DEEP_FOCUS: do not interrupt the Boss (CRITICAL only)
        sentinel = getattr(self.hub, "sentinel", None)
        if sentinel is not None and sentinel.quiet and key != "ollama":
            asyncio.run_coroutine_threadsafe(self.hub.on_activity(message + " [quiet]", "warn"), self.loop)
            return
        # notifier.notify is synchronous + thread-safe (cooldown inside)
        self.hub.notifier.notify(key, message, "warn")
        # V2: severity matrix decides whether this also gets SPOKEN
        self.policy.handle(key, message, "warn")
        asyncio.run_coroutine_threadsafe(self.hub.on_activity(message, "warn"), self.loop)

    # ------------------------------------------------ Agent 2.0 orchestrator
    async def _run_step(self, s: dict, approved: bool) -> dict:
        tool = s["tool"]
        args = s.get("args", {})
        reg = self.runtime.registry.get(tool)
        if reg is None:
            return {"ok": False, "error": f"Tool not registered: {tool}"}
        if reg["dangerous"] and not approved:
            return {"ok": False, "error": f"Confirmation required for: {tool}"}
        try:
            out = await asyncio.to_thread(
                lambda: self.runtime.executor.execute([(tool, args)], approved=approved)[0])
            return {"ok": True, "output": out}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def run_orchestrated(self, text: str, approved: bool) -> bool:
        steps = parse_plan(text)
        if len(steps) < 2:
            return False  # simple requests never enter the planner
        hub = self.hub
        agent = hub.agent
        await agent.set_state("THINKING", f"Intent: composite task ({len(steps)} steps)")
        await asyncio.sleep(0.2)
        await agent.set_state("PLANNING", " → ".join(f"{i + 1}. {s['label']}" for i, s in enumerate(steps)))
        await asyncio.sleep(0.2)
        risks = risk_report(steps)
        if risks and not approved:
            tid = uuid.uuid4().hex[:8]
            hub.pending_task = {
                "id": tid, "text": text, "created": time.time(), "risks": risks,
                "steps": [{"label": s["label"], "tool": s["tool"], "dangerous": s["dangerous"]} for s in steps],
            }
            await hub.broadcast_task()
            await agent.set_state("WAITING_APPROVAL", f"RISK ANALYSIS: {', '.join(risks)} — approval required")
            hub.notifier.notify("approval", f"Onay bekleyen görev: {tid}", "warn", force=True)
            await agent._schedule_idle()
            return True
        results = []
        for i, s in enumerate(steps, 1):
            await agent.set_state("EXECUTING", f"Step {i}/{len(steps)}: {s['label']}")
            res = await self._run_step(s, approved)
            if not res["ok"] and should_retry(res.get("error")):
                await agent.set_state("EXECUTING", f"RECOVERY: safe retry {s['label']} (1/1)")
                res = await self._run_step(s, approved)
                res["retried"] = True
            if not res["ok"]:
                alt = ALTERNATIVES.get(s["tool"])
                if alt:
                    await agent.set_state("EXECUTING", f"RECOVERY: alternative tool {alt}")
                    res = await self._run_step({"tool": alt, "args": s.get("args", {}), "label": s["label"]}, approved)
                    res["alternative"] = alt
            if res["ok"] and s.get("verify") == "find_window":
                try:
                    v = await asyncio.to_thread(
                        lambda: self.runtime.executor.execute(
                            [("find_window", {"title": s["args"].get("name", "")})], approved=True)[0])
                    res["verified"] = bool(v.get("found"))
                except Exception:  # noqa: BLE001
                    res["verified"] = False
            results.append((s, res))
        await agent.set_state("VERIFYING", "Aggregating step results…")
        await asyncio.sleep(0.2)
        okc = sum(1 for _, r in results if r["ok"])
        summary = " · ".join(
            f"{s['label']}: {'OK' + ('[verified]' if r.get('verified') else '') if r['ok'] else 'FAIL(' + str(r.get('error'))[:40] + ')'}"
            for s, r in results)
        await hub.on_activity(f"Plan {okc}/{len(results)}: {summary[:160]}", "success" if okc else "error")
        if okc == len(results):
            await agent.set_state("DONE", f"PLAN COMPLETE {okc}/{len(results)} — {summary}")
            hub.notifier.notify("task", "Görev tamamlandı.", "success", force=True)
        elif okc == 0:
            await agent.set_state("ERROR", f"PLAN FAILED 0/{len(results)} — {summary}")
            hub.notifier.notify("task", "Görev başarısız oldu.", "error", force=True)
        else:
            await agent.set_state("DONE", f"PLAN PARTIAL {okc}/{len(results)} — {summary}")
            hub.notifier.notify("task", f"Görev kısmen tamamlandı: {okc}/{len(results)}.", "warn", force=True)
        await agent._schedule_idle()
        return True

    # ------------------------------------------------ vision flow (LLaVA)
    def is_vision_flow(self, text: str) -> bool:
        return vision_pattern(text)

    async def run_vision(self, text: str, approved: bool = False) -> bool:
        """capture → verify PNG → llava vision → report. Two separate stages,
        real model output only; honest ERROR/UNAVAILABLE otherwise."""
        hub = self.hub
        agent = hub.agent
        if not self.runtime:
            await agent.set_state("ERROR", f"V16 runtime unavailable: {self.error}")
            await agent._schedule_idle()
            return True
        m = re.search(r"(\S+\.(?:png|jpe?g))", text, re.I)
        _vlog(f"detected vision intent for: {text[:80]!r}")
        await agent.set_state("THINKING", "Vision isteği algılandı (screenshot ≠ analiz).")
        await asyncio.sleep(0.2)
        await agent.set_state("PLANNING", "Plan: capture → verify PNG → llava vision → report")

        path = m.group(1) if m else None
        if not path:
            await agent.set_state("EXECUTING", "SCREENSHOT: ekran görüntüsü alınıyor…")
            try:
                path = await asyncio.to_thread(
                    lambda: self.runtime.executor.execute([("capture_screen", {})], approved=True)[0])
            except Exception as exc:  # noqa: BLE001
                _vlog(f"SCREENSHOT stage failed: {exc}")
                await agent.set_state("ERROR", f"SCREENSHOT stage failed: {exc}")
                await agent._schedule_idle()
                return True
        # stage 2: verify the PNG actually exists and is non-empty
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            _vlog(f"PNG verify FAILED: {path}")
            await agent.set_state("ERROR", f"PNG doğrulanamadı: {path}")
            await agent._schedule_idle()
            return True
        _vlog(f"screenshot path={path} size={os.path.getsize(path)}")

        models = hub.ai_status.get("models") or []
        model = self.runtime.vision_llm.resolve_model(models) or "(yok)"
        _vlog(f"selected vision model={model} endpoint={self.runtime.brain.base_url}/api/chat")
        target = click_target(text)  # Vision 2.0: locate + (confirmed) click
        prompt = build_prompt(target) if target else VISION_PROMPT
        await agent.set_state("EXECUTING", f"VISION: {model} gerçek PNG'i analiz ediyor…")
        try:
            analysis = await asyncio.to_thread(
                self.runtime.vision_llm.analyze, path, prompt, models)
        except Exception as exc:  # noqa: BLE001
            # LLaVA unavailable → honest fallback to real OCR, else real ERROR
            try:
                ocr = await asyncio.to_thread(
                    lambda: self.runtime.executor.execute([("screen_ocr", {"path": path})], approved=True)[0])
                if ocr.get("text"):
                    await agent.set_state("VERIFYING", "Vision yok → gerçek OCR doğrulaması…")
                    await asyncio.sleep(0.2)
                    await agent.set_state("DONE", f"VISION UNAVAILABLE ({exc}). OCR: {ocr['text'][:1500]}")
                    await agent._schedule_idle()
                    return True
            except Exception:  # noqa: BLE001
                pass
            await agent.set_state("ERROR", f"VISION UNAVAILABLE: {exc}")
            await agent._schedule_idle()
            return True
        await agent.set_state("VERIFYING", "Analiz yanıtı doğrulanıyor…")
        await asyncio.sleep(0.2)
        if not analysis or analysis.startswith("Vision LLM devre dışı"):
            await agent.set_state("ERROR", "VISION UNAVAILABLE: model boş yanıt verdi.")
            await agent._schedule_idle()
            return True
        if not target:
            final = f"EKRAN ANALİZİ ({model}): {analysis}"
            _vlog(f"final response sent to UI head={final[:160]!r}")
            await agent.set_state("DONE", final)
            await hub.on_activity(f"Vision analysis via {model}", "success")
            await agent._schedule_idle()
            return True
        # ---- Vision 2.0 targeting: coordinates are UNTRUSTED → confirm gate
        coords = parse_coords(analysis)
        if coords is None:
            await agent.set_state(
                "DONE",
                f"EKRAN ANALİZİ ({model}): {analysis} · KONUM güvenilir değil → tıklama YAPILMADI. "
                f"Hedefi netleştirip tekrar isteyin.",
            )
            await agent._schedule_idle()
            return True
        if not approved:
            tid = uuid.uuid4().hex[:8]
            hub.pending_task = {
                "id": tid, "text": text, "created": time.time(),
                "risks": [f"gui_click@{coords[0]:.0f},{coords[1]:.0f}"],
                "steps": [{"label": f"click: {target}", "tool": "gui_click", "dangerous": True}],
                "coords": list(coords),
            }
            await hub.broadcast_task()
            await agent.set_state(
                "WAITING_APPROVAL",
                f"Vision hedefi: {target} → tahmini KONUM {coords[0]:.0f},{coords[1]:.0f} (LLaVA tahmini, güvenilmez) — onay gerekli",
            )
            hub.notifier.notify("approval", f"Vision click onayı: {tid}", "warn", force=True)
            await agent._schedule_idle()
            return True
        nx, ny = coords
        await agent.set_state("EXECUTING", f"CLICK: {target} @ {nx:.0f},{ny:.0f}")
        try:
            def _click():
                import pyautogui
                w, h = pyautogui.size()
                x, y = int(w * nx / 100), int(h * ny / 100)
                pyautogui.click(x, y)
                return [x, y]
            px = await asyncio.to_thread(_click)
        except Exception as exc:  # noqa: BLE001
            await agent.set_state("ERROR", f"CLICK failed: {exc}")
            await agent._schedule_idle()
            return True
        await agent.set_state("VERIFYING", "Post-click screenshot doğrulaması…")
        try:
            vpath = await asyncio.to_thread(
                lambda: self.runtime.executor.execute([("capture_screen", {})], approved=True)[0])
            v = await asyncio.to_thread(self.runtime.vision_llm.analyze, vpath,
                                        f"Tıklama sonrası ekran: '{target}' hedefi gerçekleşti mi? Kısa cevap.", models)
            await agent.set_state("DONE", f"CLICK OK {px} · VERIFY: {v[:300]}")
        except Exception as exc:  # noqa: BLE001
            await agent.set_state("DONE", f"CLICK OK {px} · VERIFY UNAVAILABLE: {exc}")
        await agent._schedule_idle()
        return True

    async def _emit_tests(self, name: str, status: str, detail: str) -> None:
        await self.hub.broadcast({"type": "tests", "data": {"name": name, "status": status, "detail": detail[:200]}})

    # ------------------------------------------------ code-intent pre-route
    async def run_code_intents(self, text: str, approved: bool) -> bool:
        hub = self.hub
        agent = hub.agent
        t = text.lower()
        pending = hub.codegen.pending()

        # approval control from chat
        if pending and any(k in t for k in ("onaylıyorum", "onayliyorum", "approve", "uygula")):
            await self._apply(pending[0]["id"])
            return True
        if pending and any(k in t for k in ("reddet", "reject", "vazgeç")):
            hub.codegen.reject(pending[0]["id"])
            await agent.set_state("IDLE", "Patch rejected.")
            await hub.broadcast({"type": "patch", "data": None})
            await agent._schedule_idle()
            return True
        if pending and any(k in t for k in ("değişiklikleri göster", "değişiklikleri bana göster", "diff")):
            await hub.broadcast({"type": "patch", "data": pending[0]})
            await agent.set_state("WAITING_APPROVAL", f"Patch {pending[0]['id']} — VIEW DIFF open.")
            return True

        if any(k in t for k in ("test et", "testleri çalıştır", "test çalıştır")):
            await agent.set_state("EXECUTING", "Running validation suite…")
            results = await test_runner.run_all(self._emit_tests, quick=True)
            lines = " · ".join(("✓" if r["ok"] else "✗") + r["name"] for r in results)
            failed = [r["name"] for r in results if not r["ok"]]
            await agent.set_state("DONE" if not failed else "ERROR", f"TESTS: {lines}")
            await agent._schedule_idle()
            return True

        mfile = re.search(r"([\w./\-]+\.(?:py|tsx?|jsx?|css))", text)
        if mfile and any(k in t for k in ("ne yapıyor", "açıkla", "open dosya", "bu dosya")):
            info = await asyncio.to_thread(hub.code_intel.explain_file, mfile.group(1))
            if info.get("error"):
                await agent.set_state("ERROR", info["error"])
            else:
                await agent.set_state(
                    "DONE",
                    f"{info['file']} [{info['lang']}, {info['lines']}L] fn={len(info['functions'])} "
                    f"cls={len(info['classes'])} · {str(info.get('docstring') or '')[:140]}",
                )
            await agent._schedule_idle()
            return True

        if any(k in t for k in ("analiz et", "analiz", "bugları bul", "bug bul", "performans sorun",
                                "kullanılmayan", "duplicate", "kendi kodunu", "kendini analiz",
                                "kodunu analiz")):
            target = "frontend" if "frontend" in t else "backend" if "backend" in t else "all"
            await agent.set_state("THINKING", f"Scanning {target}…")
            rep = await asyncio.to_thread(hub.code_intel.analyze, target)
            await agent.set_state("VERIFYING", "Aggregating issues…")
            high = [i for i in rep["issues"] if i["severity"] in ("high", "error")]
            top = "; ".join(f"{i['file'].split('/')[-1]}:{i['line']} {i['message']}" for i in rep["issues"][:3])
            await agent.set_state(
                "DONE",
                f"ANALYSIS[{target}] {rep['summary']['files']} files · {len(rep['issues'])} issues "
                f"({len(high)} high) · {len(rep['duplicates'])} dup · {len(rep['todo'])} TODO"
                + (f" · TOP: {top}" if top else ""),
            )
            await hub.on_activity(f"Code analysis [{target}] completed", "success")
            await agent._schedule_idle()
            return True

        if any(k in t for k in ("hatayı düzelt", "bu hatayı", "fix the bug")):
            await agent.set_state("THINKING", "Root-cause scan…")
            rep = await asyncio.to_thread(hub.code_intel.analyze, "all")
            highs = [i for i in rep["issues"] if i["severity"] in ("high", "error")][:5]
            if not highs:
                await agent.set_state("DONE", "No high-severity static issues found.")
                await agent._schedule_idle()
                return True
            root = highs[0]
            files = sorted({i["file"] for i in highs})
            report = (f"BUG FOUND: {len(highs)} issue(s) · ROOT CAUSE: {root['message']} @ "
                      f"{root['file']}:{root['line']} · FILES: {', '.join(files)} · "
                      f"TEST PLAN: tsc+compileall+REST/WS")
            brain = self.runtime.brain if hub.bridge_available_llm() else None
            if brain:
                res = await hub.codegen.propose(f"Fix: {root['message']} in {root['file']}", brain=brain)
                if res.get("ok"):
                    await agent.set_state("WAITING_APPROVAL", f"{report} · Patch {res['proposal']['id']} ready.")
                    await hub.broadcast({"type": "patch", "data": res["proposal"]})
                    return True
            await agent.set_state("DONE", report + " · PROPOSED FIX: Ollama offline → LLM patch yok; manuel düzeltme.")
            await agent._schedule_idle()
            return True

        if ("tool" in t or "araç" in t) and any(k in t for k in ("oluştur", "yarat", "ekle", "yaz", "üret")) or (
            any(k in t for k in ("panel", "bileşen")) and any(k in t for k in ("oluştur", "yarat", "ekle", "üret"))):
            await agent.set_state("PLANNING", "Code generation: ANALYZE→PLAN→GENERATE→REVIEW")
            brain = self.runtime.brain if hub.bridge_available_llm() else None
            res = await hub.codegen.propose(text, brain=brain)
            if res.get("ok"):
                p = res["proposal"]
                await agent.set_state("WAITING_APPROVAL",
                                      f"Patch {p['id']} hazır ({p['source']}, {len(p['files'])} files) — APPROVE / REJECT / VIEW DIFF")
                await hub.broadcast({"type": "patch", "data": p})
                hub.notifier.notify("approval", f"Onay bekleyen patch: {p['id']}", "warn", force=True)
            else:
                await agent.set_state("ERROR", res.get("error", "generation failed"))
                await agent._schedule_idle()
            return True
        return False

    async def _apply(self, pid: str) -> None:
        hub = self.hub
        agent = hub.agent
        await agent.set_state("EXECUTING", f"Applying patch {pid} (approved) + auto tests…")
        res = await hub.codegen.apply(pid, run_tests=lambda quick=True: test_runner.run_all(self._emit_tests, quick=quick))
        if res.get("ok"):
            await agent.set_state("DONE", f"Patch {pid} applied — tests passed.")
        else:
            await agent.set_state("ERROR", f"Patch {pid} failed → rolled back: {res.get('error')}")
        await hub.broadcast({"type": "patch", "data": None})
        await agent._schedule_idle()

    # ------------------------------------------------------------ tier-2 run
    async def run(self, text: str, approved: bool = False) -> None:
        hub = self.hub
        agent = hub.agent
        if self.is_vision_flow(text):
            await self.run_vision(text, approved)
            return
        if await self.run_code_intents(text, approved):
            return
        if await self.run_orchestrated(text, approved):
            return
        if not self.runtime:
            await agent.set_state("ERROR", f"V16 runtime unavailable: {self.error}")
            await agent._schedule_idle()
            return
        await agent.set_state("PLANNING", "V16 agent: deterministic routes → Ollama conversation")
        await asyncio.sleep(0.25)
        await agent.set_state("EXECUTING", "V16 agent running…")
        self.voice_stack.mark("llm")
        try:
            answer = await asyncio.to_thread(self.runtime.ask, text, approved)
        except Exception as exc:  # noqa: BLE001
            self.voice_stack.mark("llm")
            self.voice_stack.commit()
            await agent.set_state("ERROR", f"V16 runtime error: {exc}")
            await hub.on_activity(f"V16 runtime error: {exc}", "error")
            await agent._schedule_idle()
            return
        self.voice_stack.mark("llm")
        self.voice_stack.commit()
        await agent.set_state("VERIFYING", "Validating V16 answer…")
        await asyncio.sleep(0.2)
        ans = str(answer or "").strip()
        failed = (not ans) or ans.startswith(("İşlemi tamamlayamadım", "HATA", "Görevi güvenli"))
        if failed:
            await agent.set_state("ERROR", ans or "Cevap alınamadı.")
            await hub.on_activity("V16 agent failed", "error")
        else:
            await agent.set_state("DONE", ans)
            await hub.on_activity(f"V16: {ans[:70]}", "success")
        await agent._schedule_idle()

    # ------------------------------------------------------------ capabilities
    def capabilities(self) -> list[dict]:
        ai = self.hub.ai_status
        win = sys.platform == "win32"
        rows = [
            {
                "id": "vision_llm",
                "label": "Vision LLM",
                "status": "READY" if (_have("PIL") and ai.get("vision")) else "UNAVAILABLE",
                "detail": "ollama vision model + Pillow" if not (_have("PIL") and ai.get("vision")) else "ready",
            },
            {
                "id": "ocr",
                "label": "OCR",
                "status": "READY" if (_have("pytesseract") and _have("PIL")) else "UNAVAILABLE",
                "detail": "pytesseract+Pillow",
            },
            {
                "id": "gui",
                "label": "GUI Automation",
                "status": "READY" if (_have("pyautogui") and win) else "UNAVAILABLE",
                "detail": "pyautogui (Windows)",
            },
            {
                "id": "stt",
                "label": "Live Voice STT",
                "status": "READY" if (_have("sounddevice") and _have("faster_whisper")) else "UNAVAILABLE",
                "detail": "sounddevice+faster-whisper",
            },
            {
                "id": "planner",
                "label": "Planner",
                "status": "READY" if ai.get("connected") else "UNAVAILABLE",
                "detail": "requires Ollama",
            },
            {
                "id": "code_agent",
                "label": "Code Agent",
                "status": "READY" if self.runtime else "UNAVAILABLE",
                "detail": "analyze/patch/test (apply gated)",
            },
            {
                "id": "proactive",
                "label": "Proactive Monitor",
                "status": "READY" if (self.runtime and self.runtime.proactive.running) else "UNAVAILABLE",
                "detail": "ram/cpu watchdog",
            },
            {
                "id": "find_window",
                "label": "Find Window",
                "status": "READY" if _have("pygetwindow") else "UNAVAILABLE",
                "detail": "pygetwindow",
            },
            {
                "id": "focus_window",
                "label": "Focus Window",
                "status": "READY" if _have("pygetwindow") else "UNAVAILABLE",
                "detail": "pygetwindow",
            },
            {
                "id": "close_application",
                "label": "Close App",
                "status": "READY",
                "detail": "taskkill/pkill · confirmation required",
            },
            {
                "id": "open_file",
                "label": "Open File",
                "status": "READY" if (win or shutil_which("xdg-open")) else "UNAVAILABLE",
                "detail": "startfile/xdg-open",
            },
            {
                "id": "open_folder",
                "label": "Open Folder",
                "status": "READY" if (win or shutil_which("xdg-open")) else "UNAVAILABLE",
                "detail": "explorer/xdg-open",
            },
        ]
        return rows

    # ------------------------------------------------------------ lifecycle
    def start_proactive(self) -> None:
        if self.runtime and self.runtime.settings.get("proactive", {}).get("enabled", False):
            self.runtime.start_proactive()

    def stop(self) -> None:
        if self.runtime:
            self.runtime.stop_proactive()
            try:
                self.runtime.stop_live_voice()
            except Exception:
                pass
