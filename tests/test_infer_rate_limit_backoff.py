from __future__ import annotations

import io
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer


def _http_error(status: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "http://localhost:11434/api/chat",
        status,
        "Too Many Requests" if status == 429 else "Bad Request",
        headers,
        io.BytesIO(b'{"error":"test"}'),
    )


def test_429_retries_with_exponential_backoff_and_then_succeeds(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []
    retry_events: list[tuple[int, int, float]] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_error(429)
        return "ok"

    monkeypatch.setattr(infer.random, "uniform", lambda low, high: high)
    monkeypatch.setattr(infer.time, "sleep", sleeps.append)

    result = infer._call_with_429_backoff(
        operation,
        retries=4,
        base_delay=1.0,
        max_delay=60.0,
        on_retry=lambda attempt, total, delay: retry_events.append(
            (attempt, total, delay)
        ),
    )

    assert result == "ok"
    assert calls == 3
    assert sleeps == [1.0, 2.0]
    assert retry_events == [(1, 4, 1.0), (2, 4, 2.0)]


def test_429_honors_retry_after_when_longer_than_backoff(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, retry_after="12")
        return "ok"

    monkeypatch.setattr(infer.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(infer.time, "sleep", sleeps.append)

    assert infer._call_with_429_backoff(operation, retries=1) == "ok"
    assert sleeps == [12.0]


def test_non_429_is_not_retried(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise _http_error(400)

    monkeypatch.setattr(infer.time, "sleep", sleeps.append)

    with pytest.raises(urllib.error.HTTPError) as raised:
        infer._call_with_429_backoff(operation, retries=8)

    assert raised.value.code == 400
    assert calls == 1
    assert sleeps == []


def test_final_429_is_raised_after_retry_budget(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise _http_error(429)

    monkeypatch.setattr(infer.random, "uniform", lambda low, high: high)
    monkeypatch.setattr(infer.time, "sleep", sleeps.append)

    with pytest.raises(urllib.error.HTTPError) as raised:
        infer._call_with_429_backoff(
            operation,
            retries=2,
            base_delay=1.0,
            max_delay=60.0,
        )

    assert raised.value.code == 429
    assert calls == 3
    assert sleeps == [1.0, 2.0]


def test_api_slot_waits_when_global_concurrency_is_full() -> None:
    events: list[str] = []

    class Semaphore:
        def __init__(self) -> None:
            self.acquire_calls = 0

        def acquire(self, blocking: bool = True) -> bool:
            self.acquire_calls += 1
            events.append(f"acquire:{blocking}")
            return self.acquire_calls > 1

        def release(self) -> None:
            events.append("release")

    result = infer._call_with_api_slot(
        lambda: events.append("operation") or "ok",
        semaphore=Semaphore(),
        on_wait=lambda: events.append("wait"),
    )

    assert result == "ok"
    assert events == [
        "acquire:False",
        "wait",
        "acquire:True",
        "operation",
        "release",
    ]


def test_api_slot_is_released_after_http_error() -> None:
    events: list[str] = []

    class Semaphore:
        def acquire(self, blocking: bool = True) -> bool:
            events.append("acquire")
            return True

        def release(self) -> None:
            events.append("release")

    with pytest.raises(urllib.error.HTTPError):
        infer._call_with_api_slot(
            lambda: (_ for _ in ()).throw(_http_error(429)),
            semaphore=Semaphore(),
        )

    assert events == ["acquire", "release"]
