# ULTRON Stage 7 — Self-Awareness

Stage 7 adds a read-only self-awareness layer so ULTRON can answer **what it can actually use right now**, without confusing capability availability with permission.

## Contract

- Report registered tools and whether their handlers are callable.
- Mark dangerous tools explicitly; self-awareness never grants approval.
- Report the actual local TTS backend (`piper-local`, `espeak-ng-local`, or unavailable).
- Report the configured brain/model without exposing credentials.
- Keep the report bounded and machine-readable.
- Never execute a tool while producing the report.

## Safety

Self-awareness is observational only. It cannot:

- approve dangerous actions;
- modify security policy;
- change sandbox roots;
- access vault secrets;
- claim unavailable hardware/models are available.

## Validation

`backend/tests/test_self_awareness.py` covers truthful local-TTS availability and dangerous-tool visibility. The existing backend doctor no longer treats obsolete cloud TTS as a core dependency.
