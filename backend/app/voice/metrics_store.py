"""Latency metrics persistence — SQLite, percentiles, integrity check."""
import sqlite3
import time
from pathlib import Path


class MetricsStore:
    def __init__(self, path="data/metrics/voice_metrics.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS metrics(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                stt_ms REAL, llm_ms REAL, tts_ms REAL, vad_mode TEXT)""")

    def insert(self, stt_ms=None, llm_ms=None, tts_ms=None, vad_mode="energy"):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO metrics(ts,stt_ms,llm_ms,tts_ms,vad_mode) VALUES(?,?,?,?,?)",
                       (time.time(), stt_ms, llm_ms, tts_ms, vad_mode))

    @staticmethod
    def _p(sorted_vals, p):
        if not sorted_vals:
            return None
        k = (len(sorted_vals) - 1) * p
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f), 1)

    def percentiles(self, hours=24):
        since = time.time() - hours * 3600
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT stt_ms,llm_ms,tts_ms FROM metrics WHERE ts>=?", (since,)).fetchall()
        out = {"count": len(rows)}
        for i, key in enumerate(("stt_ms", "llm_ms", "tts_ms")):
            vals = sorted(r[i] for r in rows if r[i] is not None)
            out[key] = {p: self._p(vals, q) for p, q in (("p50", .5), ("p95", .95), ("p99", .99))}
        return out

    def integrity(self) -> bool:
        try:
            with sqlite3.connect(self.path) as db:
                return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except Exception:
            return False
