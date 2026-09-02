import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.runtime import UltronRuntime


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
        try:
            runtime.registry.get('write_text')['fn'](str(outside), 'blocked')
        except Exception:
            pass
        assert not outside.exists()
    finally:
        if outside.exists():
            outside.unlink()
