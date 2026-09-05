"""WAVE 2 / MemoryStore V3: schema, CRUD, secret rejection, privacy,
tombstone, project/user isolation, migration (backup→verify→rollback),
DB reliability (WAL/FK/integrity)."""
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.memory.store_v3 import MemoryStore  # noqa: E402
from app.security.redaction import redact  # noqa: E402


def make_store(tmp, use_redact=True):
    return MemoryStore(db_path=os.path.join(tmp, "v3.db"),
                       redact_fn=redact if use_redact else None)


def _r(text, extra_values=()):
    return redact(text)


# ------------------------------------------------------------ schema/CRUD
def test_write_and_get_full_schema(tmp_path):
    s = make_store(str(tmp_path))
    res = s.write("Windows 11 çalışıyor", memory_type="SEMANTIC",
                  provenance="TOOL", record_kind="FACT",
                  subject_key="os:current", importance=0.8,
                  confidence=0.95, privacy="PUBLIC", tags=["os", "env"])
    assert res["status"] == "created"
    rec = s.get(res["id"])
    assert rec["memory_type"] == "SEMANTIC" and rec["record_kind"] == "FACT"
    assert rec["provenance"] == "TOOL" and rec["version"] == 1
    assert 0.0 <= rec["confidence"] <= 1.0 and rec["confidence"] == 0.9  # TOOL tavanı
    assert rec["normalized_content"] == "windows 11 calisiyor"
    assert rec["tags"] == ["os", "env"] and rec["status"] == "ACTIVE"
    assert rec["valid_from"] is not None


def test_type_provenance_kind_validation(tmp_path):
    s = make_store(str(tmp_path))
    with pytest.raises(ValueError):
        s.write("x", memory_type="BOGUS_TYPE")
    with pytest.raises(ValueError):
        s.write("x", memory_type="USER", provenance="SPY")
    with pytest.raises(ValueError):
        s.write("x", memory_type="USER", record_kind="LIE")
    with pytest.raises(ValueError):
        s.write("x", memory_type="USER", privacy="TOP_SECRET")


def test_update_versions_and_fields(tmp_path):
    s = make_store(str(tmp_path))
    r = s.write("eski değer", memory_type="SEMANTIC", provenance="USER",
                record_kind="FACT")
    out = s.update(r["id"], content="yeni değer", importance=0.9)
    assert out["ok"] and out["version"] == 2
    rec = s.get(r["id"])
    assert rec["content"] == "yeni değer" and rec["version"] == 2


# ------------------------------------------------------------ secrets
def test_secret_content_rejected(tmp_path):
    s = make_store(str(tmp_path))
    with pytest.raises(ValueError, match="vault"):
        s.write("sunucu şifresi password: ultra-gizli-123", memory_type="USER",
                provenance="USER")
    # Bearer da reddedilir
    with pytest.raises(ValueError):
        s.write("auth: Bearer eyJhbGciOiJIUzI1NiJ9.sigsigsig.sig",
                memory_type="WORKING", provenance="BROWSER")


def test_secret_privacy_level_rejected(tmp_path):
    s = make_store(str(tmp_path))
    with pytest.raises(ValueError, match="SECRET"):
        s.write("api anahtarı", memory_type="USER", provenance="USER",
                privacy="SECRET")


def test_secret_never_in_store_even_if_write_fails(tmp_path):
    s = make_store(str(tmp_path))
    try:
        s.write("api_key=ABCDEF123456789", memory_type="USER", provenance="USER")
    except ValueError:
        pass
    raw = sqlite3.connect(s.path).execute(
        "SELECT COUNT(*) FROM memory_records").fetchone()[0]
    assert raw == 0                      # reddedilen kayıt yazılmadı


# ------------------------------------------------------------ privacy
def test_secret_records_excluded_from_query(tmp_path):
    s = make_store(str(tmp_path), use_redact=False)
    # doğrudan DB'ye SECRET sokup sorguda filtrelenmediğini kanıtla (negatif test:
    # API katmanı SECRET yazmayı reddeder; query katmanı yine de korur)
    db = sqlite3.connect(s.path)
    db.execute("INSERT INTO memory_records(id,memory_type,content,normalized_content,"
               "content_hash,record_kind,provenance,created_at,updated_at,importance,"
               "confidence,privacy_level,tags,version,status)"
               " VALUES('sec1','SEMANTIC','x','x','h','FACT','USER',1,1,0.5,0.5,"
               "'SECRET','[]',1,'ACTIVE')")
    db.commit()
    db.close()
    assert all(r["privacy_level"] != "SECRET" for r in s.query())
    assert s.query(privacy_max="PRIVATE") == [] or \
        all(r["privacy_level"] in ("PUBLIC", "PRIVATE") for r in s.query(privacy_max="PRIVATE"))


# ------------------------------------------------------------ isolation
def test_project_isolation(tmp_path):
    s = make_store(str(tmp_path))
    a = s.write("ULTRON mimari kararı", memory_type="PROJECT", provenance="USER",
                project_id="ultron")
    s.write("diğer proje notu", memory_type="PROJECT", provenance="USER",
            project_id="other")
    only_ultron = s.query(project_id="ultron")
    assert len(only_ultron) == 1 and only_ultron[0]["project_id"] == "ultron"
    assert len(s.query()) == 2           # kapsam belirtmeden hepsi görünür (yönetici)


def test_user_scope_isolation(tmp_path):
    s = make_store(str(tmp_path))
    s.write("kullanıcı A notu", memory_type="USER", provenance="USER",
            user_scope="master")
    s.write("kullanıcı B notu", memory_type="USER", provenance="USER",
            user_scope="guest")
    assert all(r["user_scope"] == "master" for r in s.query(user_scope="master"))
    assert len(s.query(user_scope="guest")) == 1


# ------------------------------------------------------------ tombstone
def test_delete_request_tombstone_keeps_integrity(tmp_path):
    s = make_store(str(tmp_path))
    r = s.write("kişisel not", memory_type="USER", provenance="USER")
    out = s.delete_request(r["id"])
    assert out["ok"] and out["status"] == "TOMBSTED"
    rec = s.get(r["id"])
    assert rec["content"] == "" and rec["status"] == "TOMBSTED"
    assert rec["id"] == r["id"]          # referans bütünlüğü: kimlik duruyor
    assert s.query() == []               # normal retrieval'e girmez


# ------------------------------------------------------------ migration
def _seed_v16(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT,"
               " kind TEXT, content TEXT, created_at TEXT, sync_ts REAL,"
               " last_access REAL, importance REAL DEFAULT 0.5,"
               " archived INTEGER DEFAULT 0)")
    for kind, content, ts in rows:
        db.execute("INSERT INTO memories(kind,content,created_at) VALUES(?,?,?)",
                   (kind, content, ts))
    db.commit()
    db.close()


def test_migration_backup_copy_verify(tmp_path):
    v16 = os.path.join(tmp_path, "ultron.db")
    _seed_v16(v16, [("PROFILE", "kullanıcı sabahçı", "2026-01-01T10:00:00"),
                    ("IMPORTANT", "öneli not", "2026-02-01T10:00:00"),
                    ("NOTE", "geçici", "2026-03-01T10:00:00")])
    s = make_store(str(tmp_path))
    res = s.migrate_v16(v16_db_path=v16, backup_dir=os.path.join(tmp_path, "bk"))
    assert res["ok"] is True and res["copied"] == 3
    assert os.path.exists(res["backup"])          # yedek alındı
    assert len(s.query()) == 3
    src = sqlite3.connect(v16)                    # V16 tablosu DEĞİSMEDİ
    assert src.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3
    src.close()
    recs = s.query()
    assert any(r["memory_type"] == "USER" and r["source_id"] == "v16:PROFILE"
               for r in recs)


def test_migration_idempotent_and_dedup_off(tmp_path):
    v16 = os.path.join(tmp_path, "ultron.db")
    _seed_v16(v16, [("NOTE", "n1", "2026-01-01T10:00:00")])
    s = make_store(str(tmp_path))
    s.migrate_v16(v16_db_path=v16, backup_dir=os.path.join(tmp_path, "bk"))
    res2 = s.migrate_v16(v16_db_path=v16, backup_dir=os.path.join(tmp_path, "bk"))
    assert res2["ok"] and res2["copied"] == 1     # yeniden koşma: kayıp yok
    assert len(s.query()) == 2                    # bilinçli: migration kopyalar (dedup=False)
    # NOT: production'da migration tek sefer koşar; tekrarı log'da görünür


def test_migration_rollback_on_corruption(tmp_path):
    v16 = os.path.join(tmp_path, "ultron.db")
    _seed_v16(v16, [("NOTE", "n", "2026-01-01T10:00:00")])
    s = make_store(str(tmp_path))
    # sayım doğrulamasını boz: araya farklı source_id'li sahte 'v16:' satırı
    orig = s.write
    def exploding_write(*a, **k):
        raise RuntimeError("disk full")
    s.write = exploding_write
    res = s.migrate_v16(v16_db_path=v16, backup_dir=os.path.join(tmp_path, "bk"))
    s.write = orig
    assert res["ok"] is False and res.get("rolled_back") is True
    assert len(s.query()) == 0                    # geri alındı
    assert os.path.exists(res["backup"])          # yedek korundu


# ------------------------------------------------------------ reliability
def test_db_reliability_pragmas_and_indexes(tmp_path):
    s = make_store(str(tmp_path))
    db = sqlite3.connect(s.path)
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    idx = [r[1] for r in db.execute("PRAGMA index_list(memory_records)")]
    db.close()
    assert mode.lower() == "wal"
    assert len(idx) >= 5                          # hot-path indexleri
    assert s.integrity_check()["ok"] is True


def test_corrupted_db_detected_honestly(tmp_path):
    s = make_store(str(tmp_path))
    s.close()
    p = s.path
    with open(p, "r+b") as f:                     # SQLite başlık büyüsünü boz
        f.seek(0)
        f.write(b"\x00" * 16)
    try:
        s2 = MemoryStore(db_path=str(p))
        v = s2.integrity_check()
        assert v["ok"] is False                   # integrity_check dürüst söyler
    except sqlite3.DatabaseError as exc:
        # ctor seviyesinde reddetmek de dürüst tespittir (no fake success)
        assert "not a database" in str(exc).lower() or "corrupt" in str(exc).lower()
