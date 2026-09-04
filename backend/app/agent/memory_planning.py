"""Memory-aware planning helpers for ULTRON Stage 6.

Retrieves a small, relevant memory context for planning without granting
memory-derived content any authority over tool execution or permissions.
Sensitive values are redacted before memory reaches the planner.
"""

from __future__ import annotations

import re


class MemoryPlanningContext:
    """Build bounded, sanitized planning context from semantic memory."""

    MAX_ITEMS = 6
    MAX_CHARS = 3000
    MAX_ITEM_CHARS = 500
    _SENSITIVE_KIND = re.compile(r"^(?:SECRET|CREDENTIAL|TOKEN|PASSWORD|API[_ -]?KEY|AUTH)$", re.I)
    _SECRET_VALUE = re.compile(
        r"(?i)\b(?:password|passwd|token|secret|api[_ -]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+"
    )
    _COMMON_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AIza[A-Za-z0-9_-]{20,})\b")

    def __init__(self, semantic_memory=None):
        self.semantic_memory = semantic_memory

    @classmethod
    def _sanitize(cls, kind, content):
        if cls._SENSITIVE_KIND.fullmatch(str(kind or "")):
            return "[REDACTED MEMORY]"
        text = " ".join(str(content).split())
        text = cls._SECRET_VALUE.sub(lambda m: m.group(0).split("=", 1)[0].split(":", 1)[0] + "=[REDACTED]", text)
        text = cls._COMMON_TOKEN.sub("[REDACTED]", text)
        return text[: cls.MAX_ITEM_CHARS]

    def build(self, goal: str) -> str:
        if not goal or self.semantic_memory is None:
            return ""
        try:
            hits = self.semantic_memory.search(goal, limit=self.MAX_ITEMS)
        except Exception:
            return ""
        lines = []
        seen = set()
        for hit in hits:
            if not isinstance(hit, (tuple, list)) or len(hit) < 4:
                continue
            _score, kind, content, _created = hit[:4]
            text = self._sanitize(kind, content)
            if not text or text == "[REDACTED MEMORY]":
                continue
            key = (str(kind), text.casefold())
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"[{kind}] {text}")
        return "\n".join(lines)[: self.MAX_CHARS]
