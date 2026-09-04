import pytest

from app.agent.adaptive_loop import AdaptiveRun
from app.agent.outcome_learning import OutcomeLearner


class Memory:
    def __init__(self):
        self.rows = []

    def add(self, kind, text):
        self.rows.append((kind, text))


def test_only_verified_success_is_learned():
    memory = Memory()
    learner = OutcomeLearner(memory)
    failed = AdaptiveRun("x", {}, status="NO_REPLAN", result={"ok": False})
    assert learner.learn(failed, "goal") is None
    assert memory.rows == []


def test_verified_outcome_is_bounded_and_advisory():
    memory = Memory()
    events = []
    learner = OutcomeLearner(memory, lambda event, payload: events.append((event, payload)))
    run = AdaptiveRun("task-1", {}, attempts=2, replans=1, status="SUCCEEDED", result={"ok": True, "report": "A" * 5000})
    outcome = learner.learn(run, "build the project")
    assert outcome is not None
    assert outcome.status == "SUCCEEDED"
    assert len(memory.rows) == 1
    assert memory.rows[0][0] == "task_outcome"
    assert len(memory.rows[0][1]) <= learner.MAX_TEXT
    assert events[0][0] == "LEARNING.OUTCOME_RECORDED"


def test_missing_result_is_not_learned():
    memory = Memory()
    learner = OutcomeLearner(memory)
    run = AdaptiveRun("x", {}, status="SUCCEEDED", result=None)
    assert learner.learn(run) is None
    assert memory.rows == []
