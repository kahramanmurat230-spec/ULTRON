"""WAVE 1: cron + event-triggered task scheduler (local-first, additive).

- Minimal 5-field cron parser (minute hour dom month dow) with *, */n,
  ranges, lists and day-of-week OR semantics; next-run computed exactly.
- Schedules live in their own SQLite table (data/tasks/scheduler.db);
  every fire creates a NEW task instance from the stored template via the
  engine (never mutates the template).
- Event triggers: fnmatch patterns on an EventBus (e.g. "world.change.**").
  The bus is local-only; publishers arrive with Wave 2 (world event bus).
- No cloud, no infinite loops: fires are bounded per tick, errors are
  reported honestly via the engine's event stream.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))  # dk sa gün ay haftagünü


def parse_field(field: str, lo: int, hi: int, is_dow: bool = False) -> set:
    """Bir cron alanını olası değer kümesine çevirir (set[int])."""
    vals: set = set()
    for part in str(field).split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = max(1, int(step_s))
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
            if is_dow:                      # 7 = pazar → 0
                start, end = start % 7, end % 7
                if end < start:              # 6-0 gibi sarma
                    vals |= set(range(start, 8)) | set(range(0, end + 1))
                    continue
        else:
            start = end = int(part) % (7 if is_dow else hi + 1)
            if step > 1:
                end = hi
        for v in range(start, end + 1, step):
            vals.add(v % (7 if is_dow else hi + 1))
    if not vals:
        raise ValueError(f"empty cron field: {field!r}")
    return vals


def cron_matches(expr: str, dt: datetime) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron must have 5 fields: {expr!r}")
    mins, hrs, doms, mons, dows = (
        parse_field(f, lo, hi, is_dow=(i == 4))
        for i, (f, (lo, hi)) in enumerate(zip(parts, _BOUNDS)))
    if dt.minute not in mins or dt.hour not in hrs or dt.month not in mons:
        return False
    cron_dow = (dt.weekday() + 1) % 7        # pzt=0 → cron pazar=0
    dom_full = doms == set(range(1, 32))
    dow_full = dows == set(range(0, 7))
    if not dom_full and not dow_full:        # klasik OR kuralı
        return dt.day in doms or cron_dow in dows
    return dt.day in doms and cron_dow in dows


def cron_next(expr: str, after_ts: float) -> float:
    """after_ts'ten sonraki ilk eşleşme (UTC, dakika hassasiyeti)."""
    dt = datetime.fromtimestamp(after_ts, tz=timezone.utc)
    dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(400 * 24):                # en fazla ~400 gün tara
        if cron_matches(expr, dt):
            return dt.timestamp()
        dt += timedelta(minutes=1)
    raise ValueError(f"cron expression never matches within 400 days: {expr!r}")


class EventBus:
    """Yerel pub/sub (fnmatch desenli). Wave 2 world event bus buraya bağlanır."""

    def __init__(self):
        self._subs: list[tuple[str, object]] = []
        self._lock = threading.Lock()
        self.published = 0
        self.dispatch_errors = 0

    def subscribe(self, pattern: str, cb) -> None:
        with self._lock:
            self._subs.append((str(pattern), cb))

    def publish(self, topic: str, payload: dict | None = None) -> int:
        with self._lock:
            subs = [(p, cb) for p, cb in self._subs
                    if fnmatch.fnmatch(str(topic), p)]
        self.published += 1
        for pattern, cb in subs:
            try:
                out = cb(topic, payload or {})
                if asyncio.iscoroutine(out):
                    try:
                        asyncio.get_event_loop().create_task(out)
                    except RuntimeError:
                        asyncio.run(out)
            except Exception:
                self.dispatch_errors += 1   # abone hatası yayını durduramaz
        return len(subs)


class TaskScheduler:
    """Cron şablonları + olay tetikleyicileri → yeni task örnekleri üretir."""

    def __init__(self, engine, db_path="data/tasks/scheduler.db", now=None,
                 bus: EventBus | None = None, submit_fn=None):
        self.engine = engine
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now or time.time
        self.bus = bus or EventBus()
        self.submit_fn = submit_fn   # sunucu: (goal, kind, budgets, needs) → task
        self.fires = 0
        self.fire_errors = 0
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS schedules(
                id TEXT PRIMARY KEY, goal TEXT, cron TEXT,
                kind TEXT DEFAULT 'custom', budgets TEXT, needs TEXT,
                template_key TEXT,
                last_run REAL, next_run REAL,
                enabled INTEGER DEFAULT 1, created_at REAL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS triggers(
                id TEXT PRIMARY KEY, pattern TEXT, goal TEXT,
                budgets TEXT, enabled INTEGER DEFAULT 1,
                last_fire REAL, fire_count INTEGER DEFAULT 0,
                created_at REAL)""")

    # ------------------------------------------------------------ cron
    def add_cron(self, goal: str, cron: str, kind: str = "custom",
                 budgets: dict | None = None, needs: list | None = None,
                 template_key: str | None = None) -> dict:
        if not str(goal).strip():
            raise ValueError("empty goal")
        next_run = cron_next(cron, self.now())   # ifadeyi HEMEN doğrula
        sid = uuid.uuid4().hex[:8]
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schedules(id,goal,cron,kind,budgets,needs,"
                       "template_key,last_run,next_run,enabled,created_at)"
                       " VALUES(?,?,?,?,?,?,?,NULL,?,1,?)",
                       (sid, goal, cron, kind, json.dumps(budgets or {}),
                        json.dumps(needs or []), template_key,
                        next_run, self.now()))
        return {"ok": True, "id": sid, "cron": cron, "next_run": next_run}

    def list_schedules(self) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,goal,cron,kind,budgets,needs,template_key,"
                              "last_run,next_run,enabled,created_at FROM schedules"
                              " ORDER BY next_run").fetchall()
        cols = ("id", "goal", "cron", "kind", "budgets", "needs", "template_key",
                "last_run", "next_run", "enabled", "created_at")
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["budgets"] = json.loads(d["budgets"] or "{}")
            d["needs"] = json.loads(d["needs"] or "[]")
            out.append(d)
        return out

    def remove_schedule(self, sid: str) -> dict:
        with sqlite3.connect(self.path) as db:
            cur = db.execute("DELETE FROM schedules WHERE id=?", (sid,))
        return {"ok": cur.rowcount > 0}

    def due(self, now: float | None = None) -> list[dict]:
        ts = self.now() if now is None else now
        return [s for s in self.list_schedules()
                if s["enabled"] and s["next_run"] is not None
                and s["next_run"] <= ts]

    def _instantiate(self, s: dict):
        """Şablondan YENİ task örneği üret (şablon asla değişmez)."""
        steps = [{"label": "run", "worker": "none"}]  # custom: runner karar verir
        return self.engine.create(s["goal"], kind=s.get("kind") or "custom",
                                  steps=steps, budgets=s.get("budgets") or {},
                                  needs=s.get("needs") or [],
                                  template_key=s.get("template_key"))

    def tick(self) -> list[dict]:
        """Vadesi gelmiş cronları ateşle; üretilen task örneklerini döndür."""
        fired = []
        for s in self.due():
            try:
                task = self._instantiate(s)
                self.fires += 1
                fired.append(task)
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE schedules SET last_run=?, next_run=?"
                               " WHERE id=?",
                               (self.now(), cron_next(s["cron"], self.now()), s["id"]))
                self.engine.emit(task["id"], "scheduler", "CRON_FIRED",
                                 f"schedule {s['id']} ({s['cron']})")
            except Exception:
                self.fire_errors += 1
        return fired

    async def async_tick(self) -> list[dict]:
        """async submit_fn destekleyen tick (sunucu döngüsü için)."""
        fired = []
        for s in self.due():
            try:
                if self.submit_fn is not None:
                    out = self.submit_fn(s["goal"], s.get("kind"),
                                         s.get("budgets") or {}, s.get("needs") or [])
                    task = await out if asyncio.iscoroutine(out) else out
                else:
                    task = self._instantiate(s)
                self.fires += 1
                fired.append(task)
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE schedules SET last_run=?, next_run=?"
                               " WHERE id=?",
                               (self.now(), cron_next(s["cron"], self.now()), s["id"]))
                self.engine.emit(task["id"], "scheduler", "CRON_FIRED",
                                 f"schedule {s['id']} ({s['cron']})")
            except Exception:
                self.fire_errors += 1
        return fired

    # ------------------------------------------------------------ events
    def add_trigger(self, pattern: str, goal: str,
                    budgets: dict | None = None) -> dict:
        if not str(goal).strip():
            raise ValueError("empty goal")
        tid = uuid.uuid4().hex[:8]
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO triggers(id,pattern,goal,budgets,enabled,"
                       "last_fire,fire_count,created_at)"
                       " VALUES(?,?,?,?,1,NULL,0,?)",
                       (tid, str(pattern), goal, json.dumps(budgets or {}),
                        self.now()))
        # düğüm kendi tetikleyicilerine abone olur (yeniden başlatmada da)
        self.bus.subscribe(str(pattern),
                           lambda topic, payload, _g=goal, _b=budgets or {}:
                           self.handle_event(topic, _g, _b))
        return {"ok": True, "id": tid, "pattern": pattern}

    def list_triggers(self) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,pattern,goal,budgets,enabled,last_fire,"
                              "fire_count,created_at FROM triggers").fetchall()
        cols = ("id", "pattern", "goal", "budgets", "enabled", "last_fire",
                "fire_count", "created_at")
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["budgets"] = json.loads(d["budgets"] or "{}")
            out.append(d)
        return out

    def handle_event(self, topic: str, goal: str, budgets: dict) -> dict:
        """Olay eşleşince şablondan yeni task örneği üret."""
        try:
            task = self.engine.create(goal, budgets=budgets or {},
                                      template_key=f"event:{topic}")
            self.fires += 1
            self.engine.emit(task["id"], "scheduler", "EVENT_FIRED", topic)
            return {"ok": True, "task_id": task["id"]}
        except Exception as exc:  # noqa: BLE001
            self.fire_errors += 1
            return {"ok": False, "error": str(exc)[:200]}
