import asyncio

from app.agent.adaptive_loop import AdaptiveLimits
from app.agent.adaptive_supervisor import AdaptiveSupervisorAdapter


class FakeSupervisor:
    def __init__(self):
        self.calls = []

    async def execute(self, task_id, workers, executors, **kwargs):
        self.calls.append((task_id, workers, executors))
        return {"ok": True, "attempt": len(self.calls)}


def test_adapter_replans_real_supervisor_execution():
    supervisor = FakeSupervisor()
    plans = []
    audits = []

    def build_workers(plan):
        plans.append(("workers", plan))
        return [plan]

    def build_executors(plan):
        plans.append(("executors", plan))
        return {"role": lambda *_: {"ok": True}}

    def verify(report, plan):
        return report["attempt"] == 2

    def replan(plan, report, reason):
        return {"version": 2} if plan["version"] == 1 else None

    adapter = AdaptiveSupervisorAdapter(
        supervisor,
        build_workers=build_workers,
        build_executors=build_executors,
        verify_report=verify,
        replan=replan,
        limits=AdaptiveLimits(max_replans=1, max_attempts=2),
        audit=lambda event, payload: audits.append((event, payload)),
    )

    run = asyncio.run(adapter.run("task-36", {"version": 1}))

    assert run.status == "SUCCEEDED"
    assert run.attempts == 2
    assert run.replans == 1
    assert len(supervisor.calls) == 2
    assert any(event == "plan.replanned" for event, _ in audits)


def test_adapter_capability_failure_blocks_supervisor():
    supervisor = FakeSupervisor()
    adapter = AdaptiveSupervisorAdapter(
        supervisor,
        build_workers=lambda plan: [plan],
        build_executors=lambda plan: {},
        verify_report=lambda report, plan: True,
        replan=lambda *args: None,
        capability_check=lambda plan: False,
    )

    run = asyncio.run(adapter.run("task-36-blocked", {"tool": "browser"}))

    assert run.status == "CAPABILITY_UNAVAILABLE"
    assert supervisor.calls == []


def test_adapter_approval_failure_blocks_supervisor():
    supervisor = FakeSupervisor()
    adapter = AdaptiveSupervisorAdapter(
        supervisor,
        build_workers=lambda plan: [plan],
        build_executors=lambda plan: {},
        verify_report=lambda report, plan: True,
        replan=lambda *args: None,
        approval_check=lambda plan: False,
    )

    run = asyncio.run(adapter.run("task-36-approval", {"dangerous": True}))

    assert run.status == "APPROVAL_REQUIRED"
    assert supervisor.calls == []
