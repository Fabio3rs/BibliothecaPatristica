"""Process-level locking for index mutations."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO


class LockUnavailableError(RuntimeError):
    pass


class ActiveTransactionError(RuntimeError):
    pass


class IndexOperationLock:
    def __init__(
        self,
        path: Path,
        *,
        exclusive: bool,
        timeout: float = 0.0,
        poll_interval: float = 0.1,
        active_transaction_path: Path | None = None,
        allow_active_transaction: bool = False,
    ) -> None:
        self.path = path.resolve()
        self.exclusive = exclusive
        self.timeout = max(0.0, float(timeout))
        self.poll_interval = max(0.01, float(poll_interval))
        self.active_transaction_path = (
            active_transaction_path.resolve()
            if active_transaction_path is not None
            else self.path.parent / ".structural-target-repair" / "active_transaction.json"
        )
        self.allow_active_transaction = allow_active_transaction
        self._handle: IO[str] | None = None

    def __enter__(self) -> "IndexOperationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        operation = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    handle.seek(0)
                    holder = handle.read().strip() or "unknown holder"
                    handle.close()
                    raise LockUnavailableError(
                        f"index operation lock is busy: {self.path}; last holder: {holder}"
                    ) from exc
                time.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
        self._handle = handle
        if self.active_transaction_path.is_file() and not self.allow_active_transaction:
            self.close()
            raise ActiveTransactionError(
                "an interrupted structural-target transaction must be recovered before "
                f"another index operation: {self.active_transaction_path}"
            )
        if self.exclusive:
            metadata = {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "program": Path(sys.argv[0]).name,
                "mode": "exclusive",
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
            handle.seek(0)
            handle.truncate()
            json.dump(metadata, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return self

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def index_operation_lock(
    path: Path,
    *,
    exclusive: bool,
    timeout: float = 0.0,
    active_transaction_path: Path | None = None,
    allow_active_transaction: bool = False,
) -> IndexOperationLock:
    return IndexOperationLock(
        path,
        exclusive=exclusive,
        timeout=timeout,
        active_transaction_path=active_transaction_path,
        allow_active_transaction=allow_active_transaction,
    )
