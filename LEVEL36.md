# Level 36 — Increment 2

## Production Supervisor integration

`AdaptiveSupervisorAdapter` now connects the bounded adaptive loop to the existing `SupervisorOrchestrator.execute()` path.

Flow:

`Plan → capability check → approval check → Supervisor/Scheduler Execute → report verification → bounded Replan`

### Guarantees

- Every attempt re-checks capability and approval immediately before execution.
- Each replan rebuilds workers and executors, preventing reuse of a stale DAG.
- Supervisor, scheduler, capability tokens, approval, artifacts and judge remain authoritative.
- Executor/report failure is never converted into success.
- Verification is mandatory before `SUCCEEDED`.
- Replans and attempts are hard bounded by `AdaptiveLimits`.
- Audit callbacks receive adaptive-loop transitions.

This increment is an adapter, not a replacement for the production orchestration pipeline. Existing security boundaries remain unchanged.
