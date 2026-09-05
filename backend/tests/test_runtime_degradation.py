from pathlib import Path


def test_degraded_runtime_has_sqlite_memory(tmp_path):
    from app.core.runtime_degradation import DegradedRuntime

    runtime = DegradedRuntime(Path(tmp_path))
    assert runtime.memory.count() == 0
    runtime.memory.add("SYSTEM", "degraded boot")
    assert runtime.memory.count() == 1
    runtime.shutdown()


def test_bridge_guard_preserves_unavailable_status(monkeypatch):
    from app.core.runtime_degradation import install_bridge_guard
    from bridge import UltronBridge

    original = UltronBridge.__getattribute__
    install_bridge_guard()
    assert UltronBridge._degraded_runtime_guard is True
    # The guard must not turn a failed V16 runtime into a false "available" state.
    assert not UltronBridge.available.fget
    # Restore the method for isolation if another test constructs a bridge.
    monkeypatch.setattr(UltronBridge, "__getattribute__", original, raising=False)
