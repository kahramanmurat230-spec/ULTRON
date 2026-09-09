# Level 52 — Shell Safety Foundation

ULTRON now has a deterministic, side-effect-free shell command policy layer.

## Contract

`command → normalize → classify → execution controls`

- Destructive/system-changing patterns are blocked before execution.
- Network/process/elevation-like operations are classified high risk.
- Ordinary commands are classified standard risk.
- The policy never executes a command.
- The policy never grants approval, permissions, capabilities, or sandbox access.
- Server-side approval and sandbox enforcement remain mandatory immediately before execution.
- Empty input is rejected.

## Verification

`backend/tests/test_shell_policy.py` covers invalid input, destructive-command blocking, high-risk classification, normal commands, and the no-side-effect classification contract.

## Next integration gate

The existing `terminal` tool must consume this policy at the execution boundary and return a hard rejection for blocked commands. The capability remains an adapter until that runtime integration is verified on Windows and in E2E tests.
