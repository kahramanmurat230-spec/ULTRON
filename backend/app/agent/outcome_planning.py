"""Level 38 — bounded advisory context from verified task outcomes.

This adapter intentionally treats learned outcomes as untrusted planning
context. It cannot grant capabilities, permissions, approvals, or execution
authority.
"""
from __future__ import annotations


class OutcomePlanningContext:
    MAX_ITEMS = 4
    MAX_CHARS = 1800
    MAX_ITEM_CHARS = 400

    def __init__(self, semantic_memory=None):
        self.semantic_memory = semantic_memory

    def build(self, goal: str) -> str:
        if not goal or self.semantic_memory is None:
            return ""
        try:
            hits = self.semantic_memory.search(goal, limit=self.MAX_ITEMS * 2)
        except Exception:
            return ""
        lines = []
        seen = set()
        for hit in hits:
            if not isinstance(hit, (tuple, list)) or len(hit) < 4:
                continue
            _score, kind, content, _created = hit[:4]
            if str(kind).casefold() != "task_outcome":
                continue
            text = " ".join(str(content).split())
            if "status=SUCCEEDED" not in text:
                continue
            text = text[: self.MAX_ITEM_CHARS]
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            lines.append(f"[verified outcome] {text}")
            if len(lines) >= self.MAX_ITEMS:
                break
        return "\n".join(lines)[: self.MAX_CHARS]
