"""World Model — the CURRENT, relevant state of ULTRON's environment.

Distinct from memory (historical knowledge): the world model merges live
sources into one snapshot used for decisions and LLM context injection:

  user/presence · active window · screen state · current task · recent
  actions · system health · open applications · relevant files · IoT ·
  time · recent events

Every source degrades gracefully to {"available": false} — a missing
subsystem never breaks the snapshot. Sources are injected as callables
(the same pattern as MasterHUDCollector), so the class is fully testable.
"""
import time
from datetime import datetime


def _safe(fn, default=None):
    try:
        out = fn()
        return out if out is not None else default
    except Exception:
        return default


class WorldModel:
    def __init__(self, sources: dict | None = None, now=None):
        """sources: dict of name -> callable returning that source's state."""
        self.sources = sources or {}
        self.now = now or time.time
        self.last_snapshot = None
        self.last_snapshot_ts = 0.0

    def set_source(self, name, fn):
        self.sources[name] = fn

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> dict:
        snap = {"ts": self.now(), "iso": datetime.fromtimestamp(self.now()).isoformat(timespec="seconds")}
        for name, fn in self.sources.items():
            snap[name] = _safe(fn, {"available": False})
        self.last_snapshot = snap
        self.last_snapshot_ts = self.now()
        return snap

    # ------------------------------------------------------------ LLM context
    def context_for_llm(self, max_chars: int = 1200) -> str:
        """Compact current-world block injected into the system prompt.

        Capped hard; secrets never appear here (sources provide state, not
        credentials; audit-side redaction also applies to prompts)."""
        s = self.snapshot()
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

        line("presence", lambda v: f"user={'present' if v.get('boss_in_room') else 'away'}"
                                    f" (confidence={v.get('confidence', '?')})")
        line("workspace", lambda v: f"app={v.get('window') or '?'} mode={v.get('mode')}")
        line("screen", lambda v: f"screen={v.get('status', '?')}"
                                 + (f" diff={v['diff']}" if v.get("diff") is not None else "")
                                 + (f" last_shot_age_s={round(v['age_s'])}" if v.get("age_s") else ""))
        line("task", lambda v: f"active_task={v.get('goal')} [{v.get('status')}] step {v.get('current_step')}/{v.get('steps_total')}")
        line("system", lambda v: (f"cpu={v.get('cpu_percent')}% ram={v.get('ram_percent')}% "
                                  f"disk={v.get('disk_percent')}%") if v.get("cpu_percent") is not None else None)
        line("apps", lambda v: f"open_apps={', '.join(v.get('titles', [])[:6])}" if v.get("titles") else None)
        line("files", lambda v: f"recent_files={'; '.join(v.get('files', [])[:4])}" if v.get("files") else None)
        line("iot", lambda v: f"iot={v.get('active', 0)}/{v.get('devices', 0)} active"
                              + (f" scene={v['last_scene']}" if v.get("last_scene") else ""))
        line("events", lambda v: f"last_event={v.get('latest')}" if v.get("latest") else None)

        if not lines:
            return ""
        block = "AKTİF DÜNYA DURUMU (şimdi): " + " | ".join(lines)
        return block[:max_chars]

    def staleness_s(self) -> float:
        return self.now() - self.last_snapshot_ts if self.last_snapshot_ts else None
