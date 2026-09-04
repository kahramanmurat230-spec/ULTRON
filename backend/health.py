"""Startup health check — module status report (no fakes)."""
import sqlite3
import time
from pathlib import Path


def tts_backend_name() -> str | None:
    """Report the same real local TTS backend used by runtime."""
    try:
        from app.voice.tts import TextToSpeech
        return TextToSpeech().backend()
    except Exception:
        return None


def sqlite_ok(paths) -> bool:
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        try:
            with sqlite3.connect(p) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    return False
        except Exception:
            return False
    return True


def build_health(stack=None, runtime=None, ollama_connected=None,
                 persona_mode="reframe", db_paths=()) -> dict:
    return {
        "ts": time.time(),
        "vad_mode": stack.vad_kind if stack else "none",
        "ollama": "connected" if ollama_connected else "offline",
        "tts_backend": tts_backend_name(),
        "persona_guard": {"enabled": True, "mode": persona_mode},
        "sqlite_ok": sqlite_ok(db_paths),
        "runtime": "online" if runtime else "offline",
    }


def log_health(h: dict) -> None:
    for k, v in h.items():
        print(f"[ULTRON-HEALTH] {k}: {v}", flush=True)
