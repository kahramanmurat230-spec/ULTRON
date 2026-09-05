from app.agent.long_task import LongTaskEngine


def test_long_task_checkpoint_and_resume(tmp_path):
    path = tmp_path / "checkpoint.json"
    engine = LongTaskEngine(path, max_retries=0)
    calls = []
    steps = [{"name": "a"}, {"name": "b"}]

    def execute(step):
        calls.append(step["name"])
        if step["name"] == "b":
            raise RuntimeError("temporary")
        return "ok"

    first = engine.run(steps, execute)
    assert first["status"] == "ERROR"
    assert first["failed_step"] == 1
    assert first["next_step"] == 1

    resumed = engine.run(steps, lambda step: "recovered")
    assert resumed["status"] == "DONE"
    assert resumed["next_step"] == 2
    assert calls == ["a", "b"]


def test_long_task_bounded_retry(tmp_path):
    engine = LongTaskEngine(tmp_path / "checkpoint.json", max_retries=1)
    attempts = [0]

    def execute(_):
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("retryable")
        return "ok"

    result = engine.run([{"retryable": True}], execute)
    assert result["status"] == "DONE"
    assert attempts[0] == 2


def test_long_task_deadline(tmp_path):
    ticks = iter([0.0, 0.0, 2.0])
    engine = LongTaskEngine(tmp_path / "checkpoint.json", clock=lambda: next(ticks))
    result = engine.run([{"name": "slow"}], lambda _: "ok", deadline_s=1)
    assert result["status"] == "CANCELLED"
    assert result["reason"] == "hard_deadline"


def test_long_task_step_limit(tmp_path):
    engine = LongTaskEngine(tmp_path / "checkpoint.json", max_steps=1)
    try:
        engine.run([{}, {}], lambda _: None)
    except ValueError as exc:
        assert "step limit" in str(exc)
    else:
        raise AssertionError("step limit was not enforced")
