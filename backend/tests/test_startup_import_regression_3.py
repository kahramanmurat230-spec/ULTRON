def test_import_test_runner():
    import test_runner
    assert test_runner.run_all is not None
