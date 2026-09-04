# Level 40 — Outcome Trust & Freshness

## Contract

`Verified Outcome → provenance check → freshness check → sanitize → bounded advisory context`

- Only `task_outcome` records with the explicit `Verified outcome` provenance marker and `status=SUCCEEDED` are eligible.
- ISO timestamps older than 30 days are excluded.
- Malformed timestamps are excluded; the existing synthetic `now` test marker remains supported for adapter compatibility.
- Semantic-memory nested hit shapes are normalized before evaluation.
- Level 39 credential redaction and control-text filtering remain active.
- Existing 4-outcome / 1800-character bounds remain active.
- Learned outcomes cannot grant permissions, capabilities, approvals, risk overrides, or execution authority.

## Verification

`backend/tests/test_outcome_planning.py` covers fresh verified outcomes, stale outcomes, provenance/timestamp rejection, and nested semantic-memory hits.
