"""WAVE 2 hardening — read-only SQLite/WAL guard (app.core.sqlite_guard).

A read-only database must never look writable: with WAL journal mode already
in the header and idempotent init statements (CREATE TABLE IF NOT EXISTS,
INSERT OR IGNORE), sqlite3 can open a read-only DB silently and fail only on
the first real write. MemoryStore and DurableEventBus must instead fail fast
with PermissionError based on a REAL filesystem probe (POSIX bits + Windows
ACL/read-only attribute), including the directory (WAL -wal/-shm sidecars).

Tests skip honestly when the platform/user cannot express "read-only"
(root ignores permission bits; Windows directories ignore chmod).
"""
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.sqlite_guard import ensure_writable_sqlite_path  # noqa: E402
from app.events.bus import DurableEventBus  # noqa: E402
from app.memory.store_v3 import MemoryStore  # noqa: E402


def _chmod_can_block_writes() -> bool:
    """chmod-based read-only is meaningless for root on POSIX."""
    if hasattr(os, "geteuid"):
        return os.geteuid() != 0
    return True  # Windows: chmod sets the read-only attribute (real)


def _make_writable_db(path):
    ms = MemoryStore(db_path=str(path))
    ms.write("baseline", memory_type="SEMANTIC")
    ms.close()


def _make_writable_bus_db(path):
    bus = DurableEventBus(db_path=str(path))
    bus.publish("t.x", {"a": 1})
    bus.close()


# ------------------------------------------------------------ guard unit
def test_guard_passes_writable_path(tmp_path):
    p = tmp_path / "new.db"
    assert ensure_writable_sqlite_path(p) == p
    assert not p.exists()                       # guard does not create the DB
    assert [f for f in os.listdir(tmp_path) if "probe" in f] == []


def test_guard_rejects_readonly_file(tmp_path):
    if not _chmod_can_block_writes():
        pytest.skip("root: chmod cannot block writes — honest skip")
    p = tmp_path / "ro.db"
    p.write_bytes(b"x")
    os.chmod(p, 0o444)
    try:
        with pytest.raises(PermissionError):
            ensure_writable_sqlite_path(p)
    finally:
        os.chmod(p, 0o644)


def test_guard_rejects_readonly_directory(tmp_path):
    if not _chmod_can_block_writes():
        pytest.skip("root: chmod cannot block writes — honest skip")
    d = tmp_path / "sub"
    d.mkdir()
    os.chmod(d, 0o555)
    if os.access(d, os.W_OK):
        os.chmod(d, 0o755)
        pytest.skip("platform ignores directory chmod (Windows) — honest skip")
    try:
        with pytest.raises(PermissionError):
            ensure_writable_sqlite_path(d / "new.db")
    finally:
        os.chmod(d, 0o755)


# ------------------------------------------------------------ MemoryStore
def test_memory_store_readonly_db_raises_permission_error(tmp_path):
    """The WAL silent-success case: DB already WAL, only the file read-only,
    directory writable. Constructor must raise PermissionError, not open."""
    if not _chmod_can_block_writes():
        pytest.skip("root: chmod cannot block writes — honest skip")
    p = tmp_path / "v3.db"
    _make_writable_db(p)
    os.chmod(p, 0o444)
    try:
        with pytest.raises(PermissionError):
            MemoryStore(db_path=str(p))
    finally:
        os.chmod(p, 0o644)


def test_memory_store_readonly_dir_raises_permission_error(tmp_path):
    if not _chmod_can_block_writes():
        pytest.skip("root: chmod cannot block writes — honest skip")
    d = tmp_path / "memdir"
    d.mkdir()
    os.chmod(d, 0o555)
    if os.access(d, os.W_OK):
        os.chmod(d, 0o755)
        pytest.skip("platform ignores directory chmod (Windows) — honest skip")
    try:
        with pytest.raises(PermissionError):
            MemoryStore(db_path=str(d / "v3.db"))
    finally:
        os.chmod(d, 0o755)


def test_memory_store_writable_path_unaffected(tmp_path):
    ms = MemoryStore(db_path=str(tmp_path / "ok.db"))
    r = ms.write("guard regresyon yok", memory_type="SEMANTIC")
    assert r["ok"] is True and r["status"] == "created"
    assert ms.get(r["id"])["content"] == "guard regresyon yok"
    assert [f for f in os.listdir(tmp_path) if "probe" in f] == []


# ------------------------------------------------------------ EventBus
def test_event_bus_readonly_db_raises_permission_error(tmp_path):
    if not _chmod_can_block_writes():
        pytest.skip("root: chmod cannot block writes — honest skip")
    p = tmp_path / "bus.db"
    _make_writable_bus_db(p)
    os.chmod(p, 0o444)
    try:
        with pytest.raises(PermissionError):
            DurableEventBus(db_path=str(p))
    finally:
        os.chmod(p, 0o644)


def test_event_bus_readonly_dir_raises_permission_error(tmp_path):
    if not _chmod_can_block_writes():
        pytest.skip("root: chmod cannot block writes — honest skip")
    d = tmp_path / "busdir"
    d.mkdir()
    os.chmod(d, 0o555)
    if os.access(d, os.W_OK):
        os.chmod(d, 0o755)
        pytest.skip("platform ignores directory chmod (Windows) — honest skip")
    try:
        with pytest.raises(PermissionError):
            DurableEventBus(db_path=str(d / "bus.db"))
    finally:
        os.chmod(d, 0o755)


def test_event_bus_writable_path_unaffected(tmp_path):
    bus = DurableEventBus(db_path=str(tmp_path / "okbus.db"))
    got = []
    bus.subscribe("x.*", lambda t, p: got.append(t))
    r = bus.publish("x.y", {"k": 1})
    assert "event_id" in r and got == ["x.y"]
    assert [f for f in os.listdir(tmp_path) if "probe" in f] == []
