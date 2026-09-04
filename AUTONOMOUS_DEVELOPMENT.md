# ULTRON — Autonomous Level 1→50 Runbook

This repository can be operated by a connected coding agent as a sequential Level 1→50 engineering mission.

## Arena Agent Mode start

Use Arena's Agent Mode and enable GitHub access for the ULTRON repository. Arena provides a repository sandbox, code editing, shell/testing, diff review, commits, pushes, and pull requests. Follow `AGENTS.md` before changing code.

Start with:

> Start the ULTRON Level 1→50 autonomous development mission. Read `AGENTS.md` first. Work sequentially. Implement, test, diagnose failures, fix them, rerun tests, verify security/regressions, commit, and continue to the next level when the active session can safely continue without asking for routine confirmation.

If Arena reaches the one-PR-per-chat-session limit, finish the current coherent PR, preserve the verified checkpoint, and start a fresh Agent Mode session for the next coherent PR/level group.

## Agent responsibilities

The connected coding agent is the engineering operator: repository analysis, code changes, tests, failure diagnosis, fixes, commits/PRs, and CI verification.

The repository's own runtime must never treat an LLM instruction as permission to perform a dangerous real-world action. The existing ULTRON Supervisor, permission, risk, approval, sandbox, audit, and vault controls remain authoritative.

## Persistence

The durable checkpoint is the highest level whose implementation, required checks, and commit/PR state are actually verified. If an external agent session ends, resume from that checkpoint. Do not infer completion from conversation text alone.

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

## Failure recovery

When a test or build fails:

1. Read the actual failure output.
2. Identify the root cause rather than hiding the symptom.
3. Make the smallest safe fix.
4. Rerun the failed check.
5. Rerun the relevant full suite/builds.
6. Continue only when the verification gate is satisfied.

Do not delete tests, weaken assertions, fabricate success, or bypass security controls to obtain green CI.

## Important limitation

This runbook provides the repository-side contract for a connected coding agent; it does not create an always-running process by itself. Arena Agent Mode can autonomously perform multi-step work within its active session, but session limits, GitHub permissions, CI availability, and external resource availability still apply. Arena currently supports one pull request per chat session, so a multi-level mission may require sequential Agent Mode sessions with verified Git/CI checkpoints between them.
