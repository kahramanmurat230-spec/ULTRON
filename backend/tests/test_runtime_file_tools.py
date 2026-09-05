import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.runtime import UltronRuntime
from app.tools.file_tools import copy_path, move_path, rename_path, create_folder, delete_path


def test_file_agent_tools_registered():
    runtime = UltronRuntime('config/settings.json')
    for name in ('list_directory','find_files','read_text','write_text','copy_path','move_path','rename_path','create_folder','delete_path'):
        assert runtime.registry.get(name) is not None
    for name in ('write_text','copy_path','move_path','rename_path','create_folder','delete_path'):
        assert runtime.registry.get(name)['dangerous'] is True


def test_file_agent_tool_sandbox_blocks_workspace_escape(tmp_path):
    runtime = UltronRuntime('config/settings.json')
    outside = Path.home() / 'ultron_file_agent_test_escape.txt'
    try:
        with pytest.raises(Exception):
            runtime.registry.get('write_text')['fn'](str(outside), 'blocked')
        assert not outside.exists()
    finally:
        if outside.exists():
            outside.unlink()


def test_file_mutations_are_workspace_bound(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'source.txt'
    source.write_text('ok', encoding='utf-8')
    copied = tmp_path / 'copied.txt'
    copy_path(source, copied)
    assert copied.read_text(encoding='utf-8') == 'ok'
    renamed = tmp_path / 'renamed.txt'
    rename_path(copied, renamed.name)
    assert renamed.exists()
    folder = tmp_path / 'folder'
    create_folder(folder)
    moved = folder / renamed.name
    move_path(renamed, moved)
    assert moved.exists()
    delete_path(moved)
    delete_path(folder)
    with pytest.raises(PermissionError):
        copy_path(source, Path.home() / 'ultron_escape_copy.txt')
