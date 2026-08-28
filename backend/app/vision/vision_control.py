"""Vision Control — master privacy switch for the screen watcher.

Default OFF: nothing is observed until the Boss enables it (endpoint, UI or
voice command).  Paused => zero capture, zero analysis.
"""


class VisionControl:
    def __init__(self, watcher, engine, enabled_default=False):
        self.watcher = watcher
        self.engine = engine
        self.watcher.enabled = bool(enabled_default)

    def toggle(self, enabled: bool) -> dict:
        self.watcher.enabled = bool(enabled)
        if not enabled:
            self.watcher.last_frame = None  # discard retained pixels on pause
        return self.status()

    def status(self) -> dict:
        return {
            "status": "active" if self.watcher.enabled else "paused",
            "fps": round(1.0 / self.watcher.base_interval, 2),
            "last_diff": self.watcher.last_diff,
            "target_app": self.watcher.last_title,
            "analyses": self.watcher.analyses,
            "privacy_blocks": self.watcher.privacy_blocks,
        }

    def scan_now(self, frame=None, title=None) -> dict:
        """Immediate one-shot observation regardless of interval."""
        if not self.watcher.enabled:
            return {"status": "paused", "analyzed": False}
        res = self.watcher.tick(frame=frame, title=title)
        return res
