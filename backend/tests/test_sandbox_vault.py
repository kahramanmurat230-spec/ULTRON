"""PHASE 10: filesystem sandbox + credential vault (security foundations)."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from app.security.sandbox import (
    FilesystemSandbox, SandboxViolation, sandboxed_read_text,
    sandboxed_write_text,
)
from app.security.vault import CredentialVault


# ---------------------------------------------------------------- sandbox
def make_sandbox(tmp):
    ws = Path(tmp) / "workspace"
    (ws / "sub").mkdir(parents=True, exist_ok=True)
    (ws / "sub" / "f.txt").write_text("inside", encoding="utf-8")
    outside = Path(tmp) / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "o.txt").write_text("secret-outside", encoding="utf-8")
    return FilesystemSandbox(settings={
        "security": {"filesystem": {
            "allowed_read_roots": [str(ws), str(outside)],
            "allowed_write_roots": [str(ws)],
        }}}), ws, outside


def test_sandbox_allows_inside_read_and_write(tmp_path):
    sb, ws, _ = make_sandbox(str(tmp_path))
    p = ws / "sub" / "new.txt"
    assert sandboxed_write_text(sb, p, "ok") == str(p)
    assert sandboxed_read_text(sb, p) == "ok"


def test_sandbox_blocks_traversal_escape(tmp_path):
    sb, ws, _ = make_sandbox(str(tmp_path))
    sneaky = ws / "sub" / ".." / ".." / ".." / "etc" / "passwd"
    with pytest.raises(SandboxViolation):
        sb.validate_read(sneaky)


def test_sandbox_blocks_write_outside_root(tmp_path):
    sb, ws, outside = make_sandbox(str(tmp_path))
    # outside is READ-allowed but WRITE-denied
    sb.validate_read(outside / "o.txt")
    with pytest.raises(SandboxViolation):
        sandboxed_write_text(sb, outside / "evil.txt", "x")


def test_sandbox_blocks_absolute_escape(tmp_path):
    sb, _, _ = make_sandbox(str(tmp_path))
    with pytest.raises(SandboxViolation):
        sb.validate_read("/etc/shadow")


def test_sandbox_blocks_unc_paths(tmp_path):
    sb, _, _ = make_sandbox(str(tmp_path))
    with pytest.raises(SandboxViolation):
        sb.validate_read("\\\\attacker\\share\\file.txt")
    with pytest.raises(SandboxViolation):
        sb.validate_read("//attacker/share/file.txt")


def test_sandbox_blocks_symlink_escape(tmp_path):
    sb, ws, _ = make_sandbox(str(tmp_path))
    link = ws / "link-to-outside"
    os.symlink(str(Path(tmp_path)), link)
    with pytest.raises(SandboxViolation):
        sb.validate_write(link / "outside" / "x.txt")


def test_sandbox_system_dirs_not_writable(tmp_path):
    sb = FilesystemSandbox(workspace_root=str(tmp_path))
    with pytest.raises(SandboxViolation):
        sb.validate_write("C:/Windows/System32/evil.dll")


# ---------------------------------------------------------------- vault
def vault_ok():
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not vault_ok(), reason="cryptography not installed")
def test_vault_roundtrip_encrypted_at_rest(tmp_path):
    v = CredentialVault(dir_path=str(tmp_path / "v"))
    v.set("weather_api", "SUPERSECRET-1234", meta="test")
    raw = (tmp_path / "v" / "secrets.json").read_text(encoding="utf-8")
    assert "SUPERSECRET-1234" not in raw  # plaintext NEVER at rest
    assert v.get("weather_api") == "SUPERSECRET-1234"


@pytest.mark.skipif(not vault_ok(), reason="cryptography not installed")
def test_vault_redact_uses_stored_values(tmp_path):
    v = CredentialVault(dir_path=str(tmp_path / "v"))
    v.set("tkn", "zz-super-token-zz")
    out = v.redact("connect using zz-super-token-zz now")
    assert "zz-super-token-zz" not in out


@pytest.mark.skipif(not vault_ok(), reason="cryptography not installed")
def test_vault_list_names_never_exposes_values(tmp_path):
    v = CredentialVault(dir_path=str(tmp_path / "v"))
    v.set("a", "VALUE-A")
    listing = json.dumps(v.list_names())
    assert "VALUE-A" not in listing
    assert v.delete("a") is True and v.delete("a") is False


@pytest.mark.skipif(not vault_ok(), reason="cryptography not installed")
def test_vault_env_key_precedence(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ULTRON_VAULT_KEY", key)
    v = CredentialVault(dir_path=str(tmp_path / "v2"))
    v.set("x", "y")
    v2 = CredentialVault(dir_path=str(tmp_path / "v2"))
    assert v2.get("x") == "y"  # same env key reopens the vault


@pytest.mark.skipif(not vault_ok(), reason="cryptography not installed")
def test_vault_keyfile_permissions(tmp_path):
    v = CredentialVault(dir_path=str(tmp_path / "v3"))
    v.set("k", "v")
    mode = (tmp_path / "v3" / ".vault.key").stat().st_mode & 0o777
    if os.name == "posix":
        assert mode == 0o600
