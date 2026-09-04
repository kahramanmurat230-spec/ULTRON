# ULTRON Stage 8 — Runtime Capability Awareness

Stage 8 wires the Stage 7 read-only self-awareness layer into the real runtime.

## Contract

- Runtime owns one `SelfAwareness` instance backed by the live tool registry, TTS backend and brain.
- `self_awareness` is exposed as a read-only runtime tool.
- Tool availability is derived from actual registered callable handlers.
- Dangerous tools remain explicitly marked and are never authorized by this report.
- Local TTS reports only `piper-local`, `espeak-ng-local`, or unavailable.
- Brain reporting exposes configured model metadata only; no credentials or vault secrets are returned.
- The capability report has no execution, approval, permission, sandbox, or security-policy side effects.

## Verification

`backend/tests/test_stage8_runtime_capability.py` verifies truthful reporting and unavailable-TTS behavior.
