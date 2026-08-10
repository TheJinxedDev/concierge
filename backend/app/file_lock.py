"""Small cross-platform advisory file locks for local atomic workflows."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import time
from typing import Iterator

if os.name == "nt":  # pragma: no cover - selected by the host platform
    import msvcrt
else:  # pragma: no cover - exercised on POSIX CI
    import fcntl


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.01,
) -> Iterator[None]:
    """Hold one advisory process/inter-process lock until the context exits."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    locked = False
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out acquiring file lock: {lock_path}") from error
                    time.sleep(poll_interval)
        else:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except (BlockingIOError, OSError) as error:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out acquiring file lock: {lock_path}") from error
                    time.sleep(poll_interval)
        yield
    finally:
        if locked:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


__all__ = ["exclusive_file_lock"]
