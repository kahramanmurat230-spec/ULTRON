"""World Model — current, quality-aware state of ULTRON's environment.

The world model is live context, not historical memory. Sources degrade
independently, every snapshot is timestamped, source quality is explicit, and
changes can be compared without granting execution authority.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any


def _safe(fn, default=None):
    try:
        out = fn()
        return out if out is not None else default
    except Exception:
        return default


class WorldModel:
    """Deterministic, bounded, read-only aggregation of live world sources."""

    MAX_HISTORY = 8
    DEFAULT_SOURCE_MAX_AGE_S = 30.0

    def __init__(self, sources: dict | None = None, now=None, source_max_age_s=None):
        self.sources = sources or {}
        self.now = now or time.time
        self.source_max_age_s = dict(source_max_age_s or {})
        self.last_snapshot = None
        self.last_snapshot_ts = 0.0
        self._snapshots = deque(maxlen=self.MAX_HISTORY)

    def set_source(self, name, fn, *, max_age_s=None):
        self.sources[name] = fn
        if max_age_s is not None:
            self.source_max_age_s[name] = max(0.0, float(max_age_s))

    def _source_age(self, name: str, value: Any, ts: float) -> float | None:
        if not isinstance(value, dict):
            return None
        observed = value.get("observed_at", value.get("ts"))
        if observed is None:
            return None
        try:
            age = max(0.0, ts - float(observed))
        except (TypeError, ValueError):
            return None
        return age

    def snapshot(self) -> dict:
        ts = float(self.now())
        snap = {"ts": ts, "iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")}
        quality = {}
        for name, fn in self.sources.items():
            value = _safe(fn, {"available": False})
            snap[name] = value
            available = not (isinstance(value, dict) and value.get("available") is False)
            age = self._source_age(name, value, ts)
            max_age = float(self.source_max_age_s.get(name, self.DEFAULT_SOURCE_MAX_AGE_S))
            fresh = available and (age is None or age <= max_age)
            quality[name] = {
                "available": available,
                "fresh": fresh,
                "age_s": round(age, 3) if age is not None else None,
                "max_age_s": max_age,
            }
        snap["_quality"] = quality
        self.last_snapshot = snap
        self.last_snapshot_ts = ts
        self._snapshots.append(snap)
        return snap

    def quality(self, snapshot=None) -> dict:
        snap = snapshot or self.last_snapshot or self.snapshot()
        return dict(snap.get("_quality", {}))

    def changes(self, previous=None, current=None) -> list[dict]:
        """Return bounded top-level source changes; data only, never authority."""
        old = previous or (self._snapshots[-2] if len(self._snapshots) >= 2 else None)
        new = current or self.last_snapshot or self.snapshot()
        if old is None:
            return []
        changed = []
        keys = sorted(set(old) | set(new))
        for key in keys:
            if key in {"ts", "iso", "_quality"}:
                continue
            if old.get(key) != new.get(key):
                changed.append({"source": key, "previous": old.get(key), "current": new.get(key)})
        return changed

    def summary(self) -> dict:
        """Small operational summary for dashboards and diagnostics."""
        snap = self.last_snapshot or self.snapshot()
        q = self.quality(snap)
        available = sum(1 for x in q.values() if x["available"])
        fresh = sum(1 for x in q.values() if x["fresh"])
        return {
            "ts": snap["ts"],
            "sources": len(q),
            "available": available,
            "fresh": fresh,
            "stale": max(0, available - fresh),
            "changes": len(self.changes()) if len(self._snapshots) >= 2 else 0,
        }

    def context_for_llm(self, max_chars: int = 1200) -> str:
        """Compact current-world block for the planner/LLM.

        Source data is treated as untrusted state. It is bounded and contains
        no credential authority; security/approval/risk gates remain external.
        """
        s = self.last_snapshot or self.snapshot()
        lines = []

        def line(key, fmt):
            v = s.get(key)
            if isinstance(v, dict) and v.get("available") is False:
                return
            if v is None:
                return
            try:
                txt = fmt(v)
            except Exception:
                return
            if txt:
                lines.append(txt)

        line("presence", lambda v: f"user={'present' if v.get('boss_in_room') else 'away'} (confidence={v.get('confidence', '?')})")
        line("workspace", lambda v: f"app={v.get('window') or '?'} mode={v.get('mode')}")
        line("screen", lambda v: f"screen={v.get('status', '?')}" + (f" diff={v['diff']}" if v.get("diff") is not None else "") + (f" last_shot_age_s={round(v['age_s'])}" if v.get("age_s") else ""))
        line("task", lambda v: f"active_task={v.get('goal')} [{v.get('status')}] step {v.get('current_step')}/{v.get('steps_total')}")
        line("system", lambda v: (f"cpu={v.get('cpu_percent')}% ram={v.get('ram_percent')}% disk={v.get('disk_percent')}%") if v.get("cpu_percent") is not None else None)
        line("apps", lambda v: f"open_apps={', '.join(v.get('titles', [])[:6])}" if v.get("titles") else None)
        line("files", lambda v: f"recent_files={'; '.join(v.get('files', [])[:4])}" if v.get("files") else None)
        line("iot", lambda v: f"iot={v.get('active', 0)}/{v.get('devices', 0)} active" + (f" scene={v['last_scene']}" if v.get("last_scene") else ""))
        line("events", lambda v: f"last_event={v.get('latest')}" if v.get("latest") else None)

        if not lines:
            return ""
        block = "AKTİF DÜNYA DURUMU (şimdi): " + " | ".join(lines)
        return block[:max_chars]

    def staleness_s(self) -> float | None:
        return self.now() - self.last_snapshot_ts if self.last_snapshot_ts else None
