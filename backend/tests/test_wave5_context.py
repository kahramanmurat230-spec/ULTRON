"""Wave 5 §1 — Cognitive Context Engine testleri (gerçek davranış)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.context_engine import ContextEngine, MODALITIES  # noqa: E402


def make(tmp, budget=2000):
    return ContextEngine(db_path=os.path.join(tmp, "c.db"), token_budget=budget)


def test_add_and_snapshot_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        assert ce.add("ui_theme", "dark", modality="text")["ok"] is True
        snap = ce.snapshot()
        assert any(i["key"] == "ui_theme" and i["value"] == "dark"
                   for i in snap["items"])


def test_modality_validation_rejects_unknown():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        try:
            ce.add("x", 1, modality="telepathy")
            assert False, "unknown modality kabul edilmemeli"
        except ValueError:
            pass


def test_cross_modal_items_tagged():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        for m in MODALITIES:
            ce.add(f"k_{m}", m, modality=m)
        snap = ce.snapshot()
        tags = {i["modality"] for i in snap["items"]}
        assert tags == set(MODALITIES)  # köken etiketi korunur


def test_namespaces_isolated():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("goal", "A", namespace="task:1")
        ce.add("goal", "B", namespace="task:2")
        assert all(i["value"] == "A" for i in ce.snapshot("task:1")["items"])
        assert all(i["value"] == "B" for i in ce.snapshot("task:2")["items"])


def test_prioritization_importance_beats_junk():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d, budget=10000)
        ce.add("low", "x" * 50, importance=0.1)
        ce.add("high", "y" * 50, importance=0.95)
        snap = ce.snapshot()
        keys = [i["key"] for i in snap["items"]]
        assert keys.index("high") < keys.index("low")  # skor sırası


def test_compression_drops_low_priority_honestly():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d, budget=60)  # ~1 item'lık bütçe
        ce.add("big_low", "a " * 40, importance=0.1)
        ce.add("big_high", "b " * 40, importance=0.9)
        snap = ce.snapshot()
        assert snap["dropped"] == ["big_low"]      # sessiz kayıp YOK
        assert ce.last_compression["dropped"] == ["big_low"]
        assert len(snap["items"]) == 1


def test_conflict_new_wins_and_logged():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("city", "Ankara")
        ce.add("city", "İzmir")
        snap = ce.snapshot()
        vals = [i["value"] for i in snap["items"] if i["key"] == "city"]
        assert vals == ["İzmir"]                    # yeni kazanır
        assert any(c["resolution"] == "new-wins" for c in ce.conflicts)


def test_conflict_explicit_beats_inferred():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("lang", "tr", explicit=True)
        ce.add("lang", "en", explicit=False)    # inferred aday
        snap = ce.snapshot()
        vals = [i["value"] for i in snap["items"] if i["key"] == "lang"]
        assert vals == ["tr"]                        # explicit korunur
        assert any(c["resolution"] == "kept-old-explicit" for c in ce.conflicts)


def test_ttl_expiration_recorded():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("temp", "x", ttl_s=1.0, now=1000.0)
        snap = ce.snapshot(now=1000.5)
        assert any(i["key"] == "temp" for i in snap["items"])
        snap2 = ce.snapshot(now=1002.0)
        assert not any(i["key"] == "temp" for i in snap2["items"])
        assert "temp" in snap2["expired_now"]
        assert ce.expired and ce.expired[0]["reason"] == "ttl-or-window"


def test_temporal_window_validity():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("meeting", "plan_toplanti", valid_from=2000.0, valid_to=2100.0)
        assert not any(i["key"] == "meeting"
                      for i in ce.snapshot(now=1500.0)["items"])  # henüz değil
        assert any(i["key"] == "meeting"
                   for i in ce.snapshot(now=2050.0)["items"])     # pencerede
        assert "meeting" in ce.snapshot(now=2200.0)["expired_now"]


def test_projection_merges_namespaces():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("a", 1, namespace="task:1")
        ce.add("b", 2, namespace="project:ultron")
        p = ce.project(["task:1", "project:ultron", "global"])
        keys = {i["key"] for i in p["items"]}
        assert {"a", "b"} <= keys


def test_touch_refreshes_recency_score():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("x", "v", importance=0.5, now=1000.0)
        ce.add("y", "v", importance=0.5, now=1000.0)
        ce.touch("x", now=5000.0)                    # x tazelendi
        s = {i["key"]: i["score"] for i in ce.snapshot(now=5000.0)["items"]}
        assert s["x"] > s["y"]


def test_remove_and_stats():
    with tempfile.TemporaryDirectory() as d:
        ce = make(d)
        ce.add("k", 1)
        assert ce.remove("k") is True and ce.remove("k") is False
        assert ce.stats()["items"] == 0
