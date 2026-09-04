# Level 37 — Outcome Learning

## Contract

`Verified Outcome → bounded advisory memory → future planning context`

- Only `SUCCEEDED` runs with a structured result are learned.
- Stored text is bounded to prevent memory/prompt growth.
- Learned outcomes are advisory context only.
- Learning never grants approval, capability, permissions, risk changes, or tool execution authority.
- Persistence and audit are dependency-injected so existing memory/security layers remain authoritative.
- Failed, unverified, or missing-result runs are never recorded as successful outcomes.

## Verification

`backend/tests/test_outcome_learning.py` covers success-only learning, bounded storage, audit emission, and rejection of invalid results.
