from app.core.plugin_manager import PluginManager


def test_plugin_size_limit_is_isolated(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "too_big.py").write_text("x = 1\n" * 20, encoding="utf-8")

    manager = PluginManager(tmp_path, max_plugin_bytes=5)
    records = manager.discover()

    assert records[0]["valid"] is False
    assert "size limit" in records[0]["error"]
    assert manager.plugins == {}


def test_plugin_collision_is_rejected(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "one.py").write_text(
        'PLUGIN = {"name": "safe_tool", "description": "ok"}\n'
        'def run(parameters): return parameters\n',
        encoding="utf-8",
    )
    (plugins / "two.py").write_text(
        'PLUGIN = {"name": "safe_tool", "description": "duplicate"}\n'
        'def run(parameters): return parameters\n',
        encoding="utf-8",
    )

    manager = PluginManager(tmp_path)
    records = manager.discover()

    assert len(manager.plugins) == 1
    assert sum(1 for record in records if not record["valid"]) == 1
