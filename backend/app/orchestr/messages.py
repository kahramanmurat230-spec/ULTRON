"""WAVE 3 — Agent message bus: worker↔worker GÜVENLI iletişim.

- Şema (§AGENT MESSAGE BUS): message_id, sender, receiver, task_id,
  trace_id, type, payload, timestamp, schema_version, causation_id.
- Payload limiti (64KB) + redaction (secret mesaj kanalına düşmez).
- Kimlik doğrulama: gönderen kayıtlı bir worker olmalı ve worker_id
  kendisine ait olmalı (impersonation RED); receivers görev içinde olur
  (cross-task spoofing RED).
- Dedup: message_id UNIQUE → replay yutulur (deliveries kaydıyla).
- Loop koruması: causation zinciri derinliği + aynı (sender,receiver,
  type) tekrar pencere dedup'ı.
- Worker başka worker'ın bellek/durumuna erişemez — yalnız mesaj.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_TYPE_LEN = 80
TYPE_RE = __import__("re").compile(r"^[a-z0-9_.-]+$")
MAX_CAUSATION_DEPTH = 8
LOOP_WINDOW_S = 10.0
MAX_MESSAGES_PER_TASK = 5000          # sınırsız mesaj YASAK

from app.orchestr.safe_text import mask_trailing_secret


class MessageError(Exception):
    pass


class AgentMessageBus:
    def __init__(self, db_path="data/orchestr/messages.db", redact_fn=None,
                 now=None, registry=None):
        """registry: WorkerRegistry — gönderen doğrulaması için."""
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact_fn = redact_fn or (lambda t: t)
        self.now = now or time.time
        self.registry = registry
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._waiters: dict[str, list] = {}
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS messages(
                message_id TEXT PRIMARY KEY,
                sender TEXT NOT NULL, receiver TEXT NOT NULL,
                task_id TEXT NOT NULL, trace_id TEXT,
                type TEXT NOT NULL, payload TEXT NOT NULL,
                ts REAL NOT NULL, causation_id TEXT,
                schema_version INTEGER NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0)""")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_msg_recv"
                             " ON messages(receiver,ts)")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_msg_task"
                             " ON messages(task_id)")

    # ------------------------------------------------------------ send
    def send(self, sender: str, receiver: str, msg_type: str,
             payload: dict | None = None, *, task_id: str = "",
             trace_id: str | None = None, causation_id: str | None = None,
             message_id: str | None = None, timestamp: float | None = None
             ) -> dict:
        """Worker → worker mesaj. Dönüş: {ok, status, message_id?}."""
        msg_type = str(msg_type or "")
        if not msg_type or len(msg_type) > MAX_TYPE_LEN \
                or not TYPE_RE.match(msg_type):
            raise MessageError(f"invalid message type {msg_type!r}")
        raw = payload or {}
        size = len(json.dumps(raw, ensure_ascii=False, default=str)
                   .encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise MessageError(f"payload too large: {size} bytes")
        # kimlik doğrulama: impersonation + cross-task RED
        if self.registry is not None:
            sw = self.registry.get(sender)
            if sw is None:
                raise MessageError(f"unknown sender {sender!r} "
                                   "(impersonation rejected)")
            if task_id and sw.task_id != task_id:
                raise MessageError("cross-task message rejected")
            rw = self.registry.get(receiver)
            if rw is None:
                raise MessageError(f"unknown receiver {receiver!r}")
            if task_id and rw.task_id != task_id:
                raise MessageError("cross-task receiver rejected")
        # replay kontrolü ÖNCE: aynı message_id = bilinen tekrar (idempotent)
        if message_id and self._db.execute(
                "SELECT 1 FROM messages WHERE message_id=?",
                (message_id,)).fetchone():
            return {"ok": True, "status": "duplicate", "message_id": message_id}
        # loop koruması
        if causation_id:
            depth = self._causation_depth(causation_id)
            if depth > MAX_CAUSATION_DEPTH:
                return {"ok": False, "status": "loop_blocked",
                        "reason": "causation chain too deep"}
        dup = self._window_dup(sender, receiver, msg_type)
        if dup:
            return {"ok": False, "status": "loop_blocked",
                    "reason": "same (sender,receiver,type) within loop window"}
        count = self._db.execute("SELECT COUNT(*) FROM messages WHERE"
                                 " task_id=?", (task_id,)).fetchone()[0]
        if count >= MAX_MESSAGES_PER_TASK:
            return {"ok": False, "status": "quota_exceeded",
                    "reason": f"task message quota {MAX_MESSAGES_PER_TASK}"}
        mid = message_id or uuid.uuid4().hex[:16]
        ts = float(timestamp if timestamp is not None else self.now())
        safe = self._redact_deep(raw)
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO messages(message_id,sender,receiver,task_id,"
                    "trace_id,type,payload,ts,causation_id,schema_version)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (mid, sender, receiver, task_id, trace_id, msg_type,
                     json.dumps(safe, ensure_ascii=False, default=str), ts,
                     causation_id, SCHEMA_VERSION))
        except sqlite3.IntegrityError:
            return {"ok": True, "status": "duplicate",
                    "message_id": mid}      # replay: sessizce yut + kaydet
        self._notify(receiver)
        return {"ok": True, "status": "sent", "message_id": mid}

    def _redact_deep(self, value):
        if isinstance(value, str):
            return mask_trailing_secret(
                self.redact_fn(mask_trailing_secret(value)))[:2000]
        if isinstance(value, dict):
            return {k: ("***REDACTED***" if str(k).lower() in (
                "password", "token", "secret", "api_key", "şifre", "parola")
                and isinstance(v, str) and v else self._redact_deep(v))
                for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_deep(v) for v in value]
        return value

    def _causation_depth(self, causation_id: str) -> int:
        depth = 0
        seen = set()
        while causation_id and depth < MAX_CAUSATION_DEPTH + 2:
            if causation_id in seen:
                return 99
            seen.add(causation_id)
            row = self._db.execute("SELECT causation_id FROM messages WHERE"
                                   " message_id=?", (causation_id,)).fetchone()
            if not row:
                break
            depth += 1
            causation_id = row[0]
        return depth

    def _window_dup(self, sender: str, receiver: str, msg_type: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM messages WHERE sender=? AND receiver=? AND type=?"
            " AND ts>=? LIMIT 1",
            (sender, receiver, msg_type,
             self.now() - LOOP_WINDOW_S)).fetchone()
        return row is not None

    # ------------------------------------------------------------ receive
    def inbox(self, receiver: str, limit: int = 50,
              mark_delivered: bool = False) -> list[dict]:
        rows = self._db.execute(
            "SELECT message_id,sender,receiver,task_id,trace_id,type,payload,"
            "ts,causation_id,schema_version,delivered FROM messages WHERE"
            " receiver=? ORDER BY ts LIMIT ?", (receiver, limit)).fetchall()
        out = []
        for r in rows:
            out.append({"message_id": r[0], "sender": r[1], "receiver": r[2],
                        "task_id": r[3], "trace_id": r[4], "type": r[5],
                        "payload": json.loads(r[6]), "ts": r[7],
                        "causation_id": r[8], "schema_version": r[9],
                        "delivered": bool(r[10])})
        if mark_delivered and out:
            with self._db:
                self._db.execute("UPDATE messages SET delivered=1 WHERE"
                                 " receiver=?", (receiver,))
            for m in out:
                m["delivered"] = True
        return out

    def _notify(self, receiver: str):
        for cb in list(self._waiters.get(receiver, [])):
            try:
                cb()
            except Exception:
                pass

    def on_message(self, receiver: str, cb) -> None:
        self._waiters.setdefault(receiver, []).append(cb)

    async def wait_for(self, receiver: str, timeout: float = 30.0) -> dict | None:
        """Async bekleme: WAITING worker'lar için (scheduler ctx'e bağlanır)."""
        import asyncio
        loop = asyncio.get_event_loop()
        fut = loop.create_future()

        def _wake():
            if not fut.done():
                fut.set_result(None)
        self.on_message(receiver, _wake)
        deadline = self.now() + timeout
        while True:
            msgs = self.inbox(receiver, limit=1, mark_delivered=True)
            if msgs:
                return msgs[0]
            remain = deadline - self.now()
            if remain <= 0:
                return None
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=min(remain, 0.1))
            except asyncio.TimeoutError:
                pass
            if fut.done():
                fut = loop.create_future()
                self.on_message(receiver, _wake)

    # ------------------------------------------------------------ introspection
    def stats(self) -> dict:
        total = self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"messages": total, "quota_per_task": MAX_MESSAGES_PER_TASK}

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass
