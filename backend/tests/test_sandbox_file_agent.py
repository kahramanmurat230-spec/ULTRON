import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.security.sandbox import FilesystemSandbox, SandboxViolation


def test_sandbox_blocks_parent_escape(tmp_path):
    sandbox = FilesystemSandbox({}, workspace_root=str(tmp_path))
    with pytest.raises(SandboxViolation):
        sandbox.validate_write(tmp_path / '..' / 'escape.txt')


def test_sandbox_allows_workspace_write(tmp_path):
    sandbox = FilesystemSandbox({}, workspace_root=str(tmp_path))
    target = sandbox.validate_write(tmp_path / 'ok.txt')
    assert target == (tmp_path / 'ok.txt').resolve()


def test_sandbox_rejects_unc():
    sandbox = FilesystemSandbox({}, workspace_root=os.getcwd())
    with pytest.raises(SandboxViolation):
        sandbox.validate_read(r'\\server\share\secret.txt')
