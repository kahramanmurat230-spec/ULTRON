"""WAVE 2 — Durable Event Bus: ortak sinir sistemi (§17-§18, §22).

Garantiler:
- persistence  : her olay SQLite'a (WAL) önce yazılır, sonra dağıtılır
- ordering     : global monotonik seq (dağıtım seq sırasıyla)
- dedup        : event_id UNIQUE — tekrar publish = duplicate raporu
- idempotency  : deliveries tablosu (seq, sub) işlendi işareti; replay
                 zorlanmadıkça teslim edilmiş olayı tekrar vermez
- retry        : abone hatasında N deneme (varsayılan 3), sonra DLQ
- dead-letter  : events_dlq — sonsuz retry YOK, insan incelemesi
- replay       : from_seq..to_seq aralığı yeniden dağıtım (force opsiyonu)
- loop guard   : causation zinciri derinliği + (type,entity,correlation)
                 pencere dedup'ı — task→event→task→... sonsuz döngü RED

Wave 1 Task Runtime'ın WAL/dead-letter sistemi KOPYALANMAZ; bu, olaylar
için ortak abstraction'dır (kendi tabloları, kendi semantiği).
Secretler: payload'daki her string persist edilmeden ÖNCE maskelenir.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from app.events.schema import (
    MAX_PAYLOAD_BYTES, classify_event, new_event_id, validate_event,
)

MAX_CAUSATION_DEPTH = 6            # zincir derinliği sınırı (loop guard)
LOOP_WINDOW_S = 30.0               # aynı correlation+paket tekrarı penceresi
DEFAULT_RETRY = 3


SENSITIVE_KEYS = ("password", "passwd", "pwd", "token", "api_key", "apikey",
                  "secret", "client_secret", "şifre", "parola", "authorization")


def _redact_deep(value, redact_fn):
    """Payload içindeki tüm stringler maskelenir; hassas ANAHTARLARIN
    değerleri tamamen maskelenir (secret olay günlüğüne düşmez)."""
    if isinstance(value, str):
        return redact_fn(value)[:2000]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, str) and str(k).lower() in SENSITIVE_KEYS:
                out[k] = "***REDACTED***" if v else v
            else:
                out[k] = _redact_deep(v, redact_fn)
        return out
    if isinstance(value, list):
        return [_redact_deep(v, redact_fn) for v in value]
    return value


class DurableEventBus:
    def __init__(self, db_path="data/events/bus.db", redact_fn=None, now=None,
                 retry_attempts: int = DEFAULT_RETRY):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact_fn = redact_fn or (lambda t: t)
        self.now = now or time.time
        self.retry_attempts = max(1, int(retry_attempts))
        self._subs: dict[str, dict] = {}       # sub_id → {pattern, cb}
        self._lock = threading.RLock()
        self._db = None
        self._init()

    def _conn(self):
        if self._db is None:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=5000")
        return self._db

    def _init(self):
        with self._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                ts REAL NOT NULL,
                source TEXT NOT NULL,
                entity_id TEXT,
                payload TEXT NOT NULL,
                correlation_id TEXT,
                causation_id TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                schema_version INTEGER NOT NULL,
                class TEXT NOT NULL,
                created_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
                sub_id TEXT PRIMARY KEY, pattern TEXT NOT NULL,
                created_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries(
                seq INTEGER NOT NULL, sub_id TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, delivered_at REAL,
                PRIMARY KEY(seq, sub_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS events_dlq(
                dseq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL, sub_id TEXT,
                reason TEXT NOT NULL, detail TEXT,
                ts REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_ev_type ON events(event_type)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_ev_corr ON events(correlation_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_ev_caus ON events(causation_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_dlq_event ON events_dlq(event_id)")

    # ------------------------------------------------------------ publish
    def publish(self, topic: str, payload: dict | None = None, *,
                event_id: str | None = None, source: str = "SYSTEM",
                entity_id: str | None = None, timestamp: float | None = None,
                correlation_id: str | None = None, causation_id: str | None = None,
                confidence: float = 1.0, schema_version: int | 1 = 1,
                _loop_guard: bool = True) -> dict:
        """Olay yayınla → önce kalıcılaştır, sonra dağıt. Wave 1 Event Bus
        imzasıyla uyumlu: publish(topic, payload)."""
        # boyut doğrulaması HAM payload üzerindedir (redaction kısaltmadan önce)
        evt = validate_event({
            "event_id": event_id or new_event_id(),
            "event_type": topic,
            "timestamp": float(timestamp if timestamp is not None else self.now()),
            "source": source,
            "entity_id": entity_id,
            "payload": payload or {},
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "confidence": confidence,
            "schema_version": schema_version,
        })
        evt["payload"] = _redact_deep(evt["payload"], self.redact_fn)
        cls = classify_event(evt["event_type"])
        with self._lock:
            if _loop_guard:
                guard = self._loop_guard_check(evt)
                if guard["blocked"]:
                    self._dlq(evt["event_id"], None, "loop_guard", guard["reason"])
                    return {"ok": False, "status": "loop_guard_blocked",
                            "reason": guard["reason"], "event_id": evt["event_id"]}
            db = self._conn()
            try:
                with db:
                    cur = db.execute(
                        "INSERT INTO events(event_id,event_type,ts,source,entity_id,"
                        "payload,correlation_id,causation_id,confidence,"
                        "schema_version,class,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (evt["event_id"], evt["event_type"], evt["timestamp"],
                         evt["source"], evt["entity_id"],
                         json.dumps(evt["payload"], ensure_ascii=False, default=str),
                         evt["correlation_id"], evt["causation_id"],
                         evt["confidence"], evt["schema_version"], cls, self.now()))
                    seq = cur.lastrowid
            except sqlite3.IntegrityError:
                return {"ok": True, "status": "duplicate", "seq": None,
                        "event_id": evt["event_id"]}     # dedup: sessizce yut
            results = self._dispatch_seq(seq, evt)
            return {"ok": True, "status": "published", "seq": seq,
                    "event_id": evt["event_id"], "class": cls,
                    "delivered": sum(1 for r in results if r["status"] == "delivered"),
                    "results": results}

    def _loop_guard_check(self, evt: dict) -> dict:
        """Causation zinciri derinliği + tekrar penceresi (§22)."""
        db = self._conn()
        depth = 0
        causation = evt["causation_id"]
        seen = set()
        while causation and depth < MAX_CAUSATION_DEPTH + 2:
            if causation in seen:
                return {"blocked": True, "reason": "causation cycle detected"}
            seen.add(causation)
            row = db.execute("SELECT event_id,causation_id FROM events WHERE"
                             " event_id=?", (causation,)).fetchone()
            if not row:
                break
            depth += 1
            causation = row[1]
        if depth > MAX_CAUSATION_DEPTH:
            return {"blocked": True,
                    "reason": f"causation chain deeper than {MAX_CAUSATION_DEPTH}"}
        # aynı correlation + aynı (type,entity) kısa pencerede mi?
        if evt["correlation_id"]:
            dup = db.execute(
                "SELECT seq FROM events WHERE correlation_id=? AND event_type=?"
                " AND COALESCE(entity_id,'')=COALESCE(?,'') AND created_at>=?"
                " LIMIT 1",
                (evt["correlation_id"], evt["event_type"], evt["entity_id"],
                 self.now() - LOOP_WINDOW_S)).fetchone()
            if dup:
                return {"blocked": True,
                        "reason": "same (type,entity) republished within loop window"
                                  " with same correlation_id"}
        return {"blocked": False}

    # ------------------------------------------------------------ dispatch
    def subscribe(self, pattern: str, cb) -> str:
        """fnmatch desenli abonelik; cb(topic, payload). idempotent kayıt."""
        sub_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._subs[sub_id] = {"pattern": str(pattern), "cb": cb}
            with self._conn() as db:
                db.execute("INSERT OR REPLACE INTO subscriptions(sub_id,pattern,"
                           "created_at) VALUES(?,?,?)", (sub_id, str(pattern), self.now()))
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        with self._lock:
            return self._subs.pop(sub_id, None) is not None

    def _matching_subs(self, event_type: str) -> list[tuple[str, dict]]:
        return [(sid, s) for sid, s in self._subs.items()
                if fnmatch.fnmatch(event_type, s["pattern"])]

    def _dispatch_seq(self, seq: int, evt: dict, force: bool = False) -> list[dict]:
        results = []
        db = self._conn()
        for sub_id, sub in self._matching_subs(evt["event_type"]):
            done = db.execute("SELECT status FROM deliveries WHERE seq=? AND"
                              " sub_id=?", (seq, sub_id)).fetchone()
            if done and done[0] == "delivered" and not force:
                results.append({"sub_id": sub_id, "status": "already_delivered"})
                continue                        # idempotent at-kapı (force hariç)
            error, attempts = None, 0
            for attempt in range(1, self.retry_attempts + 1):
                attempts = attempt
                try:
                    out = sub["cb"](evt["event_type"], evt["payload"])
                    if hasattr(out, "__await__"):
                        # async abone: zamanlayıcıya bırak (sunucu döngüsünde)
                        try:
                            loop = __import__("asyncio").get_event_loop()
                            loop.create_task(out)
                        except RuntimeError:
                            __import__("asyncio").run(out)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)[:200]
            status = "delivered" if error is None else "dlq"
            with db:
                db.execute("INSERT OR REPLACE INTO deliveries(seq,sub_id,status,"
                           "attempts,last_error,delivered_at) VALUES(?,?,?,?,?,?)",
                           (seq, sub_id, status, attempts, error,
                            self.now() if status == "delivered" else None))
            if status == "dlq":
                self._dlq(evt["event_id"], sub_id,
                          f"consumer failed after {attempts} attempts", error)
            results.append({"sub_id": sub_id, "status": status,
                            "attempts": attempts})
        return results

    def _dlq(self, event_id: str, sub_id: str | None, reason: str,
             detail: str | None):
        with self._conn() as db:
            db.execute("INSERT INTO events_dlq(event_id,sub_id,reason,detail,ts)"
                       " VALUES(?,?,?,?,?)",
                       (event_id, sub_id, reason[:200],
                        (detail or "")[:400], self.now()))

    # ------------------------------------------------------------ replay
    def replay(self, from_seq: int = 1, to_seq: int | None = None,
               sub_id: str | None = None, force: bool = False) -> dict:
        """Kalıcı olayları yeniden dağıt. force=False → yalnız kaçırdıklar."""
        db = self._conn()
        hi = to_seq
        if hi is None:
            row = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()
            hi = row[0]
        rows = db.execute("SELECT seq,event_id,event_type,ts,source,entity_id,"
                          "payload,correlation_id,causation_id,confidence,"
                          "schema_version FROM events WHERE seq>=? AND seq<=?"
                          " ORDER BY seq", (int(from_seq), int(hi))).fetchall()
        redelivered, skipped = 0, 0
        for (seq, eid, etype, ts, source, entity, payload, corr, caus, conf,
             sver) in rows:
            evt = {"event_id": eid, "event_type": etype, "timestamp": ts,
                   "source": source, "entity_id": entity,
                   "payload": json.loads(payload), "correlation_id": corr,
                   "causation_id": caus, "confidence": conf,
                   "schema_version": sver}
            targets = ([sub_id] if sub_id and sub_id in self._subs
                       else [s for s in self._subs])
            for sid in targets:
                done = db.execute("SELECT status FROM deliveries WHERE seq=? AND"
                                  " sub_id=?", (seq, sid)).fetchone()
                if done and done[0] == "delivered" and not force:
                    skipped += 1
                    continue
                self._dispatch_seq(seq, evt, force=force)
                redelivered += 1
        return {"ok": True, "range": [int(from_seq), int(hi)], "events": len(rows),
                "redelivered": redelivered, "skipped_delivered": skipped}

    # ------------------------------------------------------------ introspection
    def event(self, seq: int) -> dict | None:
        row = self._conn().execute("SELECT seq,event_id,event_type,ts,source,"
            "entity_id,payload,correlation_id,causation_id,confidence,"
            "schema_version,class FROM events WHERE seq=?", (seq,)).fetchone()
        if not row:
            return None
        return {"seq": row[0], "event_id": row[1], "event_type": row[2], "ts": row[3],
                "source": row[4], "entity_id": row[5],
                "payload": json.loads(row[6]), "correlation_id": row[7],
                "causation_id": row[8], "confidence": row[9],
                "schema_version": row[10], "class": row[11]}

    def events(self, event_type: str | None = None, limit: int = 50) -> list[dict]:
        db = self._conn()
        if event_type:
            rows = db.execute("SELECT seq FROM events WHERE event_type=? OR ?='' "
                              "ORDER BY seq DESC LIMIT ?",
                              (event_type, event_type, limit)).fetchall()
        else:
            rows = db.execute("SELECT seq FROM events ORDER BY seq DESC LIMIT ?",
                              (limit,)).fetchall()
        return [self.event(r[0]) for r in reversed(rows)]

    def dlq(self, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT dseq,event_id,sub_id,reason,detail,ts FROM events_dlq"
            " ORDER BY dseq DESC LIMIT ?", (limit,)).fetchall()
        return [{"dseq": r[0], "event_id": r[1], "sub_id": r[2], "reason": r[3],
                 "detail": r[4], "ts": r[5]} for r in rows]

    def stats(self) -> dict:
        db = self._conn()
        total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        dlq_n = db.execute("SELECT COUNT(*) FROM events_dlq").fetchone()[0]
        delivered = db.execute("SELECT COUNT(*) FROM deliveries WHERE"
                               " status='delivered'").fetchone()[0]
        return {"events": total, "delivered": delivered,
                "subscriptions": len(self._subs), "dlq": dlq_n}

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
