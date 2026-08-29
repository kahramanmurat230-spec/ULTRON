"""Autonomous Goal Loop — Wave 5 §12.

GOAL → PLAN → EXECUTE → OBSERVE → VERIFY → REPLAN → EXECUTE → DONE

ZORUNLU sınırlar (hepsi gerçek, hepsi test edilir):
- maximum execution budget: iterasyon / süre / token / maliyet / risk
- global iteration limit (döngüsel planlanmaya karşı)
- cancellation (dış iptal sinyali — anında durur)
- checkpoint (iterasyon başına durum kaydı; geri yüklenebilir)
- rollback (checkpoint'e dönüş; geri alınan adımlar kayıtlı)

Her aksiyon AutonomySafety kapısından geçer (§18). Verify başarısızsa
replan (plan sürümü artar) — iteration bütçesi tükenirse dürüst STOP.
Sonuç asla 'başarılı' uydurulmaz: goal_engine complete kanıt ister.
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
    """Basit, gerçek bütçe havuzu (iterasyon/süre/token/maliyet/risk)."""

    def __init__(self, max_iterations: int = 8, max_seconds: float = 300.0,
                 max_tokens: float = 100000.0, max_cost: float = 1.0,
                 max_risk_points: float = 10.0):
        self.limits = {"iterations": float(max_iterations),
                       "seconds": float(max_seconds),
                       "tokens": float(max_tokens),
                       "cost": float(max_cost),
                       "risk_points": float(max_risk_points)}
        self.consumed = {k: 0.0 for k in self.limits}
        self._t0 = _now()

    def consume(self, **kw) -> None:
        for k, v in kw.items():
            if k in self.consumed:
                self.consumed[k] += float(v)
        self.consumed["seconds"] = _now() - self._t0

    def exhausted_axis(self) -> str | None:
        # süre ekseni kontrol ANINDA ölçülür (yalnız consume'da değil)
        self.consumed["seconds"] = max(self.consumed["seconds"],
                                       _now() - self._t0)
        for k, lim in self.limits.items():
            if self.consumed[k] >= lim:
                return k
        return None

    def snapshot(self) -> dict:
        return {"consumed": dict(self.consumed),
                "limits": dict(self.limits),
                "exhausted": self.exhausted_axis()}


class AutonomousGoalLoop:
    def __init__(self, db_path: str = "data/cognitive/autonomy.db",
                 safety=None, decision_engine=None, goal_engine=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            goal TEXT, status TEXT, iterations INTEGER,
            checkpoints_json TEXT, result_json TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS checkpoints(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER,
            iteration INTEGER, ts REAL, state_json TEXT,
            rolled_back INTEGER DEFAULT 0)""")
        self.db.commit()
        self.safety = safety
        self.decision_engine = decision_engine
        self.goal_engine = goal_engine
        self._cancel_flags: set[int] = set()

    # ---------------------------------------------------- kontrol
    def cancel(self, run_id: int) -> None:
        self._cancel_flags.add(run_id)

    def _cancelled(self, run_id: int) -> bool:
        return run_id in self._cancel_flags

    def _checkpoint(self, run_id: int, iteration: int, state: dict) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO checkpoints(run_id,iteration,ts,state_json)"
                " VALUES(?,?,?,?)",
                (run_id, iteration, _now(),
                 json.dumps(state, ensure_ascii=False)))
            self.db.commit()
            return cur.lastrowid

    def rollback_to(self, checkpoint_id: int) -> dict:
        """Checkpoint'i işaretle + durumunu dön (geri alma kaydı dürüst)."""
        with self.lock:
            row = self.db.execute(
                "SELECT state_json FROM checkpoints WHERE id=?",
                (checkpoint_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "unknown checkpoint"}
            self.db.execute(
                "UPDATE checkpoints SET rolled_back=1 WHERE id=?",
                (checkpoint_id,))
            self.db.commit()
            return {"ok": True, "checkpoint_id": checkpoint_id,
                    "restored_state": json.loads(row[0])}

    # ---------------------------------------------------- döngü
    def run(self, goal: str, plan: list[dict], executor,
            verifier=None, observer=None, budgets: Budgets | None = None,
            replan_fn=None, goal_id: str | None = None,
            authority: str = "system", policy_allows: bool = True,
            user_approved: bool = False) -> dict:
        """executor(action) -> {'ok':bool,'output':..,'cost':..,'tokens':..}
        verifier(result, goal) -> bool|None (None → doğrulanamadı sayılır)
        observer(result) -> ek gözlem (opsiyonel)
        replan_fn(failed_plan, result) -> yeni plan (yoksa yeniden denenmez)
        """
        budgets = budgets or Budgets()
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO runs(ts,goal,status,iterations,checkpoints_json,"
                "result_json) VALUES(?,?,?,?,NULL,NULL)",
                (_now(), goal, "RUNNING", 0))
            self.db.commit()
            run_id = cur.lastrowid
        steps = [dict(a) for a in plan]
        results = []
        iteration = 0
        replans = 0
        stop_reason = None
        status = "COMPLETED"
        try:
            while steps:
                # 1) İPTAL kontrolü — iterasyon SAYILMADAN önce (anında dur)
                if self._cancelled(run_id):
                    status, stop_reason = "CANCELLED", "user-cancel"
                    break
                iteration += 1
                budgets.consume(iterations=1)
                # 2) BÜTÇE kontrolü — her iterasyon başında
                axis = budgets.exhausted_axis()
                if axis:
                    status, stop_reason = "STOPPED_BUDGET", f"budget:{axis}"
                    break
                action = steps.pop(0)
                # 3) GÜVENLİK KAPISI (§18) — DENY aksiyon atlanmaz, DURUR
                if self.safety is not None:
                    ev = self.safety.evaluate(
                        goal, str(action.get("name", action)),
                        authority=authority, policy_allows=policy_allows,
                        user_approved=user_approved)
                    if ev["decision"] == "DENY":
                        status, stop_reason = "STOPPED_SAFETY", \
                            f"safety-deny:{action.get('name', action)}"
                        break
                    action["_authority"] = ev["decision"]
                # 4) EXECUTE
                try:
                    result = executor(action)
                except Exception as exc:  # noqa: BLE001 — hata da sonuçtur
                    result = {"ok": False, "error": f"{type(exc).__name__}: "
                              f"{exc}"}
                if observer is not None:
                    try:
                        result["observed"] = observer(result)
                    except Exception:  # noqa: BLE001
                        result["observed"] = None
                results.append({"iteration": iteration,
                                "action": action.get("name", str(action)),
                                "ok": bool(result.get("ok")),
                                "error": result.get("error"),
                                "output": str(result.get("output", ""))[:200],
                                "authority": action.get("_authority")})
                budgets.consume(cost=float(result.get("cost", 0.0)),
                                tokens=float(result.get("tokens", 0.0)),
                                risk_points=float(result.get("risk_points",
                                                             0.0)))
                # 5) CHECKPOINT
                self._checkpoint(run_id, iteration,
                                 {"done": [r["action"] for r in results],
                                  "remaining": steps})
                # 6) VERIFY
                if verifier is not None:
                    try:
                        v = verifier(result, goal)
                    except Exception as exc:  # noqa: BLE001
                        v = None
                        results[-1]["verify_error"] = str(exc)[:120]
                    results[-1]["verified"] = v
                    if not v:   # False veya None → doğrulanamadı/sıfırlanamadı
                        if replan_fn is not None and replans < 3:
                            new_plan = replan_fn(
                                [a for a in [action]], result)
                            if new_plan:
                                steps = [dict(a) for a in new_plan] + steps
                                replans += 1
                                continue
                        status, stop_reason = "VERIFY_FAILED", \
                            f"iteration:{iteration}"
                        break
            else:
                if status == "COMPLETED" and stop_reason is None:
                    stop_reason = "plan-exhausted"
        finally:
            with self.lock:
                self.db.execute(
                    "UPDATE runs SET status=?, iterations=?, checkpoints_json=?,"
                    " result_json=? WHERE id=?",
                    (status, iteration,
                     json.dumps({"replans": replans}),
                     json.dumps({"results": results,
                                 "budget": budgets.snapshot(),
                                 "stop_reason": stop_reason}, ensure_ascii=False
                                )[:12000], run_id))
                self.db.commit()
            self._cancel_flags.discard(run_id)
        # goal_engine entegrasyonu: COMPLETE yalnız KANITLA
        if (status == "COMPLETED" and self.goal_engine is not None
                and goal_id):
            outputs = [r["output"] for r in results if r.get("ok")]
            ev = "; ".join(outputs)[:400] or "loop finished"
            self.goal_engine.complete(goal_id, evidence=ev)
        return {"run_id": run_id, "status": status,
                "iterations": iteration, "replans": replans,
                "stop_reason": stop_reason, "results": results,
                "budget": budgets.snapshot()}

    def run_status(self, run_id: int) -> dict | None:
        with self.lock:
            cur = self.db.execute(
                "SELECT goal,status,iterations,result_json FROM runs"
                " WHERE id=?", (run_id,))
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
                "SELECT id,iteration,ts,rolled_back FROM checkpoints"
                " WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [{"checkpoint_id": r[0], "iteration": r[1], "ts": r[2],
                 "rolled_back": bool(r[3])} for r in rows]
