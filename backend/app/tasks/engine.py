"""Long-running task engine — persistent, resumable, budgeted.

TASK statuses:
  PENDING RUNNING WAITING_APPROVAL PAUSED FAILED RECOVERING COMPLETED CANCELLED

Every task is stored in SQLite (data/tasks/tasks.db) with steps, checkpoint,
retry count and budgets, so ULTRON can be restarted and continue where it
stopped. A step runner is an async callable:

    async def runner(task: dict, step: dict, ctx: dict) -> dict
        # -> {"ok": bool, "output": any}
        # raise NeedsApproval(reason, risks) to enter WAITING_APPROVAL

Budgets per task: max_iterations, timeout_s (overall), tool_budget,
retry_budget (per step). No infinite loops: the engine enforces all of them
and emits TASK_* / STEP_* / REPLAN / APPROVAL events for observability.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

STATUSES = ("PENDING", "RUNNING", "WAITING_APPROVAL", "PAUSED",
            "FAILED", "RECOVERING", "COMPLETED", "CANCELLED")

ALLOWED_TRANSITIONS = {
    "PENDING": {"RUNNING", "CANCELLED", "RECOVERING"},
    "RUNNING": {"WAITING_APPROVAL", "PAUSED", "FAILED", "COMPLETED", "CANCELLED", "RECOVERING"},
    "RECOVERING": {"RUNNING", "FAILED", "CANCELLED"},
    "WAITING_APPROVAL": {"RUNNING", "CANCELLED", "FAILED"},
    "PAUSED": {"RUNNING", "CANCELLED"},
    "FAILED": {"RECOVERING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

DEFAULT_BUDGETS = {
    "max_iterations": 8,       # plan-level iterations (incl. replans)
    "timeout_s": 600,          # overall wall-clock budget
    "tool_budget": 50,         # max tool/worker invocations
    "retry_budget": 1,         # retries per step
    "step_timeout_s": 240,     # single step wall-clock
}


class NeedsApproval(Exception):
    """Raised by a step runner when explicit user approval is required."""

    def __init__(self, reason: str, risks: list | None = None):
        super().__init__(reason)
        self.reason = reason
        self.risks = risks or []


class InvalidTransition(Exception):
    pass


class TaskEngine:
    def __init__(self, db_path="data/tasks/tasks.db", event_cb=None, now=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.event_cb = event_cb
        self.now = now or time.time
        self._running: dict[str, asyncio.Task] = {}
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 5,
                steps TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 0,
                checkpoint TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                budgets TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                updated_at REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")

    # ------------------------------------------------------------ events
    def emit(self, task_id: str, component: str, status: str, detail: str = "") -> None:
        ev = {"task_id": task_id, "ts": self.now(), "component": component,
              "status": status, "detail": str(detail)[:300]}
        if self.event_cb:
            try:
                self.event_cb(ev)
            except Exception:
                pass

    # ------------------------------------------------------------ CRUD
    def create(self, goal: str, kind: str = "custom", steps: list | None = None,
               priority: int = 5, budgets: dict | None = None) -> dict:
        tid = uuid.uuid4().hex[:8]
        merged = dict(DEFAULT_BUDGETS)
        merged.update(budgets or {})
        now = self.now()
        task = {
            "id": tid, "goal": goal, "kind": kind, "status": "PENDING",
            "priority": int(priority),
            "steps": [self._norm_step(s, i) for i, s in enumerate(steps or [])],
            "current_step": 0, "checkpoint": None, "retry_count": 0,
            "budgets": merged, "result": None, "error": None,
            "created_at": now, "started_at": None, "updated_at": now,
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO tasks(id,goal,kind,status,priority,steps,current_step,"
                "checkpoint,retry_count,budgets,result,error,created_at,started_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, goal, kind, "PENDING", task["priority"],
                 json.dumps(task["steps"]), 0, None, 0, json.dumps(merged),
                 None, None, now, None, now))
        self.emit(tid, "engine", "TASK_CREATED", goal)
        return task

    @staticmethod
    def _norm_step(step: dict, index: int) -> dict:
        return {
            "index": index,
            "label": str(step.get("label", f"step-{index}")),
            "worker": str(step.get("worker", "unknown")),
            "args": step.get("args", {}) or {},
            "status": "PENDING",  # PENDING RUNNING SUCCESS FAILED SKIPPED
            "attempts": 0,
            "result": None,
            "critical": bool(step.get("critical", True)),
        }

    def get(self, task_id: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        cols = ("id", "goal", "kind", "status", "priority", "steps", "current_step",
                "checkpoint", "retry_count", "budgets", "result", "error",
                "created_at", "started_at", "updated_at")
        t = dict(zip(cols, row))
        t["steps"] = json.loads(t["steps"])
        t["budgets"] = json.loads(t["budgets"])
        t["checkpoint"] = json.loads(t["checkpoint"]) if t["checkpoint"] else None
        t["result"] = json.loads(t["result"]) if t["result"] else None
        return t

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            if status:
                rows = db.execute(
                    "SELECT id FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)).fetchall()
            else:
                rows = db.execute(
                    "SELECT id FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,)).fetchall()
        return [self.get(r[0]) for r in rows]

    def _save(self, task: dict, status: str | None = None) -> None:
        if status is not None:
            self._transition(task, status)
        task["updated_at"] = self.now()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE tasks SET status=?,steps=?,current_step=?,checkpoint=?,"
                "retry_count=?,result=?,error=?,started_at=?,updated_at=? WHERE id=?",
                (task["status"], json.dumps(task["steps"]), task["current_step"],
                 json.dumps(task["checkpoint"]) if task["checkpoint"] is not None else None,
                 task["retry_count"], json.dumps(task["result"]) if task["result"] is not None else None,
                 task["error"], task["started_at"], task["updated_at"], task["id"]))

    def _transition(self, task: dict, new: str) -> None:
        cur = task["status"]
        if new not in STATUSES:
            raise InvalidTransition(f"unknown status {new}")
        if new == cur:
            return
        allowed = ALLOWED_TRANSITIONS.get(cur, set())
        if new not in allowed:
            raise InvalidTransition(f"{cur} -> {new} not allowed")
        task["status"] = new

    # ------------------------------------------------------------ control
    def cancel(self, task_id: str) -> dict:
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        if t["status"] in ("COMPLETED", "CANCELLED"):
            return {"ok": False, "error": f"task already {t['status']}"}
        handle = self._running.pop(task_id, None)
        if handle:
            handle.cancel()
        try:
            self._save(t, "CANCELLED")
            self.emit(task_id, "engine", "TASK_CANCELLED")
            return {"ok": True, "task": self.get(task_id)}
        except InvalidTransition as e:
            return {"ok": False, "error": str(e)}

    def pause(self, task_id: str) -> dict:
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        handle = self._running.pop(task_id, None)
        if handle:
            handle.cancel()
        try:
            self._save(t, "PAUSED")
            return {"ok": True, "task": self.get(task_id)}
        except InvalidTransition as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------ execution
    async def execute(self, task_id: str, step_runner) -> dict:
        """Run pending steps of a task under its budgets. Returns final task."""
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task["status"] == "COMPLETED":
            return task
        self._save(task, "RUNNING")
        if not task["started_at"]:
            task["started_at"] = self.now()
        task["error"] = None
        self.emit(task_id, "engine", "TASK_START", task["goal"])
        deadline = self.now() + float(task["budgets"].get("timeout_s", 600))
        tool_budget = int(task["budgets"].get("tool_budget", 50))
        iterations = 0
        ctx = {"task": task, "tools_used": 0, "engine": self, "deadline": deadline}

        try:
            while task["current_step"] < len(task["steps"]):
                iterations += 1
                if iterations > int(task["budgets"].get("max_iterations", 8)):
                    raise RuntimeError("max plan iterations exceeded (replan budget)")
                if self.now() > deadline:
                    raise TimeoutError("task timeout budget exhausted")
                if ctx["tools_used"] >= tool_budget:
                    raise RuntimeError("tool budget exhausted")

                step = task["steps"][task["current_step"]]
                if step["status"] == "SUCCESS" or step["status"] == "SKIPPED":
                    task["current_step"] += 1
                    continue
                step["status"] = "RUNNING"
                self.emit(task_id, f"worker:{step['worker']}", "STEP_START", step["label"])

                attempts_allowed = 1 + int(task["budgets"].get("retry_budget", 1))
                result = None
                last_err = None
                while step["attempts"] < attempts_allowed:
                    step["attempts"] += 1
                    ctx["tools_used"] += 1
                    try:
                        result = await asyncio.wait_for(
                            step_runner(task, step, ctx),
                            timeout=float(task["budgets"].get("step_timeout_s", 240)))
                        break
                    except NeedsApproval as ap:
                        step["status"] = "PENDING"
                        task["checkpoint"] = {"awaiting_approval_for": step["label"]}
                        self._save(task, "WAITING_APPROVAL")
                        self.emit(task_id, "engine", "APPROVAL_REQUIRED",
                                  f"{ap.reason} risks={ap.risks}")
                        return self.get(task_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc if str(exc) else type(exc).__name__
                        result = None
                        self.emit(task_id, f"worker:{step['worker']}", "STEP_ERROR",
                                  f"attempt {step['attempts']}: {last_err}")
                        if self.now() > deadline:
                            break
                if self.now() > deadline:
                    raise TimeoutError("task timeout budget exhausted")
                if result is None or not result.get("ok"):
                    step["status"] = "FAILED"
                    step["result"] = {"ok": False, "error": str(last_err)}
                    self.emit(task_id, f"worker:{step['worker']}", "STEP_FAILED", str(last_err))
                    if step.get("critical", True):
                        raise RuntimeError(
                            f"critical step failed: {step['label']}: {last_err}")
                    task["current_step"] += 1
                    continue
                step["status"] = "SUCCESS"
                step["result"] = {"ok": True, "output": _jsonable(result.get("output"))}
                self.emit(task_id, f"worker:{step['worker']}", "STEP_SUCCESS", step["label"])
                task["checkpoint"] = {"last_successful_step": step["label"],
                                      "ts": self.now()}
                task["current_step"] += 1
                self._save(task)
            task["result"] = {"ok": True, "completed_steps":
                              [s["label"] for s in task["steps"] if s["status"] == "SUCCESS"],
                              "ts": self.now()}
            self._save(task, "COMPLETED")
            self.emit(task_id, "engine", "TASK_COMPLETE", task["goal"])
        except asyncio.CancelledError:
            # pause/cancel: status already transitioned by the caller
            self.emit(task_id, "engine", "TASK_INTERRUPTED")
            raise
        except Exception as exc:  # noqa: BLE001
            task["error"] = str(exc)[:500]
            self._save(task, "FAILED")
            self.emit(task_id, "engine", "TASK_FAILED", str(exc))
        return self.get(task_id)

    def approve(self, task_id: str) -> dict:
        """Leave WAITING_APPROVAL (server-side approval store decision)."""
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        try:
            self._save(t, "RUNNING")
            self.emit(task_id, "engine", "APPROVAL_GRANTED")
            return {"ok": True, "task": self.get(task_id)}
        except InvalidTransition as e:
            return {"ok": False, "error": str(e)}

    def spawn(self, task_id: str, step_runner) -> bool:
        """Attach a background asyncio task; returns False if already running."""
        if task_id in self._running:
            return False
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            return False

        async def _wrap():
            try:
                await self.execute(task_id, step_runner)
            finally:
                self._running.pop(task_id, None)

        self._running[task_id] = loop.create_task(_wrap())
        return True

    # ------------------------------------------------------------ recovery
    def recover_incomplete(self) -> list[str]:
        """Mark crash-orphaned tasks (RUNNING at boot) as RECOVERING."""
        recovered = []
        for t in self.list(limit=500):
            if t["status"] in ("PENDING", "RECOVERING"):
                recovered.append(t["id"])
            elif t["status"] == "RUNNING":
                self._save(t, "RECOVERING")
                self.emit(t["id"], "engine", "TASK_RECOVERING", "found RUNNING at boot")
                recovered.append(t["id"])
        return recovered


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)
