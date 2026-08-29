"""Sovereign Privacy Shield — local-first audit + architectural cloud block.

sovereign_mode=true => any non-local endpoint raises SovereignViolation before
a request leaves the process.  Startup audit reports LLM/TTS/STT locality.
"""
import re
import time

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")

# Engines that synthesize ON THIS MACHINE. edge-tts streams from Microsoft's
# cloud service, so it is real neural TTS but NOT local — the audit must say so.
LOCAL_TTS_BACKENDS = ("kokoro", "pykokoro")

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


# DÜRÜST yerellik: bulut motorları ASLA local sayılmaz
CLOUD_TTS = ("edge", "azure", "google", "amazon", "polly", "openai", "eleven")


def _tts_is_local(backend: str | None) -> bool:
    if not backend:
        return False
    b = backend.lower()
    return not any(c in b for c in CLOUD_TTS)


def audit(llm_host: str, tts_backend: str | None, stt_local: bool,
          ollama_connected: bool) -> dict:
    llm_local = is_local(llm_host or "")
    # BİRLEŞİK (merge): iki dürüstlük katmani birlikte — allowlist (R)
    # VE bulut-önek denylist (L). Yerel denmesi için ikisi de geçmeli.
    tts_local = (bool(tts_backend)
                 and tts_backend in LOCAL_TTS_BACKENDS
                 and _tts_is_local(tts_backend))
    return {
        "sovereign_mode": state["sovereign_mode"],
        "llm": {"endpoint": llm_host, "local": llm_local,
                "status": "PASS" if (llm_local and ollama_connected) else
                          ("LOCAL-BUT-OFFLINE" if llm_local else "FAIL")},
        "tts": {"backend": tts_backend, "local": tts_local,
                "status": "PASS" if tts_local else
                          ("CLOUD" if tts_backend else "NONE")},
        "stt": {"local": bool(stt_local),
                "status": "PASS" if stt_local else "BROWSER-CLOUD-OR-ABSENT"},
        "denied_calls": state["denied"][-10:],
    }


def sovereign_status(a: dict) -> str:
    if a["sovereign_mode"]:
        return "ENFORCED" if a["llm"]["local"] else "VIOLATED"
    return "LOCAL-READY" if a["llm"]["local"] and a["tts"]["local"] else "PARTIAL"
