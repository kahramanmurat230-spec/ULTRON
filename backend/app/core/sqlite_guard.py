"""Real filesystem writability guard for SQLite (+WAL) stores.

Why this module exists
----------------------
`sqlite3.connect()` on a read-only database file can SILENTLY succeed when:
- the database header is already in WAL journal mode (the pragma becomes a
  no-op instead of a write), and
- every init statement is idempotent (`CREATE TABLE IF NOT EXISTS`,
  `INSERT OR IGNORE` on existing rows).

In that state a read-only database *looks writable*: construction succeeds,
reads succeed, and the failure only surfaces much later on the first real
write as `sqlite3.OperationalError: attempt to write a readonly database`.
If the containing directory is writable, SQLite may even create fresh
`-wal`/`-shm` sidecar files, reinforcing the false "writable" impression.

This guard performs a REAL filesystem permission probe (not just
`os.access`, which on Windows only inspects the read-only attribute and
ignores ACLs) and fails fast with `PermissionError` before a connection is
opened. It never grants anything; it only removes a false sense of
durability. Security posture is unchanged — reads remain possible by
opening the store elsewhere; this protects the WRITE path's honesty.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path


def _file_writable(path: Path) -> bool:
    """True only if the existing file can really be opened for writing."""
    if not os.access(path, os.W_OK):
        return False
    try:
        # Real handle probe: honours POSIX permission bits AND Windows ACLs
        # / read-only attributes, unlike os.access alone.
        with open(path, "r+b"):
            return True
    except OSError:
        return False


def _dir_writable(directory: Path) -> bool:
    """True only if a file can really be created inside the directory.

    WAL mode requires creating `-wal` and `-shm` sidecar files next to the
    database file, so directory writability is part of the guarantee.
    """
    if not os.access(directory, os.W_OK):
        return False
    probe = directory / f".ultron-wprobe-{uuid.uuid4().hex[:8]}"
    try:
        with open(probe, "xb"):
            pass
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def ensure_writable_sqlite_path(db_path) -> Path:
    """Fail fast with PermissionError if the SQLite path is not really writable.

    Checks, in order:
    1. parent directory exists / can be created,
    2. parent directory really accepts new files (WAL sidecars),
    3. existing db file really accepts writes; a missing db file must be
       creatable in that directory.

    Returns the resolved Path for convenience. Raises PermissionError (never
    a bare sqlite error) so callers can distinguish "you do not have
    permission" from "the database is corrupted/busy".
    """
    path = Path(db_path)
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PermissionError(
                f"database directory cannot be created: {parent} ({exc})"
            ) from exc
    if not _dir_writable(parent):
        raise PermissionError(
            f"database directory is not writable (SQLite WAL requires "
            f"-wal/-shm sidecar files): {parent}"
        )
    if path.exists():
        if not _file_writable(path):
            raise PermissionError(
                f"database file is read-only; refusing to pretend it is "
                f"writable: {path}"
            )
    else:
        probe = parent / f".{path.name}.create-probe-{uuid.uuid4().hex[:8]}"
        try:
            with open(probe, "xb"):
                pass
        except OSError as exc:
            raise PermissionError(
                f"database file cannot be created: {path} ({exc})"
            ) from exc
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
    return path
