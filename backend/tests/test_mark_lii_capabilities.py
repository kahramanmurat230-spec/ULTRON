from pathlib import Path

from app.core.undo_journal import UndoJournal
from app.core.plugin_manager import PluginManager


def test_undo_restores_previous_content(tmp_path):
    p = tmp_path / "demo.txt"
    p.write_text("before", encoding="utf-8")
    journal = UndoJournal(tmp_path)
    before = journal.snapshot(p)
    p.write_text("after", encoding="utf-8")
    entry_id = journal.record_file_change("write", p, before)
    assert entry_id
    out = journal.undo()
    assert out["ok"] is True
    assert p.read_text(encoding="utf-8") == "before"
    assert journal.list() == []


def test_undo_rejects_path_escape(tmp_path):
    journal = UndoJournal(tmp_path)
    try:
        journal.snapshot(Path(tmp_path).parent / "outside.txt")
    except PermissionError:
        pass
    else:
        raise AssertionError("path escape was not rejected")


def test_plugin_discovery_rejects_invalid_and_loads_valid(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "good.py").write_text(
        'PLUGIN = {"name": "hello", "description": "test"}\n'
        'def run(parameters):\n    return parameters.get("value", "ok")\n', encoding="utf-8")
    (plugins / "bad.py").write_text("PLUGIN = {}\n", encoding="utf-8")
    manager = PluginManager(tmp_path)
    records = manager.discover(reserved={"hello_core"})
    assert any(r["name"] == "hello" and r["valid"] for r in records)
    assert any(r["name"] == "bad" and not r["valid"] for r in records)
    assert manager.call("hello", {"value": "works"}) == "works"
