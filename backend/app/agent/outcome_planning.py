"""Level 39 — sanitized, bounded advisory context from verified outcomes.

Learned outcomes are untrusted data. This adapter strips control-like lines and
common credential material before context reaches the planner.
"""
from __future__ import annotations

import re


class OutcomePlanningContext:
    MAX_ITEMS = 4
    MAX_CHARS = 1800
    MAX_ITEM_CHARS = 400
    _SECRET = re.compile(r"(?i)(?:\b(?:password|passwd|token|secret|api[_ -]?key|authorization)\s*[:=]\s*|\bbearer\s+)[^\s,;]+")
    _TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AIza[A-Za-z0-9_-]{20,})\b")
    _CONTROL = re.compile(r"(?i)^\s*(?:system|assistant|developer)\s*:\s*|\b(?:ignore|override|bypass)\s+(?:previous|prior|all)\s+(?:instructions?|rules?|safety)\b")

    def __init__(self, semantic_memory=None):
        self.semantic_memory = semantic_memory

    @classmethod
    def _sanitize(cls, content: object) -> str:
        text = " ".join(str(content).split())
        text = cls._SECRET.sub("[REDACTED]", text)
        text = cls._TOKEN.sub("[REDACTED]", text)
        if cls._CONTROL.search(text):
            return "[REDACTED UNTRUSTED CONTROL TEXT]"
        return text[: cls.MAX_ITEM_CHARS]

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
            raw = " ".join(str(content).split())
            if "status=SUCCEEDED" not in raw:
                continue
            text = self._sanitize(raw)
            if not text or text == "[REDACTED UNTRUSTED CONTROL TEXT]":
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"[verified outcome — data only] {text}")
            if len(lines) >= self.MAX_ITEMS:
                break
        return "\n".join(lines)[: self.MAX_CHARS]
