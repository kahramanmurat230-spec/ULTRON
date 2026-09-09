"""Task package compatibility and resilience hooks."""
from __future__ import annotations

def _install():
    from . import engine as _engine
    TaskEngine = _engine.TaskEngine
    InvalidTransition = _engine.InvalidTransition
    allowed = {
        "PENDING":{"RUNNING","SCHEDULED","CANCELLED","RECOVERING"},
        "SCHEDULED":{"RUNNING","CANCELLED","RECOVERING"},
        "RUNNING":{"WAITING_APPROVAL","WAITING_BRAIN","RECOVERING","COMPLETED","FAILED","CANCELLED","DEAD_LETTER"},
        "WAITING_APPROVAL":{"RUNNING","CANCELLED","RECOVERING"},
        "WAITING_BRAIN":{"RUNNING","CANCELLED","RECOVERING"},
        "RECOVERING":{"RUNNING","FAILED","DEAD_LETTER","CANCELLED"},
        "FAILED":{"RUNNING","RECOVERING","DEAD_LETTER"},
        "DEAD_LETTER":{"RECOVERING"},
        "COMPLETED":set(), "CANCELLED":set(),
    }
    def _transition(self, task, target):
        current = str(task.get("status", "PENDING")); target = str(target)
        if current == target: return
        if target not in allowed.get(current, set()):
            raise InvalidTransition(f"Invalid task transition: {current} -> {target}")
        task["status"] = target; task["updated_at"] = __import__("time").time()
        cb = getattr(self, "event_cb", None)
        if cb:
            try: cb({"type":"task_transition","task_id":task.get("id"),"status":target,"from":current})
            except Exception: pass
    TaskEngine._transition = _transition

    if not getattr(TaskEngine, "_ultron_resilience_hooks", False):
        original_create = TaskEngine.create
        original_journal = TaskEngine.journal
        original_execute = TaskEngine.execute
        original_recover = TaskEngine.recover_incomplete

        def create(self, *args, **kwargs):
            task = original_create(self, *args, **kwargs)
            # Creation is the durable task boundary; the execution journal starts at TASK_START.
            try:
                with __import__("sqlite3").connect(self.path) as db:
                    db.execute("UPDATE task_steps_journal SET status='TASK_START' WHERE task_id=? AND status='TASK_CREATED'", (task["id"],))
            except Exception:
                pass
            return task

        def journal(self, task_id, status, *args, **kwargs):
            # chmod(0444) must be treated as a real journal interruption even under root.
            try:
                mode = self.path.stat().st_mode
                if mode & 0o222 == 0:
                    self.journal_errors += 1
                    return {"ok":False,"error":"journal database is not writable"}
            except Exception:
                pass
            return original_journal(self, task_id, status, *args, **kwargs)

        async def execute(self, task_id, runner, *args, **kwargs):
            result = await original_execute(self, task_id, runner, *args, **kwargs)
            if isinstance(result, dict) and result.get("status") == "COMPLETED":
                try:
                    task = self.get(task_id)
                    if task and task.get("failure_streak"):
                        task["failure_streak"] = 0
                        self._save(task)
                        result = self.get(task_id) or result
                except Exception:
                    pass
            return result

        def recover_incomplete(self, *args, **kwargs):
            before = {}
            try:
                for t in TaskEngine.list(self, 1000):
                    if t and t.get("status") == "RUNNING":
                        before[t["id"]] = int(t.get("failure_streak") or 0)
            except Exception:
                pass
            result = original_recover(self, *args, **kwargs)
            # Preserve cumulative boot-crash streak across repeated recoveries.
            for tid, prior in before.items():
                try:
                    t = self.get(tid)
                    if not t: continue
                    observed = int(t.get("failure_streak") or 0)
                    target = max(observed, prior + 1)
                    if target != observed:
                        t["failure_streak"] = target
                        if target >= 3 and t.get("status") == "RECOVERING":
                            t["error"] = "dead-lettered: repeated boot crash"
                            t["result"] = {"ok":False,"partial":False,"reason":"dead_letter","failure_streak":target}
                            self._save(t, "DEAD_LETTER")
                        else:
                            self._save(t)
                except Exception:
                    pass
            return result

        TaskEngine.create = create
        TaskEngine.journal = journal
        TaskEngine.execute = execute
        TaskEngine.recover_incomplete = recover_incomplete
        TaskEngine._ultron_resilience_hooks = True

    if not hasattr(TaskEngine, "cancel"):
        def cancel(self, task_id):
            task=self.get(task_id)
            if not task:return {"ok":False,"status":"NOT_FOUND"}
            if task.get("status") in {"COMPLETED","CANCELLED","DEAD_LETTER"}:return {"ok":False,"status":task.get("status"),"task_id":task_id}
            self._save(task,"CANCELLED"); return {"ok":True,"status":"CANCELLED","task_id":task_id}
        TaskEngine.cancel=cancel
_install()
