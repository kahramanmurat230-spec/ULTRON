"""Wave 5 §20 — performance: GERÇEK ölçüm (uydurma benchmark YOK).
Eşikler kategori makul sınır; geçilemezse test KIRMIZI kalır."""
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.autonomy_loop import Budgets  # noqa: E402
from app.cognitive.capability_discovery import MetaReasoning  # noqa: E402
from app.cognitive.cognitive_observability import CognitiveTrace  # noqa: E402
from app.cognitive.context_engine import ContextEngine  # noqa: E402
from app.cognitive.decision_engine import DecisionEngine  # noqa: E402
from app.cognitive.knowledge_engine import KnowledgeEngine  # noqa: E402
from app.cognitive.knowledge_graph import KnowledgeGraph  # noqa: E402
from app.cognitive.predictive import PredictiveEngine  # noqa: E402


def p50_ms(fn, n=5):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def test_context_snapshot_200_items_under_100ms():
    with tempfile.TemporaryDirectory() as d:
        ce = ContextEngine(db_path=os.path.join(d, "c.db"))
        for i in range(200):
            ce.add(f"k{i}", f"değer {i} " * 5, importance=0.1 + (i % 10) / 20)
        ms = p50_ms(lambda: ce.snapshot())
        assert ms < 100, f"context snapshot p50 {ms:.1f}ms"


def test_bm25_search_30_docs_under_400ms():
    with tempfile.TemporaryDirectory() as d:
        ke = KnowledgeEngine(db_path=os.path.join(d, "k.db"))
        for i in range(30):
            ke.ingest(f"belge{i}", (f"konu {i} içeriği. " * 12) +
                      "ortak anahtar kelime arama test", source=f"s{i}")
        ms = p50_ms(lambda: ke.search("ortak anahtar kelime"))
        assert ms < 400, f"bm25 p50 {ms:.1f}ms"


def test_graph_traversal_150_nodes_under_150ms():
    with tempfile.TemporaryDirectory() as d:
        kg = KnowledgeGraph(db_path=os.path.join(d, "kg.db"))
        for i in range(150):
            kg.add_entity(f"n{i}", f"n{i}")
        for i in range(149):
            kg.relate(f"n{i}", f"n{i + 1}", "next", supersede=False)
        ms = p50_ms(lambda: kg.traverse("n0", max_depth=4))
        assert ms < 150, f"traversal p50 {ms:.1f}ms"


def test_decision_engine_100_decisions_under_300ms():
    with tempfile.TemporaryDirectory() as d:
        de = DecisionEngine(db_path=os.path.join(d, "d.db"))
        opts = [{"id": f"o{j}", "value": 10 * j, "cost": j,
                 "confidence": 0.5 + j / 10} for j in range(6)]

        def hundred():
            for i in range(100):
                de.decide("bench", f"s{i}", options=opts)
        t0 = time.perf_counter()
        hundred()
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 300, f"100 karar {ms:.1f}ms"


def test_trace_log_redact_200_under_400ms():
    with tempfile.TemporaryDirectory() as d:
        tr = CognitiveTrace(db_path=os.path.join(d, "t.db"))

        def two_hundred():
            for i in range(200):
                tr.log("decision", f"s{i}",
                       {"detail": f"token=secret{i} karar detayı {i}"})
        t0 = time.perf_counter()
        two_hundred()
        ms = (time.perf_counter() - t0) * 1000
        assert ms < 400, f"200 redact+log {ms:.1f}ms"


def test_predictive_duration_500_samples_fast():
    with tempfile.TemporaryDirectory() as d:
        pe = PredictiveEngine(db_path=os.path.join(d, "p.db"))
        for i in range(500):
            pe.record_task("job", 10 + (i % 7))
        ms = p50_ms(lambda: pe.duration_estimate("job"), n=7)
        assert ms < 50, f"duration estimate p50 {ms:.1f}ms"


def test_meta_reasoning_composite_under_5ms():
    mr = MetaReasoning()
    evs = [{"confidence": 0.7, "text": "x=1"}] * 10

    def call():
        return mr.reasoning_confidence(evidences=evs,
                                       claims=[{"key": "x", "value": 1,
                                                "source": "a"}],
                                       citations=["u"], answer="x=1")
    ms = p50_ms(call, n=7)
    assert ms < 5, f"composite p50 {ms:.1f}ms"
