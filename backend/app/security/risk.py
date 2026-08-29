"""Unified Risk Engine — single authority for action risk levels.

Levels: SAFE < LOW < MEDIUM < HIGH < CRITICAL.
- SAFE:    pure read/compute, no side effects
- LOW:     external interaction without state change (open URL, weather)
- MEDIUM:  state-changing but reversible (default for dangerous tools)
- HIGH:    writes, GUI actions, patches, sends (approval-gated)
- CRITICAL: destructive/irreversible (shell, delete, install, system
            settings, process kill) — NEVER runs without explicit
            server-validated user approval; the agent can never
            self-approve these (runtime.approved is user-driven only).

Self-coding boundary (architectural, not policy): files that implement
the security system itself can never be modified by codegen/self_repair/
write_text tooling, even WITH approval — changing the approval system,
audit, vault, sandbox or redaction requires a human editing the repo.
"""
import re
from pathlib import Path

LEVELS = ("SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL")
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}

# Explicit overrides (argued per tool, not per author claim)
TOOL_RISK = {
    "run_shell": "CRITICAL",
    "delete_file": "CRITICAL",
    "install_software": "CRITICAL",
    "change_system_setting": "CRITICAL",
    "process_kill": "CRITICAL",
    "write_text": "HIGH",
    "apply_code_patch": "HIGH",
    "self_repair": "HIGH",
    "email_send": "HIGH",
    "skill_run_dangerous": "HIGH",
    "gui_click": "HIGH", "gui_double_click": "HIGH", "gui_right_click": "HIGH",
    "gui_type": "HIGH", "gui_press": "HIGH", "gui_hotkey": "HIGH",
    "gui_scroll": "HIGH", "gui_locate_and_click": "HIGH", "click_text": "HIGH",
    "close_application": "MEDIUM",
    "browser_click": "HIGH", "browser_type": "HIGH", "browser_select": "HIGH",
    # LOW: external interaction, no durable state change
    "open_url": "LOW", "search_web": "LOW",
    "weather_current": "LOW", "weather_forecast": "LOW",
    "browser_navigate": "LOW", "open_application": "LOW", "open_file": "LOW",
    "open_folder": "LOW",
}

# Self-coding can NEVER touch these (approval system, security boundary,
# audit, credential vault, sandbox, redaction, master rules).
SELF_CODING_PROTECTED = (
    re.compile(r"(^|/)app/security/.*\.py$"),
    re.compile(r"(^|/)auth\.py$"),
    re.compile(r"(^|/)config/security/.*$"),
    re.compile(r"(^|/)app/core/(tool_registry|runtime)\.py$"),
    re.compile(r"(^|/)codegen\.py$"),
    re.compile(r"(^|/)app/agent/executor\.py$"),
)
PROTECTED_REASON = ("hedef dosya güvenlik çekirdeğine ait (approval/audit/vault/"
                    "sandbox/redaction) — self-coding için mimari olarak korumalı")


def _norm(path) -> str:
    try:
        p = Path(str(path))
        return p.as_posix()
    except Exception:
        return str(path)


class SelfCodeBoundary:
    """Path-level architectural guard for codegen / self_repair / write tools."""

    @staticmethod
    def is_protected(path) -> bool:
        n = _norm(path)
        return any(rx.search(n) for rx in SELF_CODING_PROTECTED)

    @staticmethod
    def check(path) -> None:
        if SelfCodeBoundary.is_protected(path):
            raise PermissionError(PROTECTED_REASON)


class RiskEngine:
    """Classify (tool, args) → risk decision. Pure; registry only supplies
    the dangerous flag so skills/agents cannot influence their own risk."""

    def __init__(self, dangerous_flags: dict | None = None):
        self.dangerous_flags = dangerous_flags or {}

    @staticmethod
    def level_of(name: str, dangerous: bool = False) -> str:
        lvl = TOOL_RISK.get(name)
        if lvl:
            return lvl
        return "MEDIUM" if dangerous else "SAFE"

    def classify(self, name: str, args: dict | None = None) -> dict:
        args = args or {}
        dangerous = bool(self.dangerous_flags.get(name))
        level = self.level_of(name, dangerous)
        # arg-based escalation: writing to a protected security file is always
        # blocked for tooling — represent as CRITICAL + blocked reason
        blocked = None
        for key in ("path", "image"):
            if key in args and name in ("write_text", "apply_code_patch") \
                    and SelfCodeBoundary.is_protected(args[key]):
                blocked = PROTECTED_REASON
                level = "CRITICAL"
        if "paths" in args and isinstance(args["paths"], (list, tuple)):
            if any(SelfCodeBoundary.is_protected(p) for p in args["paths"]):
                blocked = PROTECTED_REASON
                level = "CRITICAL"
        return {
            "tool": name,
            "level": level,
            "requires_approval": RANK[level] >= RANK["MEDIUM"],
            "blocked": blocked,
            # CRITICAL: only an explicit, server-validated user approval counts
            "critical": level == "CRITICAL",
        }

    # Convenience for executor/skills
    def guard(self, name: str, args: dict | None, approved: bool) -> dict:
        r = self.classify(name, args)
        if r["blocked"]:
            raise PermissionError(r["blocked"])
        if r["requires_approval"] and not approved:
            raise PermissionError(
                f"Confirmation required for: {name} (risk={r['level']})")
        return r


# ------------------------------------------------------------ module helpers
def evaluate(name: str, args: dict | None, dangerous: bool = False) -> dict:
    eng = RiskEngine({name: dangerous})
    return eng.classify(name, args)


def guard(name: str, args: dict | None, dangerous: bool, approved: bool) -> dict:
    """Raise PermissionError unless the call may proceed; returns the decision."""
    eng = RiskEngine({name: dangerous})
    return eng.guard(name, args, approved)
