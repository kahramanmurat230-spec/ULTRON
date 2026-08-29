"""WAVE 3 — Immutable Artifact Manager.

Artifact şeması: artifact_id, owner_task, producer_worker, type, hash
(sha256), size, created_at, status. Immutable: güncelleme YOK; aynı id
ile ikinci yazım iyimser eşzamanlılık çakışması olarak RED. Erişim:
aynı görevün worker'ları OKUYABİLİR; değiştiremez; başka görev RED.
İçerik redaction'dan geçirilir (secret artifact'a gömülemez).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ARTIFACT_TYPES = ("report", "code", "data", "analysis", "evidence", "note",
                  "diff", "verification")
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class ArtifactError(Exception):
    pass


class ArtifactManager:
    def __init__(self, db_path="data/orchestr/artifacts.db", redact_fn=None,
                 now=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact_fn = redact_fn or (lambda t: t)
        self.now = now or time.time
        self._lock = threading.RLock()   # paylaşılan bağlantı: race önle
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS artifacts(
                artifact_id TEXT PRIMARY KEY,
                owner_task TEXT NOT NULL,
                producer_worker TEXT NOT NULL,
                type TEXT NOT NULL, hash TEXT NOT NULL,
                size INTEGER NOT NULL, created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'FINAL',
                content TEXT NOT NULL)""")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_art_task"
                             " ON artifacts(owner_task)")

    # ------------------------------------------------------------ create
    def create(self, owner_task: str, producer_worker: str, type_: str,
               content: str, artifact_id: str | None = None) -> dict:
        type_ = str(type_).lower()
        if type_ not in ARTIFACT_TYPES:
            raise ArtifactError(f"unknown artifact type {type_!r}")
        if not isinstance(content, str):
            raise ArtifactError("content must be text")
        safe = self.redact_fn(content)
        if len(safe.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise ArtifactError("artifact too large")
        aid = artifact_id or f"art-{uuid.uuid4().hex[:12]}"
        digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()
        with self._lock:
            try:
                with self._db:
                    self._db.execute(
                        "INSERT INTO artifacts(artifact_id,owner_task,"
                        "producer_worker,type,hash,size,created_at,status,"
                        "content) VALUES(?,?,?,?,?,?,?,?,?)",
                        (aid, owner_task, producer_worker, type_, digest,
                         len(safe.encode("utf-8")), self.now(), "FINAL", safe))
            except sqlite3.IntegrityError:
                # iyimser eşzamanlılık: id zaten var — lost update YOK
                raise ArtifactError(f"artifact {aid} already exists "
                                    "(optimistic concurrency conflict)")
        return {"artifact_id": aid, "owner_task": owner_task,
                "producer_worker": producer_worker, "type": type_,
                "hash": digest, "size": len(safe.encode("utf-8")),
                "created_at": self.now(), "status": "FINAL"}

    # ------------------------------------------------------------ read
    def get(self, artifact_id: str, *, requester_worker: str,
            requester_task: str) -> dict:
        """Erişim kontrolü: yalnız aynı görev. Başka görev RED."""
        row = self._db.execute(
            "SELECT artifact_id,owner_task,producer_worker,type,hash,size,"
            "created_at,status,content FROM artifacts WHERE artifact_id=?",
            (artifact_id,)).fetchone()
        if not row:
            raise ArtifactError("no such artifact")
        if row[1] != requester_task:
            raise ArtifactError(
                f"unauthorized artifact access: {artifact_id} belongs to "
                f"task {row[1]} (requester task {requester_task})")
        return {"artifact_id": row[0], "owner_task": row[1],
                "producer_worker": row[2], "type": row[3], "hash": row[4],
                "size": row[5], "created_at": row[6], "status": row[7],
                "content": row[8]}

    def verify_integrity(self, artifact_id: str) -> dict:
        row = self._db.execute("SELECT hash,content FROM artifacts WHERE"
                               " artifact_id=?", (artifact_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "no such artifact"}
        digest = hashlib.sha256(row[1].encode("utf-8")).hexdigest()
        return {"ok": digest == row[0], "hash": row[0]}

    def for_task(self, owner_task: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT artifact_id,owner_task,producer_worker,type,hash,size,"
            "created_at,status FROM artifacts WHERE owner_task=?"
            " ORDER BY created_at", (owner_task,)).fetchall()
        cols = ("artifact_id", "owner_task", "producer_worker", "type",
                "hash", "size", "created_at", "status")
        return [dict(zip(cols, r)) for r in rows]

    def stats(self) -> dict:
        n = self._db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        return {"artifacts": n}

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass
