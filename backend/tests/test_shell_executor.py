import asyncio
from pathlib import Path

from app.tools.shell import ShellExecutor


def test_blocked_command_never_runs(tmp_path):
    ex = ShellExecutor(tmp_path)
    result = asyncio.run(ex.execute("format C:"))
    assert result["ok"] is False
    assert result["blocked"] is True
    assert "subprocess" not in result


def test_workspace_cwd_is_enforced(tmp_path):
    ex = ShellExecutor(tmp_path)
    outside = Path(tmp_path).parent
    result = asyncio.run(ex.execute("echo safe", cwd=str(outside)))
    assert result["ok"] is False
    assert "workspace" in result["error"]


def test_standard_command_executes_in_workspace(tmp_path):
    ex = ShellExecutor(tmp_path)
    result = asyncio.run(ex.execute("python -c \"print('ULTRON_SHELL_OK')\""))
    assert result["ok"] is True
    assert "ULTRON_SHELL_OK" in result["output"]


def test_high_risk_command_is_classified_before_execution():
    from app.security.shell_policy import ShellPolicy
    decision = ShellPolicy().evaluate("git push")
    assert decision.allowed is True
    assert decision.risk == "high"
