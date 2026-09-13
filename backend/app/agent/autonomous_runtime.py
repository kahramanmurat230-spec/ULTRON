"""Runtime adapter for ULTRON's bounded autonomous task coordinator.

The adapter deliberately owns the approval boundary: callers provide the
runtime approval state, while the model/tool arguments cannot self-authorize.
"""
from __future__ import annotations

from app.agent.autonomous_task import AutonomousTask


class AutonomousRuntimeAdapter:
    """Expose AutonomousTask with a narrow, auditable runtime interface."""

    def __init__(self, code_agent, audit=None):
        self.task = AutonomousTask(code_agent, audit=audit)

    def run(self, goal, paths=None, *, runtime_approved=False,
            max_iterations=3, timeout=180):
        return self.task.run(
            goal=goal,
            paths=paths,
            approved=bool(runtime_approved),
            max_iterations=max_iterations,
            timeout=timeout,
        )
