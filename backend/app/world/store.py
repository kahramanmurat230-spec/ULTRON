"""WAVE 2 — World Store: persistent, temporal, versioned world state (§14-16).

Foundation `app/world/model.py::WorldModel` (sources→snapshot, stateless)
aynen korunur; WorldStore onun KALICI kardeşidir: entity sürümü, geçiş
tarihi, tazelik, delta ve toplulaştırılmış retansiyon sunar.

"Şu anda dünyada ne oluyor?" → WorldStore.upsert + snapshot
"Ne değişti?" → delta / world.change.* olayları
"Ne zaman ve nasıl değişti?" → history + aggregates + trend
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from pathlib import Path

from app.world.entities import (
    DEFAULT_STALENESS_S, is_stale, validate_entity_type, validate_relation,
)

RAW_HISTORY_PER_ENTITY = 2016      # ~7 gün @5dk ham nokta
AGG_BUCKET_S = 3600.0              # 1 saatlik toplulaştırma


class WorldStore:
    def __init__(self, db_path="data/world/world.db", now=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now or time.time
        self._db = None
        self._init()

    def _conn(self):
        if self._db is None:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA foreign_keys=ON")
        return self._db

    def _init(self):
        db = self._conn()
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS world_entities(
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                state TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.8,
                observed_at REAL NOT NULL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                source TEXT NOT NULL DEFAULT 'SYSTEM',
                version INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS world_history(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL REFERENCES world_entities(entity_id)
                    ON DELETE CASCADE,
                ts REAL NOT NULL, state TEXT NOT NULL,
                confidence REAL, source TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS world_aggregates(
                entity_id TEXT NOT NULL, bucket INTEGER NOT NULL,
                field TEXT NOT NULL, avg REAL, min REAL, max REAL,
                count INTEGER NOT NULL, PRIMARY KEY(entity_id,bucket,field))""")
            db.execute("""CREATE TABLE IF NOT EXISTS entity_links(
                subject_id TEXT NOT NULL, relation TEXT NOT NULL,
                object_id TEXT NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY(subject_id, relation, object_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_wh_entity_ts"
                       " ON world_history(entity_id,ts)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_we_observed"
                       " ON world_entities(observed_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_we_type"
                       " ON world_entities(entity_type)")

    # ------------------------------------------------------------ update
    def upsert(self, entity_id: str, entity_type: str, state: dict, *,
               source: str = "SYSTEM", confidence: float = 0.8,
               observed_at: float | None = None, valid_until: float | None = None,
               record_history: bool = True) -> dict:
        entity_type = validate_entity_type(entity_type)
        if not isinstance(state, dict):
            raise ValueError("state must be a dict")
        payload = json.dumps(state, ensure_ascii=False, default=str)
        if len(payload) > 200_000:
            raise ValueError("state too large (>200KB)")
        ts = float(observed_at if observed_at is not None else self.now())
        db = self._conn()
        row = db.execute("SELECT version,state,observed_at FROM world_entities"
                         " WHERE entity_id=?", (entity_id,)).fetchone()
        if row is not None and ts < float(row[2]):
            # STALE EVENT: varlığı geri sürme; yalnızca geçmişe tarih yaz
            with db:
                db.execute("INSERT INTO world_history(entity_id,ts,state,"
                           "confidence,source) VALUES(?,?,?,?,?)",
                           (entity_id, ts, payload, confidence, source))
            return {"entity_id": entity_id, "version": int(row[0]),
                    "changed": False, "stale_event": True}
        with db:
            if row is None:
                db.execute("""INSERT INTO world_entities(entity_id,entity_type,state,
                    confidence,observed_at,valid_from,valid_until,source,version,
                    created_at) VALUES(?,?,?,?,?,?,?,?,1,?)""",
                    (entity_id, entity_type, payload, min(1.0, max(0.0, confidence)),
                     ts, ts, valid_until, source, ts))
                version, changed = 1, True
            else:
                version = int(row[0]) + 1
                changed = (row[1] != payload)
                db.execute("""UPDATE world_entities SET entity_type=?,state=?,
                    confidence=?,observed_at=?,valid_until=?,source=?,version=?
                    WHERE entity_id=?""",
                    (entity_type, payload, min(1.0, max(0.0, confidence)), ts,
                     valid_until, source, version, entity_id))
            if record_history:
                db.execute("INSERT INTO world_history(entity_id,ts,state,confidence,"
                           "source) VALUES(?,?,?,?,?)",
                           (entity_id, ts, payload, confidence, source))
        if changed:
            self._compact_entity(entity_id)
        return {"entity_id": entity_id, "version": version, "changed": changed}

    def _touch_history_fk(self, *a):  # test kancası değil; yer tutucu
        return None

    # ------------------------------------------------------------ read
    def get(self, entity_id: str, now: float | None = None) -> dict | None:
        db = self._conn()
        row = db.execute("""SELECT entity_id,entity_type,state,confidence,observed_at,
            valid_from,valid_until,source,version FROM world_entities
            WHERE entity_id=?""", (entity_id,)).fetchone()
        if not row:
            return None
        rec = self._row_to_rec(row)
        rec["stale"] = is_stale(rec, self.now() if now is None else now)
        return rec

    @staticmethod
    def _row_to_rec(row) -> dict:
        return {"entity_id": row[0], "entity_type": row[1],
                "state": json.loads(row[2]), "confidence": row[3],
                "observed_at": row[4], "valid_from": row[5],
                "valid_until": row[6], "source": row[7], "version": row[8]}

    def snapshot(self, entity_type: str | None = None,
                 include_stale: bool = True) -> dict:
        """Tam dünya anlık görüntüsü — her kayıt stale bayrağıyla (§15)."""
        db = self._conn()
        if entity_type:
            rows = db.execute("""SELECT entity_id,entity_type,state,confidence,
                observed_at,valid_from,valid_until,source,version
                FROM world_entities WHERE entity_type=? ORDER BY entity_id""",
                (validate_entity_type(entity_type),)).fetchall()
        else:
            rows = db.execute("""SELECT entity_id,entity_type,state,confidence,
                observed_at,valid_from,valid_until,source,version
                FROM world_entities ORDER BY entity_id""").fetchall()
        now = self.now()
        ents = []
        for r in rows:
            rec = self._row_to_rec(r)
            rec["stale"] = is_stale(rec, now)
            ents.append(rec)
        if not include_stale:
            ents = [e for e in ents if not e["stale"]]
        return {"ts": now, "entities": ents, "count": len(ents)}

    def delta(self, since_ts: float, entity_type: str | None = None) -> dict:
        """since_ts sonrası değişen varlıklar (güncel hâlleriyle)."""
        db = self._conn()
        rows = db.execute("""SELECT DISTINCT entity_id FROM world_history
            WHERE ts>? ORDER BY ts""", (float(since_ts),)).fetchall()
        changed = []
        for (eid,) in rows:
            rec = self.get(eid)
            if rec and (entity_type is None or rec["entity_type"] == entity_type):
                changed.append(rec)
        return {"since": float(since_ts), "ts": self.now(), "changed": changed}

    # ------------------------------------------------------------ temporal
    def history(self, entity_id: str, window_s: float | None = None,
                limit: int = 500) -> list[dict]:
        db = self._conn()
        cutoff = (self.now() - window_s) if window_s else None
        if cutoff:
            rows = db.execute("""SELECT ts,state,confidence,source FROM world_history
                WHERE entity_id=? AND ts>=? ORDER BY ts LIMIT ?""",
                (entity_id, cutoff, limit)).fetchall()
        else:
            rows = db.execute("""SELECT ts,state,confidence,source FROM world_history
                WHERE entity_id=? ORDER BY ts DESC LIMIT ?""",
                (entity_id, limit)).fetchall()
            rows = list(reversed(rows))
        return [{"ts": r[0], "state": json.loads(r[1]), "confidence": r[2],
                 "source": r[3]} for r in rows]

    def trend(self, entity_id: str, field: str,
              window_s: float = 420.0) -> dict:
        """Zaman serisi + anlatı: 'CPU son 7 dakikada yükseldi' (§16)."""
        pts = []
        for h in self.history(entity_id, window_s=window_s):
            v = h["state"].get(field)
            if isinstance(v, (int, float)):
                pts.append((h["ts"], float(v)))
        if len(pts) < 2:
            return {"entity_id": entity_id, "field": field, "points": len(pts),
                    "direction": "unknown", "change": None, "narrative": None}
        third = max(1, len(pts) // 3)
        first = statistics.fmean(p[1] for p in pts[:third])
        last = statistics.fmean(p[1] for p in pts[-third:])
        change = last - first
        span_m = (pts[-1][0] - pts[0][0]) / 60.0
        if abs(change) < max(0.01 * abs(first) if first else 0.01, 1e-9):
            direction, narrative = "flat", f"{field} son {span_m:.0f} dakikada stabil"
        elif change > 0:
            direction = "rising"
            narrative = f"{field} son {span_m:.0f} dakikada yükseldi ({first:.0f}→{last:.0f})"
        else:
            direction = "falling"
            narrative = f"{field} son {span_m:.0f} dakikada düştü ({first:.0f}→{last:.0f})"
        return {"entity_id": entity_id, "field": field, "points": len(pts),
                "direction": direction, "change": round(change, 3),
                "first": round(first, 3), "last": round(last, 3),
                "narrative": narrative}

    def _compact_entity(self, entity_id: str):
        """Retention (§16): ham noktalar sınırlı; eskiler saatlik özetlere
        dönüşür — history sınırsız büyümez."""
        db = self._conn()
        rows = db.execute("SELECT ts,state FROM world_history WHERE entity_id=?"
                          " ORDER BY ts", (entity_id,)).fetchall()
        if len(rows) <= RAW_HISTORY_PER_ENTITY:
            return
        cutoff_ts = rows[-RAW_HISTORY_PER_ENTITY][0]
        old = [r for r in rows if r[0] < cutoff_ts]
        if not old:
            return
        fields: dict[tuple[int, str], list[float]] = {}
        for ts, state_json in old:
            bucket = int(ts // AGG_BUCKET_S)
            st = json.loads(state_json)
            for k, v in st.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    fields.setdefault((bucket, k), []).append(float(v))
        with db:
            for (bucket, k), vals in fields.items():
                db.execute("""INSERT INTO world_aggregates(entity_id,bucket,field,
                    avg,min,max,count) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(entity_id,bucket,field) DO UPDATE SET
                    avg=excluded.avg, min=excluded.min, max=excluded.max,
                    count=excluded.count""",
                    (entity_id, bucket, k, statistics.fmean(vals), min(vals),
                     max(vals), len(vals)))
            db.execute("DELETE FROM world_history WHERE entity_id=? AND ts<?",
                       (entity_id, cutoff_ts))

    def aggregates(self, entity_id: str, field: str,
                   bucket_gte: int | None = None) -> list[dict]:
        db = self._conn()
        if bucket_gte is None:
            rows = db.execute("""SELECT bucket,avg,min,max,count FROM world_aggregates
                WHERE entity_id=? AND field=? ORDER BY bucket""",
                (entity_id, field)).fetchall()
        else:
            rows = db.execute("""SELECT bucket,avg,min,max,count FROM world_aggregates
                WHERE entity_id=? AND field=? AND bucket>=? ORDER BY bucket""",
                (entity_id, field, bucket_gte)).fetchall()
        return [{"bucket": r[0], "avg": round(r[1], 3), "min": r[2],
                 "max": r[3], "count": r[4]} for r in rows]

    # ------------------------------------------------------------ KG prep (§23)
    def link(self, subject_id: str, relation: str, object_id: str) -> dict:
        relation = validate_relation(relation)
        if not subject_id or not object_id:
            raise ValueError("link endpoints required")
        with self._conn() as db:
            db.execute("INSERT OR REPLACE INTO entity_links(subject_id,relation,"
                       "object_id,ts) VALUES(?,?,?,?)",
                       (subject_id, relation, object_id, self.now()))
        return {"ok": True, "subject": subject_id, "relation": relation,
                "object": object_id}

    def links(self, subject_id: str | None = None) -> list[dict]:
        db = self._conn()
        if subject_id:
            rows = db.execute("SELECT subject_id,relation,object_id,ts FROM"
                              " entity_links WHERE subject_id=?", (subject_id,)).fetchall()
        else:
            rows = db.execute("SELECT subject_id,relation,object_id,ts FROM"
                              " entity_links").fetchall()
        return [{"subject": r[0], "relation": r[1], "object": r[2], "ts": r[3]}
                for r in rows]

    def integrity_check(self) -> dict:
        try:
            row = self._conn().execute("PRAGMA integrity_check").fetchone()
            fk = self._conn().execute("PRAGMA foreign_key_check").fetchall()
            return {"ok": row[0] == "ok" and not fk, "fk_violations": len(fk)}
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "error": str(exc)[:200]}

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
