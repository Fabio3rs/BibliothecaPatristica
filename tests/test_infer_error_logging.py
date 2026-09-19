from __future__ import annotations

import io
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer


def test_format_inference_error_includes_urllib_http_response_body() -> None:
    headers = Message()
    headers["Content-Type"] = "application/json; charset=utf-8"
    headers["X-Request-ID"] = "req-123"
    error = urllib.error.HTTPError(
        "https://ollama.example/api/chat?token=secret",
        400,
        "Bad Request",
        headers,
        io.BytesIO(b'{"error": "model does not support images"}'),
    )

    result = infer.format_inference_error(
        error,
        stage="api",
        backend=infer.BackendSpec("ollama", "qwen3.5:397b-cloud"),
        total_elapsed=2.5,
        api_elapsed=2.0,
        image_source="crop",
        image_bytes=b"png",
        prompt_chars=11,
    )

    assert "stage=api" in result
    assert "backend=ollama:qwen3.5:397b-cloud" in result
    assert "http_status=400" in result
    assert "model does not support images" in result
    assert "X-Request-ID" in result
    assert "token=secret" not in result
    assert "image_bytes=3" in result


def test_format_inference_error_includes_requests_response_body() -> None:
    response = requests.Response()
    response.status_code = 429
    response.reason = "Too Many Requests"
    response.url = "https://api.openai.com/v1/chat/completions"
    response.headers["Retry-After"] = "10"
    response._content = b'{"error":{"message":"rate limited"}}'
    error = requests.HTTPError(response=response)

    result = infer.format_inference_error(
        error,
        stage="api",
        backend=infer.BackendSpec("openai", "gpt-5-mini"),
        total_elapsed=1.5,
        api_elapsed=1.25,
        image_source="line_image",
        image_bytes=b"image",
        prompt_chars=20,
    )

    assert "http_status=429" in result
    assert "rate limited" in result
    assert "Retry-After" in result


def test_format_inference_error_keeps_non_http_exception_message() -> None:
    result = infer.format_inference_error(
        ValueError("imagem vazia"),
        stage="prepare",
        backend=None,
        total_elapsed=0.1,
        api_elapsed=None,
        image_source="line_image",
        image_bytes=None,
        prompt_chars=11,
    )

    assert "type=ValueError" in result
    assert "message='imagem vazia'" in result


def test_format_inference_error_dumps_unexpected_openai_response_safely() -> None:
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response.url = "https://api.meta.ai/v1/chat/completions?token=secret"
    response.headers["X-Request-ID"] = "req-456"
    response.headers["Content-Type"] = "application/json"
    response.headers["Set-Cookie"] = "session=secret"
    response._content = b'{"choices":[{"message":{"content":null}}]}'
    error = infer.UnexpectedAPIResponseError(
        "resposta OpenAI incompatível: choices[0].message.content é NoneType, esperado str",
        response=response,
        request_payload={
            "model": "muse-spark-1.3-contributor",
            "messages": [{"role": "user", "content": "Transcribe:"}],
        },
    )

    result = infer.format_inference_error(
        error,
        stage="api_response",
        backend=infer.BackendSpec("openai", "muse-spark-1.3-contributor"),
        total_elapsed=8.31,
        api_elapsed=8.2,
        image_source="crop",
        image_bytes=b"png",
        prompt_chars=11,
    )

    assert "http_status=200" in result
    assert "Content-Type" in result
    assert "X-Request-ID" in result
    assert "Set-Cookie': '<redacted>'" in result
    assert "response_payload='{" in result
    assert "request_payload={'model': 'muse-spark-1.3-contributor'" in result
    assert "token=secret" not in result
    assert "session=secret" not in result


def test_compact_error_body_redacts_credentials() -> None:
    result = infer._compact_error_body(
        b'{"authorization": "Bearer secret", "api_key": "also-secret"}'
    )

    assert "secret" not in result
    assert result.count("<redacted>") == 2
