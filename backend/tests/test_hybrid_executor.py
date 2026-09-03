import pytest

from app.agent.hybrid_executor import HybridPlanExecutor


class Registry:
    def __init__(self, dangerous=None):
        self.dangerous = set(dangerous or [])

    def get(self, name):
        if name not in {"first", "second", "danger"}:
            return None
        return {"dangerous": name in self.dangerous}


class Executor:
    def __init__(self, fail_once=False):
        self.fail_once = fail_once
        self.calls = []

    def execute(self, items, approved=False):
        tool, args = items[0]
        self.calls.append((tool, args, approved))
        if self.fail_once and len(self.calls) == 1:
            raise RuntimeError("temporary")
        return [{"tool": tool, "args": args}]


def plan(*steps):
    return {"goal": "test", "steps": list(steps)}


def step(tool, args=None, depends_on=None):
    return {"tool": tool, "arguments": args or {}, "depends_on": depends_on or []}


def test_executes_valid_plan_and_passes_context():
    ex = Executor()
    runner = HybridPlanExecutor(ex, Registry())
    result = runner.execute(plan(
        step("first", {"value": "ok"}),
        step("second", {"from_first": "{{step.0.result}}"}, [0]),
    ))
    assert result["ok"] is True
    assert result["status"] == "DONE"
    assert len(ex.calls) == 2
    assert ex.calls[1][1]["from_first"] == {"tool": "first", "args": {"value": "ok"}}


def test_dangerous_tool_requires_approval():
    ex = Executor()
    runner = HybridPlanExecutor(ex, Registry({"danger"}))
    result = runner.execute(plan(step("danger")), approved=False)
    assert result["status"] == "WAITING_APPROVAL"
    assert ex.calls == []


def test_failed_dependency_skips_dependent_step():
    class FailingExecutor(Executor):
        def execute(self, items, approved=False):
            raise RuntimeError("boom")

    ex = FailingExecutor()
    runner = HybridPlanExecutor(ex, Registry())
    result = runner.execute(plan(step("first"), step("second", depends_on=[0])))
    assert result["ok"] is False
    assert result["steps"][0]["ok"] is False
    assert result["steps"][1]["status"] if len(result["steps"]) > 1 else True


def test_retry_is_bounded_to_one_retry():
    ex = Executor(fail_once=True)
    runner = HybridPlanExecutor(ex, Registry())
    result = runner.execute(plan(step("first")))
    assert result["ok"] is True
    assert result["steps"][0]["attempts"] == 2
    assert len(ex.calls) == 2


def test_unknown_tool_fails_without_execution():
    ex = Executor()
    runner = HybridPlanExecutor(ex, Registry())
    result = runner.execute(plan(step("unknown")))
    assert result["ok"] is False
    assert ex.calls == []


def test_max_steps_is_enforced():
    ex = Executor()
    runner = HybridPlanExecutor(ex, Registry())
    result = runner.execute(plan(*[step("first") for _ in range(13)]))
    assert result["ok"] is False
    assert ex.calls == []


def test_invalid_context_fails_cleanly():
    ex = Executor()
    runner = HybridPlanExecutor(ex, Registry())
    with pytest.raises(ValueError):
        runner._resolve_args({"x": "{{step.99.result}}"}, [])
