"""A lock file next to the database that the running server holds, so that a restore cannot replace the database under it.

The server takes `<database>.server.lock` when it starts and keeps it until it stops; the operating system frees it when the process ends, also after a crash.
`held(path)` tells whether a server holds it now. It works on Windows (msvcrt) and POSIX (fcntl)."""

from __future__ import annotations

import os
from pathlib import Path


class ServerRunning(Exception):
    """A server holds the database."""


def lock_path(db_path: str | os.PathLike) -> Path:
    return Path(str(db_path) + ".server.lock")


def _try_lock(f) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def acquire(db_path: str | os.PathLike):
    """Hold the lock for the life of the process. Raises ServerRunning when another server holds it. Returns the open file (keep a reference)."""
    p = lock_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")
    if f.tell() == 0:
        f.write(b"\0")
        f.flush()
    if not _try_lock(f):
        f.close()
        raise ServerRunning(f"another SPC server uses {db_path}")
    return f


def held(db_path: str | os.PathLike) -> bool:
    """True when a running server holds the lock of this database."""
    p = lock_path(db_path)
    if not p.exists():
        return False
    with open(p, "a+b") as f:
        if _try_lock(f):
            _unlock(f)
            return False
        return True
