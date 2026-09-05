"""Wave 5 §2 — User Intelligence testleri (gerçek davranış; LLM yok)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.user_intelligence import (  # noqa: E402
    PRIVACY_BOUNDARY_FIELDS, UserIntelligence)


def make(tmp):
    return UserIntelligence(db_path=os.path.join(tmp, "u.db"))


def test_explicit_preference_confidence_one():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        out = ui.set_explicit("ui", "theme", "dark")
        assert out["ok"] and out["confidence"] == 1.0
        prefs = ui.preferences("ui")
        assert prefs[0]["kind"] == "explicit" and prefs[0]["value"] == "dark"


def test_inferred_confidence_starts_low_and_never_reaches_one():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        for i in range(20):
            ui.observe("editor", "font_size", 14)
        p = [x for x in ui.preferences("editor") if x["kind"] == "inferred"][0]
        assert 0 < p["confidence"] < 1.0   # hassas değil: asla 'gerçek' değil


def test_explicit_protected_from_observations():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        ui.set_explicit("editor", "font_size", 12)
        for _ in range(5):
            ui.observe("editor", "font_size", 20)
        p = ui.preferences("editor")
        assert len(p) == 1 and p[0]["value"] == 12  # davranış beyanı ezmez


def test_correction_overrides_and_records():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        ui.observe("news", "topic", "tech")
        out = ui.correct("news", "topic", "science")
        assert out["ok"] is True
        p = ui.preferences("news")
        assert p[0]["kind"] == "explicit" and p[0]["value"] == "science"
        assert ui.stats()["corrections"] == 1


def test_privacy_boundary_blocks_auto_learning():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        r = ui.observe("personal", "health", "gerçek veri")
        assert r["ok"] is False and "privacy" in r["error"]
        r2 = ui.set_explicit("personal", "political_view", "x")
        assert r2["ok"] is False  # profil alanı olarak da açılmaz
        assert "health" in PRIVACY_BOUNDARY_FIELDS


def test_inferred_value_change_resets_counter():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        ui.observe("ui", "density", "compact")
        ui.observe("ui", "density", "compact")
        ui.observe("ui", "density", "spacious")   # çelişki → sıfırdan
        p = [x for x in ui.preferences("ui") if x["name"] == "density"][0]
        assert p["n_obs"] == 1 and p["value"] == "spacious"


def test_behavior_patterns_and_peak_hour():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        base = 1_700_000_000.0
        for i in range(4):   # hep 09:00 civarı çalışan saatte
            t = base + i * 86400
            ui.record_event("daily_brief", now=t)
        pats = ui.behavior_patterns(min_count=3)
        assert any(p["action"] == "daily_brief" and p["count"] == 4 for p in pats)


def test_recurring_workflow_bigram():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        for _ in range(3):
            ui.record_event("open_editor")
            ui.record_event("run_tests")
        wf = ui.recurring_workflows(min_len=2, min_count=2)
        assert any(w["workflow"] == ["open_editor", "run_tests"] and w["count"] >= 2
                   for w in wf)


def test_communication_style_from_real_lengths():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        for _ in range(6):
            ui.note_response_length(40)     # kısa yanıtlar üretiyoruz
        st = ui.communication_style()
        assert st["style"] == "concise" and st["confidence"] > 0


def test_communication_style_honest_without_data():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        st = ui.communication_style()
        assert st["style"] is None and st["confidence"] == 0.0  # dürüst


def test_min_confidence_filter():
    with tempfile.TemporaryDirectory() as d:
        ui = make(d)
        ui.set_explicit("ui", "a", 1)
        ui.observe("ui", "b", 2)
        strong = ui.preferences(min_confidence=0.5)
        assert all(x["name"] == "a" for x in strong)
