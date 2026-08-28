"""Cooldown/debounce notifier — proactive alerts without spam."""
import time


class Notifier:
    def __init__(self, callback, cooldown_s: float = 60.0):
        self.cb = callback
        self.cooldown = cooldown_s
        self.last: dict[str, float] = {}

    def notify(self, key: str, text: str, level: str = "warn", force: bool = False) -> bool:
        now = time.time()
        if not force and now - self.last.get(key, -1e9) < self.cooldown:
            return False
        self.last[key] = now
        try:
            self.cb(text, level)
        except Exception:
            pass
        return True
