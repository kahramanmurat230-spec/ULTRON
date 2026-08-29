"""Wave 5 §3-4 — Goal Intelligence + Decision Engine testleri."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.decision_engine import DecisionEngine  # noqa: E402
from app.cognitive.goal_engine import GoalEngine  # noqa: E402


# ---------------------------------------------------------------- goals
def make_g(tmp):
    return GoalEngine(db_path=os.path.join(tmp, "g.db"))


def test_goal_create_and_active():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        r = ge.create("CI'yi yeşile çek", intent="kullanıcı: build kırık")
        assert r["ok"] and ge.active_goals()[0]["title"].startswith("CI")


def test_decompose_sequential_subgoals():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("release")["goal"]
        out = ge.decompose(g, ["test", "tag", "publish"])
        subs = out["subgoals"]
        assert ge.blocked_by(subs[0]) == []
        assert ge.blocked_by(subs[1]) == [subs[0]]   # sıralı bağımlılık
        assert ge.blocked_by(subs[2]) == [subs[1]]


def test_circular_dependency_rejected():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        a = ge.create("A")["goal"]
        b = ge.create("B")["goal"]
        assert ge.add_dependency(b, a)["ok"] is True
        r = ge.add_dependency(a, b)                  # çevrim!
        assert r["ok"] is False and "circular" in r["error"]
        assert ge.add_dependency(a, a)["ok"] is False  # self


def test_complete_requires_evidence():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("doğrulanabilir iş")["goal"]
        r = ge.complete(g)                            # kanıtsız
        assert r["ok"] is False and "evidence" in r["error"]
        r2 = ge.complete(g, evidence="test: 12 passed, CI yeşil")
        assert r2["ok"] is True


def test_complete_blocked_by_open_subgoals_and_deps():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("parent")["goal"]
        subs = ge.decompose(g, ["a", "b"])["subgoals"]
        r = ge.complete(g, evidence="x")
        assert r["ok"] is False and "open subgoals" in r["error"]
        ge.complete(subs[0], evidence="a bitti")
        r2 = ge.complete(g, evidence="x")
        assert r2["ok"] is False and subs[1] in r2.get("open", [])


def test_progress_real_induction():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("root")["goal"]
        s1, s2 = ge.decompose(g, ["s1", "s2"])["subgoals"]
        p0 = ge.progress(g)
        assert p0["fraction"] == 0.0
        ge.complete(s1, evidence="e1")
        p1 = ge.progress(g)
        assert abs(p1["fraction"] - 0.5) < 1e-6        # 2 yapraktan 1'i
        ge.complete(s2, evidence="e2")
        assert ge.progress(g)["fraction"] == 1.0
        assert ge.complete(g, evidence="her iki alt done")["ok"] is True


def test_blockers_block_and_unblock():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("iş")["goal"]
        ge.add_blocker(g, "API anahtarı yok")
        assert ge.active_goals()[0]["status"] == "BLOCKED"
        ge.clear_blocker(g, "API anahtarı")
        assert ge.active_goals()[0]["status"] == "ACTIVE"


def test_cancel_with_reason_and_guard():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("eski iş")["goal"]
        assert ge.cancel(g, "kullanıcı vazgeçti")["ok"] is True
        r = ge.complete(g, evidence="x")
        assert r["ok"] is False                        # iptalli tamamlanmaz
        r2 = ge.cancel(g, "tekrar")
        assert r2["ok"] is False and "already" in r2["error"]


def test_plan_versioning_keeps_history():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        g = ge.create("planlı")["goal"]
        ge.set_plan(g, ["adım1", "adım2"])
        ge.set_plan(g, ["adım1", "adım2b", "adım3"])   # replan
        cur = ge.current_plan(g)
        assert cur["version"] == 2 and len(cur["steps"]) == 3
        # sürüm 1 hâlâ okunabilir mi (kayıp yok)?
        with ge.lock:
            old = ge.db.execute("SELECT steps FROM plans WHERE goal=? AND version=1",
                                (g,)).fetchone()
        assert old and "adım2" in old[0] and "adım2b" not in old[0]


def test_deadline_risk_levels():
    with tempfile.TemporaryDirectory() as d:
        ge = make_g(d)
        now = 1000.0
        late = ge.create("geç", deadline=1100.0)["goal"]
        assert ge.deadline_risk(late, now=now)["risk"] == "low"
        overdue = ge.create("kaçtı", deadline=900.0)["goal"]
        assert ge.deadline_risk(overdue, now=now)["risk"] == "overdue"
        done = ge.create("bitti", deadline=1100.0)["goal"]
        ge.complete(done, evidence="e")
        assert ge.deadline_risk(done, now=now)["risk"] == "none"


# ------------------------------------------------------------ decisions
def make_d(d):
    return DecisionEngine(db_path=os.path.join(d, "d.db"))


def test_decide_picks_highest_scored_option():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("deploy", "v1.2", options=[
            {"id": "canary", "value": 80, "cost": 10, "confidence": 0.9},
            {"id": "all-at-once", "value": 100, "cost": 5, "confidence": 0.4},
        ])
        assert r["ok"] and r["chosen"] == "canary"   # 72 > 35
        assert r["alternatives"] == ["all-at-once"]
        assert r["authority"] == "AUTONOMOUS"        # low risk + reversible


def test_decision_record_stores_all_fields():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("tool", "hangi aracı", options=[
            {"id": "a", "value": 10, "confidence": 0.9,
             "reason_code": "FASTER", "expected": "hızlı"}],
            goals=["g1"], constraints=["no-cloud"],
            expected_outcome="5dk içinde sonuç")
        rec = de.get(r["decision_id"])
        for k in ("confidence", "risk_level", "reason_code", "reversibility",
                  "authority", "expected_outcome", "options_json"):
            assert rec[k] is not None, f"{k} kaydedilmeli"
        assert rec["goals_json"] == '["g1"]'


def test_constraint_filters_viable_options():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("tts", "motor", options=[
            {"id": "cloud", "value": 90, "confidence": 0.9,
             "violates": "no-cloud"},
            {"id": "local", "value": 60, "confidence": 0.8},
        ], constraints=["no-cloud"])
        assert r["chosen"] == "local"


def test_no_viable_option_is_honest_rejection():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("x", "y", options=[
            {"id": "only", "value": 10, "violates": "no-mutation"}],
            constraints=["no-mutation"])
        assert r["ok"] is False and r["reason_code"] == "NO_VIABLE_OPTION"
        assert r["authority"] == "APPROVAL"


def test_low_confidence_requires_human():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("crit", "karar", options=[
            {"id": "guess", "value": 10, "confidence": 0.2}])
        assert r["ok"] is False and "confidence" in r["error"]
        assert r["reason_code"] == "LOW_CONFIDENCE"


def test_tie_reduces_confidence():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("t", "berabere", options=[
            {"id": "x", "value": 50, "confidence": 0.9},
            {"id": "y", "value": 50, "confidence": 0.9},
        ])
        rec = de.get(r["decision_id"])
        assert rec["confidence"] < 0.9 * 0.6 + 1e-9  # belirsizlik→güven düşüşü


def test_policy_matrix_high_irreversible_needs_approval():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("del", "dosya sil", options=[
            {"id": "rm", "value": 50, "confidence": 0.9}],
            tool="delete_file", tool_args={"path": "x"},
            reversibility="irreversible")
        assert r["authority"] == "APPROVAL"


def test_risk_guard_critical_when_blocked():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        # security core yazımı: guard RED der → critical risk
        r = de.decide("codegen", "vault'a dokun", options=[
            {"id": "patch", "value": 90, "confidence": 0.9}],
            tool="write_text",
            tool_args={"path": "app/security/vault.py", "content": "x"})
        assert r["risk_level"] == "critical"
        assert r["authority"] == "APPROVAL"


def test_verify_outcome_closure():
    with tempfile.TemporaryDirectory() as d:
        de = make_d(d)
        r = de.decide("k", "s", options=[{"id": "o", "value": 1,
                                          "confidence": 0.9}],
                      expected_outcome="5 kayıt")
        v = de.verify(r["decision_id"], "5 kayıt", success=True)
        assert v["verified"] == "SUCCESS"
        # tek karar TEK verify: tekrar İLK SONUCU EZMEZ (kayıp yok)
        again = de.verify(r["decision_id"], "3 kayıt", success=None)
        assert again["ok"] is False and again["verified"] == "SUCCESS"
        # MISMATCH ayrı kararda ölçülür
        r2 = de.decide("k2", "s2", options=[{"id": "o2", "value": 1,
                                             "confidence": 0.9}],
                       expected_outcome="5 kayıt")
        m = de.verify(r2["decision_id"], "3 kayıt", success=None)
        assert m["verified"] == "MISMATCH"
        s = de.stats()
        assert s["total"] == 2 and s["verified"]["SUCCESS"] == 1
        assert s["verified"]["MISMATCH"] == 1
