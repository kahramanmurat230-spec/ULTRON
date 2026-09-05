"""ULTRON system doctor.

Checks runtime health without confusing optional features with core failures.
The doctor is intentionally read-only and returns machine-readable status for
self-diagnostic and the HUD.
"""
import importlib.util
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

CORE_DEPS = ("aiohttp", "psutil")
CAPABILITY_DEPS = {
    "screen": ("PIL",),
    "ocr": ("pytesseract",),
    "gui": ("pyautogui",),
    "stt": ("faster_whisper", "sounddevice"),
    "live_vad": ("webrtcvad", "sounddevice"),
    "semantic_chroma": ("chromadb",),
    "emotion_librosa": ("librosa",),
}


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def check_deps() -> dict:
    core = {m: _have(m) for m in CORE_DEPS}
    capabilities = {name: all(_have(m) for m in mods) for name, mods in CAPABILITY_DEPS.items()}
    missing_core = [m for m, ok in core.items() if not ok]
    status = "FAIL" if missing_core else "PASS"
    return {"python": sys.version.split()[0], "core": core, "capabilities": capabilities,
            "status": status, "missing_core": missing_core}


def check_ollama(host: str, want=("qwen", "llava")) -> dict:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=2.5) as r:
            models = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception as exc:
        return {"status": "WARN", "connected": False, "models": [],
                "missing_models": list(want), "error": str(exc)[:200]}
    low = [m.lower() for m in models]
    missing = [w for w in want if not any(w in m for m in low)]
    return {"status": "PASS" if not missing else "WARN", "connected": True,
            "models": models, "missing_models": missing}


def check_dbs(paths) -> dict:
    import sqlite3
    rows, bad = [], 0
    for p in paths:
        p = Path(p)
        if not p.exists():
            rows.append({"path": str(p), "ok": False, "reason": "missing"})
            bad += 1
            continue
        try:
            with sqlite3.connect(p) as db:
                ok = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except Exception as exc:
            ok = False
            rows.append({"path": str(p), "ok": False, "reason": str(exc)[:200]})
        else:
            rows.append({"path": str(p), "ok": ok})
        if not ok:
            bad += 1
    return {"status": "FAIL" if bad else "PASS", "dbs": rows}


def check_rules(path) -> dict:
    from app.personal.user_dna import CANONICAL_RULES, _canon_hash
    p = Path(path)
    if not p.exists():
        return {"status": "FAIL", "reason": "missing"}
    try:
        current = json.loads(p.read_text(encoding="utf-8"))
        ok = current == CANONICAL_RULES
    except Exception as exc:
        return {"status": "FAIL", "sha_ok": False, "reason": str(exc)[:200],
                "canonical_sha": _canon_hash()[:16]}
    return {"status": "PASS" if ok else "FAIL", "sha_ok": ok,
            "canonical_sha": _canon_hash()[:16]}


def check_hw() -> dict:
    result = {"screen": _have("PIL"), "mic": False, "speaker": False}
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        result["mic"] = any(d.get("max_input_channels", 0) > 0 for d in devices)
        result["speaker"] = any(d.get("max_output_channels", 0) > 0 for d in devices)
    except Exception:
        result["mic"] = _have("faster_whisper")
        result["speaker"] = sys.platform == "win32"
    missing = [k for k, v in result.items() if not v]
    return {"status": "WARN" if missing else "PASS", **result, "missing": missing}


def check_ports(ports=(8000, 5173, 5174), allow_busy=False) -> dict:
    rows = []
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            free = True
        except OSError:
            free = False
        finally:
            s.close()
        rows.append({"port": port, "free": free})
    busy = [r["port"] for r in rows if not r["free"]]
    return {"status": "PASS" if (allow_busy or not busy) else "WARN", "busy": busy, "rows": rows}


def run_doctor(ctx: dict, persona=None) -> dict:
    sections = {
        "deps": check_deps(),
        "ollama": check_ollama(ctx.get("ollama_host", "http://127.0.0.1:11434")),
        "databases": check_dbs(ctx.get("db_paths", [])),
        "master_rules": check_rules(ctx.get("rules_path", "config/security/master_rules.json")),
        "hardware": check_hw(),
        "ports": check_ports(ctx.get("ports", (8000, 5173, 5174)), allow_busy=bool(ctx.get("allow_busy_ports", False))),
    }
    fails = [k for k, v in sections.items() if v.get("status") == "FAIL"]
    warns = [k for k, v in sections.items() if v.get("status") == "WARN"]
    overall = "FAIL" if fails else ("WARN" if warns else "PASS")
    n_ok = sum(1 for v in sections.values() if v.get("status") == "PASS")
    summary = f"Boss, teşhis tamam: {n_ok}/{len(sections)} bölüm kusursuz. "
    if fails:
        summary += f"Kritik arıza: {', '.join(fails)}. "
    if warns:
        summary += f"Uyarılar: {', '.join(warns)}. "
    if overall == "PASS":
        summary += "Tüm zorunlu kontroller geçti."
    elif not fails:
        summary += "Çekirdek sistem çalışıyor; uyarılar isteğe bağlı yeteneklere veya geçmiş kayıtlara ait."
    if persona:
        try:
            rep = persona.check(summary)
            if rep.get("violations"):
                summary = persona.reframe(summary)
            if "boss" not in summary.lower():
                summary = "Boss, " + summary
        except Exception:
            pass
    return {"ts": time.time(), "overall": overall, "sections": sections, "summary": summary}
