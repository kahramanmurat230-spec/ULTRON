def test_test_runner_exposes_validation_entrypoint():
    import test_runner
    assert callable(test_runner.run_all)
