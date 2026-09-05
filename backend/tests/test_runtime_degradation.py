from pathlib import Path


def test_degraded_runtime_has_sqlite_memory(tmp_path):
    from app.core.runtime_degradation import DegradedRuntime

    runtime = DegradedRuntime(Path(tmp_path))
    assert runtime.memory.count() == 0
    runtime.memory.add("SYSTEM", "degraded boot")
    assert runtime.memory.count() == 1
    runtime.shutdown()


def test_bridge_guard_preserves_unavailable_status():
    from app.core.runtime_degradation import install_bridge_guard
    from bridge import UltronBridge

    install_bridge_guard()
    bridge = object.__new__(UltronBridge)
    object.__setattr__(bridge, "runtime", None)
    assert bridge.available is False
    assert bridge.runtime.memory.count() >= 0
