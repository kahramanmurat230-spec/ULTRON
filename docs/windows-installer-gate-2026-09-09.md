# Windows installer gate — 2026-09-09

This marker intentionally triggers the Windows installer workflow on the same production-gate commit lineage.

Required checks:
- PyInstaller backend executable
- React frontend production build
- Electron + NSIS Windows installer
- artifact upload

Security boundaries remain unchanged: approval, audit, sandbox and rollback are mandatory for dangerous actions.
