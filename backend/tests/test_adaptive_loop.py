import asyncio

from app.agent.adaptive_loop import AdaptiveAgentLoop, AdaptiveLimits


def test_execute_must_be_verified_before_success():
    events = []
    checks = []

    async def execute(plan):
        events.append(("execute", plan))
        return {"ok": True, "output": "done"}

    async def verify(plan, result):
        events.append(("verify", result["output"]))
        return True

    loop = AdaptiveAgentLoop(
        task_id="t1",
        initial_plan={"step": 1},
        execute=execute,
        verify=verify,
        replan=lambda *_: None,
        capability_check=lambda plan: checks.append(("cap", plan)) or True,
        approval_check=lambda plan: checks.append(("approval", plan)) or True,
    )
    run = asyncio.run(loop.run_once())

    assert run.status == "SUCCEEDED"
    assert [x[0] for x in events] == ["execute", "verify"]
    assert [x[0] for x in checks] == ["cap", "approval"]


def test_failed_verification_replans_with_bounded_limit():
    plans = []
    replans = []

    def execute(plan):
        plans.append(plan)
        return {"ok": True, "output": plan["version"]}

    def verify(plan, result):
        return result["output"] == 2

    def replan(plan, result, reason):
        replans.append((plan["version"], reason))
        if plan["version"] == 1:
            return {"version": 2}
        return None

    loop = AdaptiveAgentLoop(
        task_id="t2",
        initial_plan={"version": 1},
        execute=execute,
        verify=verify,
        replan=replan,
        limits=AdaptiveLimits(max_replans=1, max_attempts=2),
    )
    run = asyncio.run(loop.run_once())

    assert run.status == "SUCCEEDED"
    assert plans == [{"version": 1}, {"version": 2}]
    assert run.replans == 1
    assert len(replans) == 1


def test_capability_is_rechecked_and_unavailable_never_executes():
    executed = []
    audited = []

    loop = AdaptiveAgentLoop(
        task_id="t3",
        initial_plan={"tool": "browser"},
        execute=lambda plan: executed.append(plan) or {"ok": True},
        verify=lambda *_: True,
        replan=lambda *_: None,
        capability_check=lambda _plan: False,
        audit=lambda event, payload: audited.append((event, payload)),
    )
    run = asyncio.run(loop.run_once())

    assert run.status == "CAPABILITY_UNAVAILABLE"
    assert executed == []
    assert audited[-1][0] == "loop.stopped"


def test_approval_is_rechecked_before_each_attempt():
    approvals = []
    executed = []

    loop = AdaptiveAgentLoop(
        task_id="t4",
        initial_plan={"dangerous": True},
        execute=lambda plan: executed.append(plan) or {"ok": True},
        verify=lambda *_: True,
        replan=lambda *_: None,
        approval_check=lambda plan: approvals.append(plan) or False,
    )
    run = asyncio.run(loop.run_once())

    assert run.status == "APPROVAL_REQUIRED"
    assert executed == []
    assert len(approvals) == 1


def test_executor_ok_without_verification_cannot_fake_success():
    loop = AdaptiveAgentLoop(
        task_id="t5",
        initial_plan={"step": 1},
        execute=lambda _plan: {"ok": True},
        verify=lambda *_: False,
        replan=lambda *_: None,
    )
    run = asyncio.run(loop.run_once())

    assert run.status == "NO_REPLAN"
    assert run.result is None
