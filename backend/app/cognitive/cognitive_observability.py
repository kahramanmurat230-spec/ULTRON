"""Cognitive Observability — Wave 5 §19.

Mevcut Observability (app/observability/trace.py) ÜZERİNE additive:
cognitive iz kanalı — goal state, decision state, context state,
confidence, prediction, capability selection, research provenance,
learning event, autonomous action.

GÜVENLİK KURALI (korunur): tüm kayıtlar app/security/redaction.redact()
süzgecinden geçer — secret değerler cognitive trace'e DÜŞMEZ.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

KINDS = ("goal", "decision", "context", "confidence", "prediction",
         "capability", "research", "learning", "autonomous", "communication")


def _now() -> float:
    return time.time()


class CognitiveTrace:
    def __init__(self, db_path: str = "data/cognitive/trace.db",
                 redact_fn=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS trace(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            kind TEXT, subject TEXT, data TEXT, redacted INTEGER DEFAULT 1)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_kind ON trace(kind)")
        self.db.commit()
        self._pending = 0
        if redact_fn is None:
            from app.security.redaction import redact as _r

            def redact_fn(x):
                return _r(x)
        self.redact_fn = redact_fn

    # ---------------------------------------------------- yazma
    def log(self, kind: str, subject: str, data: dict | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown trace kind: {kind} ({KINDS})")
        with self.lock:
            raw = json.dumps(data or {}, ensure_ascii=False)
            red = self.redact_fn(raw)     # secret DEĞERLER burada yakalanır
            self.db.execute(
                "INSERT INTO trace(ts,kind,subject,data) VALUES(?,?,?,?)",
                (_now(), kind, self.redact_fn(str(subject))[:160],
                 red[:4000]))
            self._pending += 1
            if self._pending >= 25:   # batch commit: kayıt başına disk sync YOK
                self.db.commit()
                self._pending = 0
            return {"ok": True, "kind": kind, "redacted": red != raw}

    # ---------------------------------------------------- okuma
    def query(self, kind: str | None = None, subject_like: str = "",
              limit: int = 50) -> list[dict]:
        q = "SELECT ts,kind,subject,data FROM trace WHERE 1=1"
        args: list = []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if subject_like:
            q += " AND subject LIKE ?"
            args.append(f"%{subject_like}%")
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            if self._pending:      # askıda kayıt varsa önce floş (tutarlı okuma)
                self.db.commit()
                self._pending = 0
            rows = self.db.execute(q, args).fetchall()
        return [{"ts": r[0], "kind": r[1], "subject": r[2],
                 "data": json.loads(r[3])} for r in rows]

    def stats(self) -> dict:
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM trace").fetchone()[0]
            by_kind = dict(self.db.execute(
                "SELECT kind, COUNT(*) FROM trace GROUP BY kind").fetchall())
        return {"total": total, "by_kind": by_kind}
