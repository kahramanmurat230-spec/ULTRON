from app.core.runtime import UltronRuntime


def test_runtime_exposes_live_voice_shutdown_hook():
    assert callable(getattr(UltronRuntime, "stop_live_voice", None))
