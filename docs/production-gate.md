# ULTRON Production Gate

This marker intentionally triggers the full CI/package validation after capability-runtime hardening.

Release gate:
- backend tests must pass
- frontend build must pass
- mobile build must pass
- Windows backend executable must build
- Windows NSIS installer must build and upload
- dangerous actions retain approval, audit, sandbox, and rollback boundaries
- unavailable provider/device capabilities must never be reported as live
