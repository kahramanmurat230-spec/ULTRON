"""WAVE 3 — Worker model + deterministic state machine + persistence.

Her worker tam şemayı taşır (worker_id, task_id, parent_task_id, role,
capabilities, permissions, budget, state, trace_id, created_at,
started_at, finished_at). Durum geçişleri deterministiktir; geçersiz
geçiş InvalidWorkerTransition ile reddedilir.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

# ------------------------------------------------------------ roles
WORKER_ROLES = ("RESEARCH", "CODING", "VISION", "BROWSER", "COMPUTER",
                "SYSTEM", "MEMORY", "VERIFICATION", "REVIEWER", "JUDGE")
ROLE_CAPABILITIES = {
    "RESEARCH": ("WEB_SEARCH", "FETCH", "PARSE"),
    "CODING": ("READ_WORKSPACE", "WRITE_WORKSPACE", "RUN_TEST"),
    "VISION": ("SCREEN_CAPTURE", "OCR", "VISION_ANALYSIS"),
    "BROWSER": ("BROWSER_NAVIGATE", "BROWSER_READ"), "COMPUTER": ("GUI_OBSERVE",),
    "SYSTEM": ("SYSTEM_READ",), "MEMORY": ("MEMORY_READ",),
    "VERIFICATION": ("READ_WORKSPACE", "SYSTEM_READ"),
    "REVIEWER": ("MEMORY_READ", "SYSTEM_READ"), "JUDGE": ("MEMORY_READ",),
}
CAPABILITY_RISK = {
    "WEB_SEARCH": "LOW", "FETCH": "LOW", "PARSE": "SAFE", "READ_WORKSPACE": "SAFE",
    "WRITE_WORKSPACE": "HIGH", "RUN_TEST": "MEDIUM", "SCREEN_CAPTURE": "MEDIUM",
    "OCR": "SAFE", "VISION_ANALYSIS": "SAFE", "BROWSER_NAVIGATE": "LOW",
    "BROWSER_READ": "SAFE", "GUI_OBSERVE": "MEDIUM", "SYSTEM_READ": "SAFE", "MEMORY_READ": "SAFE",
}
WORKER_STATES = ("PENDING", "READY", "RUNNING", "WAITING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "BLOCKED", "RETRYING")
ALLOWED_WORKER_TRANSITIONS = {
    "PENDING": {"READY", "CANCELLED", "BLOCKED"}, "READY": {"RUNNING", "CANCELLED", "BLOCKED"},
    "RUNNING": {"WAITING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "RETRYING", "BLOCKED"},
    "WAITING": {"RUNNING", "CANCELLED", "TIMEOUT", "FAILED", "BLOCKED"},
    "RETRYING": {"RUNNING", "CANCELLED", "FAILED", "BLOCKED"}, "BLOCKED": {"READY", "CANCELLED", "FAILED"},
    "SUCCEEDED": set(), "FAILED": {"RETRYING"}, "CANCELLED": set(), "TIMEOUT": {"RETRYING", "CANCELLED"},
}
TERMINAL_STATES = ("SUCCEEDED", "CANCELLED")
MAX_ATTEMPTS = 3

class InvalidWorkerTransition(Exception):
    pass
class WorkerLimitExceeded(Exception):
    """Sınırsız worker oluşumu YASAK — global eşik aşılırsa reddedilir."""

def _now(): return time.time()

class Worker:
    """Tek bir uzman agent örneği (değer nesnesi + durum geçişleri)."""
    __slots__ = ("worker_id", "task_id", "parent_task_id", "role", "capabilities", "permissions", "budget", "state", "trace_id", "created_at", "started_at", "finished_at", "attempts", "error", "result_id", "depends_on", "last_state_change", "wait_since", "priority")
    def __init__(self, task_id: str, role: str, *, parent_task_id=None, capabilities=None, permissions=None, budget=None, trace_id=None, depends_on=None, priority: int = 5, worker_id=None, now=None):
        role = str(role).upper()
        if role not in WORKER_ROLES: raise ValueError(f"unknown worker role {role!r}")
        self.worker_id = worker_id or uuid.uuid4().hex[:12]; self.task_id = task_id; self.parent_task_id = parent_task_id or task_id; self.role = role
        allowed = set(ROLE_CAPABILITIES[role]); caps = set(capabilities or allowed)
        if not caps <= allowed: raise ValueError(f"capability escalation rejected: {sorted(caps - allowed)}")
        self.capabilities = tuple(sorted(caps)); self.permissions = tuple(sorted(set(permissions or ()))); self.budget = dict(budget or {}); self.state = "PENDING"; self.trace_id = trace_id
        self.created_at = float(now if now is not None else _now()); self.started_at = None; self.finished_at = None; self.attempts = 0; self.error = None; self.result_id = None; self.depends_on = tuple(depends_on or ()); self.last_state_change = self.created_at; self.wait_since = None; self.priority = int(priority)
    def transition(self, new_state: str, *, now=None, reason: str = None) -> str:
        new_state = str(new_state).upper()
        if new_state not in WORKER_STATES: raise InvalidWorkerTransition(f"unknown state {new_state!r}")
        if new_state not in ALLOWED_WORKER_TRANSITIONS.get(self.state, set()): raise InvalidWorkerTransition(f"{self.state} -> {new_state} not allowed (worker {self.worker_id})")
        ts = float(now if now is not None else _now())
        if new_state == "RUNNING": self.attempts += 1; self.started_at = self.started_at or ts
        if new_state == "WAITING": self.wait_since = ts
        if new_state in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"): self.finished_at = ts
        self.state = new_state; self.last_state_change = ts
        if reason: self.error = str(reason)[:300] if new_state in ("FAILED", "TIMEOUT", "BLOCKED") else None
        return self.state
    def can_transition(self, new_state: str) -> bool: return str(new_state).upper() in ALLOWED_WORKER_TRANSITIONS.get(self.state, set())
    @property
    def is_terminal(self) -> bool: return self.state in TERMINAL_STATES
    def to_dict(self) -> dict:
        return {"worker_id": self.worker_id, "task_id": self.task_id, "parent_task_id": self.parent_task_id, "role": self.role, "capabilities": list(self.capabilities), "permissions": list(self.permissions), "budget": dict(self.budget), "state": self.state, "trace_id": self.trace_id, "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at, "attempts": self.attempts, "error": self.error, "result_id": self.result_id, "depends_on": list(self.depends_on), "last_state_change": self.last_state_change, "wait_since": self.wait_since, "priority": self.priority}
    @classmethod
    def from_dict(cls, d: dict) -> "Worker":
        w = cls.__new__(cls)
        for slot in cls.__slots__: setattr(w, slot, d.get(slot))
        w.capabilities = tuple(d.get("capabilities") or ()); w.permissions = tuple(d.get("permissions") or ()); w.depends_on = tuple(d.get("depends_on") or ()); w.budget = dict(d.get("budget") or {})
        return w

class WorkerRegistry:
    """Kalıcı worker kaydı (SQLite) — supervisor restart kurtarması için."""
    def __init__(self, db_path="data/orchestr/workers.db", max_workers=500):
        self.path = Path(db_path); self.path.parent.mkdir(parents=True, exist_ok=True); self.max_workers = max(1, int(max_workers)); self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL preserves transactional commits while avoiding an fsync per worker transition.
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS workers(worker_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, parent_task_id TEXT, role TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, updated_at REAL NOT NULL)""")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_w_task ON workers(task_id)"); self._db.execute("CREATE INDEX IF NOT EXISTS idx_w_state ON workers(state)")
    def save(self, worker: Worker) -> None:
        if worker.state not in TERMINAL_STATES:
            alive = self._db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('SUCCEEDED','CANCELLED')").fetchone()[0]
            existing = self._db.execute("SELECT 1 FROM workers WHERE worker_id=?", (worker.worker_id,)).fetchone()
            if not existing and alive >= self.max_workers: raise WorkerLimitExceeded(f"global worker limit {self.max_workers} reached — unbounded worker creation is forbidden")
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO workers(worker_id,task_id,parent_task_id,role,state,data,updated_at) VALUES(?,?,?,?,?,?,?)", (worker.worker_id, worker.task_id, worker.parent_task_id, worker.role, worker.state, json.dumps(worker.to_dict(), ensure_ascii=False, default=str), _now()))
    def get(self, worker_id: str) -> Worker | None:
        row = self._db.execute("SELECT data FROM workers WHERE worker_id=?", (worker_id,)).fetchone(); return Worker.from_dict(json.loads(row[0])) if row else None
    def for_task(self, task_id: str) -> list[Worker]:
        rows = self._db.execute("SELECT data FROM workers WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall(); return [Worker.from_dict(json.loads(r[0])) for r in rows]
    def active_count(self) -> int: return self._db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('SUCCEEDED','CANCELLED')").fetchone()[0]
    def close(self):
        try: self._db.close()
        except Exception: pass
