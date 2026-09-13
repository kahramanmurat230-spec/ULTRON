# Level 53 — Terminal Approval Boundary

Level 52 is now enforced at the real terminal tool boundary.

## Contract

`terminal request → shell policy → approval/security layer → subprocess`

- Blocked shell commands are rejected before subprocess creation.
- The policy result is never treated as approval.
- Standard/high-risk commands continue through the existing execution path, where the caller must enforce approval and sandbox controls.
- Failed commands cannot be converted into success.
- Existing terminal timeout and output bounds remain active.

## Verification

The policy unit tests cover blocked, high-risk, standard and invalid commands. Full Windows E2E approval/sandbox verification remains required before declaring terminal production-ready.
