"""PHASE 2: unified risk engine + self-coding security boundary."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.agent.executor import Executor  # noqa: E402
from app.core.tool_registry import ToolRegistry  # noqa: E402
from app.security.audit import AuditLog  # noqa: E402
from app.security.permissions import PermissionManager  # noqa: E402
from app.security.risk import (  # noqa: E402
    LEVELS, RANK, RiskEngine, SelfCodeBoundary, evaluate, guard,
)


def test_level_ordering_and_mapping():
    assert LEVELS == ("SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert evaluate("system_status", {})["level"] == "SAFE"
    assert evaluate("weather_current", {})["level"] == "LOW"
    assert evaluate("browser_navigate", {})["level"] == "LOW"
    assert evaluate("some_unknown_dangerous", {}, dangerous=True)["level"] == "MEDIUM"
    assert evaluate("gui_click", {})["level"] == "HIGH"
    assert evaluate("write_text", {})["level"] == "HIGH"
    for crit in ("run_shell", "delete_file", "install_software",
                 "change_system_setting", "process_kill"):
        d = evaluate(crit, {})
        assert d["level"] == "CRITICAL" and d["critical"] is True
        assert d["requires_approval"] is True


def test_approval_threshold_is_medium():
    assert evaluate("system_status", {})["requires_approval"] is False
    assert evaluate("open_url", {})["requires_approval"] is False
    assert evaluate("gui_click", {})["requires_approval"] is True
    assert evaluate("run_shell", {})["requires_approval"] is True


def test_guard_blocks_unapproved_and_allows_approved():
    with pytest.raises(PermissionError):
        guard("gui_click", {}, dangerous=True, approved=False)
    d = guard("gui_click", {}, dangerous=True, approved=True)
    assert d["level"] == "HIGH"
    assert guard("system_status", {}, dangerous=False, approved=False)["level"] == "SAFE"


def test_self_coding_boundary_paths():
    for prot in ("backend/app/security/vault.py", "app/security/sandbox.py",
                 "backend/auth.py", "config/security/master_rules.json",
                 "backend/app/core/runtime.py", "backend/app/agent/executor.py",
                 "backend/codegen.py"):
        assert SelfCodeBoundary.is_protected(prot), prot
    for free in ("backend/app/tools/new_tool.py", "frontend/src/components/X.tsx",
                 "backend/app/world/model.py", "README.md"):
        assert not SelfCodeBoundary.is_protected(free), free
    with pytest.raises(PermissionError):
        SelfCodeBoundary.check("backend/app/security/vault.py")


def test_write_text_to_security_core_blocked_even_approved():
    d = evaluate("write_text", {"path": "backend/app/security/vault.py",
                                "content": "x"}, dangerous=True)
    assert d["level"] == "CRITICAL" and d["blocked"]
    with pytest.raises(PermissionError):
        guard("write_text", {"path": "backend/app/security/vault.py", "content": "x"},
              dangerous=True, approved=True)  # onay OLSA BİLE mimari blok


def test_executor_integrates_risk_and_boundary(tmp_path):
    reg = ToolRegistry()
    reg.register("system_status", lambda: {"ok": 1}, "durum")
    out = tmp_path / "o.txt"
    reg.register("write_text",
                 lambda path, content: Path(path).write_text(content, encoding="utf-8"),
                 "yazar", dangerous=True)
    perms = PermissionManager({"security": {"require_confirmation_for": ["write_text"]}})
    audit = AuditLog(path=str(tmp_path / "a.log"))
    ex = Executor(reg, perms, audit)
    assert ex.execute([("system_status", {})], approved=False)[0] == {"ok": 1}
    with pytest.raises(PermissionError):
        ex.execute([("write_text", {"path": str(out), "content": "x"})], approved=False)
    # güvenli hedef + onay → çalışır
    ex.execute([("write_text", {"path": str(out), "content": "x"})], approved=True)
    assert out.exists()
    # KORUMALI hedef + onay → yine reddedilir
    with pytest.raises(PermissionError):
        ex.execute([("write_text", {"path": "app/security/vault.py",
                                    "content": "hacked"})], approved=True)
    log = (tmp_path / "a.log").read_text(encoding="utf-8")
    assert "risk=" in log  # audit artık risk seviyesini taşıyor
