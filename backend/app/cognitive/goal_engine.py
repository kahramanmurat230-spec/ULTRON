"""Goal Intelligence — Wave 5 §3.

USER INTENT → GOAL → SUBGOALS → DEPENDENCIES → PLAN → EXECUTION →
VERIFICATION → COMPLETION zinciri için kalıcı goal grafiği:
- goal creation (kaynak intent ile)
- decomposition (parent/child subgoal ağacı; döngü koruması)
- priorities (P0-P3) & deadlines
- dependencies (goal'lar arası; döngü algılama — DAG zorunlu)
- blockers (external engel kaydı)
- progress (alt goal tamamlanmasından gerçek tümevarım; uydurma % yok)
- replanning (plan değişikliği sürümlenir; eski plan kaybolmaz)
- cancellation (neden ile) & completion verification
  (doğrulama KANITI yoksa COMPLETE olamaz — dürüst)

SQLite-backed; mevcut task engine'in ÜSTÜNE additive (onu değiştirmez).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

PRIORITIES = ("P0", "P1", "P2", "P3")
STATUSES = ("ACTIVE", "BLOCKED", "DONE", "CANCELLED", "FAILED")


def _now() -> float:
    return time.time()


class GoalEngine:
    def __init__(self, db_path: str = "data/cognitive/goals.db"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS goals(
            id TEXT PRIMARY KEY, parent TEXT, title TEXT, intent TEXT,
            priority TEXT DEFAULT 'P2', status TEXT DEFAULT 'ACTIVE',
            created REAL, deadline REAL, completed REAL,
            verify_fn TEXT, evidence TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS dependencies(
            goal TEXT, depends_on TEXT,
            PRIMARY KEY(goal, depends_on))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS blockers(
            id INTEGER PRIMARY KEY AUTOINCREMENT, goal TEXT,
            reason TEXT, created REAL, cleared REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS plans(
            id INTEGER PRIMARY KEY AUTOINCREMENT, goal TEXT, version INTEGER,
            steps TEXT, created REAL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, goal TEXT,
            kind TEXT, detail TEXT, ts REAL)""")
        self.db.commit()
        self._seq = 0

    # ---------------------------------------------------------- helpers
    def _next_id(self) -> str:
        self._seq += 1
        return f"g{int(_now())}_{self._seq}"

    def _log(self, goal: str, kind: str, detail: str = ""):
        self.db.execute("INSERT INTO events(goal,kind,detail,ts) VALUES(?,?,?,?)",
                        (goal, kind, str(detail)[:200], _now()))

    def _get(self, goal_id: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM goals WHERE id=?", (goal_id,))
        cols = [d[0] for d in cur.description]
        r = cur.fetchone()
        return dict(zip(cols, r)) if r else None

    # ---------------------------------------------------------- creation
    def create(self, title: str, intent: str = "", parent: str | None = None,
               priority: str = "P2", deadline: float | None = None) -> dict:
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        with self.lock:
            if parent is not None and self._get(parent) is None:
                return {"ok": False, "error": f"unknown parent: {parent}"}
            gid = self._next_id()
            self.db.execute(
                """INSERT INTO goals(id,parent,title,intent,priority,status,
                   created,deadline) VALUES(?,?,?,?,?,?,?,?)""",
                (gid, parent, title, intent, priority, "ACTIVE", _now(), deadline))
            self._log(gid, "CREATED", f"{title} (from intent: {intent})")
            self.db.commit()
            return {"ok": True, "goal": gid, "parent": parent}

    def decompose(self, goal_id: str, sub_titles: list[str],
                  intent: str = "") -> dict:
        """Goal → subgoals (dependency zinciri otomatik: sıralı kardeşler)."""
        with self.lock:
            if self._get(goal_id) is None:
                return {"ok": False, "error": "unknown goal"}
            made = []
            prev = None
            for t in sub_titles:
                r = self.create(t, intent=intent, parent=goal_id)
                made.append(r["goal"])
                if prev:
                    self.db.execute(
                        "INSERT OR IGNORE INTO dependencies VALUES(?,?)",
                        (r["goal"], prev))
                prev = r["goal"]
            self.db.commit()
            return {"ok": True, "subgoals": made}

    # ---------------------------------------------------------- structure
    def add_dependency(self, goal: str, depends_on: str) -> dict:
        with self.lock:
            if self._get(goal) is None or self._get(depends_on) is None:
                return {"ok": False, "error": "unknown goal in dependency"}
            if goal == depends_on:
                return {"ok": False, "error": "self-dependency"}
            # döngü kontrolü: depends_on ağacından goal'a yol var mı?
            frontier, seen = [depends_on], set()
            while frontier:
                cur = frontier.pop()
                if cur == goal:
                    return {"ok": False, "error": "circular dependency"}
                if cur in seen:
                    continue
                seen.add(cur)
                frontier += [r[0] for r in self.db.execute(
                    "SELECT depends_on FROM dependencies WHERE goal=?", (cur,))]
            self.db.execute("INSERT OR IGNORE INTO dependencies VALUES(?,?)",
                            (goal, depends_on))
            self._log(goal, "DEPENDENCY", f"after {depends_on}")
            self.db.commit()
            return {"ok": True}

    def blocked_by(self, goal: str) -> list[str]:
        """Henüz tamamlanmamış bağımlılıklar."""
        with self.lock:
            rows = self.db.execute(
                "SELECT depends_on FROM dependencies WHERE goal=?", (goal,)).fetchall()
        out = []
        for (dep,) in rows:
            g = self._get(dep)
            if g and g["status"] not in ("DONE",):
                out.append(dep)
        return out

    def children(self, goal: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,title,status,priority FROM goals WHERE parent=?",
                (goal,)).fetchall()
        return [{"id": r[0], "title": r[1], "status": r[2], "priority": r[3]}
                for r in rows]

    # ---------------------------------------------------------- blockers
    def add_blocker(self, goal: str, reason: str) -> dict:
        with self.lock:
            self.db.execute(
                "INSERT INTO blockers(goal,reason,created) VALUES(?,?,?)",
                (goal, reason, _now()))
            self.db.execute("UPDATE goals SET status='BLOCKED' WHERE id=?"
                            " AND status='ACTIVE'", (goal,))
            self._log(goal, "BLOCKED", reason)
            self.db.commit()
            return {"ok": True}

    def clear_blocker(self, goal: str, reason_like: str = "") -> dict:
        with self.lock:
            rows = self.db.execute(
                "SELECT id FROM blockers WHERE goal=? AND cleared IS NULL"
                " AND reason LIKE ?", (goal, f"%{reason_like}%")).fetchall()
            for (bid,) in rows:
                self.db.execute("UPDATE blockers SET cleared=? WHERE id=?",
                                (_now(), bid))
            still = self.db.execute(
                "SELECT COUNT(*) FROM blockers WHERE goal=? AND cleared IS NULL",
                (goal,)).fetchone()[0]
            if not still:
                self.db.execute("UPDATE goals SET status='ACTIVE' WHERE id=?"
                                " AND status='BLOCKED'", (goal,))
            self._log(goal, "UNBLOCKED", reason_like or "all")
            self.db.commit()
            return {"ok": True, "cleared": len(rows), "still_blocked": bool(still)}

    # ---------------------------------------------------------- lifecycle
    def set_plan(self, goal: str, steps: list[str]) -> dict:
        with self.lock:
            v = self.db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM plans WHERE goal=?",
                (goal,)).fetchone()[0]
            self.db.execute("INSERT INTO plans(goal,version,steps,created)"
                            " VALUES(?,?,?,?)",
                            (goal, v, self._jsteps(steps), _now()))
            self._log(goal, "PLAN", f"v{v}: {len(steps)} adım")
            self.db.commit()
            return {"ok": True, "version": v, "steps": steps}

    @staticmethod
    def _jsteps(steps):
        import json
        return json.dumps(steps, ensure_ascii=False)

    def current_plan(self, goal: str) -> dict | None:
        with self.lock:
            r = self.db.execute(
                "SELECT version,steps,created FROM plans WHERE goal=?"
                " ORDER BY version DESC LIMIT 1", (goal,)).fetchone()
        if not r:
            return None
        import json
        return {"version": r[0], "steps": json.loads(r[1]), "created": r[2]}

    def complete(self, goal: str, evidence: str = "",
                 verify_fn: str = "") -> dict:
        """Completion verification: kanıt ZORUNLU. Kanıtsız complete RED."""
        with self.lock:
            g = self._get(goal)
            if g is None:
                return {"ok": False, "error": "unknown goal"}
            if g["status"] == "CANCELLED":
                return {"ok": False, "error": "goal cancelled"}
            # alt goal'ların hepsi bitmiş olmalı
            open_children = [c for c in self.children(goal)
                             if c["status"] not in ("DONE", "CANCELLED")]
            if open_children:
                return {"ok": False, "error": "open subgoals remain",
                        "open": [c["id"] for c in open_children]}
            deps = self.blocked_by(goal)
            if deps:
                return {"ok": False, "error": "unmet dependencies",
                        "waiting_on": deps}
            if not evidence.strip():
                return {"ok": False,
                        "error": "completion requires evidence (dürüst doğrulama)"}
            self.db.execute(
                "UPDATE goals SET status='DONE', completed=?, evidence=?, verify_fn=?"
                " WHERE id=?", (_now(), evidence[:500], verify_fn[:120], goal))
            self._log(goal, "COMPLETED", evidence)
            self.db.commit()
            return {"ok": True, "verified_by": evidence}

    def cancel(self, goal: str, reason: str) -> dict:
        with self.lock:
            g = self._get(goal)
            if g is None:
                return {"ok": False, "error": "unknown goal"}
            if g["status"] == "DONE":
                return {"ok": False, "error": "already completed"}
            self.db.execute("UPDATE goals SET status='CANCELLED' WHERE id=?",
                            (goal,))
            self._log(goal, "CANCELLED", reason)
            self.db.commit()
            return {"ok": True, "reason": reason}

    def fail(self, goal: str, reason: str) -> dict:
        with self.lock:
            self.db.execute("UPDATE goals SET status='FAILED' WHERE id=?"
                            " AND status='ACTIVE'", (goal,))
            self._log(goal, "FAILED", reason)
            self.db.commit()
            return {"ok": True}

    # ---------------------------------------------------------- views
    def progress(self, goal: str) -> dict:
        """Gerçek tümevarımlı ilerleme: alt ağaç DONE oranı (yaprıksa
        kendisinin durumu). Uydurma ara değer YOK — kanıt bazlı."""
        with self.lock:
            kids = self.children(goal)
            if not kids:
                g = self._get(goal)
                done = 1.0 if g and g["status"] == "DONE" else 0.0
                sub = 1
            else:
                sub = 0
                done_sum = 0.0
                for c in kids:
                    pr = self.progress(c["id"])
                    sub += pr["subtree_size"]
                    done_sum += pr["fraction"] * pr["subtree_size"]
                done = done_sum / sub if sub else 0.0
            return {"goal": goal, "fraction": round(done, 3),
                    "subtree_size": sub,
                    "blocked_by": self.blocked_by(goal)}

    def deadline_risk(self, goal: str, now: float | None = None) -> dict:
        now = _now() if now is None else now
        with self.lock:
            g = self._get(goal)
            if not g or g["deadline"] is None:
                return {"goal": goal, "deadline": None, "risk": "none"}
            remaining = g["deadline"] - now
            pr = self.progress(goal)
            if g["status"] == "DONE":
                risk = "none"
            elif remaining <= 0:
                risk = "overdue"
            elif pr["fraction"] >= 0.99:
                risk = "none"
            else:
                # kalan iş / kalan zaman oranı: kaba ama dürüst
                remaining_fraction = max(0.01, 1.0 - pr["fraction"])
                time_fraction = remaining / max(1.0, g["deadline"] - g["created"])
                if remaining_fraction > time_fraction * 1.5:
                    risk = "high"
                elif remaining_fraction > time_fraction:
                    risk = "medium"
                else:
                    risk = "low"
            return {"goal": goal, "deadline": g["deadline"],
                    "seconds_left": round(remaining, 1), "risk": risk}

    def active_goals(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,title,priority,status,deadline FROM goals"
                " WHERE status IN ('ACTIVE','BLOCKED') ORDER BY priority").fetchall()
        return [{"id": r[0], "title": r[1], "priority": r[2], "status": r[3],
                 "deadline": r[4]} for r in rows]

    def history(self, goal: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT kind,detail,ts FROM events WHERE goal=? ORDER BY id",
                (goal,)).fetchall()
        return [{"kind": r[0], "detail": r[1], "ts": r[2]} for r in rows]
