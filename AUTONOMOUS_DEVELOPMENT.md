# ULTRON — Autonomous Level 1→50 Runbook

This repository can be operated by a connected coding agent as a continuous Level 1→50 engineering mission.

## Start command

Use this instruction in the connected ChatGPT Agent:

> Start the ULTRON Level 1→50 autonomous development mission. Read `AGENTS.md` first. Work sequentially. Implement, test, diagnose failures, fix them, rerun tests, verify security/regressions, commit, and continue to the next level without asking for routine confirmation.

## Agent responsibilities

The connected ChatGPT Agent is the engineering operator: repository analysis, code changes, tests, failure diagnosis, fixes, commits/PRs, and CI verification.

The repository's own runtime must never treat an LLM instruction as permission to perform a dangerous real-world action. The existing ULTRON Supervisor, permission, risk, approval, sandbox, audit, and vault controls remain authoritative.

## Persistence

The agent should treat the latest verified Git commit/PR and CI result as the durable checkpoint. If an external agent session ends, resume from the highest level whose required checks and commit/PR state are actually verified. Do not infer completion from conversation text alone.

Recommended status fields for an external runner/session:

```json
{
  "current_level": 1,
  "status": "PENDING",
  "attempt": 0,
  "last_verified_commit": null,
  "last_ci_run": null,
  "blocking_error": null
}
```

Do not commit secrets, vault contents, local runtime state, or fake completion markers.

## Verification gate

Advance only after the current level's implementation and applicable checks are green. A failed test means the current level remains active until the root cause is fixed and verified.

## Important limitation

This runbook makes the repository instructions explicit for a connected coding agent; it does not create an always-running ChatGPT process by itself. Continuous execution still depends on the connected Agent/session being active and permitted to perform the requested GitHub/CI operations.
