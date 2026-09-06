"""Self-Coding Agent 2.0 orchestration facade.

Keeps self-coding bounded, approval-gated, auditable, and test-verified while
reusing the existing CodeAgent safety boundary and rollback implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.code_agent.code_agent import CodeAgent


@dataclass(frozen=True)
class SelfCodingLimits:
    max_iterations: int = 3
    max_patch_files: int = 8
    max_changed_lines: int = 4000
    max_test_timeout: float = 180.0


class SelfCodingAgent:
    """Public 2.0 facade for analyze -> patch -> verify -> repair."""

    def __init__(self, brain, root, audit=None, limits: SelfCodingLimits | None = None):
        self.limits = limits or SelfCodingLimits()
        self.code_agent = CodeAgent(brain, root, audit=audit)
        self.code_agent.MAX_REPAIR_ITERATIONS = self.limits.max_iterations
        self.code_agent.MAX_PATCH_FILES = self.limits.max_patch_files
        self.code_agent.MAX_CHANGED_LINES = self.limits.max_changed_lines
        self.code_agent.MAX_TEST_TIMEOUT = self.limits.max_test_timeout

    @property
    def root(self):
        return self.code_agent.root

    def analyze(self, paths=None, goal=None, feedback=None):
        return self.code_agent.analyze(paths, goal=goal, feedback=feedback)

    def propose(self, paths=None, goal=None, feedback=None):
        return self.code_agent.propose_patch(paths, goal=goal, feedback=feedback)

    def verify(self, timeout=None):
        return self.code_agent.test(timeout or self.limits.max_test_timeout)

    def repair(self, paths=None, goal=None, approved=False, max_iterations=None, timeout=None):
        return self.code_agent.self_coding_loop(
            paths=paths,
            goal=goal,
            approved=approved,
            max_iterations=max_iterations or self.limits.max_iterations,
            timeout=timeout or self.limits.max_test_timeout,
        )

    def run(self, paths=None, goal=None, approved=False, max_iterations=None, timeout=None) -> dict[str, Any]:
        """Execute a bounded self-coding cycle; mutation always needs approval."""
        if not approved:
            return {
                "ok": False,
                "status": "APPROVAL_REQUIRED",
                "phase": "APPROVAL",
                "iterations": [],
            }
        result = self.repair(paths, goal, approved=True, max_iterations=max_iterations, timeout=timeout)
        result["phase"] = "VERIFIED" if result.get("ok") else "STOPPED"
        return result
