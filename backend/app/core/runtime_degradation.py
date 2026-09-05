"""Safe degraded runtime used only when the optional V16 runtime fails to boot.

The V15 API layer intentionally remains available when V16 cannot initialize. Some
legacy startup paths still need a SQLite memory object for mesh synchronization;
this adapter supplies only that dependency without pretending that V16 is online.
"""
from pathlib import Path


class DegradedRuntime:
    """Minimal runtime surface required by V15 startup cleanup/mesh code."""

    def __init__(self, root: Path):
        from app.memory.sqlite_memory import Memory

        self.memory = Memory(path=str(root / "data" / "memory" / "ultron.db"))

    def shutdown(self) -> None:
        """No-op: there are no background services in degraded mode."""
        return None


def install_bridge_guard() -> None:
    """Keep V15 alive if V16 construction fails, without changing availability.

    ``UltronBridge.available`` remains False. Only direct legacy access to
    ``bridge.runtime.memory`` receives a minimal SQLite-backed adapter so optional
    mesh/HUD initialization can degrade instead of crashing the whole server.
    """
    from bridge import UltronBridge

    if getattr(UltronBridge, "_degraded_runtime_guard", False):
        return

    original_getattribute = UltronBridge.__getattribute__

    def guarded_getattribute(self, name):
        value = original_getattribute(self, name)
        if name == "runtime" and value is None:
            root = Path(__file__).resolve().parents[2]
            value = DegradedRuntime(root)
        return value

    UltronBridge.__getattribute__ = guarded_getattribute
    # Keep availability based on the real backing attribute, not the degraded
    # adapter returned for legacy startup code.
    UltronBridge.available = property(
        lambda self: object.__getattribute__(self, "runtime") is not None
    )
    UltronBridge._degraded_runtime_guard = True
