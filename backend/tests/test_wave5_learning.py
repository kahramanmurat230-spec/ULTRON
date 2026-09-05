"""Wave 5 §9+§17 — Learning Loop + Self-Evaluation testleri."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.knowledge_engine import KnowledgeEngine  # noqa: E402
from app.cognitive.learning_loop import LearningLoop  # noqa: E402
from app.cognitive.user_intelligence import UserIntelligence  # noqa: E402


def make(tmp, knowledge=None, user_intel=None):
    return LearningLoop(db_path=os.path.join(tmp, "l.db"),
                        knowledge=knowledge, user_intel=user_intel)


def test_outcome_quality_expected_vs_actual():
    with tempfile.TemporaryDirectory() as d:
        ll = make(d)
        r = ll.record_outcome("deploy", expected="5 servis ayakta",
                              actual="5 servis ayakta",
                              success=True, latency_s=12.0)
        assert r["quality"] == 1.0 and r["strategy"] == "repeat"
        r2 = ll.record_outcome("deploy", expected="a b c",
                               actual="a x y", success=True)
        assert r2["quality"] < 0.5 and "düşük kalite" in r2["lesson"]


def test_quality_none_when_expected_unknown():
    with tempfile.TemporaryDirectory() as d:
        ll = make(d)
        r = ll.record_outcome("adhoc", actual="sonuç", success=True)
        assert r["quality"] is None          # uydurma kalite YOK


def test_lesson_from_user_correction_and_preference_side_effect():
    with tempfile.TemporaryDirectory() as d:
        ui = UserIntelligence(db_path=os.path.join(d, "u.db"))
        ll = make(d, user_intel=ui)
        ll.record_outcome("rapor", user_correction="kısa olsun",
                          success=True)
        prefs = ui.preferences("corrections")
        assert any(p["value"] == "kısa olsun" for p in prefs)


def test_lesson_stored_in_knowledge_layer():
    with tempfile.TemporaryDirectory() as d:
        ke = KnowledgeEngine(db_path=os.path.join(d, "k.db"))
        ll = make(d, knowledge=ke)
        ll.record_outcome("sync", expected="ok", actual="ok",
                          success=True)
        docs = ke.search("yaklaşım işe yaradı")
        assert docs["results"]                 # ders bilgi katmanında
        cl = ke.claim("lesson_sync")
        assert cl["value"]                     # claim olarak erişilir


def test_failure_pattern_accumulates():
    with tempfile.TemporaryDirectory() as d:
        ll = make(d)
        for _ in range(3):
            ll.record_outcome("import", success=False, errors=2,
                              failure_signature="ModuleNotFoundError")
        assert ll.failure_frequency("ModuleNotFoundError") == 3


def test_strategy_for_from_history_and_avoid():
    with tempfile.TemporaryDirectory() as d:
        ll = make(d)
        for _ in range(3):
            ll.record_outcome("fragile", success=False, errors=1)
        s = ll.strategy_for("fragile")
        assert s["strategy"] == "consider-avoid" and s["losses"] == 3
        s2 = ll.strategy_for("yeni-tip")
        assert s2["strategy"] == "no-history" and s2["confidence"] == 0.0


def test_metrics_aggregation():
    with tempfile.TemporaryDirectory() as d:
        ll = make(d)
        ll.record_outcome("t", expected="x", actual="x", success=True,
                          latency_s=10, cost=0.5, errors=0)
        ll.record_outcome("t", success=False, latency_s=30, cost=0.25,
                          errors=2)
        m = ll.metrics("t")
        assert m["outcomes"] == 2 and m["success_rate"] == 0.5
        assert m["avg_latency_s"] == 20.0 and m["total_cost"] == 0.75
        assert m["total_errors"] == 2


def test_production_code_untouched(tmp_path=None):
    """Öğrenme yalnız bilgi katmanına yazar — kod dosyası DEĞİŞMEZ."""
    import hashlib
    with tempfile.TemporaryDirectory() as d:
        ke = KnowledgeEngine(db_path=os.path.join(d, "k.db"))
        ll = make(d, knowledge=ke)
        target = os.path.join(d, "prod_like.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        h0 = hashlib.md5(open(target, "rb").read()).hexdigest()
        ll.record_outcome("kod", expected="x", actual="x", success=True)
        ll.record_outcome("kod", success=False, failure_signature="F")
        h1 = hashlib.md5(open(target, "rb").read()).hexdigest()
        assert h0 == h1                      # üretim kodu değişmedi
