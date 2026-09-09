"""Level 52: deterministic shell safety policy tests."""
from app.security.shell_policy import ShellPolicy, evaluate_shell


def test_empty_command_is_invalid() -> None:
    d = ShellPolicy().evaluate("  ")
    assert d.allowed is False
    assert d.risk == "invalid"


def test_destructive_commands_are_blocked() -> None:
    policy = ShellPolicy()
    for command in ("format C:", "diskpart", "shutdown /s", "reg delete HKCU\\Software\\X"):
        d = policy.evaluate(command)
        assert d.allowed is False
        assert d.risk == "blocked"


def test_network_or_process_escalation_is_high_risk() -> None:
    policy = ShellPolicy()
    for command in ("curl https://example.com", "powershell Invoke-WebRequest x", "Start-Process calc.exe"):
        d = policy.evaluate(command)
        assert d.allowed is True
        assert d.risk == "high"


def test_normal_command_is_standard() -> None:
    d = ShellPolicy().evaluate("python --version")
    assert d.allowed is True
    assert d.risk == "standard"
    assert d.normalized == "python --version"


def test_policy_is_classification_only() -> None:
    result = evaluate_shell("echo hello")
    assert set(result) == {"allowed", "risk", "reason", "normalized"}
