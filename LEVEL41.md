# Level 41 — Outcome Consistency

## Goal
Prevent contradictory verified outcomes from being presented as planning guidance.

## Behavior
- Reuses Level 40 provenance, freshness, and sanitization gates.
- Normalizes flat and nested semantic-memory search hits.
- Groups outcomes by task and goal identity.
- If fresh verified outcomes for the same identity contain different result values, the entire conflicting group is suppressed.
- Identical duplicates are deduplicated.
- Unrelated consistent outcomes remain available within existing bounds.

## Security boundary
Consistency filtering is **advisory data handling only**. It cannot grant capabilities, permissions, approvals, execution authority, or bypass risk/security gates.

## Bounds
- Maximum 4 exposed outcomes.
- Maximum 1800 characters total.
- Maximum 400 characters per outcome.
- Maximum outcome age: 30 days.

## Failure behavior
Memory/search/parse failures degrade to empty advisory context. No fallback treats untrusted or conflicting data as authoritative.
