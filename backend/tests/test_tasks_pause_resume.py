"""P0-2 / P0-3 regression tests: TaskEngine status filtering + pause/resume.

Contract locked here:
- list(limit, status) filters by the persisted status (the /api/tasks contract).
- pause() is only valid on a RUNNING task; anything else is an honest error.
- pause is cooperative: the in-flight step finishes, its result is persisted,
  and execute() stops at a step boundary with the REAL PAUSED status.
- resume() re-enters execution at the first non-SUCCESS step and never
  re-runs already-successful steps (idempotent continuation).
- pause state survives an engine restart (persisted, not in-memory).
"""
import asyncio
import os

from app.tasks.engine import TaskEngine


def make_engine(tmp_path, event_cb=None):
    return TaskEngine(db_path=os.path.join(str(tmp_path), "tasks.db"), event_cb=event_cb)


def run(coro):
    return asyncio.run(coro)


def set_status(engine, task_id, *path):
    """Walk a legal transition path (the FSM rejects shortcuts)."""
    for target in path:
        engine._save(engine.get(task_id), target)


# ------------------------------------------------------------- P0-2: list
def test_list_filters_by_status(tmp_path):
    e = make_engine(tmp_path)
    a = e.create("goal A", kind="supervisor")
    b = e.create("goal B", kind="supervisor")
    set_status(e, b["id"], "RUNNING")
    c = e.create("goal C", kind="supervisor")
    set_status(e, c["id"], "RUNNING", "COMPLETED")

    all_ids = {t["id"] for t in e.list(limit=10)}
    assert all_ids == {a["id"], b["id"], c["id"]}

    running = e.list(limit=10, status="RUNNING")
    assert [t["id"] for t in running] == [b["id"]]
    assert all(t["status"] == "RUNNING" for t in running)

    completed = e.list(limit=10, status="COMPLETED")
    assert [t["id"] for t in completed] == [c["id"]]

    pending = e.list(limit=10, status="PENDING")
    assert [t["id"] for t in pending] == [a["id"]]

    assert e.list(limit=10, status="DEAD_LETTER") == []


def test_list_status_filter_matches_server_contract(tmp_path):
    """The exact call shape the /api/tasks handler uses must work."""
    e = make_engine(tmp_path)
    t = e.create("goal", kind="supervisor")
    set_status(e, t["id"], "RUNNING", "PAUSED")
    rows = e.list(status="PAUSED", limit=50)  # keyword args, server order
    assert len(rows) == 1 and rows[0]["status"] == "PAUSED"


# ---------------------------------------------------------- P0-3: pause
def test_pause_requires_running_status(tmp_path):
    e = make_engine(tmp_path)
    t = e.create("goal", steps=[{"label": "s1", "worker": "w"}])
    assert t["status"] == "PENDING"
    res = e.pause(t["id"])
    assert res["ok"] is False and "not RUNNING" in res["error"]
    set_status(e, t["id"], "RUNNING", "COMPLETED")
    res = e.pause(t["id"])
    assert res["ok"] is False
    assert e.pause("no-such-id")["ok"] is False


def test_pause_running_task_stops_between_steps_and_persists(tmp_path):
    e = make_engine(tmp_path)
    calls = []

    async def runner(task, step, ctx):
        calls.append(step["label"])
        if step["label"] == "s2":
            # external pause arrives while this step is in flight
            assert e.pause(task["id"])["ok"] is True
        return {"ok": True, "output": f"done {step['label']}"}

    t = e.create("goal", steps=[{"label": "s1", "worker": "w"},
                                {"label": "s2", "worker": "w"},
                                {"label": "s3", "worker": "w"}])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "PAUSED"
    assert calls == ["s1", "s2"]          # s3 never started
    statuses = [s["status"] for s in out["steps"]]
    assert statuses == ["SUCCESS", "SUCCESS", "PENDING"]
    assert out["checkpoint"]["last_successful_step"] == "s2"

    # persisted across an engine restart — not an in-memory fake
    e2 = make_engine(tmp_path)
    t2 = e2.get(t["id"])
    assert t2["status"] == "PAUSED"
    assert [s["status"] for s in t2["steps"]] == ["SUCCESS", "SUCCESS", "PENDING"]


def test_resume_continues_without_rerunning_successful_steps(tmp_path):
    e = make_engine(tmp_path)
    calls = []

    async def runner(task, step, ctx):
        calls.append(step["label"])
        if step["label"] == "s2":
            e.pause(task["id"])
        return {"ok": True, "output": f"done {step['label']}"}

    t = e.create("goal", steps=[{"label": "s1", "worker": "w"},
                                {"label": "s2", "worker": "w"},
                                {"label": "s3", "worker": "w"}])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "PAUSED"

    res = e.resume(t["id"])
    assert res["ok"] is True
    assert res["status"] == "RUNNING"
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert calls == ["s1", "s2", "s3"]     # s1/s2 not re-run after resume
    assert out["result"]["ok"] is True


def test_resume_requires_paused_status(tmp_path):
    e = make_engine(tmp_path)
    t = e.create("goal")
    res = e.resume(t["id"])
    assert res["ok"] is False and "not PAUSED" in res["error"]


def test_pause_between_retry_attempts_leaves_step_pending(tmp_path):
    e = make_engine(tmp_path)
    attempts = []

    async def runner(task, step, ctx):
        attempts.append((step["label"], step["attempts"]))
        if step["label"] == "flaky":
            if step["attempts"] == 1:
                # pause lands between attempt 1 (failed) and attempt 2
                e.pause(task["id"])
                raise RuntimeError("flaky failure")
            return {"ok": True, "output": "recovered"}
        return {"ok": True, "output": "ok"}

    t = e.create("goal", steps=[{"label": "flaky", "worker": "w"}],
                 budgets={"retry_budget": 2})
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "PAUSED"
    assert out["steps"][0]["status"] == "PENDING"   # not faked as SUCCESS/FAILED
    assert out["steps"][0]["attempts"] == 1

    e.resume(t["id"])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "COMPLETED"
    assert out["steps"][0]["status"] == "SUCCESS"
    assert ("flaky", 2) in attempts


def test_paused_task_is_cancellable(tmp_path):
    e = make_engine(tmp_path)

    async def runner(task, step, ctx):
        if step["label"] == "s1":
            e.pause(task["id"])
        return {"ok": True, "output": "done"}

    t = e.create("goal", steps=[{"label": "s1", "worker": "w"},
                                {"label": "s2", "worker": "w"}])
    out = run(e.execute(t["id"], runner))
    assert out["status"] == "PAUSED"
    assert e.cancel(t["id"])["ok"] is True
    assert e.get(t["id"])["status"] == "CANCELLED"


def test_pause_resume_journalled(tmp_path):
    events = []
    e = make_engine(tmp_path, event_cb=events.append)

    async def runner(task, step, ctx):
        if step["label"] == "s1":
            e.pause(task["id"])
        return {"ok": True, "output": "done"}

    t = e.create("goal", steps=[{"label": "s1", "worker": "w"},
                                {"label": "s2", "worker": "w"}])
    run(e.execute(t["id"], runner))
    e.resume(t["id"])
    rows = [r["status"] for r in e.journal_rows(t["id"])]
    assert "TASK_PAUSED" in rows
    assert "TASK_RESUMED" in rows
    emitted = [ev["status"] for ev in events]
    assert "TASK_PAUSED" in emitted and "TASK_RESUMED" in emitted


def test_approve_rejected_outside_approval_gate(tmp_path):
    """Approval is ONLY for WAITING_APPROVAL tasks (final-audit finding:
    approving a FAILED task resurrected it and leaked a stale one-shot
    user_approved=True into a later retry)."""
    e = make_engine(tmp_path)
    t = e.create("goal", kind="plan", steps=[{"label": "s", "worker": "w"}])
    set_status(e, t["id"], "RUNNING", "FAILED")
    res = e.approve(t["id"])
    assert res["ok"] is False and "not WAITING_APPROVAL" in res["error"]
    assert e.get(t["id"])["user_approved"] is False
    # RUNNING task also cannot be approved
    t2 = e.create("goal2", kind="plan", steps=[{"label": "s", "worker": "w"}])
    set_status(e, t2["id"], "RUNNING")
    assert e.approve(t2["id"])["ok"] is False
