# Pending CI workflow changes (apply manually)

The Arena GitHub App that produced this branch is not allowed to modify
`.github/workflows/*` (GitHub rejects pushes touching workflows without the
`workflows` permission). The two CI improvements from the production-hardening
pass are therefore stored as a ready-to-apply patch:

```
git apply docs/pending-ci-workflows.patch
git add .github/workflows
git commit -m "ci: backend cwd for windows smoke + Python 3.13 Windows job"
git push
```

## What the patch contains

1. `windows-smoke.yml` — **Start backend** step gains
   `working-directory: backend` (and `Start-Process -WorkingDirectory`), so CI
   starts the backend exactly like `scripts/start_ultron.bat` does.
   `UltronRuntime` resolves relative `data/` paths against the process cwd;
   `server.py` now also `os.chdir(BASE)` at import time, so both cwd styles are
   safe — the workflow change keeps CI representative of the real launch path.

2. `backend-tests.yml` — new job **backend-tests-windows-py313**
   (`windows-latest`, Python 3.13): installs the deterministic core dependency
   set (aiohttp, psutil, pytest, cryptography, pillow, numpy, pytesseract,
   pypdf, pvporcupine), runs `compileall` and the full backend test suite.
   This continuously validates the actual target environment
   (Windows 11 + Python 3.13). Verified locally on Python 3.11:
   962 passed / 4 honest skips with the same dependency set.

Neither change touches security policies; both are additive to CI coverage.
