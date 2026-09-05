def test_test_runner_import_is_safe():
    import test_runner
    assert callable(test_runner.run_all)
