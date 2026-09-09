"""Bounded reversible action journal for ULTRON.

Storage-only: recording an action never grants execution permission.
"""
from __future__ import annotations
import hashlib, json, time
from pathlib import Path
from threading import RLock

class UndoJournal:
    def __init__(self, root, max_entries=100, max_snapshot_bytes=2_000_000):
        self.root = Path(root).resolve()
        self.max_entries = max(1, int(max_entries))
        self.max_snapshot_bytes = max(1, int(max_snapshot_bytes))
        self._entries = []
        self._lock = RLock()

    def _safe(self, path):
        p = Path(path)
        if not p.is_absolute(): p = self.root / p
        p = p.resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError("Undo yolu proje dışına çıkamaz.")
        return p

    @staticmethod
    def _digest(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None

    def snapshot(self, path):
        p = self._safe(path)
        if not p.exists(): return {"path": str(p), "exists": False, "content": None, "digest": None}
        if not p.is_file(): raise ValueError("Undo snapshot yalnızca dosyalar için destekleniyor.")
        if p.stat().st_size > self.max_snapshot_bytes:
            raise ValueError(f"Undo snapshot dosya boyutu sınırını aşıyor ({self.max_snapshot_bytes} bytes).")
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"path": str(p), "exists": True, "content": content, "digest": self._digest(content)}

    def record(self, operation, before, after):
        entry = {"id": str(time.time_ns()), "operation": str(operation), "before": before, "after": after, "ts": time.time()}
        with self._lock:
            self._entries.append(entry)
            self._entries = self._entries[-self.max_entries:]
        return entry["id"]

    def record_file_change(self, operation, path, before):
        return self.record(operation, before, self.snapshot(path))

    def list(self):
        with self._lock: return [json.loads(json.dumps(x)) for x in reversed(self._entries)]

    def undo(self, entry_id=None):
        with self._lock:
            if not self._entries: return {"ok": False, "status": "EMPTY"}
            idx = next((i for i,e in enumerate(self._entries) if entry_id is None or e["id"] == entry_id), None)
            if idx is None: return {"ok": False, "status": "NOT_FOUND"}
            entry = self._entries[idx]; target = entry["before"]; expected = entry.get("after") or {}
            p = self._safe(target["path"])
            # Never overwrite a newer change made after the journal entry.
            current = self.snapshot(p)
            if current.get("digest") != expected.get("digest") or current.get("exists") != expected.get("exists"):
                return {"ok": False, "status": "STALE", "id": entry["id"], "operation": entry["operation"], "path": str(p)}
            if target["exists"]:
                p.parent.mkdir(parents=True, exist_ok=True); p.write_text(target["content"], encoding="utf-8")
            else: p.unlink(missing_ok=True)
            self._entries.pop(idx)
            return {"ok": True, "status": "UNDONE", "id": entry["id"], "operation": entry["operation"], "path": str(p)}

    def clear(self):
        with self._lock: self._entries.clear()
