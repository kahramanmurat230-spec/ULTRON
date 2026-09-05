import sqlite3
from pathlib import Path
from datetime import datetime

class Memory:
    def __init__(self, path="data/memory/ultron.db", redact_fn=None):
        self.redact_fn = redact_fn  # optional: vault-backed redaction on write
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            # V7 organic-memory migration (non-destructive)
            for col in ("last_access REAL", "importance REAL DEFAULT 0.5",
                        "archived INTEGER DEFAULT 0"):
                try:
                    db.execute(f"ALTER TABLE memories ADD COLUMN {col}")
                except Exception:
                    pass

    def add(self, kind, content):
        import time as _t
        if self.redact_fn is not None:
            try: content = self.redact_fn(str(content))
            except Exception: pass
        iso = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.path) as db:
            try:
                cur = db.execute(
                    "INSERT INTO memories(kind,content,created_at,sync_ts) VALUES(?,?,?,?)",
                    (kind, content, iso, _t.time()))
            except sqlite3.OperationalError:
                cur = db.execute(
                    "INSERT INTO memories(kind,content,created_at) VALUES(?,?,?)",
                    (kind, content, iso))
            return cur.lastrowid

    def delete(self, row_id):
        with sqlite3.connect(self.path) as db:
            cur = db.execute("DELETE FROM memories WHERE id=?", (int(row_id),))
            return cur.rowcount > 0

    def recent_full(self, limit=50):
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT id,kind,content,created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"id": r[0], "kind": r[1], "content": r[2], "created_at": r[3]} for r in rows]

    def kind_counts(self):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT kind, COUNT(*) FROM memories GROUP BY kind").fetchall()
        return {k: c for k, c in rows}

    def recent(self, limit=20):
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT kind,content,created_at FROM memories ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()

    def clear(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM memories")

    def count(self):
        # V15.1 integration: UI memory matrix shows the real SQLite total.
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
