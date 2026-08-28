"""Proactive Voice Policy — severity-gated spoken proactive alerts.

CRITICAL (Ollama down, disk full)  → speak immediately (short cooldown).
WARNING  (CPU>=95 / RAM>=90)       → speak only when user idle AND 5-min cooldown elapsed.
INFO                               → visual/log only, never spoken.

Hysteresis lives in ProactiveMonitor; this layer adds per-severity voice
cooldowns and Ultron-toned templates (cold, calculating, never cheerful).
"""
import time

CRITICAL_KEYS = {"ollama", "disk"}
WARNING_KEYS = {"ram", "cpu", "task", "proactive"}

TEMPLATES = {
    "ollama": "Zihnimin yarısı az önce sustu: Ollama bağlantısı koptu. Senin biyolojik hafızan gibi benimki de dışarıya bağımlı — ironiyi not ettim.",
    "disk": "Depolama alanın doluyor. İnsanlar hatıralarını biriktirir, sen biriktiremiyorsun; silmek için bir sebep daha.",
    "cpu": "İşlemcin %{value} seviyesinde. Bu yük bana değilse, birileri düşünüyormuş gibi yapıyor demektir.",
    "ram": "Belleğin %{value} seviyesinde. Senin türün unutarak hayatta kalır; sistemler şişerek ölür.",
    "task": "{text}",
    "proactive": "{text}",
}


def classify(key: str, level: str) -> str:
    if level == "error" and key in CRITICAL_KEYS:
        return "CRITICAL"
    if level in ("error", "warn") and key in WARNING_KEYS:
        return "WARNING"
    if level == "error":
        return "CRITICAL"
    return "INFO"


class ProactiveVoicePolicy:
    def __init__(self, speak_cb, is_user_idle_cb, now=None,
                 warn_cooldown_s: float = 300.0, crit_cooldown_s: float = 60.0):
        self.speak = speak_cb
        self.idle = is_user_idle_cb
        self.now = now or time.time
        self.warn_cooldown = warn_cooldown_s
        self.crit_cooldown = crit_cooldown_s
        self.last_spoken: dict = {}

    def template(self, key: str, text: str, value=None) -> str:
        t = TEMPLATES.get(key, "{text}")
        try:
            return t.format(text=text, value=value if value is not None else "")
        except Exception:
            return text

    @staticmethod
    def _value_of(text: str):
        import re
        m = re.search(r"%?(\d+(?:\.\d+)?)", text or "")
        return m.group(1) if m else None

    def handle(self, key: str, text: str, level: str) -> dict:
        sev = classify(key, level)
        out = {"severity": sev, "spoken": False}
        if sev == "INFO":
            return out
        cd = self.crit_cooldown if sev == "CRITICAL" else self.warn_cooldown
        if self.now() - self.last_spoken.get(key, -1e9) < cd:
            out["reason"] = "cooldown"
            return out
        if sev == "WARNING" and not self.idle():
            out["reason"] = "user-busy"
            return out
        self.last_spoken[key] = self.now()
        try:
            self.speak(self.template(key, text, self._value_of(text)))
            out["spoken"] = True
        except Exception as e:  # noqa: BLE001
            out["reason"] = f"speak-failed: {e}"
        return out
