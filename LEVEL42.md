# Level 42 — Outcome Confidence

Adds deterministic, bounded confidence scoring to already verified, fresh and consistent task outcomes.

## Rules
- Confidence is ranking metadata only.
- Scores are bounded to 0–100.
- Repeated identical outcomes increase confidence by at most 20 points.
- Low attempt count and zero replans add bounded bonuses.
- Conflicting outcomes remain suppressed by Level 41.
- Provenance, freshness, sanitization, nested-hit support and output bounds remain enforced.
- Confidence cannot grant capabilities, permissions, approvals, execution authority, or change risk decisions.
