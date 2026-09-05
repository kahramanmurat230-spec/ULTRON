"""Level 36 — bounded Plan → Execute → Verify → Replan loop.

The loop is intentionally callback-based so existing Supervisor/Executor,
risk, approval and capability implementations remain authoritative. This
layer coordinates them; it does not grant permissions or fabricate success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class AdaptiveLoopError(RuntimeError):
    """Base error for adaptive-loop contract violations."""


@dataclass
class AdaptiveLimits:
    """Hard bounds for autonomous recovery."""

    max_replans: int = 3
    max_attempts: int = 4

    def __post_init__(self) -> None:
        self.max_replans = max(0, int(self.max_replans))
        self.max_attempts = max(1, int(self.max_attempts))


@dataclass
class AdaptiveRun:
    """Auditable state for one adaptive task."""

    task_id: str
    plan: Any
    attempts: int = 0
    replans: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    status: str = "RUNNING"
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "attempts": self.attempts,
            "replans": self.replans,
            "history": list(self.history),
            "result": self.result,
            "error": self.error,
        }


async def _maybe_await(value: Any) -> Any:
    if isinstance(value, Awaitable):
        return await value
    return value


class AdaptiveAgentLoop:
    """Coordinate bounded execution, verification and replanning.

    Callbacks are deliberately explicit:
      - execute(plan) performs the already-authorized execution path.
      - verify(plan, result) decides whether the observed outcome satisfies
        the plan. A false value is never converted into success.
      - replan(plan, result, reason) returns a replacement plan or None.
      - capability_check(plan) must be true immediately before execution.
      - approval_check(plan) must be true immediately before execution.

    This class never calls tools directly and never bypasses security gates.
    """

    def __init__(
        self,
        *,
        task_id: str,
        initial_plan: Any,
        execute: Callable[[Any], Any],
        verify: Callable[[Any, Any], Any],
        replan: Callable[[Any, Any, str], Any],
        capability_check: Callable[[Any], Any] | None = None,
        approval_check: Callable[[Any], Any] | None = None,
        limits: AdaptiveLimits | None = None,
        audit: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.run = AdaptiveRun(task_id=task_id, plan=initial_plan)
        self.execute = execute
        self.verify = verify
        self.replan = replan
        self.capability_check = capability_check or (lambda _plan: True)
        self.approval_check = approval_check or (lambda _plan: True)
        self.limits = limits or AdaptiveLimits()
        self.audit = audit or (lambda _event, _payload: None)

    async def _check(self, callback: Callable[[Any], Any], plan: Any) -> bool:
        return bool(await _maybe_await(callback(plan)))

    async def run_once(self) -> AdaptiveRun:
        """Run until verified success or a bounded terminal failure."""
        while self.run.attempts < self.limits.max_attempts:
            plan = self.run.plan

            # Re-check both gates immediately before every execution attempt.
            if not await self._check(self.capability_check, plan):
                return self._stop("CAPABILITY_UNAVAILABLE", "plan capability unavailable")
            if not await self._check(self.approval_check, plan):
                return self._stop("APPROVAL_REQUIRED", "plan requires approval")

            self.run.attempts += 1
            self._record("execution.started", {"attempt": self.run.attempts})
            try:
                result = await _maybe_await(self.execute(plan))
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

            if not isinstance(result, dict):
                result = {"ok": False, "error": "executor returned non-object result"}

            # Execution success is not task success: verification is mandatory.
            verified = bool(await _maybe_await(self.verify(plan, result))) if result.get("ok") else False
            if verified:
                self.run.result = result
                self.run.status = "SUCCEEDED"
                self._record("execution.verified", {"attempt": self.run.attempts})
                return self.run

            reason = str(result.get("error") or "verification failed")
            self._record("execution.unverified", {"attempt": self.run.attempts, "reason": reason[:300]})

            if self.run.replans >= self.limits.max_replans:
                return self._stop("REPLAN_LIMIT", reason)

            next_plan = await _maybe_await(self.replan(plan, result, reason))
            if next_plan is None:
                return self._stop("NO_REPLAN", reason)

            self.run.replans += 1
            self.run.plan = next_plan
            self._record("plan.replanned", {"replan": self.run.replans, "reason": reason[:300]})

        return self._stop("ATTEMPT_LIMIT", "adaptive execution attempt limit reached")

    def _record(self, event: str, payload: dict[str, Any]) -> None:
        self.run.history.append({"event": event, **payload})
        self.audit(event, {"task_id": self.run.task_id, **payload})

    def _stop(self, status: str, error: str) -> AdaptiveRun:
        self.run.status = status
        self.run.error = error[:500]
        self._record("loop.stopped", {"status": status, "error": self.run.error})
        return self.run
