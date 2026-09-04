"""Memory-aware planning helpers for ULTRON Stage 6.

Retrieves a small, relevant memory context for planning without granting
memory-derived content any authority over tool execution or permissions.
"""

from __future__ import annotations


class MemoryPlanningContext:
    """Build bounded planning context from the existing semantic memory."""

    MAX_ITEMS = 6
    MAX_CHARS = 3000

    def __init__(self, semantic_memory):
        self.semantic_memory = semantic_memory

    def build(self, goal: str) -> str:
        if not goal or self.semantic_memory is None:
            return ""
        try:
            hits = self.semantic_memory.search(goal, limit=self.MAX_ITEMS)
        except Exception:
            return ""
        lines = []
        for _score, kind, content, _created in hits:
            text = " ".join(str(content).split())
            if not text:
                continue
            lines.append(f"[{kind}] {text[:500]}")
        return "\n".join(lines)[: self.MAX_CHARS]
