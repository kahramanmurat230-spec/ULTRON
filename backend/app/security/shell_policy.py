"""Deterministic shell safety policy for ULTRON.

This module only classifies commands; it never executes them and never grants
approval. The caller must still perform server-side approval and sandbox
checks immediately before execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellDecision:
    allowed: bool
    risk: str
    reason: str
    normalized: str


_BLOCKED = (
    r"\bformat\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bshutdown(?:\.exe)?\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bremove-item\b[^\n]*\s-recurse\b",
    r"\bdel\b[^\n]*\s/(?:s|q)\b",
    r"\brmdir\b[^\n]*\s/(?:s|q)\b",
    r"\breg\s+(?:delete|add)\b",
    r"\bsc(?:\.exe)?\s+(?:delete|stop|config)\b",
    r"\bnetsh\b",
    r"\bnet\s+(?:user|localgroup|stop|start)\b",
    r"\bwevtutil\b",
    r"\b(?:powershell|pwsh)\b[^\n]*\b-enc(?:odedcommand)?\b",
)

_HIGH_RISK = (
    r"\b(?:curl|wget|bitsadmin|certutil)\b",
    r"\b(?:invoke-webrequest|invoke-restmethod)\b",
    r"\b(?:set-itemproperty|new-itemproperty)\b",
    r"\b(?:start-process|invoke-expression|iex)\b",
    r"\b(?:git\s+(?:push|reset|clean))\b",
)


class ShellPolicy:
    """Classify a command without executing it or changing security state."""

    def evaluate(self, command: str) -> ShellDecision:
        normalized = " ".join(str(command or "").strip().split())
        if not normalized:
            return ShellDecision(False, "invalid", "empty command", normalized)

        lower = normalized.casefold()
        for pattern in _BLOCKED:
            if re.search(pattern, lower, flags=re.IGNORECASE):
                return ShellDecision(False, "blocked", "command matches blocked operation policy", normalized)

        for pattern in _HIGH_RISK:
            if re.search(pattern, lower, flags=re.IGNORECASE):
                return ShellDecision(True, "high", "command requires explicit approval and sandbox enforcement", normalized)

        return ShellDecision(True, "standard", "command requires normal execution controls", normalized)


def evaluate_shell(command: str) -> dict[str, object]:
    """JSON-friendly policy result for API/tool boundaries."""
    d = ShellPolicy().evaluate(command)
    return {
        "allowed": d.allowed,
        "risk": d.risk,
        "reason": d.reason,
        "normalized": d.normalized,
    }
