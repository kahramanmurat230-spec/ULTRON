"""Task package state-machine compatibility helpers."""
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
        task["status"] = target
        task["updated_at"] = __import__("time").time()
        cb = getattr(self, "event_cb", None)
        if cb:
            try: cb({"type":"task_transition","task_id":task.get("id"),"status":target,"from":current})
            except Exception: pass
    TaskEngine._transition = _transition
    if not hasattr(TaskEngine, "cancel"):
        def cancel(self, task_id):
            task = self.get(task_id)
            if not task: return {"ok":False,"status":"NOT_FOUND"}
            if task.get("status") in {"COMPLETED","CANCELLED","DEAD_LETTER"}:
                return {"ok":False,"status":task.get("status"),"task_id":task_id}
            self._save(task,"CANCELLED")
            return {"ok":True,"status":"CANCELLED","task_id":task_id}
        TaskEngine.cancel = cancel
_install()
