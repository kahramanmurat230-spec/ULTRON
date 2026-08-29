from pathlib import Path
from datetime import datetime
from app.security.redaction import redact

class AuditLog:
    def __init__(self, path="data/logs/audit.log", extra_values_fn=None):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.extra_values_fn=extra_values_fn  # e.g. vault.all_values
    def write(self,event,detail=""):
        # PHASE 1 (merge): audit kanalı secret sızdıramaz — redact ZORUNLU.
        # İki katman birlikte: pattern redact (R+L) + vault concrete değerler
        # (R: extra_values_fn) + tip güvenliği (L: non-str coercion).
        if not isinstance(detail, str):
            detail = str(detail)
        extra=()
        if self.extra_values_fn is not None:
            try: extra=tuple(self.extra_values_fn())
            except Exception: extra=()
        detail=redact(detail, extra_values=extra)
        self.path.open("a",encoding="utf-8").write(f"{datetime.now().isoformat(timespec='seconds')} | {event} | {detail}\n")
    def recent(self, limit=20, contains=None):
        if not self.path.exists(): return []
        lines=self.path.read_text(encoding="utf-8",errors="replace").splitlines()
        if contains: lines=[x for x in lines if contains.lower() in x.lower()]
        return lines[-limit:][::-1]
