"""Level 36 production adapter for SupervisorOrchestrator.

Bridges the bounded adaptive loop to the existing Supervisor execution path
without replacing scheduler, approval, capability, artifact, or judge policy.
"""
from __future__ import annotations

from typing import Any, Callable

from app.agent.adaptive_loop import AdaptiveAgentLoop, AdaptiveLimits, AdaptiveRun


class AdaptiveSupervisorAdapter:
    """Run Supervisor executions through a bounded verify/replan loop.

    ``build_workers`` and ``build_executors`` are called for every attempt so a
    replan gets a fresh DAG and execution map. Capability and approval checks
    run immediately before each Supervisor execution. They are callbacks so
    the existing authoritative security implementations remain in control.
    """

    def __init__(
        self,
        supervisor: Any,
        *,
        build_workers: Callable[[Any], Any],
        build_executors: Callable[[Any], Any],
        verify_report: Callable[[dict, Any], Any],
        replan: Callable[[Any, dict, str], Any],
        capability_check: Callable[[Any], Any] | None = None,
        approval_check: Callable[[Any], Any] | None = None,
        limits: AdaptiveLimits | None = None,
        audit: Callable[[str, dict], Any] | None = None,
        execute_kwargs: dict | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.build_workers = build_workers
        self.build_executors = build_executors
        self.verify_report = verify_report
        self.replan = replan
        self.capability_check = capability_check or (lambda _plan: True)
        self.approval_check = approval_check or (lambda _plan: True)
        self.limits = limits or AdaptiveLimits()
        self.audit = audit or (lambda _event, _payload: None)
        self.execute_kwargs = dict(execute_kwargs or {})

    async def run(self, task_id: str, initial_plan: Any) -> AdaptiveRun:
        """Execute Supervisor attempts until verification succeeds or bounds stop it."""
        async def execute(plan: Any) -> dict:
            workers = self.build_workers(plan)
            executors = self.build_executors(plan)
            report = await self.supervisor.execute(
                task_id, workers, executors, **self.execute_kwargs
            )
            return {"ok": bool(report.get("ok")), "report": report}

        async def verify(plan: Any, result: dict) -> bool:
            if not result.get("ok"):
                return False
            return bool(await _maybe_await(self.verify_report(result["report"], plan)))

        async def next_plan(plan: Any, result: dict, reason: str) -> Any:
            report = result.get("report")
            return await _maybe_await(self.replan(plan, report, reason))

        loop = AdaptiveAgentLoop(
            task_id=task_id,
            initial_plan=initial_plan,
            execute=execute,
            verify=verify,
            replan=next_plan,
            capability_check=self.capability_check,
            approval_check=self.approval_check,
            limits=self.limits,
            audit=self.audit,
        )
        return await loop.run_once()


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
