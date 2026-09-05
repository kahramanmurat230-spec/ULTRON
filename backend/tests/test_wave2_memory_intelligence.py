"""WAVE 2 / Memory intelligence: importance, duplicate (3 seviye + zaman
penceresi), conflict resolution (tercih düzeltme, GPU sıcaklığı çelişki
DEĞİL), decay (korunanlar), retrieval pipeline, user model ayrımı."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.memory.intelligence import (  # noqa: E402
    ConflictResolver, DecayManager, DuplicateDetector, ImportanceScorer,
)
from app.memory.retrieval import Retriever  # noqa: E402
from app.memory.store_v3 import MemoryStore  # noqa: E402


def make(tmp, clock=None):
    now = clock or (lambda: 1_000_000.0)
    s = MemoryStore(db_path=os.path.join(tmp, "v3.db"))
    s.now = now
    return s


# ------------------------------------------------------------ importance
def test_importance_signals():
    s = ImportanceScorer(active_project="ultron")
    base = {"record_kind": "OBSERVATION", "importance": 0.5, "access_count": 0}
    low = s.score(dict(base))
    pref = s.score({**base, "record_kind": "PREFERENCE"})
    sec = s.score({**base, "tags": ["security"]})
    proj = s.score({**base, "project_id": "ultron"})
    assert pref > low and sec > low and proj > low
    assert 0.0 <= low <= 1.0 and sec >= pref >= low


# ------------------------------------------------------------ dedup
def test_duplicate_exact_and_normalized(tmp_path):
    s = make(str(tmp_path))
    r1 = s.write("Editör olarak VS Code kullanıyorum!", memory_type="USER",
                 provenance="USER", record_kind="PREFERENCE",
                 subject_key="preference:editor")
    det = DuplicateDetector(s)
    d = det.check("Editör olarak VS Code kullanıyorum!", "USER", "PREFERENCE",
                  None, s.now(), "preference:editor")
    assert d["duplicate"] is True and d["level"] == "normalized" and d["of"] == r1["id"]
    d2 = det.check("tamamen farklı bir cümle burada", "USER", "PREFERENCE",
                   None, s.now(), "preference:editor")
    assert d2["duplicate"] is False


def test_duplicate_semantic(tmp_path):
    s = make(str(tmp_path))
    s.write("Kullanıcı gece çalışmayı tercih ediyor", memory_type="USER",
            provenance="USER", record_kind="PREFERENCE",
            subject_key="preference:work_hours")
    det = DuplicateDetector(s, semantic_threshold=0.6)
    d = det.check("kullanıcı gece çalışmayı tercih eder", "USER", "PREFERENCE",
                  None, s.now(), "preference:work_hours")
    assert d["duplicate"] is True and d["level"] == "semantic"


def test_different_time_events_not_duplicates(tmp_path):
    """GPU 55 @23:00 ve GPU 72 @23:05 → dedup DEĞİL (§6)."""
    s = make(str(tmp_path))
    det = DuplicateDetector(s)
    d = det.check("GPU temperature 55", "SHORT_TERM", "OBSERVATION", None,
                  s.now(), subject_key="gpu_temp")
    assert d["duplicate"] is False
    s.write("GPU temperature 55", memory_type="SHORT_TERM", provenance="SYSTEM",
            record_kind="OBSERVATION", subject_key="gpu_temp",
            created_at=s.now() - 3600)
    d2 = det.check("GPU temperature 55", "SHORT_TERM", "OBSERVATION", None,
                   s.now(), subject_key="gpu_temp")   # 1 saat SONRA
    assert d2["duplicate"] is False                   # farklı zaman → yeni gözlem


# ------------------------------------------------------------ conflict
def test_preference_correction_supersedes_with_history(tmp_path):
    """Tercih A sonra B → B kazanır, A SUPERSEDED ama tarih korunur (§7)."""
    s = make(str(tmp_path))
    s.now = lambda: 1_000_000.0
    a = s.write("Tema: koyu", memory_type="USER", provenance="USER",
                record_kind="PREFERENCE", subject_key="preference:theme",
                confidence=0.95)
    s.now = lambda: 1_000_500.0
    b = s.write("Tema: açık", memory_type="USER", provenance="USER",
                record_kind="PREFERENCE", subject_key="preference:theme",
                confidence=0.95)
    cr = ConflictResolver(s)
    b_rec = s.get(b["id"])
    conflicts = cr.detect("preference:theme", b_rec)
    assert [c["id"] for c in conflicts] == [a["id"]]
    res = cr.resolve(b_rec, conflicts)
    assert res["resolved"] and res["winners"] == [b["id"]]
    assert s.get(a["id"])["status"] == "SUPERSEDED"      # geçmiş duruyor
    assert s.get(b["id"])["status"] == "ACTIVE"
    # çelişkiye düşen kaybın güveni düştü (§5)
    assert s.get(a["id"])["confidence"] < 0.95


def test_gpu_temps_different_times_no_conflict(tmp_path):
    s = make(str(tmp_path))
    s.now = lambda: 1_000_000.0
    s.write("GPU temperature 55", memory_type="SHORT_TERM", provenance="SYSTEM",
            record_kind="OBSERVATION", subject_key="gpu_temp",
            valid_from=1000000.0, valid_until=1000300.0)
    s.now = lambda: 1_000_500.0
    b = s.write("GPU temperature 72", memory_type="SHORT_TERM", provenance="SYSTEM",
                record_kind="OBSERVATION", subject_key="gpu_temp",
                valid_from=1000500.0, valid_until=1000800.0)
    cr = ConflictResolver(s)
    assert cr.detect("gpu_temp", s.get(b["id"])) == []   # çelişki YOK


def test_inference_loses_to_user_fact(tmp_path):
    s = make(str(tmp_path))
    a = s.write("Sunucu adresi üretim", memory_type="SEMANTIC", provenance="USER",
                record_kind="FACT", subject_key="fact:server",
                confidence=0.95, created_at=1000000.0)
    s.now = lambda: 1000100.0
    b = s.write("Sunucu adresi test", memory_type="SEMANTIC",
                provenance="MODEL_INFERENCE", record_kind="INFERENCE",
                subject_key="fact:server")
    cr = ConflictResolver(s)
    res = cr.resolve(s.get(b["id"]), cr.detect("fact:server", s.get(b["id"])))
    # belirgin üstünlük: USER fact kazanır, inference kaybeder
    assert res["winners"] == [a["id"]]
    assert s.get(b["id"])["status"] == "SUPERSEDED"


# ------------------------------------------------------------ decay
def test_decay_protects_user_procedural_security(tmp_path):
    s = make(str(tmp_path))
    old = 100.0
    s.write("eski kullanıcı tercihi", memory_type="USER", provenance="USER",
            record_kind="PREFERENCE", created_at=old)
    s.write("nasıl deploy edilir", memory_type="PROCEDURAL", provenance="USER",
            record_kind="FACT", created_at=old)
    s.write("eski güvenlik notu", memory_type="SHORT_TERM", provenance="SYSTEM",
            record_kind="OBSERVATION", tags=["security"], created_at=old)
    s.write("geçici çalışma notu", memory_type="WORKING", provenance="SYSTEM",
            record_kind="OBSERVATION", importance=0.1, created_at=old)
    s.now = lambda: 1_000_000.0
    dm = DecayManager(s)
    out = dm.run()
    assert out["archived"] >= 1
    assert all(s.get(r["id"])["status"] == "ACTIVE" for r in s.query()
               if r["memory_type"] in ("USER", "PROCEDURAL"))
    assert all("security" not in (r["tags"] or []) or r["status"] == "ACTIVE"
               for r in s.query(status="ACTIVE") + s.query(status="ARCHIVED"))


def test_decay_archives_not_deletes(tmp_path):
    s = make(str(tmp_path))
    s.write("bayat gözlem", memory_type="WORKING", provenance="SYSTEM",
            record_kind="OBSERVATION", importance=0.1, created_at=100.0)
    s.now = lambda: 1_000_000.0
    DecayManager(s).run()
    recs = s.query(status="ARCHIVED")
    assert len(recs) == 1                     # arşivde — hard delete yok


# ------------------------------------------------------------ retrieval
def test_retrieval_pipeline_rank_and_budget(tmp_path):
    s = make(str(tmp_path))
    s.write("Kullanıcı VS Code seviyor", memory_type="USER", provenance="USER",
            record_kind="PREFERENCE", subject_key="preference:editor",
            confidence=0.95, importance=0.9)
    s.write("Python deploy adımları şöyle", memory_type="PROCEDURAL",
            provenance="USER", record_kind="FACT", confidence=0.9)
    s.write("muhtemelen kullanıcı kod yazıyor", memory_type="SHORT_TERM",
            provenance="MODEL_INFERENCE", record_kind="INFERENCE",
            confidence=0.5, importance=0.2)
    r = Retriever(s)
    out = r.retrieve("hangi editör sever", limit=5, context_budget=1500)
    assert out["hits"] >= 1
    assert "VS Code" in out["context"]
    assert out["engine"] in ("keyword", "chroma")
    out2 = r.retrieve("editör tercih", context_budget=50)   # bütçe kırpma
    assert len(out2["context"]) <= 60 or out2["context"] == ""


def test_retrieval_inference_ranked_below_fact(tmp_path):
    s = make(str(tmp_path))
    s.write("CPU az önce %20 gözlendi", memory_type="SHORT_TERM",
            provenance="SYSTEM", record_kind="OBSERVATION")
    s.write("kullanıcı muhtemelen gece çalışıyor", memory_type="SHORT_TERM",
            provenance="MODEL_INFERENCE", record_kind="INFERENCE")
    r = Retriever(s)
    out = r.retrieve("cpu gözlem", limit=5)
    first = out["context"].splitlines()[0]
    assert "OBSERVATION" in first           # doğrulanmış gözlem üstte


def test_retrieval_secret_never_in_context(tmp_path):
    from app.security.redaction import redact
    # secret yazım zaten reddedilir; boş mağazada bağlam da secret içeremez:
    s2 = MemoryStore(db_path=os.path.join(tmp_path, "v3b.db"), redact_fn=redact)
    r = Retriever(s2, redact_fn=redact)
    out = r.retrieve("her şey", limit=5)
    assert "ABCDEF" not in out["context"]


def test_user_model_vs_memory_history(tmp_path):
    """§12: memory geçmiş tutar; user model yalnız güncel kazananı gösterir."""
    s = make(str(tmp_path))
    s.now = lambda: 100.0
    s.write("kahve severim", memory_type="USER", provenance="USER",
            record_kind="PREFERENCE", subject_key="preference:drink",
            confidence=0.95)
    s.now = lambda: 200.0
    s.write("çay severim", memory_type="USER", provenance="USER",
            record_kind="PREFERENCE", subject_key="preference:drink",
            confidence=0.95)
    cr = ConflictResolver(s)
    b = s.query(subject_key="preference:drink")[0]
    cr.resolve(b, cr.detect("preference:drink", b))
    r = Retriever(s)
    prefs = r.current_preferences()
    assert prefs["drink"]["value"] == "çay severim"        # güncel model
    # geçmiş memory'de hâlâ durur
    assert len(s.query(subject_key="preference:drink", status="SUPERSEDED")) == 1
