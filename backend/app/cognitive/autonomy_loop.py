"""Autonomous Goal Loop — Wave 5 §12, hardened recovery runtime.

GOAL → PLAN → EXECUTE → OBSERVE → VERIFY → REPLAN → EXECUTE → DONE

Safety, approval and capability boundaries remain authoritative. Recovery never
retries an action blindly: only explicitly idempotent actions may be retried.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


def _now() -> float:
    return time.time()


class Budgets:
    """Real bounded execution budget."""
    def __init__(self, max_iterations: int = 8, max_seconds: float = 300.0,
                 max_tokens: float = 100000.0, max_cost: float = 1.0,
                 max_risk_points: float = 10.0):
        self.limits = {"iterations": float(max_iterations), "seconds": float(max_seconds),
                       "tokens": float(max_tokens), "cost": float(max_cost),
                       "risk_points": float(max_risk_points)}
        self.consumed = {k: 0.0 for k in self.limits}
        self._t0 = _now()

    def consume(self, **kw) -> None:
        for k, v in kw.items():
            if k in self.consumed:
                self.consumed[k] += float(v)
        self.consumed["seconds"] = _now() - self._t0

    def exhausted_axis(self) -> str | None:
        self.consumed["seconds"] = max(self.consumed["seconds"], _now() - self._t0)
        for k, lim in self.limits.items():
            if self.consumed[k] >= lim:
                return k
        return None

    def snapshot(self) -> dict:
        return {"consumed": dict(self.consumed), "limits": dict(self.limits),
                "exhausted": self.exhausted_axis()}


class AutonomousGoalLoop:
    MAX_CHECKPOINTS_PER_RUN = 32
    MAX_REPLANS = 3
    MAX_RETRIES = 2
    RETRY_BACKOFF_S = 0.05

    def __init__(self, db_path: str = "data/cognitive/autonomy.db",
                 safety=None, decision_engine=None, goal_engine=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, goal TEXT, status TEXT,
            iterations INTEGER, checkpoints_json TEXT, result_json TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS checkpoints(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, iteration INTEGER,
            ts REAL, state_json TEXT, rolled_back INTEGER DEFAULT 0)""")
        self.db.commit()
        self.safety = safety
        self.decision_engine = decision_engine
        self.goal_engine = goal_engine
        self._cancel_flags: set[int] = set()

    def cancel(self, run_id: int) -> None:
        with self.lock:
            self._cancel_flags.add(run_id)

    def _cancelled(self, run_id: int) -> bool:
        with self.lock:
            return run_id in self._cancel_flags

    def _checkpoint(self, run_id: int, iteration: int, state: dict) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO checkpoints(run_id,iteration,ts,state_json) VALUES(?,?,?,?)",
                (run_id, iteration, _now(), json.dumps(state, ensure_ascii=False)))
            self.db.execute(
                "DELETE FROM checkpoints WHERE run_id=? AND id NOT IN "
                "(SELECT id FROM checkpoints WHERE run_id=? ORDER BY id DESC LIMIT ?)",
                (run_id, run_id, self.MAX_CHECKPOINTS_PER_RUN))
            self.db.commit()
            return cur.lastrowid

    def rollback_to(self, checkpoint_id: int) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT run_id,iteration,state_json FROM checkpoints WHERE id=?",
                (checkpoint_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "unknown checkpoint"}
            self.db.execute("UPDATE checkpoints SET rolled_back=1 WHERE id=?", (checkpoint_id,))
            self.db.commit()
            return {"ok": True, "checkpoint_id": checkpoint_id, "run_id": row[0],
                    "iteration": row[1], "restored_state": json.loads(row[2])}

    def _execute_with_recovery(self, action: dict, executor):
        """Retry only actions explicitly declared idempotent; never blind-retry mutation."""
        attempts = 0
        last = None
        retries = 0
        for attempt in range(self.MAX_RETRIES + 1):
            attempts += 1
            try:
                last = executor(action)
            except Exception as exc:  # noqa: BLE001
                last = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if not isinstance(last, dict):
                last = {"ok": False, "error": "executor returned non-object result"}
            if last.get("ok"):
                break
            if not action.get("idempotent", False) or attempt >= self.MAX_RETRIES:
                break
            retries += 1
            time.sleep(self.RETRY_BACKOFF_S * (2 ** attempt))
        return last, attempts, retries

    def run(self, goal: str, plan: list[dict], executor, verifier=None, observer=None,
            budgets: Budgets | None = None, replan_fn=None, goal_id: str | None = None,
            authority: str = "system", policy_allows: bool = True,
            user_approved: bool = False) -> dict:
        budgets = budgets or Budgets()
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO runs(ts,goal,status,iterations,checkpoints_json,result_json) "
                "VALUES(?,?,?,?,NULL,NULL)", (_now(), goal, "RUNNING", 0))
            self.db.commit()
            run_id = cur.lastrowid
        steps = [dict(a) for a in plan]
        results, iteration, replans = [], 0, 0
        stop_reason, status = None, "COMPLETED"
        try:
            while steps:
                if self._cancelled(run_id):
                    status, stop_reason = "CANCELLED", "user-cancel"
                    break
                iteration += 1
                budgets.consume(iterations=1)
                axis = budgets.exhausted_axis()
                if axis:
                    status, stop_reason = "STOPPED_BUDGET", f"budget:{axis}"
                    break
                action = steps.pop(0)
                if self.safety is not None:
                    ev = self.safety.evaluate(
                        goal, str(action.get("name", action)), authority=authority,
                        policy_allows=policy_allows, user_approved=user_approved)
                    if ev["decision"] == "DENY":
                        status, stop_reason = "STOPPED_SAFETY", \
                            f"safety-deny:{action.get('name', action)}"
                        break
                    action["_authority"] = ev["decision"]
                result, attempts, retries = self._execute_with_recovery(action, executor)
                if observer is not None:
                    try:
                        result["observed"] = observer(result)
                    except Exception:  # noqa: BLE001
                        result["observed"] = None
                results.append({"iteration": iteration, "action": action.get("name", str(action)),
                                "ok": bool(result.get("ok")), "error": result.get("error"),
                                "output": str(result.get("output", ""))[:200],
                                "authority": action.get("_authority"),
                                "attempts": attempts, "retries": retries})
                budgets.consume(cost=float(result.get("cost", 0.0)),
                                tokens=float(result.get("tokens", 0.0)),
                                risk_points=float(result.get("risk_points", 0.0)))
                self._checkpoint(run_id, iteration,
                                 {"done": [r["action"] for r in results], "remaining": steps,
                                  "replans": replans})
                if verifier is not None:
                    try:
                        v = verifier(result, goal)
                    except Exception as exc:  # noqa: BLE001
                        v = None
                        results[-1]["verify_error"] = str(exc)[:120]
                    results[-1]["verified"] = v
                    if not v:
                        if replan_fn is not None and replans < self.MAX_REPLANS:
                            try:
                                new_plan = replan_fn([action], result)
                            except Exception:
                                new_plan = None
                            if new_plan:
                                steps = [dict(a) for a in new_plan] + steps
                                replans += 1
                                continue
                        status, stop_reason = "VERIFY_FAILED", f"iteration:{iteration}"
                        break
            else:
                stop_reason = "plan-exhausted"
        finally:
            with self.lock:
                self.db.execute(
                    "UPDATE runs SET status=?,iterations=?,checkpoints_json=?,result_json=? WHERE id=?",
                    (status, iteration, json.dumps({"replans": replans}),
                     json.dumps({"results": results, "budget": budgets.snapshot(),
                                 "stop_reason": stop_reason}, ensure_ascii=False)[:12000], run_id))
                self.db.commit()
                self._cancel_flags.discard(run_id)
        if status == "COMPLETED" and self.goal_engine is not None and goal_id:
            outputs = [r["output"] for r in results if r.get("ok")]
            self.goal_engine.complete(goal_id, evidence="; ".join(outputs)[:400] or "loop finished")
        return {"run_id": run_id, "status": status, "iterations": iteration,
                "replans": replans, "stop_reason": stop_reason, "results": results,
                "budget": budgets.snapshot()}

    def run_status(self, run_id: int) -> dict | None:
        with self.lock:
            cur = self.db.execute("SELECT goal,status,iterations,result_json FROM runs WHERE id=?", (run_id,))
            cols = [d[0] for d in cur.description]
            r = cur.fetchone()
        if not r:
            return None
        out = dict(zip(cols, r))
        out["result"] = json.loads(out.pop("result_json") or "{}")
        return out

    def checkpoints(self, run_id: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,iteration,ts,rolled_back FROM checkpoints WHERE run_id=? ORDER BY id",
                (run_id,)).fetchall()
        return [{"checkpoint_id": r[0], "iteration": r[1], "ts": r[2],
                 "rolled_back": bool(r[3])} for r in rows]
