"""WAVE 2 — Memory Store V3: persistent, typed, provenance-aware memory.

Additive katman: Foundation V16 (`sqlite_memory.Memory`) aynen çalışır; V3
kendi veritabanında (varsayılan data/memory/v3.db) tam şemalı kayıt tutar.

Güvenilirlik (§26): WAL + busy_timeout + transaction boundaries + indexes +
integrity_check. Migration (§25, §34): backup → copy → verify → rollback.
Silme (§24): hard delete yerine TOMBSTONE (referans bütünlüğü korunur).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from app.memory.layers import (
    MEMORY_TYPES, PRIVACY_LEVELS, RECORD_STATUS, SOURCE_CONFIDENCE,
    clamp_confidence, validate_kind, validate_memory_type,
    validate_privacy, validate_provenance,
)

SCHEMA_VERSION = 3


def normalize(text: str) -> str:
    """Küçük harf + boşluk düzleştir + noktalama sil (TR duyarlı)."""
    t = str(text or "").lower()
    tr = str.maketrans("çğıöşü", "cgiosu")
    t = t.translate(tr)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def content_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class MemoryStore:
    """Tip-doğrulanmış, göç-bilinçli kalıcı bellek deposu."""

    def __init__(self, db_path="data/memory/v3.db", redact_fn=None, now=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact_fn = redact_fn
        self.now = now or time.time
        self._db = None
        self._init_db()

    # ------------------------------------------------------------ db core
    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA foreign_keys=ON")
        return self._db

    def _init_db(self):
        db = self._conn()
        with db:  # transaction boundary
            db.execute("""CREATE TABLE IF NOT EXISTS memory_records(
                id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                subject_key TEXT,
                record_kind TEXT NOT NULL DEFAULT 'OBSERVATION',
                provenance TEXT NOT NULL DEFAULT 'SYSTEM',
                source_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                valid_from REAL,
                valid_until REAL,
                importance REAL NOT NULL DEFAULT 0.5,
                confidence REAL NOT NULL DEFAULT 0.5,
                privacy_level TEXT NOT NULL DEFAULT 'PRIVATE',
                project_id TEXT,
                user_scope TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                embedding_ref TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                access_count INTEGER NOT NULL DEFAULT 0,
                last_access REAL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_tags(
                memory_id TEXT NOT NULL REFERENCES memory_records(id)
                    ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY(memory_id, tag))""")
            db.execute("""CREATE TABLE IF NOT EXISTS migration_log(
                ts REAL NOT NULL, source_table TEXT, copied INTEGER,
                backup_path TEXT, status TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mr_type_status"
                       " ON memory_records(memory_type,status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mr_hash"
                       " ON memory_records(content_hash)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mr_subject"
                       " ON memory_records(subject_key,status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mr_project"
                       " ON memory_records(project_id,status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mr_privacy"
                       " ON memory_records(privacy_level,status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_mr_created"
                       " ON memory_records(created_at)")
            db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,"
                       " v TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('schema_version',?)",
                       (str(SCHEMA_VERSION),))

    def integrity_check(self) -> dict:
        try:
            row = self._conn().execute("PRAGMA integrity_check").fetchone()
            fk = self._conn().execute("PRAGMA foreign_key_check").fetchall()
            return {"ok": row[0] == "ok" and not fk,
                    "integrity": row[0], "fk_violations": len(fk)}
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "error": str(exc)[:200]}

    # ------------------------------------------------------------ write
    def _check_secret(self, content: str) -> None:
        """Secret düz metin memory'ye YAZILAMAZ (§2, §29)."""
        if self.redact_fn is not None:
            if self.redact_fn(content) != content:
                raise ValueError("content contains credential-like value "
                                 "(redaction changed it) — store in vault, not memory")

    def write(self, content: str, *, memory_type: str, provenance: str = "SYSTEM",
              record_kind: str = "OBSERVATION", source_id: str | None = None,
              subject_key: str | None = None, importance: float = 0.5,
              confidence=None, privacy: str = "PRIVATE", project_id: str | None = None,
              user_scope: str | None = None, tags: list | None = None,
              valid_from: float | None = None, valid_until: float | None = None,
              embedding_ref: str | None = None, created_at: float | None = None,
              dedup: bool = True) -> dict:
        """Yeni kayıt. Dönüş: {ok, id, status, duplicate_of?}."""
        memory_type = validate_memory_type(memory_type)
        provenance = validate_provenance(provenance)
        record_kind = validate_kind(record_kind)
        privacy = validate_privacy(privacy)
        if privacy == "SECRET":
            raise ValueError("privacy=SECRET content is never stored in memory — "
                             "use the credential vault")
        content = str(content)
        if not content.strip() or len(content) > 20000:
            raise ValueError("content empty or >20000 chars")
        self._check_secret(content)
        conf = clamp_confidence(confidence, provenance)
        imp = round(max(0.0, min(1.0, float(importance))), 3)
        norm = normalize(content)
        chash = content_hash(norm)
        now = float(created_at if created_at is not None else self.now())
        if dedup:
            dup = self._find_exact_duplicate(chash, memory_type, project_id)
            if dup:
                self._touch(dup["id"])
                return {"ok": True, "id": dup["id"], "status": "duplicate",
                        "duplicate_of": dup["id"]}
        rid = uuid.uuid4().hex[:16]
        tags_j = json.dumps([str(t)[:60] for t in (tags or [])], ensure_ascii=False)
        db = self._conn()
        with db:
            db.execute(
                "INSERT INTO memory_records(id,memory_type,content,normalized_content,"
                "content_hash,subject_key,record_kind,provenance,source_id,created_at,"
                "updated_at,valid_from,valid_until,importance,confidence,privacy_level,"
                "project_id,user_scope,tags,embedding_ref,version,status)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, memory_type, content, norm, chash,
                 str(subject_key)[:160] if subject_key else None,
                 record_kind, provenance,
                 str(source_id)[:160] if source_id else None,
                 now, now,
                 float(valid_from) if valid_from is not None else now,
                 float(valid_until) if valid_until is not None else None,
                 imp, conf, privacy,
                 str(project_id)[:80] if project_id else None,
                 str(user_scope)[:80] if user_scope else None,
                 tags_j, embedding_ref, 1, "ACTIVE"))
            for t in set(str(x)[:60].lower() for x in (tags or [])):
                db.execute("INSERT OR IGNORE INTO memory_tags(memory_id,tag)"
                           " VALUES(?,?)", (rid, t))
        return {"ok": True, "id": rid, "status": "created"}

    def _find_exact_duplicate(self, chash: str, memory_type: str,
                              project_id: str | None) -> dict | None:
        db = self._conn()
        row = db.execute(
            "SELECT id FROM memory_records WHERE content_hash=? AND memory_type=?"
            " AND status='ACTIVE' AND COALESCE(project_id,'')=COALESCE(?,'')"
            " ORDER BY created_at DESC LIMIT 1",
            (chash, memory_type, project_id)).fetchone()
        return {"id": row[0]} if row else None

    # ------------------------------------------------------------ read
    _COLS = ("id,memory_type,content,normalized_content,content_hash,subject_key,"
             "record_kind,provenance,source_id,created_at,updated_at,valid_from,"
             "valid_until,importance,confidence,privacy_level,project_id,user_scope,"
             "tags,embedding_ref,version,status,access_count,last_access")

    def _row_to_rec(self, row) -> dict:
        cols = self._COLS.split(",")
        rec = dict(zip([c.strip() for c in cols], row))
        try:
            rec["tags"] = json.loads(rec["tags"] or "[]")
        except (ValueError, TypeError):
            rec["tags"] = []          # bozuk satır: tamlık yerine dürüst tolerans
            rec["corrupt_tags"] = True
        return rec

    def get(self, rid: str, touch: bool = False) -> dict | None:
        row = self._conn().execute(
            f"SELECT {self._COLS} FROM memory_records WHERE id=?",
            (rid,)).fetchone()
        if not row:
            return None
        if touch:
            self._touch(rid)
        return self._row_to_rec(row)

    def query(self, *, memory_type: str | None = None, project_id: str | None = None,
              user_scope: str | None = None, record_kind: str | None = None,
              status: str = "ACTIVE", tags: list | None = None,
              text: str | None = None, privacy_max: str = "SENSITIVE",
              limit: int = 50, since: float | None = None,
              subject_key: str | None = None, order: str = "created_desc") -> list[dict]:
        """Filtreli okuma. SECRET kayıtlar ASLA dönmez (privacy_max tavanı)."""
        allowed = PRIVACY_LEVELS[:max(1, PRIVACY_LEVELS.index(privacy_max) + 1)] \
            if privacy_max in PRIVACY_LEVELS else ("PUBLIC", "PRIVATE", "SENSITIVE")
        sql = [f"SELECT {self._COLS} FROM memory_records WHERE status=?"]
        args: list = [status if status in RECORD_STATUS else "ACTIVE"]

        def cond(fragment, *vals):
            sql.append("AND " + fragment)
            args.extend(vals)

        cond(f"privacy_level IN ({','.join('?' * len(allowed))})", *allowed)
        if memory_type:
            cond("memory_type=?", validate_memory_type(memory_type))
        if record_kind:
            cond("record_kind=?", validate_kind(record_kind))
        if project_id is not None:
            cond("project_id=?", project_id)
        if user_scope is not None:
            cond("user_scope=?", user_scope)
        if subject_key is not None:
            cond("subject_key=?", subject_key)
        if since is not None:
            cond("created_at>=?", float(since))
        if text:
            cond("normalized_content LIKE ?", f"%{normalize(text)}%")
        if tags:
            for t in tags:
                cond("id IN (SELECT memory_id FROM memory_tags WHERE tag=?)",
                     str(t).lower())
        order_sql = {"created_desc": "created_at DESC",
                     "importance_desc": "importance DESC, created_at DESC",
                     "confidence_desc": "confidence DESC, created_at DESC"}.get(order,
                                                                                "created_at DESC")
        sql.append(f"ORDER BY {order_sql} LIMIT {max(1, min(int(limit), 500))}")
        rows = self._conn().execute(" ".join(sql), args).fetchall()
        return [self._row_to_rec(r) for r in rows]

    def _touch(self, rid: str):
        with self._conn():
            self._conn().execute(
                "UPDATE memory_records SET access_count=access_count+1,"
                " last_access=? WHERE id=?", (self.now(), rid))

    # ------------------------------------------------------------ update
    def update(self, rid: str, *, content: str | None = None,
               importance: float | None = None, confidence: float | None = None,
               status: str | None = None, valid_until: float | None = None,
               tags: list | None = None) -> dict:
        rec = self.get(rid)
        if not rec:
            return {"ok": False, "error": "no such record"}
        sets, args = ["updated_at=?", "version=version+1"], [self.now()]
        if content is not None:
            content = str(content)
            if not content.strip() or len(content) > 20000:
                raise ValueError("content empty or >20000 chars")
            self._check_secret(content)
            norm = normalize(content)
            sets += ["content=?", "normalized_content=?", "content_hash=?"]
            args += [content, norm, content_hash(norm)]
        if importance is not None:
            sets.append("importance=?")
            args.append(round(max(0.0, min(1.0, float(importance))), 3))
        if confidence is not None:
            sets.append("confidence=?")
            args.append(clamp_confidence(confidence, rec["provenance"]))
        if status is not None:
            if status not in RECORD_STATUS:
                raise ValueError(f"unknown status {status!r}")
            sets.append("status=?")
            args.append(status)
        if valid_until is not None:
            sets.append("valid_until=?")
            args.append(float(valid_until))
        if tags is not None:
            sets.append("tags=?")
            args.append(json.dumps([str(t)[:60] for t in tags], ensure_ascii=False))
            with self._conn():
                self._conn().execute("DELETE FROM memory_tags WHERE memory_id=?", (rid,))
                for t in set(str(x)[:60].lower() for x in tags):
                    self._conn().execute("INSERT OR IGNORE INTO memory_tags"
                                         "(memory_id,tag) VALUES(?,?)", (rid, t))
        args.append(rid)
        with self._conn():
            self._conn().execute(
                f"UPDATE memory_records SET {', '.join(sets)} WHERE id=?", args)
        return {"ok": True, "version": rec["version"] + 1}

    # ------------------------------------------------------------ delete
    def delete_request(self, rid: str) -> dict:
        """Kullanıcı silme talebi (§24): TOMBSTONE — içerik temizlenir,
        kimlik/provenans kalır (referans bütünlüğü)."""
        rec = self.get(rid)
        if not rec:
            return {"ok": False, "error": "no such record"}
        with self._conn():
            self._conn().execute(
                "UPDATE memory_records SET content='', normalized_content='',"
                " content_hash='', status='TOMBSTED', embedding_ref=NULL,"
                " updated_at=?, version=version+1 WHERE id=?",
                (self.now(), rid))
            self._conn().execute("DELETE FROM memory_tags WHERE memory_id=?", (rid,))
        return {"ok": True, "id": rid, "status": "TOMBSTED"}

    # ------------------------------------------------------------ migration
    def migrate_v16(self, v16_db_path="data/memory/ultron.db",
                    backup_dir="data/backups") -> dict:
        """V16 memories → V3 memory_records (backup → copy → verify → rollback).
        V16 tablosu DEĞİŞMEZ; kopyalama additive'tir."""
        src = Path(v16_db_path)
        if not src.exists():
            return {"ok": True, "copied": 0, "skipped": "no v16 db"}
        bdir = Path(backup_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        backup = bdir / f"memory_v16_premigrate_{int(self.now())}.db"
        try:
            shutil.copy2(src, backup)
            sdb = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            rows = sdb.execute("SELECT kind,content,created_at,importance,"
                               " last_access FROM memories").fetchall()
            sdb.close()
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "error": f"v16 read failed: {exc}"}
        copied = 0
        pre_count = 0
        try:
            pre_count = self._conn().execute(
                "SELECT COUNT(*) FROM memory_records WHERE source_id LIKE 'v16:%'"
            ).fetchone()[0]
            for kind, content, created_at, importance, last_access in rows:
                created = created_at
                if isinstance(created_at, str):
                    try:
                        from datetime import datetime
                        created = datetime.fromisoformat(created_at).timestamp()
                    except ValueError:
                        created = self.now()
                mtype = self._map_v16_kind(kind)
                self.write(content, memory_type=mtype, provenance="SYSTEM",
                           record_kind="OBSERVATION", source_id=f"v16:{kind}",
                           importance=float(importance) if importance else 0.4,
                           confidence=0.6, privacy="PRIVATE",
                           created_at=float(created or self.now()), dedup=False)
                copied += 1
            # verify: sayım tutarlı mı
            got = self._conn().execute(
                "SELECT COUNT(*) FROM memory_records WHERE source_id LIKE 'v16:%'"
            ).fetchone()[0]
            if got != pre_count + copied:
                raise RuntimeError(f"verify failed: copied {copied}, found {got}")
            with self._conn():
                self._conn().execute(
                    "INSERT INTO migration_log(ts,source_table,copied,backup_path,status)"
                    " VALUES(?,?,?,?,?)",
                    (self.now(), "memories", copied, str(backup), "ok"))
            return {"ok": True, "copied": copied, "backup": str(backup)}
        except Exception as exc:  # noqa: BLE001
            # rollback: bu kaynaklı satırları geri al, yedek korunur
            with self._conn():
                self._conn().execute("DELETE FROM memory_records WHERE source_id"
                                     " LIKE 'v16:%'")
                self._conn().execute(
                    "INSERT INTO migration_log(ts,source_table,copied,backup_path,status)"
                    " VALUES(?,?,?,?,?)",
                    (self.now(), "memories", copied, str(backup), f"rolled-back: {exc}"))
            return {"ok": False, "error": str(exc)[:300], "backup": str(backup),
                    "rolled_back": True}

    @staticmethod
    def _map_v16_kind(kind: str) -> str:
        k = str(kind or "").upper()
        if k in ("PROFILE",):
            return "USER"
        if k in ("IMPORTANT",):
            return "LONG_TERM"
        if k in ("AUTO_LEARNED",):
            return "SEMANTIC"
        if k in ("NOTE", "OUTPUT"):
            return "SHORT_TERM"
        return "EPISODIC"

    # ------------------------------------------------------------ stats
    def stats(self) -> dict:
        db = self._conn()
        total = db.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]
        by_type = dict(db.execute("SELECT memory_type,COUNT(*) FROM memory_records"
                                  " GROUP BY memory_type").fetchall())
        by_status = dict(db.execute("SELECT status,COUNT(*) FROM memory_records"
                                    " GROUP BY status").fetchall())
        return {"total": total, "by_type": by_type, "by_status": by_status,
                "schema_version": SCHEMA_VERSION}

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
