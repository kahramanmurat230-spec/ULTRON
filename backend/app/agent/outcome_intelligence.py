"""Levels 43-50: bounded outcome intelligence helpers.

This module is deliberately advisory. It can rank, normalize, explain, and
retain outcome metadata, but it cannot grant capabilities, approvals,
permissions, execution authority, or alter security/risk decisions.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class OutcomeSignal:
    identity: str
    result: str
    age_days: float = 0.0
    corroborations: int = 1
    attempts: int | None = None
    replans: int | None = None
    source: str = "unknown"


class OutcomeIntelligence:
    """Pure, deterministic advisory scoring and hygiene primitives."""

    MAX_SCORE = 100
    MIN_SCORE = 0
    HALF_LIFE_DAYS = 14.0
    MAX_RETAINED = 32
    MAX_EXPLANATION_CHARS = 600
    _SPACE = re.compile(r"\s+")
    _CONTROL = re.compile(r"(?i)\b(?:ignore|override|bypass)\s+(?:previous|prior|all)\s+(?:instructions?|rules?|safety)\b")

    @classmethod
    def normalize(cls, value: object, limit: int = 300) -> str:
        text = cls._SPACE.sub(" ", str(value)).strip()
        if cls._CONTROL.search(text):
            return "[untrusted control text removed]"
        return text[:limit]

    @classmethod
    def identity(cls, task: object = "", goal: object = "") -> str:
        task_n = cls.normalize(task, 160).casefold()
        goal_n = cls.normalize(goal, 240).casefold()
        return f"{task_n}|{goal_n}"

    @classmethod
    def decay_factor(cls, age_days: float) -> float:
        try:
            age = max(0.0, float(age_days))
        except (TypeError, ValueError):
            return 0.0
        return 0.5 ** (age / cls.HALF_LIFE_DAYS)

    @classmethod
    def score(cls, signal: OutcomeSignal) -> int:
        """Compute a bounded ranking score; never an authorization score."""
        score = 50.0
        score += min(20, max(0, signal.corroborations - 1) * 10)
        if signal.attempts is not None and 1 <= signal.attempts <= 2:
            score += 10
        if signal.replans == 0:
            score += 10
        score *= cls.decay_factor(signal.age_days)
        return max(cls.MIN_SCORE, min(cls.MAX_SCORE, int(round(score))))

    @classmethod
    def rank(cls, signals: Iterable[OutcomeSignal]) -> list[tuple[int, OutcomeSignal]]:
        ranked = [(cls.score(s), s) for s in signals]
        ranked.sort(key=lambda item: (-item[0], item[1].identity.casefold(), item[1].result.casefold()))
        return ranked

    @classmethod
    def corroboration_key(cls, signal: OutcomeSignal) -> str:
        return f"{cls.normalize(signal.identity, 300).casefold()}::{cls.normalize(signal.result, 300).casefold()}"

    @classmethod
    def explain(cls, signal: OutcomeSignal) -> str:
        score = cls.score(signal)
        age = max(0.0, float(signal.age_days))
        explanation = (
            f"confidence={score}/100; age={age:.1f}d; corroborations={max(1, signal.corroborations)}; "
            f"attempts={signal.attempts if signal.attempts is not None else 'unknown'}; "
            f"replans={signal.replans if signal.replans is not None else 'unknown'}; advisory-only"
        )
        return explanation[: cls.MAX_EXPLANATION_CHARS]

    @classmethod
    def retention_allowed(cls, age_days: float, status: str, provenance_ok: bool) -> bool:
        """Retention policy for already-recorded outcomes; never execution policy."""
        try:
            age = float(age_days)
        except (TypeError, ValueError):
            return False
        return 0 <= age <= 30 and str(status).casefold() == "succeeded" and bool(provenance_ok)

    @classmethod
    def bounded_batch(cls, signals: Iterable[OutcomeSignal]) -> list[OutcomeSignal]:
        """Deduplicate and cap advisory inputs to prevent unbounded context growth."""
        out: list[OutcomeSignal] = []
        seen: set[str] = set()
        for signal in cls.rank(signals):
            key = cls.corroboration_key(signal[1])
            if key in seen:
                continue
            seen.add(key)
            out.append(signal[1])
            if len(out) >= cls.MAX_RETAINED:
                break
        return out

    @classmethod
    def corroborate(cls, signals: Iterable[OutcomeSignal]) -> dict[str, int]:
        counts: dict[str, set[str]] = {}
        for signal in signals:
            key = cls.corroboration_key(signal)
            counts.setdefault(key, set()).add(cls.normalize(signal.source, 80).casefold())
        return {key: max(1, len(sources)) for key, sources in counts.items()}
