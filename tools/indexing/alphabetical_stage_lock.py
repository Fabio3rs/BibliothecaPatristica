"""Shared, nonblocking per-volume lock for all mutating analysis stages."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import hashlib
from pathlib import Path


@contextmanager
def volume_lock(analysis_db, volume_id, no_api=False):
    if no_api:
        yield
        return
    database = Path(analysis_db).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    suffix = hashlib.sha256(volume_id.encode()).hexdigest()[:16]
    # Keep the original Jev lock filename so older Jev processes also conflict.
    path = database.with_name(database.name + '.jev-' + suffix + '.lock')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('analysis stage already running for this volume') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def locked_stage(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with volume_lock(kwargs['analysis_db'], kwargs['volume_id']):
            return function(*args, **kwargs)
    return wrapped
