"""PHASE 3: persistent long-running task engine — budgets, retries, approval,
recovery, resume across restarts and durable terminal reports."""
import asyncio
import os

from app.tasks.engine import TaskEngine, NeedsApproval, InvalidTransition


def make_engine(tmp, event_cb=None):
    return TaskEngine(db_path=os.path.join(tmp, "tasks.db"), event_cb=event_cb)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- CRUD/state
def test_create_and_persist(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("goal A", kind="supervisor", steps=[{"label": "s1", "worker": "w"}])
    assert t["status"] == "PENDING"
    e2 = make_engine(str(tmp_path))
    t2 = e2.get(t["id"])
    assert t2 and t2["goal"] == "goal A" and t2["steps"][0]["worker"] == "w"


def test_invalid_transition_rejected(tmp_path):
    e = make_engine(str(tmp_path)); t = e.create("g")
    try:
        e._save(t, "COMPLETED")
        raise AssertionError("PENDING->COMPLETED must be rejected")
    except InvalidTransition:
        pass


def test_cancel_terminal(tmp_path):
    e = make_engine(str(tmp_path)); t = e.create("g")
    assert e.cancel(t["id"])["ok"]
    assert e.cancel(t["id"])["ok"] is False


# ---------------------------------------------------------------- execution
def test_execute_completes_and_checkpoints(tmp_path):
    e = make_engine(str(tmp_path))

    async def runner(task, step, ctx):
        return {"ok": True, "output": f"done {step['label']}"}

    t = e.create("g", steps=[{"label": "a", "worker": "x"}, {"label": "b", "worker": "y"}])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert [s["status"] for s in out["steps"]] == ["SUCCESS", "SUCCESS"]
    assert out["checkpoint"]["last_successful_step"] == "b"
    assert out["result"]["ok"] is True


def test_final_report_and_evaluation_are_durable(tmp_path):
    e = make_engine(str(tmp_path))

    async def runner(task, step, ctx):
        if step["label"] == "report":
            ctx["final_report"] = "ULTRON FINAL REPORT\nVERIFICATION: PASS\nEVIDENCE: tests passed"
            ctx["local_evaluation"] = {"winner": "qwen3:4b", "winner_score": 91.5}
            return {"ok": True, "output": ctx["final_report"]}
        return {"ok": True, "output": "worker output"}

    t = e.create("supervisor goal", kind="supervisor",
                 steps=[{"label": "work", "worker": "worker"},
                        {"label": "report", "worker": "report"}])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert out["result"]["final_report"].startswith("ULTRON FINAL REPORT")
    assert out["result"]["local_evaluation"]["winner"] == "qwen3:4b"

    fresh = make_engine(str(tmp_path)).get(t["id"])
    assert fresh["result"]["final_report"] == out["result"]["final_report"]
    assert fresh["result"]["local_evaluation"]["winner_score"] == 91.5


def test_flaky_step_retried_within_budget(tmp_path):
    events = []
    e = make_engine(str(tmp_path), event_cb=lambda ev: events.append(ev))
    calls = {"n": 0}

    async def flaky(task, step, ctx):
        calls["n"] += 1
        if calls["n"] == 1: raise RuntimeError("transient failure")
        return {"ok": True, "output": "recovered"}

    t = e.create("g", steps=[{"label": "flaky", "worker": "w"}])
    out = run(e.execute(t["id"], flaky))
    assert out["status"] == "COMPLETED" and calls["n"] == 2
    assert any(ev["status"] == "STEP_ERROR" for ev in events)


def test_critical_failure_fails_task(tmp_path):
    e = make_engine(str(tmp_path))
    async def bad(task, step, ctx): raise RuntimeError("boom")
    t = e.create("g", steps=[{"label": "x", "worker": "w", "critical": True}])
    out = run(e.execute(t["id"], bad))
    assert out["status"] == "FAILED" and "boom" in (out["error"] or "")


def test_noncritical_failure_continues(tmp_path):
    e = make_engine(str(tmp_path))
    async def runner(task, step, ctx):
        if step["label"] == "optional": raise RuntimeError("skip me")
        return {"ok": True, "output": "ok"}
    t = e.create("g", steps=[{"label": "optional", "worker": "w", "critical": False}, {"label": "must", "worker": "w"}])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert out["steps"][0]["status"] == "FAILED" and out["steps"][1]["status"] == "SUCCESS"


def test_overall_timeout_budget(tmp_path):
    e = make_engine(str(tmp_path))
    async def slow(task, step, ctx): await asyncio.sleep(5); return {"ok": True}
    t = e.create("g", steps=[{"label": "s", "worker": "w"}], budgets={"timeout_s": 0.2, "step_timeout_s": 5})
    out = run(e.execute(t["id"], slow))
    assert out["status"] == "FAILED" and "timeout" in (out["error"] or "").lower()


def test_step_timeout_budget(tmp_path):
    e = make_engine(str(tmp_path))
    async def slow(task, step, ctx): await asyncio.sleep(10); return {"ok": True}
    t = e.create("g", steps=[{"label": "s", "worker": "w"}], budgets={"step_timeout_s": 0.2, "retry_budget": 0})
    assert run(e.execute(t["id"], slow))["status"] == "FAILED"


def test_tool_budget_exhausted(tmp_path):
    e = make_engine(str(tmp_path))
    async def runner(task, step, ctx): return {"ok": True}
    t = e.create("g", steps=[{"label": "s1", "worker": "w"}, {"label": "s2", "worker": "w"}], budgets={"tool_budget": 1})
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "FAILED" and "tool budget" in (out["error"] or "")


# ---------------------------------------------------------------- approval
def test_needs_approval_pauses_and_resumes(tmp_path):
    events = []; e = make_engine(str(tmp_path), event_cb=lambda ev: events.append(ev)); approved = {"flag": False}
    async def gated(task, step, ctx):
        if not approved["flag"]: raise NeedsApproval("dangerous step", risks=["shell:rm"])
        return {"ok": True, "output": "ran after approval"}
    t = e.create("g", steps=[{"label": "gated", "worker": "w"}])
    out = run(e.execute(t["id"], gated))
    assert out["status"] == "WAITING_APPROVAL" and any(ev["status"] == "APPROVAL_REQUIRED" for ev in events)
    assert e.approve(t["id"])["ok"]; approved["flag"] = True
    assert run(e.execute(t["id"], gated))["status"] == "COMPLETED"


# ---------------------------------------------------------------- recovery
def test_recover_incomplete_marks_running_as_recovering(tmp_path):
    e = make_engine(str(tmp_path)); t = e.create("g"); e._save(t, "RUNNING")
    ids = e.recover_incomplete()
    assert t["id"] in ids and e.get(t["id"])["status"] == "RECOVERING"


def test_resume_from_checkpoint_skips_done_steps(tmp_path):
    e = make_engine(str(tmp_path)); ran = []
    async def runner(task, step, ctx): ran.append(step["label"]); return {"ok": True}
    t = e.create("g", steps=[{"label": "one", "worker": "w"}, {"label": "two", "worker": "w"}])
    task = e.get(t["id"]); task["steps"][0]["status"] = "SUCCESS"; task["current_step"] = 1
    task["checkpoint"] = {"last_successful_step": "one"}; e._save(task, "RECOVERING")
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED" and ran == ["two"]
