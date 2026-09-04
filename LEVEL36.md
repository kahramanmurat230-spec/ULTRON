# Level 36 — Adaptive Agent Loop

## Contract

ULTRON's adaptive execution path is bounded and verification-first:

`Plan → capability check → approval check → Execute → Verify → Replan`

Execution success is **not** task success. A task reaches `SUCCEEDED` only when
its verifier explicitly accepts the observed result.

## Safety invariants

- Capability is re-checked immediately before every execution attempt.
- Approval is re-checked immediately before every execution attempt.
- Replanning is bounded (`max_replans`).
- Total attempts are bounded (`max_attempts`).
- Executor exceptions become explicit failed observations.
- Non-object executor results are failures, never successes.
- Failed verification cannot be converted into success.
- No tool is invoked directly by the adaptive loop; existing Supervisor,
  permission, risk, approval and sandbox layers remain authoritative.
- Every execution/replan/stop transition can be sent to the existing audit path.

## Current integration boundary

The first Level 36 implementation is a small callback-based coordination layer
(`backend/app/agent/adaptive_loop.py`). This keeps the existing Supervisor
Orchestrator and security core stable while establishing a tested contract.
The next increment will connect this contract to the production Supervisor
execution path and its existing verification/result pipeline.

## Expected lifecycle

```text
PLAN
  ↓
CAPABILITY CHECK
  ↓
APPROVAL CHECK
  ↓
EXECUTE
  ↓
OBSERVE RESULT
  ↓
VERIFY
 ├── PASS → SUCCEEDED
 └── FAIL → REPLAN (bounded) → checks → EXECUTE
```
