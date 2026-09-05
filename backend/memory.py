"""Session + persistent memory. Persistent memory is a real JSON file on disk."""
import json
import os
import time
from collections import deque


class MemorySystem:
    QUOTA_BYTES = 1024 * 1024  # 1 MiB soft quota for the UI meter

    def __init__(self, data_dir: str, persistent_enabled: bool) -> None:
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "memory.json")
        self.persistent_enabled = persistent_enabled
        self.session: deque[dict] = deque(maxlen=200)
        self.persistent: list[dict] = []
        if self.persistent_enabled:
            self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self.persistent = data if isinstance(data, list) else []
        except Exception:
            self.persistent = []

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.persistent, f, indent=1)
        os.replace(tmp, self.path)

    def add_session(self, kind: str, text: str) -> None:
        self.session.appendleft({"ts": time.time(), "kind": kind, "text": text})

    def add_persistent(self, text: str) -> bool:
        if not self.persistent_enabled:
            return False
        self.persistent.insert(0, {"ts": time.time(), "text": text})
        self.persistent = self.persistent[:200]
        self._save()
        return True

    def clear_session(self) -> None:
        self.session.clear()

    def clear_all(self) -> None:
        self.session.clear()
        self.persistent = []
        if self.persistent_enabled:
            self._save()

    def status(self) -> dict:
        size = 0
        if self.persistent_enabled:
            try:
                if os.path.exists(self.path):
                    size = os.path.getsize(self.path)
            except OSError:
                size = 0
        return {
            "session_count": len(self.session),
            "persistent_enabled": self.persistent_enabled,
            "persistent_count": len(self.persistent) if self.persistent_enabled else 0,
            "usage_bytes": size,
            "quota_bytes": self.QUOTA_BYTES,
            "recent_session": list(self.session)[:6],
            "recent_persistent": self.persistent[:6] if self.persistent_enabled else [],
        }
