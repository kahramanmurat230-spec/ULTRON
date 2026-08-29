from pathlib import Path
from datetime import datetime

class AuditLog:
    def __init__(self, path="data/logs/audit.log"):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def write(self,event,detail=""):
        # PHASE 1: audit kanalı secret sızdıramaz — redact ZORUNLU
        from app.security.redaction import redact
        if not isinstance(detail, str):
            detail = str(detail)
        detail = redact(detail)
        self.path.open("a",encoding="utf-8").write(f"{datetime.now().isoformat(timespec='seconds')} | {event} | {detail}\n")
    def recent(self, limit=20, contains=None):
        if not self.path.exists(): return []
        lines=self.path.read_text(encoding="utf-8",errors="replace").splitlines()
        if contains: lines=[x for x in lines if contains.lower() in x.lower()]
        return lines[-limit:][::-1]
