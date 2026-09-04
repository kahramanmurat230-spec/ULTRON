# ULTRON Stage 9 — Capability-Aware Planning

Stage 9 makes planning capability-aware without changing the authorization boundary.

## Contract

- Planner may only select tools that are actually registered with callable handlers.
- An unavailable tool is rejected during plan validation instead of being presented as executable.
- Dangerous tools remain plan-able when registered, but execution still requires the existing approval/risk boundary.
- Capability validation is observational; it cannot grant permissions, bypass approval, or alter security policy.
- No fake availability is introduced.

## Verification

`backend/tests/test_stage9_capability_planning.py` verifies callable availability and preserves dangerous-tool marking.
