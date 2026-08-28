"""Sovereign Privacy Shield — local-first audit + architectural cloud block.

sovereign_mode=true => any non-local endpoint raises SovereignViolation before
a request leaves the process.  Startup audit reports LLM/TTS/STT locality.
"""
import re
import time

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")

state = {"sovereign_mode": False, "denied": []}


class SovereignViolation(Exception):
    pass


def configure(settings: dict) -> None:
    state["sovereign_mode"] = bool(settings.get("sovereign_mode", False))


def is_local(endpoint: str) -> bool:
    if not endpoint:
        return True
    if endpoint.startswith(("/", ".")):
        return True
    m = re.match(r"(?:https?://)?([^/:]+)", endpoint)
    host = (m.group(1) if m else endpoint).lower()
    return host in _LOCAL_HOSTS or host.endswith(".local")


def assert_local(endpoint: str) -> None:
    if state["sovereign_mode"] and not is_local(endpoint):
        state["denied"].append({"ts": time.time(), "endpoint": endpoint})
        msg = f"Sovereign mode aktif, dış çağrı reddedildi: {endpoint}"
        print(f"[ULTRON-SOVEREIGN] {msg}", flush=True)
        raise SovereignViolation(msg)


def assert_vision_residency(dest: str) -> None:
    """Screen pixels never leave local disk/memory — architectural."""
    if not is_local(dest):
        msg = f"Sovereign: ekran verisi yerel dışına çıkamaz: {dest}"
        print(f"[ULTRON-SOVEREIGN] {msg}", flush=True)
        raise SovereignViolation(msg)


def audit(llm_host: str, tts_backend: str | None, stt_local: bool,
          ollama_connected: bool) -> dict:
    llm_local = is_local(llm_host or "")
    return {
        "sovereign_mode": state["sovereign_mode"],
        "llm": {"endpoint": llm_host, "local": llm_local,
                "status": "PASS" if (llm_local and ollama_connected) else
                          ("LOCAL-BUT-OFFLINE" if llm_local else "FAIL")},
        "tts": {"backend": tts_backend, "local": tts_backend is not None,
                "status": "PASS" if tts_backend else "NONE"},
        "stt": {"local": bool(stt_local),
                "status": "PASS" if stt_local else "BROWSER-CLOUD-OR-ABSENT"},
        "denied_calls": state["denied"][-10:],
    }


def sovereign_status(a: dict) -> str:
    if a["sovereign_mode"]:
        return "ENFORCED" if a["llm"]["local"] else "VIOLATED"
    return "LOCAL-READY" if a["llm"]["local"] and a["tts"]["local"] else "PARTIAL"
