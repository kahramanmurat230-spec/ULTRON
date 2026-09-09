"""Task package state-machine compatibility helpers."""
from __future__ import annotations

def _install():
    from . import engine as _engine
    TaskEngine=_engine.TaskEngine
    if not hasattr(TaskEngine,"_transition"):
        allowed={"PENDING":{"RUNNING","SCHEDULED","CANCELLED","RECOVERING"},"SCHEDULED":{"RUNNING","CANCELLED","RECOVERING"},"RUNNING":{"WAITING_APPROVAL","WAITING_BRAIN","RECOVERING","COMPLETED","FAILED","CANCELLED","DEAD_LETTER"},"WAITING_APPROVAL":{"RUNNING","CANCELLED","RECOVERING"},"WAITING_BRAIN":{"RUNNING","CANCELLED","RECOVERING"},"RECOVERING":{"RUNNING","FAILED","DEAD_LETTER","CANCELLED"},"FAILED":{"RUNNING","RECOVERING","DEAD_LETTER"},"COMPLETED":set(),"CANCELLED":set(),"DEAD_LETTER":set()}
        def _transition(self,task,target):
            current=str(task.get("status","PENDING")); target=str(target)
            if current==target:return
            if target not in allowed.get(current,set()):raise ValueError(f"Invalid task transition: {current} -> {target}")
            task["status"]=target; task["updated_at"]=__import__("time").time()
            cb=getattr(self,"event_cb",None)
            if cb:
                try:cb({"type":"task_transition","task_id":task.get("id"),"status":target,"from":current})
                except Exception:pass
        TaskEngine._transition=_transition
    if not hasattr(TaskEngine,"cancel"):
        def cancel(self,task_id):
            task=self.get(task_id)
            if not task:return {"ok":False,"status":"NOT_FOUND"}
            if task.get("status") in {"COMPLETED","CANCELLED","DEAD_LETTER"}:return {"ok":False,"status":task.get("status"),"task_id":task_id}
            self._save(task,"CANCELLED");return {"ok":True,"status":"CANCELLED","task_id":task_id}
        TaskEngine.cancel=cancel
    if not getattr(TaskEngine,"_ultron_journal_guard",False):
        import os
        original=TaskEngine.journal
        def journal(self,*args,**kwargs):
            try:
                if os.path.exists(self.path) and not (os.stat(self.path).st_mode & 0o222):
                    return {"ok":False,"error":"journal path is not writable"}
            except Exception:pass
            return original(self,*args,**kwargs)
        TaskEngine.journal=journal;TaskEngine._ultron_journal_guard=True
_install()
