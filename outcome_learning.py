"""Level 37 — bounded outcome learning for future planning.

Records verified task outcomes as advisory memory. Learned data never grants
permissions, changes risk, or authorizes tools.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Outcome:
    task_id: str
    goal: str
    status: str
    attempts: int
    replans: int
    summary: str


class OutcomeLearner:
    """Persist only bounded, verified outcomes through an injected memory API."""

    MAX_TEXT = 1200

    def __init__(self, memory: Any = None, audit: Callable[[str, dict], Any] | None = None):
        self.memory = memory
        self.audit = audit or (lambda _event, _payload: None)

    def learn(self, run: Any, goal: str = "") -> Outcome | None:
        if run is None or getattr(run, "status", None) != "SUCCEEDED":
            return None
        result = getattr(run, "result", None)
        if not isinstance(result, dict):
            return None
        report = result.get("report", result)
        summary = " ".join(str(report).split())[: self.MAX_TEXT]
        outcome = Outcome(
            task_id=str(getattr(run, "task_id", ""))[:200],
            goal=str(goal)[:500],
            status="SUCCEEDED",
            attempts=int(getattr(run, "attempts", 0)),
            replans=int(getattr(run, "replans", 0)),
            summary=summary,
        )
        if self.memory is not None:
            text = (
                f"Verified outcome task={outcome.task_id}; goal={outcome.goal}; "
                f"status={outcome.status}; attempts={outcome.attempts}; "
                f"replans={outcome.replans}; result={outcome.summary}"
            )
            self.memory.add("task_outcome", text[: self.MAX_TEXT])
        self.audit("LEARNING.OUTCOME_RECORDED", {
            "task_id": outcome.task_id,
            "attempts": outcome.attempts,
            "replans": outcome.replans,
        })
        return outcome
