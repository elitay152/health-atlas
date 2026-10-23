"""Single-host publication: readers keep the old inode until a full commit exists."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile


@contextmanager
def snapshot(path, validate_previous=None):
    """Serialize publishers, build beside the destination, replace only on success.

    Requires a local POSIX filesystem; this is not a distributed lock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ('.' + path.name + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Snapshot publication is busy; retry later.') from None
        temp = None
        try:
            if path.exists() and validate_previous:
                validate_previous(path)
            fd, name = tempfile.mkstemp(dir=path.parent, suffix='.duckdb')
            os.close(fd)
            temp = Path(name)
            temp.unlink()  # DuckDB creates its own header.
            yield temp
            with temp.open('rb') as published:
                os.fsync(published.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
                Path(str(temp) + '.wal').unlink(missing_ok=True)
            fcntl.flock(lock, fcntl.LOCK_UN)
