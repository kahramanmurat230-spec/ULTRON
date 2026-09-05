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

WAVE 1 additions (additive):
  - append-only task_steps_journal: every step event persisted with
    input_hash, output summary (redacted), duration, risk decision,
    error class and trace/span ids
  - structured traces via app.observability.trace.Tracer (task span +
    nested step spans); secrets redacted before persist
  - WAL recovery: wal_replay() + reconcile_from_journal() let a crashed
    task resume without re-running already-SUCCESSFUL steps (idempotency)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

STATUSES = ("PENDING", "RUNNING", "WAITING_APPROVAL", "PAUSED",
            "FAILED", "RECOVERING", "COMPLETED", "CANCELLED",
            # WAVE 1 (additive):
            "SCHEDULED",        # cron/event tetiklenmesini bekliyor
            "WAITING_BRAIN",    # yerel LLM yok — kalıcı bekleme, fail YOK
            "DEAD_LETTER")      # 3+ üst üste crash — OTOMATİK RETRY YOK

ALLOWED_TRANSITIONS = {
    "PENDING": {"RUNNING", "CANCELLED", "RECOVERING", "SCHEDULED"},
    "SCHEDULED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING_APPROVAL", "PAUSED", "FAILED", "COMPLETED", "CANCELLED", "RECOVERING", "WAITING_BRAIN", "DEAD_LETTER"},
    "WAITING_BRAIN": {"RUNNING", "CANCELLED", "FAILED"},
    "DEAD_LETTER": {"RECOVERING"},   # yalnız İNSAN requeue ile canlandırır
    "RECOVERING": {"RUNNING", "FAILED", "CANCELLED", "DEAD_LETTER"},
    "WAITING_APPROVAL": {"RUNNING", "CANCELLED", "FAILED"},
    "PAUSED": {"RUNNING", "CANCELLED"},
    "FAILED": {"RECOVERING", "CANCELLED", "DEAD_LETTER"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

DEFAULT_BUDGETS = {
    # WAVE 1 additive budgets (None = sınırsız; mevcut anahtarlar korunur):
    "wall_clock_s": None,   # duvar saati; timeout_s penceresinden sıkıysa kazanır
    "token_budget": None,   # runner ctx["usage"]["tokens"] raporladıkça denetlenir
    "cost_budget": None,    # ctx["usage"]["cost"] (soyut maliyet puanı)
    "cpu_max_pct": None,    # süreç geneli CPU %% (örneklemeli, dürüst kapsam)
    "mem_max_mb": None,     # süreç RSS üst sınırı (MB)
    "max_iterations": 8,       # plan-level iterations (incl. replans)
    "timeout_s": 600,          # overall wall-clock budget
    "tool_budget": 50,         # max tool/worker invocations
    "retry_budget": 1,         # retries per step
    "step_timeout_s": 240,     # single step wall-clock
}


class BrainUnavailable(Exception):
    """Raised by a step runner when the local LLM is required but down.
    WAVE 1: engine parks the task in WAITING_BRAIN instead of failing."""


class NeedsApproval(Exception):
    """Raised by a step runner when explicit user approval is required."""

    def __init__(self, reason: str, risks: list | None = None):
        super().__init__(reason)
        self.reason = reason
        self.risks = risks or []


class InvalidTransition(Exception):
    pass


class _HardDeadlineExceeded(Exception):
    """Internal: hard deadline reached — graceful cancel + partial report."""


def step_i_hint(task: dict) -> int:
    return int(task.get("current_step", 0))


class TaskEngine:
    def __init__(self, db_path="data/tasks/tasks.db", event_cb=None, now=None,
                 tracer=None, journal_enabled=True, on_dead_letter=None,
                 resource_sampler=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.event_cb = event_cb
        self.now = now or time.time
        self._running: dict[str, asyncio.Task] = {}
        # WAVE 1: observability + append-only journal
        self._tracer = tracer          # lazy default: shared local Tracer
        self.journal_enabled = journal_enabled
        self.journal_writes = 0
        self.journal_errors = 0
        # WAVE 1: poison-task dead-letter + kaynak bütçeleri
        self.on_dead_letter = on_dead_letter        # insan bildirimi kancası
        self._sample_resources = resource_sampler or self._psutil_sample
        self.dead_letter_count = 0
        self._journal_db = None        # kalıcı bağlantı (connect maliyeti yok)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS task_steps_journal(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_index INTEGER, step_label TEXT, worker TEXT,
                status TEXT NOT NULL,
                input_hash TEXT, output_summary TEXT,
                duration_ms REAL, risk TEXT, approval TEXT, error_class TEXT,
                trace_id TEXT, span_id TEXT,
                ts REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_journal_task"
                       " ON task_steps_journal(task_id)")
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
            db.execute("""CREATE TABLE IF NOT EXISTS task_artifacts(
                artifact_id TEXT PRIMARY KEY,
                task_id TEXT, path TEXT, sha256 TEXT, created_at REAL)""")
            # WAVE 1 additive columns on tasks (existing DBs migrate in place)
            have = {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
            for col, ddl in (("needs", "TEXT"),
                             ("deadline_soft", "REAL"),
                             ("deadline_hard", "REAL"),
                             ("template_key", "TEXT"),
                             ("failure_streak", "INTEGER")):
                if col not in have:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")

    @staticmethod
    def _psutil_sample() -> tuple:
        """(cpu_pct, rss_mb) — süreç geneli, dürüst kapsam."""
        try:
            import psutil
            proc = psutil.Process()
            cpu = proc.cpu_percent(interval=None)
            return float(cpu), proc.memory_info().rss / (1024 * 1024)
        except Exception:
            return (0.0, 0.0)

    def _resource_check(self, task: dict) -> None:
        budgets = task.get("budgets") or {}
        cpu_max = budgets.get("cpu_max_pct")
        mem_max = budgets.get("mem_max_mb")
        if cpu_max is None and mem_max is None:
            return
        cpu, mem = self._sample_resources()
        if mem_max is not None and mem > float(mem_max):
            raise RuntimeError(f"memory_budget exceeded: {mem:.0f}MB > {mem_max}MB")
        if cpu_max is not None and cpu > float(cpu_max):
            raise RuntimeError(f"cpu_budget exceeded: {cpu:.0f}% > {cpu_max}%")

    def _usage_check(self, task: dict, usage: dict) -> None:
        budgets = task.get("budgets") or {}
        tb = budgets.get("token_budget")
        cb = budgets.get("cost_budget")
        if tb is not None and usage.get("tokens", 0) > float(tb):
            raise RuntimeError(f"token_budget exceeded: {usage.get('tokens')} > {tb}")
        if cb is not None and usage.get("cost", 0.0) > float(cb):
            raise RuntimeError(f"cost_budget exceeded: {usage.get('cost')} > {cb}")

    def _dead_letter(self, task: dict, reason: str) -> None:
        task["error"] = self._redact(f"dead-lettered: {reason}")[:500]
        task["result"] = {"ok": False, "partial": False, "reason": "dead_letter",
                          "failure_streak": task.get("failure_streak"),
                          "detail": self._redact(reason)[:200], "ts": self.now()}
        self._save(task, "DEAD_LETTER")
        self.dead_letter_count += 1
        self.emit(task["id"], "engine", "TASK_DEAD_LETTER",
                  f"failure_streak={task.get('failure_streak')}: {reason}")
        self.journal(task["id"], "TASK_DEAD_LETTER",
                     output_summary=f"failure_streak="
                                    f"{task.get('failure_streak')}: {reason}",
                     error_class="DeadLetter")
        if self.on_dead_letter:
            try:
                self.on_dead_letter(self.get(task["id"]) or task)
            except Exception:
                pass  # bildirim kancası hatası motora sıçramaz

    def requeue(self, task_id: str) -> dict:
        """İNSAN onaylı canlandırma: DEAD_LETTER → RECOVERING (streak sıfırlanır).
        Otomatik sonsuz retry YASAKTIR; yalnızca bu açık API ile dönülür."""
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        if t["status"] != "DEAD_LETTER":
            return {"ok": False, "error": f"task not DEAD_LETTER ({t['status']})"}
        t["failure_streak"] = 0
        t["checkpoint"] = None
        for st_ in t["steps"]:
            if st_["status"] != "SUCCESS":
                st_["attempts"] = 0
                st_["status"] = "PENDING"
        self._save(t, "RECOVERING")
        self.journal(task_id, "TASK_REQUEUE", output_summary="manual requeue")
        return self.get(task_id)

    def _get_tracer(self):
        if self._tracer is None:
            from app.observability.trace import Tracer
            self._tracer = Tracer()
        return self._tracer

    # ------------------------------------------------------------ journal
    def journal(self, task_id: str, status: str, step: dict | None = None,
                input_hash: str | None = None, output_summary: str | None = None,
                duration_ms: float | None = None, risk=None, approval=None,
                error_class: str | None = None, trace_id: str | None = None,
                span_id: str | None = None) -> dict:
        """Append-only step journal. NEVER raises (observability must not
        break execution); failures are counted honestly instead."""
        if not self.journal_enabled:
            return {"ok": False, "error": "journal disabled"}
        try:
            summary = self._get_tracer().redact_fn(str(output_summary or ""))[:200]
            risk_s = json.dumps(risk, ensure_ascii=False, default=str) if risk else None
            appr = self._get_tracer().redact_fn(str(approval))[:120] if approval else None
            if self._journal_db is None:   # kalıcı bağlantı: bağlantı maliyeti yok
                self._journal_db = sqlite3.connect(self.path,
                                                   check_same_thread=False)
                self._journal_db.execute("PRAGMA journal_mode=WAL")
                self._journal_db.execute("PRAGMA synchronous=NORMAL")
            self._journal_db.execute(
                "INSERT INTO task_steps_journal(task_id,step_index,step_label,"
                "worker,status,input_hash,output_summary,duration_ms,risk,"
                "approval,error_class,trace_id,span_id,ts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id,
                 int(step["index"]) if step else None,
                 str(step.get("label"))[:120] if step else None,
                 str(step.get("worker"))[:80] if step else None,
                 status, input_hash, summary,
                 float(duration_ms) if duration_ms is not None else None,
                 risk_s, appr, error_class, trace_id, span_id, self.now()))
            self._journal_db.commit()      # her satır dayanıklı (WAL + commit)
            self.journal_writes += 1
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            self.journal_errors += 1
            return {"ok": False, "error": str(exc)[:200]}

    def journal_rows(self, task_id: str, limit: int = 500) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT seq,task_id,step_index,step_label,worker,status,input_hash,"
                "output_summary,duration_ms,risk,approval,error_class,trace_id,"
                "span_id,ts FROM task_steps_journal WHERE task_id=?"
                " ORDER BY seq LIMIT ?", (task_id, limit)).fetchall()
        cols = ("seq", "task_id", "step_index", "step_label", "worker", "status",
                "input_hash", "output_summary", "duration_ms", "risk", "approval",
                "error_class", "trace_id", "span_id", "ts")
        return [dict(zip(cols, r)) for r in rows]

    @staticmethod
    def _step_input_hash(step: dict) -> str:
        import hashlib
        canon = json.dumps({"label": step.get("label"), "worker": step.get("worker"),
                            "args": step.get("args", {})}, sort_keys=True,
                           ensure_ascii=False, default=str)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _risk_decision(step: dict) -> dict:
        """Honest risk mapping for a plan step (read-only use of risk core)."""
        from app.security.risk import TOOL_RISK
        worker = str(step.get("worker", "unknown"))
        return {"worker": worker, "level": TOOL_RISK.get(worker) or "UNMAPPED"}

    # ------------------------------------------------------------ WAL
    def wal_replay(self, task_id: str) -> dict:
        """Read the journal as a write-ahead log: which steps are known
        SUCCESS, which were mid-flight (STEP_START without outcome)."""
        state: dict[int, str] = {}
        labels: dict[int, str] = {}
        for r in self.journal_rows(task_id):
            idx = r["step_index"]
            if idx is None:
                continue
            if r["status"] == "STEP_START":
                state[idx] = "START"
            elif r["status"] == "STEP_SUCCESS":
                state[idx] = "SUCCESS"
            elif r["status"] == "STEP_FAILED":
                state[idx] = "FAILED"
            labels[idx] = r["step_label"] or f"step-{idx}"
        successful = sorted(i for i, st in state.items() if st == "SUCCESS")
        incomplete = sorted(i for i, st in state.items() if st == "START")
        return {"successful_steps": [{"index": i, "label": labels[i]} for i in successful],
                "incomplete": incomplete,
                "labels": labels}

    def reconcile_from_journal(self, task_id: str) -> dict:
        """WAL replay: mark journaled-SUCCESS steps as SUCCESS in the task
        record so resume NEVER re-runs them (idempotency)."""
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        replay = self.wal_replay(task_id)
        changed = False
        for done in replay["successful_steps"]:
            step = next((s for s in t["steps"]
                         if s["label"] == done["label"] and s["status"] != "SUCCESS"), None)
            if step is not None:
                step["status"] = "SUCCESS"
                step["result"] = {"ok": True,
                                  "output": "(WAL replay: journaled SUCCESS)"}
                changed = True
        for i, s in enumerate(t["steps"]):
            if s["status"] not in ("SUCCESS", "SKIPPED"):
                t["current_step"] = i
                break
        else:
            t["current_step"] = len(t["steps"])
        if changed:
            self._save(t)
        return {"ok": True, "changed": changed,
                "replayed": [d["label"] for d in replay["successful_steps"]],
                "incomplete": replay["incomplete"]}

    # ------------------------------------------------------------ events
    def _redact(self, text) -> str:
        """Güvenlik: error/event kanallarına çıkan HER metin maskelenir."""
        try:
            return self._get_tracer().redact_fn(str(text))[:300]
        except Exception:
            return str(text)[:300]

    def emit(self, task_id: str, component: str, status: str, detail: str = "") -> None:
        ev = {"task_id": task_id, "ts": self.now(), "component": component,
              "status": status, "detail": self._redact(detail)}
        if self.event_cb:
            try:
                self.event_cb(ev)
            except Exception:
                pass

    # ------------------------------------------------------------ CRUD
    # ------------------------------------------------------------ needs
    def _norm_needs(self, needs: list | None) -> list[str]:
        out = []
        for n in (needs or []):
            n = str(n).strip()
            if not n:
                continue
            if n.startswith("artifact:"):
                aid = n[len("artifact:"):]
                if (not aid or len(aid) > 120 or "/" in aid or "\\" in aid
                        or ".." in aid):
                    raise ValueError("invalid artifact dependency id")
                out.append(n)          # artifact henüz üretilmemiş olabilir (gelecek)
            elif n.startswith("task:"):
                dep = n[5:]
                if not self.get(dep):
                    raise ValueError(f"unknown task dependency: {dep}")
                out.append(n)
            else:
                if not self.get(n):
                    raise ValueError(f"unknown task dependency: {n}")
                out.append(f"task:{n}")
        if len(out) > 32:
            raise ValueError("too many dependencies (max 32)")
        return out

    def _task_edges(self) -> dict[str, set[str]]:
        """Mevcut tüm task->task kenarları (needs grafiği)."""
        edges: dict[str, set[str]] = {}
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,needs FROM tasks").fetchall()
        for tid, needs_json in rows:
            deps = set()
            try:
                for n in json.loads(needs_json or "[]"):
                    if str(n).startswith("task:"):
                        deps.add(str(n)[5:])
            except Exception:
                pass
            edges[tid] = deps
        return edges

    def _would_cycle(self, extra: dict[str, set[str]] | None = None) -> str | None:
        """DFS ile döngü arar; döngü varsa görevli düğümü döner."""
        edges = self._task_edges()
        for k, v in (extra or {}).items():
            edges.setdefault(k, set()).update(v)

        def dfs(node: str, visiting: set, done: set) -> str | None:
            if node in visiting:
                return node
            if node in done:
                return None
            visiting.add(node)
            for nxt in edges.get(node, ()):  # noqa: B023
                hit = dfs(nxt, visiting, done)
                if hit:
                    return hit
            visiting.discard(node)
            done.add(node)
            return None

        done: set = set()
        for node in list(edges):
            hit = dfs(node, set(), done)
            if hit:
                return hit
        return None

    def update_needs(self, task_id: str, needs: list) -> dict:
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        norm = self._norm_needs(needs)
        mine = {n[5:] for n in norm if n.startswith("task:")}
        if task_id in mine:
            raise ValueError("circular dependency: task cannot need itself")
        cycle = self._would_cycle({task_id: mine})
        if cycle:
            raise ValueError(f"circular dependency rejected (cycle at {cycle})")
        t["needs"] = norm
        self._save(t)
        return {"ok": True, "needs": norm}

    # ------------------------------------------------------------ artifacts
    def mark_artifact(self, task_id: str, artifact_id: str, path: str | None = None,
                      sha256: str | None = None) -> dict:
        aid = str(artifact_id).strip()
        if not aid or len(aid) > 120 or "/" in aid or ".." in aid or "\\" in aid:
            raise ValueError("invalid artifact id (path traversal rejected)")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO task_artifacts"
                       "(artifact_id,task_id,path,sha256,created_at) VALUES(?,?,?,?,?)",
                       (aid, task_id, str(path or "")[:300],
                        str(sha256 or "")[:64], self.now()))
        self.journal(task_id, "ARTIFACT_MARKED", output_summary=aid)
        return {"ok": True, "artifact_id": aid}

    def artifact_exists(self, artifact_id: str) -> bool:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT 1 FROM task_artifacts WHERE artifact_id=?",
                             (artifact_id,)).fetchone()
        return row is not None

    def dependency_state(self, task_id: str) -> dict:
        """ready | blocked | broken — DAG + artifact bağımlılıkları."""
        t = self.get(task_id)
        if not t:
            return {"state": "broken", "details": ["no such task"]}
        details = []
        state = "ready"
        for n in t.get("needs") or []:
            if n.startswith("artifact:"):
                aid = n[len("artifact:"):]
                if self.artifact_exists(aid):
                    details.append({"need": n, "ok": True})
                else:
                    state = "blocked" if state == "ready" else state
                    details.append({"need": n, "ok": False, "waiting": True})
                continue
            dep = self.get(n[5:])
            if not dep:
                state = "broken"
                details.append({"need": n, "ok": False, "missing": True})
            elif dep["status"] == "COMPLETED":
                details.append({"need": n, "ok": True})
            elif dep["status"] in ("FAILED", "CANCELLED"):
                state = "broken"
                details.append({"need": n, "ok": False, "failed": dep["status"]})
            else:
                state = "blocked" if state == "ready" else state
                details.append({"need": n, "ok": False, "waiting": dep["status"]})
        return {"state": state, "details": details}

    def create(self, goal: str, kind: str = "custom", steps: list | None = None,
               priority: int = 5, budgets: dict | None = None,
               needs: list | None = None, deadline_soft_s: float | None = None,
               deadline_hard_s: float | None = None, template_key: str | None = None,
               scheduled: bool = False) -> dict:
        tid = uuid.uuid4().hex[:8]
        merged = dict(DEFAULT_BUDGETS)
        merged.update(budgets or {})
        now = self.now()
        norm_needs = self._norm_needs(needs)
        if norm_needs:
            mine = {n[5:] for n in norm_needs if n.startswith("task:")}
            if self._would_cycle({tid: mine}):
                raise ValueError("circular dependency rejected")
        task = {
            "id": tid, "goal": goal, "kind": kind,
            "status": "SCHEDULED" if scheduled else "PENDING",
            "priority": int(priority),
            "steps": [self._norm_step(s, i) for i, s in enumerate(steps or [])],
            "current_step": 0, "checkpoint": None, "retry_count": 0,
            "budgets": merged, "result": None, "error": None,
            "created_at": now, "started_at": None, "updated_at": now,
            "needs": norm_needs,
            "deadline_soft": now + float(deadline_soft_s) if deadline_soft_s else None,
            "deadline_hard": now + float(deadline_hard_s) if deadline_hard_s else None,
            "template_key": template_key,
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO tasks(id,goal,kind,status,priority,steps,current_step,"
                "checkpoint,retry_count,budgets,result,error,created_at,started_at,"
                "updated_at,needs,deadline_soft,deadline_hard,template_key)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, goal, kind, task["status"], task["priority"],
                 json.dumps(task["steps"]), 0, None, 0, json.dumps(merged),
                 None, None, now, None, now, json.dumps(norm_needs),
                 task["deadline_soft"], task["deadline_hard"], template_key))
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
                "created_at", "started_at", "updated_at",
                "needs", "deadline_soft", "deadline_hard", "template_key",
                "failure_streak")
        row = list(row) + [None] * (len(cols) - len(row))  # eski DB satırları
        t = dict(zip(cols, row))
        t["failure_streak"] = int(t.get("failure_streak") or 0)
        t["steps"] = json.loads(t["steps"])
        t["budgets"] = json.loads(t["budgets"])
        t["checkpoint"] = json.loads(t["checkpoint"]) if t["checkpoint"] else None
        t["result"] = json.loads(t["result"]) if t["result"] else None
        t["needs"] = json.loads(t["needs"]) if t["needs"] else []
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
                "retry_count=?,result=?,error=?,started_at=?,updated_at=?,needs=?,"
                "failure_streak=? WHERE id=?",
                (task["status"], json.dumps(task["steps"]), task["current_step"],
                 json.dumps(task["checkpoint"]) if task["checkpoint"] is not None else None,
                 task["retry_count"], json.dumps(task["result"]) if task["result"] is not None else None,
                 task["error"], task["started_at"], task["updated_at"],
                 json.dumps(task.get("needs") or []),
                 int(task.get("failure_streak") or 0), task["id"]))

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
        if t["status"] in ("COMPLETED", "CANCELLED", "DEAD_LETTER"):
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
        if task["status"] == "DEAD_LETTER":
            raise RuntimeError("dead-lettered task: manual requeue() required "
                               "(automatic retry is forbidden)")
        # WAVE 1: DAG bağımlılık kapısı (broken → dürüst fail, blocked → bekle)
        if task.get("needs"):
            dep = self.dependency_state(task_id)
            if dep["state"] == "broken":
                # PENDING->FAILED geçişi mevcut durum makinesinde yok; dürüst
                # terminal çözüm: CANCELLED + reason=dependency_broken (retry yok)
                task["error"] = self._redact(
                    f"dependency broken: {dep['details']}")[:500]
                task["result"] = {"ok": False, "partial": False,
                                  "reason": "dependency_broken",
                                  "details": dep["details"], "ts": self.now()}
                self._save(task, "CANCELLED")
                self.emit(task_id, "engine", "TASK_DEP_BROKEN", task["error"])
                self.journal(task_id, "TASK_DEP_BROKEN",
                             output_summary=f"dependency broken: {dep['details']}",
                             error_class="DependencyBroken")
                return self.get(task_id)
            if dep["state"] == "blocked":
                self.emit(task_id, "engine", "TASK_BLOCKED",
                          f"waiting on {dep['details']}")
                self.journal(task_id, "TASK_BLOCKED",
                             output_summary=f"waiting on {dep['details']}")
                return self.get(task_id)   # durum değişmez; scheduler tekrar dener
        if task["status"] == "RECOVERING":
            # çökme/poison sonrası yeniden deneme: bitkin attempt'ler sıfırlanır
            for st_ in task["steps"]:
                if st_["status"] == "RUNNING":
                    st_["status"] = "PENDING"
                if st_["status"] != "SUCCESS":
                    st_["attempts"] = 0
        self._save(task, "RUNNING")
        if not task["started_at"]:
            task["started_at"] = self.now()
        task["error"] = None
        self.emit(task_id, "engine", "TASK_START", task["goal"])
        tracer = self._get_tracer()
        root = tracer.start_trace(f"task:{task_id}", kind="task", task_id=task_id,
                                  attributes={"task": task["kind"], "goal": task["goal"]})
        self.journal(task_id, "TASK_START", trace_id=root.trace_id,
                     span_id=root.span_id)

        def _budget_snap():
            return {"tools_used": ctx["tools_used"], "iterations": iterations,
                    "tool_budget": tool_budget,
                    "timeout_s": task["budgets"].get("timeout_s")}

        def _end_root(status, error_class=None, summary=None, approval=None):
            try:
                root.end(status=status, error_class=error_class,
                         result_summary=summary, approval=approval,
                         budget=_budget_snap())
            except Exception:
                pass
        deadline = self.now() + float(task["budgets"].get("timeout_s", 600))
        wall = task["budgets"].get("wall_clock_s")
        if wall is not None:
            deadline = min(deadline, self.now() + float(wall))  # sıkı olan kazanır
        tool_budget = int(task["budgets"].get("tool_budget", 50))
        iterations = 0
        ctx = {"task": task, "tools_used": 0, "engine": self, "deadline": deadline,
               "usage": {"tokens": 0, "cost": 0.0}}  # runner burayı artırır
        # WAVE 1: soft/hard deadline (görev kaydından, mutlak zaman)
        soft_dl = task.get("deadline_soft")
        hard_dl = task.get("deadline_hard")
        soft_warned = False

        try:
            while task["current_step"] < len(task["steps"]):
                iterations += 1
                if iterations > int(task["budgets"].get("max_iterations", 8)):
                    raise RuntimeError("max plan iterations exceeded (replan budget)")
                if self.now() > deadline:
                    raise TimeoutError("task timeout budget exhausted")
                if ctx["tools_used"] >= tool_budget:
                    raise RuntimeError("tool budget exhausted")
                self._resource_check(task)   # cpu_max_pct / mem_max_mb (örneklem)
                _now = self.now()
                if hard_dl and _now > hard_dl:
                    raise _HardDeadlineExceeded("hard deadline reached")
                if soft_dl and _now > soft_dl and not soft_warned:
                    soft_warned = True
                    self.emit(task_id, "engine", "TASK_SOFT_DEADLINE",
                              f"soft deadline passed at step {step_i_hint(task)}")
                    self.journal(task_id, "TASK_SOFT_DEADLINE",
                                 output_summary="soft deadline exceeded")

                step = task["steps"][task["current_step"]]
                if step["status"] == "SUCCESS" or step["status"] == "SKIPPED":
                    task["current_step"] += 1
                    continue
                step["status"] = "RUNNING"
                step["input_hash"] = self._step_input_hash(step)
                self.emit(task_id, f"worker:{step['worker']}", "STEP_START", step["label"])
                step_span = tracer.start_span(
                    f"step:{step['label']}", kind="step", parent=root,
                    task_id=task_id,
                    attributes={"step": step["label"], "worker": step["worker"]})
                self.journal(task_id, "STEP_START", step=step,
                             input_hash=step["input_hash"],
                             trace_id=root.trace_id, span_id=step_span.span_id)
                step_t0 = self.now()

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
                        self.journal(task_id, "STEP_WAITING_APPROVAL", step=step,
                                     approval=ap.reason, trace_id=root.trace_id,
                                     span_id=step_span.span_id)
                        try:
                            step_span.end(result_summary=f"approval required: {ap.reason}",
                                          approval="required",
                                          risk=self._risk_decision(step))
                        except Exception:
                            pass
                        _end_root("OK", summary="waiting approval",
                                  approval=f"{ap.reason} risks={ap.risks}")
                        self.journal(task_id, "TASK_WAITING_APPROVAL",
                                     trace_id=root.trace_id, span_id=root.span_id)
                        return self.get(task_id)
                    except asyncio.CancelledError:
                        raise
                    except BrainUnavailable as bu:
                        step["status"] = "PENDING"
                        task["checkpoint"] = {"waiting_brain_for": step["label"]}
                        self._save(task, "WAITING_BRAIN")
                        self.emit(task_id, "engine", "TASK_WAITING_BRAIN", str(bu))
                        try:
                            step_span.end(result_summary=f"brain unavailable: {bu}",
                                          risk=self._risk_decision(step))
                        except Exception:
                            pass
                        self.journal(task_id, "STEP_WAITING_BRAIN", step=step,
                                     output_summary=str(bu),
                                     trace_id=root.trace_id, span_id=step_span.span_id)
                        _end_root("OK", summary="waiting brain")
                        self.journal(task_id, "TASK_WAITING_BRAIN",
                                     output_summary=str(bu),
                                     trace_id=root.trace_id, span_id=root.span_id)
                        return self.get(task_id)
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc          # nesne korunur (sınıf bilgisi için)
                        result = None
                        self.emit(task_id, f"worker:{step['worker']}", "STEP_ERROR",
                                  f"attempt {step['attempts']}: {last_err}")
                        if self.now() > deadline:
                            break
                if self.now() > deadline:
                    raise TimeoutError("task timeout budget exhausted")
                if result is None or not result.get("ok"):
                    err_text = (str(last_err) or type(last_err).__name__) \
                        if isinstance(last_err, Exception) else str(last_err)
                    err_cls = type(last_err).__name__ \
                        if isinstance(last_err, Exception) else "RuntimeError"
                    step["status"] = "FAILED"
                    step["result"] = {"ok": False, "error": self._redact(err_text)}
                    self.emit(task_id, f"worker:{step['worker']}", "STEP_FAILED", err_text)
                    try:
                        step_span.end(status="ERROR", error_class=err_cls,
                                      result_summary=err_text[:200],
                                      risk=self._risk_decision(step),
                                      budget=_budget_snap())
                    except Exception:
                        pass
                    self.journal(task_id, "STEP_FAILED", step=step,
                                 output_summary=err_text,
                                 duration_ms=(self.now() - step_t0) * 1000,
                                 risk=self._risk_decision(step),
                                 error_class=err_cls,
                                 trace_id=root.trace_id, span_id=step_span.span_id)
                    if step.get("critical", True):
                        raise RuntimeError(
                            f"critical step failed: {step['label']}: {err_text}")
                    task["current_step"] += 1
                    continue
                out_summary = str(_jsonable(result.get("output")))
                step["status"] = "SUCCESS"
                step["result"] = {"ok": True, "output": _jsonable(result.get("output"))}
                self.emit(task_id, f"worker:{step['worker']}", "STEP_SUCCESS", step["label"])
                try:
                    step_span.end(result_summary=out_summary[:200],
                                  risk=self._risk_decision(step),
                                  budget=_budget_snap())
                except Exception:
                    pass
                self.journal(task_id, "STEP_SUCCESS", step=step,
                             input_hash=step.get("input_hash"),
                             output_summary=out_summary,
                             duration_ms=(self.now() - step_t0) * 1000,
                             risk=self._risk_decision(step),
                             trace_id=root.trace_id, span_id=step_span.span_id)
                task["checkpoint"] = {"last_successful_step": step["label"],
                                      "ts": self.now()}
                task["current_step"] += 1
                self._save(task)
                self._usage_check(task, ctx["usage"])  # token/cost bütçeleri
                # adım sonu deadline kontrolü (tek adımlık görevlerde de çalışır)
                _now = self.now()
                if hard_dl and _now > hard_dl and task["current_step"] < len(task["steps"]):
                    raise _HardDeadlineExceeded("hard deadline reached (post-step)")
                if soft_dl and _now > soft_dl and not soft_warned:
                    soft_warned = True
                    self.emit(task_id, "engine", "TASK_SOFT_DEADLINE",
                              f"soft deadline passed after step {step['label']}")
                    self.journal(task_id, "TASK_SOFT_DEADLINE",
                                 output_summary="soft deadline exceeded")
            task["result"] = {"ok": True, "completed_steps":
                              [s["label"] for s in task["steps"] if s["status"] == "SUCCESS"],
                              "ts": self.now()}
            task["failure_streak"] = 0            # başarı streak'i temizler
            self._save(task, "COMPLETED")
            self.emit(task_id, "engine", "TASK_COMPLETE", task["goal"])
            _end_root("OK", summary="completed")
            self.journal(task_id, "TASK_COMPLETE", output_summary="completed",
                         trace_id=root.trace_id, span_id=root.span_id)
        except asyncio.CancelledError:
            # pause/cancel: status already transitioned by the caller
            self.emit(task_id, "engine", "TASK_INTERRUPTED")
            raise
        except _HardDeadlineExceeded:
            # graceful cancel + KISMİ SONUÇ raporu (veri kaybı yok)
            done = [s["label"] for s in task["steps"] if s["status"] == "SUCCESS"]
            task["result"] = {"ok": False, "partial": True, "reason": "hard_deadline",
                              "completed_steps": done, "ts": self.now()}
            self._save(task, "CANCELLED")
            self.emit(task_id, "engine", "TASK_HARD_DEADLINE",
                      f"graceful cancel; completed: {done}")
            _end_root("ERROR", error_class="HardDeadlineExceeded",
                      summary=f"graceful cancel; completed: {done}")
            self.journal(task_id, "TASK_HARD_DEADLINE",
                         output_summary=f"graceful cancel; completed: {done}",
                         error_class="HardDeadlineExceeded",
                         trace_id=root.trace_id, span_id=root.span_id)
        except Exception as exc:  # noqa: BLE001
            task["error"] = self._redact(exc)[:500]
            task["failure_streak"] = int(task.get("failure_streak") or 0) + 1
            _end_root("ERROR", error_class=type(exc).__name__,
                      summary=str(exc)[:200])
            self.journal(task_id, "TASK_FAILED", output_summary=str(exc),
                         error_class=type(exc).__name__,
                         trace_id=root.trace_id, span_id=root.span_id)
            if task["failure_streak"] >= 3:
                # POISON TASK: 3 üst üste başarısızlık → otomatik retry DURUR
                self._dead_letter(task, str(exc))
            else:
                self._save(task, "FAILED")
                self.emit(task_id, "engine", "TASK_FAILED", str(exc))
        return self.get(task_id)

    def schedule(self, task_id: str) -> dict:
        """PENDING → SCHEDULED (cron/event tetiklenmesini bekleyecek)."""
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        self._save(t, "SCHEDULED")
        self.journal(task_id, "TASK_SCHEDULED", output_summary="scheduled")
        return self.get(task_id)

    def set_brain_unavailable(self, task_id: str) -> dict:
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        self._save(t, "WAITING_BRAIN")
        self.journal(task_id, "TASK_WAITING_BRAIN",
                     output_summary="manual: brain unavailable")
        return self.get(task_id)

    def resume_brain(self, task_id: str) -> dict:
        t = self.get(task_id)
        if not t:
            return {"ok": False, "error": "no such task"}
        t["checkpoint"] = None
        self._save(t, "RUNNING")
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
        """Mark crash-orphaned tasks (RUNNING at boot) as RECOVERING and
        WAL-replay their journal: journaled-SUCCESS steps are restored so
        resume never re-runs them (idempotency)."""
        recovered = []
        for t in self.list(limit=500):
            if t["status"] in ("PENDING", "RECOVERING"):
                recovered.append(t["id"])
            elif t["status"] == "RUNNING":
                t["failure_streak"] = int(t.get("failure_streak") or 0) + 1
                if t["failure_streak"] >= 3:
                    # 3 üst üste ÇÖKME (process kill) → poison: retry YOK
                    self._dead_letter(t, "3 consecutive crashes at boot recovery")
                    continue
                self._save(t, "RECOVERING")
                self.emit(t["id"], "engine", "TASK_RECOVERING", "found RUNNING at boot")
                recovered.append(t["id"])
            else:
                continue
            try:
                rec = self.reconcile_from_journal(t["id"])
                if rec.get("changed"):
                    self.emit(t["id"], "engine", "TASK_WAL_REPLAY",
                              f"restored steps: {rec.get('replayed')}")
            except Exception as exc:  # noqa: BLE001
                self.emit(t["id"], "engine", "TASK_WAL_ERROR", str(exc)[:200])
        return recovered


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)
