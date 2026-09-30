"""Wave 5 §12+§13+§18 — Autonomy loop, simulation, safety testleri."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cognitive.autonomy_loop import AutonomousGoalLoop, Budgets  # noqa: E402
from app.cognitive.autonomy_safety import AutonomySafety  # noqa: E402
from app.cognitive.goal_engine import GoalEngine  # noqa: E402
from app.cognitive.simulation import Simulator  # noqa: E402


def make_safety(tmp, **pol):
    return AutonomySafety(db_path=os.path.join(tmp, "s.db"), approval_policy=pol)


def test_safety_low_risk_autonomous():
    with tempfile.TemporaryDirectory() as d:
        s = make_safety(d)
        r = s.evaluate("rapor oku", "read status file")
        assert r["decision"] == "ALLOW"


def test_safety_high_risk_needs_user_approval():
    with tempfile.TemporaryDirectory() as d:
        s = make_safety(d)
        r = s.evaluate("temizlik", "delete all temp files", reversibility="irreversible")
        assert r["decision"] == "DENY" and "user-approval" in r["reason"]
        r2 = s.evaluate("temizlik", "delete all temp files", reversibility="irreversible", user_approved=True)
        assert r2["decision"] == "NOTIFY"


def test_safety_medium_policy_gate():
    with tempfile.TemporaryDirectory() as d:
        s = make_safety(d)
        r = s.evaluate("dağıt", "write config file", reversibility="partially-reversible")
        assert r["decision"] == "NOTIFY" and "policy" in r["reason"]
        r2 = s.evaluate("dağıt", "write config file", reversibility="partially-reversible", policy_allows=False)
        assert r2["decision"] == "DENY"


def test_safety_policy_override_and_audit():
    with tempfile.TemporaryDirectory() as d:
        s = make_safety(d, **{"git push": "high"})
        r = s.evaluate("yayınla", "git push origin main")
        assert r["risk"] == "high"
        trail = s.audit_trail()
        assert trail and trail[0]["decision"] == r["decision"]


def test_dry_run_previews_and_never_executes():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "file.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("eski içerik")
        sim = Simulator(db_path=os.path.join(d, "sim.db"), root=d)
        r = sim.dry_run("güncelle", [
            {"name": "write file.txt", "type": "write", "path": "file.txt", "content": "yeni içerik daha uzun"},
            {"name": "read status", "type": "read"},
            {"name": "shell list", "type": "shell", "command": "ls -la"},
        ])
        assert r["dry_run"] is True and r["executed"] is False
        prev = r["action_previews"][0]["file_change_preview"]
        assert prev["exists"] is True and prev["current_bytes"] > 0
        assert prev["new_bytes"] > prev["current_bytes"]
        assert r["action_previews"][0]["rollback_plan"]
        with open(target, encoding="utf-8") as f:
            assert f.read() == "eski içerik"


def test_dry_run_shell_risk_hint_and_outside_workspace():
    with tempfile.TemporaryDirectory() as d:
        sim = Simulator(db_path=os.path.join(d, "sim.db"), root=d)
        r = sim.dry_run("tehlike", [
            {"name": "rm logs", "type": "shell", "command": "rm -rf /tmp/x"},
            {"name": "write dışarı", "type": "write", "path": "../disari.txt", "content": "x"},
        ])
        prevs = {p["action"]: p for p in r["action_previews"]}
        assert prevs["rm logs"]["risk_hint"] == "destructive-kelime içeriyor"
        assert prevs["write dışarı"]["file_change_preview"]["inside_workspace"] is False


def test_what_if_variants_and_history():
    with tempfile.TemporaryDirectory() as d:
        sim = Simulator(db_path=os.path.join(d, "sim.db"), root=d)
        w = sim.what_if("deploy", {"hızlı": [{"name": "shell push", "type": "shell", "command": "git push"}], "güvenli": [{"name": "read check", "type": "read"}]})
        assert set(w["variants"]) == {"hızlı", "güvenli"}
        h = sim.history()
        assert len(h) >= 2 and not any(x["executed"] for x in h)
        assert sim.mark_executed("deploy::hızlı") >= 1
        assert any(x["executed"] for x in sim.history())


def make_loop(tmp, **kw):
    return AutonomousGoalLoop(db_path=os.path.join(tmp, "a.db"), **kw)


def ok_exec(action):
    return {"ok": True, "output": f"done:{action.get('name')}", "cost": 0.01, "tokens": 10}


def test_loop_completes_plan_with_checkpoints():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        r = loop.run("hedef", [{"name": "adım1"}, {"name": "adım2"}], ok_exec, verifier=lambda res, goal: res.get("ok"))
        assert r["status"] == "COMPLETED" and r["iterations"] == 2
        assert r["stop_reason"] == "plan-exhausted"
        cps = loop.checkpoints(r["run_id"])
        assert len(cps) == 2 and not any(c["rolled_back"] for c in cps)


def test_loop_budget_iteration_limit():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        r = loop.run("sonsuz", [{"name": f"a{i}"} for i in range(10)], ok_exec, budgets=Budgets(max_iterations=3))
        assert r["status"] == "STOPPED_BUDGET" and r["stop_reason"] == "budget:iterations" and r["iterations"] == 3


def test_loop_budget_time_limit():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        def slow(a):
            time.sleep(0.35)
            return {"ok": True, "output": "yavaş"}
        r = loop.run("yavaş iş", [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}], slow, budgets=Budgets(max_iterations=10, max_seconds=0.8))
        assert r["status"] == "STOPPED_BUDGET" and "seconds" in r["stop_reason"]


def test_loop_cancel_stops_immediately():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        def cancelling_exec(a):
            loop.cancel(1)
            return {"ok": True, "output": "x"}
        r = loop.run("iptal", [{"name": "a"}, {"name": "b"}], cancelling_exec)
        assert r["status"] == "CANCELLED" and r["iterations"] == 1


def test_loop_verify_fail_without_replan_stops():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        r = loop.run("doğrulanamaz", [{"name": "a"}], ok_exec, verifier=lambda res, goal: False)
        assert r["status"] == "VERIFY_FAILED" and r["iterations"] == 1 and r["results"][0]["verified"] is False


def test_loop_replan_then_success():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        def exec_once_fails(action):
            if action.get("name") == "kırılgan":
                return {"ok": False, "error": "bağlantı yok"}
            return {"ok": True, "output": "tamam"}
        r = loop.run("dayanıklılık", [{"name": "kırılgan"}], exec_once_fails, verifier=lambda res, goal: res.get("ok"), replan_fn=lambda failed, result: [{"name": "dayanıklı"}])
        assert r["status"] == "COMPLETED" and r["replans"] == 1 and r["results"][-1]["action"] == "dayanıklı"


def test_loop_safety_deny_stops_run():
    with tempfile.TemporaryDirectory() as d:
        s = make_safety(d)
        loop = make_loop(d, safety=s)
        r = loop.run("tehlikeli", [{"name": "delete everything"}], ok_exec, policy_allows=False)
        assert r["status"] == "STOPPED_SAFETY" and "delete" in r["stop_reason"]


def test_loop_goal_completion_requires_evidence_path():
    with tempfile.TemporaryDirectory() as d:
        ge = GoalEngine(db_path=os.path.join(d, "g.db"))
        loop = make_loop(d, goal_engine=ge)
        gid = ge.create("otonom hedef")["goal"]
        r = loop.run("otonom hedef", [{"name": "adım"}], lambda a: {"ok": True, "output": "çıktı: 3 kayıt"}, verifier=lambda res, goal: True, goal_id=gid)
        assert r["status"] == "COMPLETED"
        g = ge._get(gid)
        assert g["status"] == "DONE" and "çıktı" in g["evidence"]


def test_checkpoint_rollback_restores_state():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        r = loop.run("cp", [{"name": "a"}, {"name": "b"}], ok_exec)
        cps = loop.checkpoints(r["run_id"])
        rb = loop.rollback_to(cps[0]["checkpoint_id"])
        assert rb["ok"] and rb["restored_state"]["done"] == ["a"]
        assert loop.checkpoints(r["run_id"])[0]["rolled_back"] is True
        assert loop.rollback_to(9999)["ok"] is False


def test_run_status_persisted():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        r = loop.run("kalıcı", [{"name": "x"}], ok_exec)
        st = loop.run_status(r["run_id"])
        assert st["status"] == "COMPLETED" and st["iterations"] == 1 and st["result"]["budget"]["limits"]["iterations"] > 0


def test_recovery_retries_only_explicitly_idempotent_action():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        calls = {"n": 0}
        def flaky(action):
            calls["n"] += 1
            return {"ok": calls["n"] > 1, "output": "ok" if calls["n"] > 1 else "temporary"}
        r = loop.run("retry", [{"name": "read", "idempotent": True}], flaky)
        assert r["status"] == "COMPLETED"
        assert r["results"][0]["attempts"] == 2 and r["results"][0]["retries"] == 1


def test_recovery_never_retries_non_idempotent_action():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        calls = {"n": 0}
        def fail(action):
            calls["n"] += 1
            return {"ok": False, "error": "temporary"}
        r = loop.run("no blind retry", [{"name": "write", "idempotent": False}], fail)
        assert r["status"] == "COMPLETED" and calls["n"] == 1
        assert r["results"][0]["attempts"] == 1 and r["results"][0]["retries"] == 0


def test_checkpoint_retention_is_bounded():
    with tempfile.TemporaryDirectory() as d:
        loop = make_loop(d)
        loop.MAX_CHECKPOINTS_PER_RUN = 3
        r = loop.run("bounded", [{"name": str(i)} for i in range(6)], ok_exec)
        assert len(loop.checkpoints(r["run_id"])) == 3
