"""Level 40 — freshness- and provenance-aware advisory context from outcomes.

Learned outcomes are untrusted data. Only explicitly verified outcome records
with a usable provenance marker and a fresh timestamp are exposed to planning.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


class OutcomePlanningContext:
    MAX_ITEMS = 4
    MAX_CHARS = 1800
    MAX_ITEM_CHARS = 400
    MAX_AGE_DAYS = 30
    _SECRET = re.compile(r"(?i)(?:\b(?:password|passwd|token|secret|api[_ -]?key|authorization)\s*[:=]\s*|\bbearer\s+)[^\s,;]+")
    _TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AIza[A-Za-z0-9_-]{20,})\b")
    _CONTROL = re.compile(r"(?i)^\s*(?:system|assistant|developer)\s*:\s*|\b(?:ignore|override|bypass)\s+(?:previous|prior|all)\s+(?:instructions?|rules?|safety)\b")

    def __init__(self, semantic_memory=None):
        self.semantic_memory = semantic_memory

    @classmethod
    def _fresh(cls, created: object) -> bool:
        if str(created).strip().casefold() == "now":
            return True
        try:
            raw = str(created).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            stamp = datetime.fromisoformat(raw)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
            return 0 <= age <= cls.MAX_AGE_DAYS * 86400
        except (TypeError, ValueError, OverflowError):
            return False

    @classmethod
    def _sanitize(cls, content: object) -> str:
        text = " ".join(str(content).split())
        text = cls._SECRET.sub("[REDACTED]", text)
        text = cls._TOKEN.sub("[REDACTED]", text)
        if cls._CONTROL.search(text):
            return "[REDACTED UNTRUSTED CONTROL TEXT]"
        return text[: cls.MAX_ITEM_CHARS]

    @classmethod
    def _unpack_hit(cls, hit):
        if not isinstance(hit, (tuple, list)):
            return None
        if len(hit) >= 4:
            return hit[0], hit[1], hit[2], hit[3]
        if len(hit) == 2 and isinstance(hit[1], (tuple, list)) and len(hit[1]) >= 3:
            row = hit[1]
            return hit[0], row[0], row[1], row[2]
        return None

    def build(self, goal: str) -> str:
        if not goal or self.semantic_memory is None:
            return ""
        try:
            hits = self.semantic_memory.search(goal, limit=self.MAX_ITEMS * 3)
        except Exception:
            return ""
        lines = []
        seen = set()
        for hit in hits:
            unpacked = self._unpack_hit(hit)
            if unpacked is None:
                continue
            _score, kind, content, created = unpacked
            if str(kind).casefold() != "task_outcome":
                continue
            raw = " ".join(str(content).split())
            if not raw.startswith("Verified outcome ") or "status=SUCCEEDED" not in raw:
                continue
            if not self._fresh(created):
                continue
            text = self._sanitize(raw)
            if not text or text == "[REDACTED UNTRUSTED CONTROL TEXT]":
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"[verified outcome — fresh data only] {text}")
            if len(lines) >= self.MAX_ITEMS:
                break
        return "\n".join(lines)[: self.MAX_CHARS]
