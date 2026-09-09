"""Level 52: deterministic shell safety policy tests.

P0-1 regression set: destructive commands must be blocked cross-platform,
and the block can NEVER be overridden by approval (the policy is evaluated
at both shell execution boundaries immediately before subprocess creation,
independently of any approval state).
"""
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


# ---------------- P0-1: Linux/Unix destructive vectors ----------------

def test_linux_destructive_rm_is_blocked() -> None:
    policy = ShellPolicy()
    vectors = (
        "rm -rf /",
        "rm -rf /*",
        "rm -fr /",
        "rm -r -f /",
        "rm --recursive --force /",
        "sudo rm -rf /",
        "sudo rm -rf /*",
        "rm -rf ~",
        "rm -rf /home",
        "rm -rf /home/*",
        "rm -rf /home/user",
        "rm -rf /etc",
        "rm -rf /usr/local",
        "rm -rf /var/log",
        "rm -rf .",
        "rm -rf ..",
        "rm -rf *",
        "rm -rf $HOME",
        "rm -rf ' /*'",
        "echo hi && rm -rf /",
        "rm -rf / & echo done",
    )
    for command in vectors:
        d = policy.evaluate(command)
        assert d.allowed is False, f"must be blocked: {command!r}"
        assert d.risk == "blocked", f"must be blocked risk: {command!r}"


def test_linux_flag_order_and_prefixes_are_handled() -> None:
    policy = ShellPolicy()
    # flags after targets, privilege prefix, env prefix, quoted wrapper
    for command in (
        "rm / -rf",
        "sudo rm / --recursive --force",
        "FOO=bar rm -rf /",
        "bash -c 'rm -rf /'",
        "sh -c \"rm -rf /\"",
        "sudo sh -c 'rm -rf /'",
        "nice rm -rf /",
        "timeout 10 rm -rf /",
    ):
        d = policy.evaluate(command)
        assert d.allowed is False, f"must be blocked: {command!r}"


def test_linux_device_destruction_is_blocked() -> None:
    policy = ShellPolicy()
    vectors = (
        "mkfs.ext4 /dev/sda1",
        "mkfs.btrfs /dev/nvme0n1",
        "mkfs /dev/sdb",
        "mkswap /dev/sda3",
        "wipefs -a /dev/sda",
        "blkdiscard /dev/sdb",
        "sfdisk /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "dd of=/dev/nvme0n1",
        "sudo dd if=/dev/zero of=/dev/mmcblk0",
        "cat x.img > /dev/sda",
        "echo data >> /dev/sdb",
    )
    for command in vectors:
        d = policy.evaluate(command)
        assert d.allowed is False, f"must be blocked: {command!r}"


def test_fork_bombs_are_blocked() -> None:
    policy = ShellPolicy()
    for command in (
        ":(){ :|:& };:",
        ":() { : | : & } ; :",
        "f(){ f|f& };f",
        "bomb() { bomb | bomb & }; bomb",
    ):
        d = policy.evaluate(command)
        assert d.allowed is False, f"must be blocked: {command!r}"


def test_recursive_chmod_chown_on_system_paths_is_blocked() -> None:
    policy = ShellPolicy()
    vectors = (
        "chmod -R 777 /",
        "chmod -R 000 /etc",
        "chmod --recursive 755 /usr",
        "sudo chmod -R 777 /var",
        "chown -R user /etc",
        "chown --recursive user:group /usr",
        "chmod -R 777 ~",
        "chmod 777 /etc/shadow",
        "chown user /etc/passwd",
        "rm /etc/passwd",
        "rm -f /etc/shadow",
    )
    for command in vectors:
        d = policy.evaluate(command)
        assert d.allowed is False, f"must be blocked: {command!r}"


def test_power_and_process_destruction_is_blocked() -> None:
    policy = ShellPolicy()
    for command in (
        "reboot", "halt", "poweroff", "init 0", "init 6",
        "systemctl reboot", "systemctl poweroff",
        "kill -9 -1", "kill -1", "killall5",
        "xargs rm -rf", "ls | xargs rm",
        "find / -delete", "find . -delete", "find / -name x -exec rm {} ;",
    ):
        d = policy.evaluate(command)
        assert d.allowed is False, f"must be blocked: {command!r}"


def test_legitimate_commands_still_pass() -> None:
    policy = ShellPolicy()
    for command, expected_risk in (
        ("rm -rf ./build", "standard"),
        ("rm -rf build/", "standard"),
        ("rm -rf /home/user/project/build", "standard"),
        ("rm -rf ~/Downloads/old-cache", "standard"),
        ("rm -rf /tmp/cache", "standard"),
        ("rm notes.txt", "standard"),
        ("rm -f log.txt", "standard"),
        ("chmod +x script.sh", "standard"),
        ("chmod -R 755 ./site", "standard"),
        ("chown -R user ./project", "standard"),
        ("dd if=x.iso of=/dev/null", "standard"),
        ("ls -la", "standard"),
        ("python --version", "standard"),
        ("git status", "standard"),
        ("curl https://example.com", "high"),
        ("git push", "high"),
    ):
        d = policy.evaluate(command)
        assert d.allowed is True, f"must stay allowed: {command!r} ({d.reason})"
        assert d.risk == expected_risk, f"wrong risk for {command!r}: {d.risk}"


# ---------------- P0-1: approval can NEVER override a hard block ----------------

def test_blocked_command_is_rejected_by_shell_executor_even_though_gated() -> None:
    """ShellExecutor is the post-approval execution boundary: it must refuse a
    blocked command regardless of how the caller handled approval (it has no
    approval parameter at all — policy is an independent second gate)."""
    import asyncio
    from pathlib import Path
    import tempfile

    from app.tools.shell import ShellExecutor

    with tempfile.TemporaryDirectory() as tmp:
        ex = ShellExecutor(Path(tmp))
        result = asyncio.run(ex.execute("rm -rf /"))
        assert result["ok"] is False
        assert result["blocked"] is True
        result = asyncio.run(ex.execute("sudo rm -rf /"))
        assert result["ok"] is False
        assert result["blocked"] is True
        assert "subprocess" not in result


def test_approved_terminal_command_is_still_blocked_by_policy() -> None:
    """End-to-end through the V15 tools layer: the terminal tool evaluates the
    policy before subprocess creation; approval state cannot bypass it."""
    import asyncio
    from tools import ToolRegistry

    async def _broadcast(_payload):
        pass

    async def _on_activity(_text, _kind="info"):
        pass

    reg = ToolRegistry(
        workspace=".",
        broadcast=_broadcast,
        on_activity=_on_activity,
        get_ai_status=lambda: {"connected": False},
    )
    result = asyncio.run(reg.execute("terminal", "rm -rf /"))
    assert result["ok"] is False
    assert result.get("blocked") is True



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
