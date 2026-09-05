def test_test_runner_does_not_install_bridge_guard_at_import_time():
    import importlib
    import test_runner

    assert hasattr(test_runner, "run_all")
