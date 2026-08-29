"""Wave 5 §5+§10 — Predictive Intelligence testleri (gerçek istatistik)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.predictive import PredictiveEngine  # noqa: E402


def make(tmp):
    return PredictiveEngine(db_path=os.path.join(tmp, "p.db"))


def test_duration_insufficient_data_is_honest():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        pe.record_task("sync", 5.0)
        r = pe.duration_estimate("sync")
        assert r["ok"] is False and r["reason"] == "insufficient-data"


def test_duration_median_band_and_prediction_record():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for t in (10, 10, 10, 10, 60):     # bir aykırı
            pe.record_task("index", t)
        r = pe.duration_estimate("index")
        assert r["ok"] is True
        assert r["estimate_s"] == 10       # medyan aykırıya dayanıklı
        assert r["band"][0] <= 10 <= r["band"][1]
        assert r["prediction_id"] > 0


def test_failure_probability_laplace_never_zero():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for _ in range(10):
            pe.record_task("clean", 1.0, failed=False)
        r = pe.failure_probability("clean")
        assert r["ok"] is True and 0 < r["probability"] < 0.2  # 1/12
        for _ in range(4):
            pe.record_task("fragile", 1.0, failed=True)
        r2 = pe.failure_probability("fragile")
        assert r2["probability"] > 0.5


def test_dependency_risk_chain_product_and_unknown():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        r = pe.dependency_risk([
            {"name": "build", "failure_probability": 0.1},
            {"name": "test", "failure_probability": 0.2},
        ])
        assert r["ok"] and abs(r["failure_probability"] - 0.28) < 1e-6
        r2 = pe.dependency_risk([{"name": "x"},
                                 {"name": "y", "failure_probability": 0.1}])
        assert r2["ok"] is False and r2["unknown_steps"] == ["x"]


def test_resource_trend_linear_with_honest_r2():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for v in (10, 20, 30, 40):         # tam lineer
            pe.record_metric(v)
        r = pe.resource_trend(horizon_s=2)  # 2 örnek ileri
        assert r["ok"] and r["r2"] > 0.99 and r["confidence"] > 0.9
        assert abs(r["predicted"] - 60) < 1e-6


def test_resource_trend_noisy_keeps_low_confidence():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for v in (50, 10, 55, 5, 48):      # gürültülü
            pe.record_metric(v)
        r = pe.resource_trend()
        assert r["ok"] and r["confidence"] < 0.5   # R² dürüstçe düşük


def test_anomaly_robust_z():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for v in (10, 10, 11, 10, 10):
            pe.record_metric(v)
        ok_val = pe.anomaly_risk(11)
        bad = pe.anomaly_risk(90)
        assert ok_val["anomaly"] is False and bad["anomaly"] is True
        assert bad["robust_z"] > 3.5


def test_maintenance_due_math():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        m = pe.maintenance_due(last_at=1000.0, interval_s=100, now=1050.0)
        assert m["due"] is False and m["next_in_s"] == 50
        m2 = pe.maintenance_due(last_at=1000.0, interval_s=100, now=1120.0)
        assert m2["due"] is True


def test_intent_prediction_markov():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for i in ["code", "test", "code", "test", "code"]:
            pe.record_intent(i)
        # code → test geçişi baskın
        r = pe.intent_prediction()
        assert r["ok"] and r["next_intent"] == "test" and r["evidence"] >= 2


def test_prediction_outcome_separation_and_calibration():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for t in (10, 10, 10):
            pe.record_task("job", t)
        est = pe.duration_estimate("job")
        pe.outcome(est["prediction_id"], 12.0)      # gerçek: 12
        c = pe.calibration("duration")
        assert c["closed"] == 1 and abs(c["mean_abs_error"] - 2.0) < 1e-6
        # kapalı tahmin tekrar kapanamaz
        assert pe.outcome(est["prediction_id"], 99.0)["ok"] is False


def test_planner_budget_feasibility_from_estimates():
    with tempfile.TemporaryDirectory() as d:
        pe = make(d)
        for t in (10, 10, 12):
            pe.record_task("step_a", t)
        for t in (20, 20, 22):
            pe.record_task("step_b", t)
        ests = [pe.duration_estimate("step_a"), pe.duration_estimate("step_b")]
        total = sum(e["estimate_s"] for e in ests)
        # §10: deadline feasibility — toplam tahmin < bütçe → feasible
        assert total <= 35
        r = pe.dependency_risk([
            {"name": "a", "failure_probability":
                pe.failure_probability("step_a")["probability"]
             if pe.failure_probability("step_a")["ok"] else None},
            {"name": "b", "failure_probability": 0.1},
        ])
        assert r["ok"] is True   # zincir riski üretilebilir
