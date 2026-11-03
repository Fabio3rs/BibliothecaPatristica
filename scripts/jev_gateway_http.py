"""Single HTTP attempts shared by serial and process-pool Jev execution."""
import math
import re
import time

import requests


def parse_answer(result):
    try:
        p = result["answers"]["topic_present"]["probability"]
    except (KeyError, TypeError):
        raise ValueError("missing_probability") from None
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1 or not math.isfinite(p):
        raise ValueError("invalid_probability")
    return p


def usage_fields(result):
    def number(value):
        try:
            return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0
        except (ValueError, TypeError, OverflowError):
            return False
    result = result if isinstance(result, dict) else {}
    usage = result.get("usage") or {}
    metadata = result.get("providerMetadata") or {}
    gateway = metadata.get("gateway") if isinstance(metadata, dict) else {}
    gateway = gateway if isinstance(gateway, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    return {key: value if number(value) else None for key, value in {
        "input_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens"),
        "cost_usd": gateway.get("cost"), "market_cost_usd": gateway.get("marketCost")}.items()}


def safe_gateway_error(response, result, api_key):
    """Keep diagnostic fields only, redacting credentials BEFORE bounding output."""
    def clean(value):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None
        text = str(value)
        if api_key:
            text = text.replace(api_key, "[REDACTED]")
        text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
        text = re.sub(r"(?i)([\"']?(?:authorization|api[_-]?key|token|secret)[\"']?\s*[:=]\s*)"
                      r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[REDACTED]", text)
        return " ".join(text.split())[:1500]

    error = result.get("error") if isinstance(result, dict) else None
    fields = error if isinstance(error, dict) else (result if isinstance(result, dict) else {})
    detail = {key: clean(fields.get(key)) for key in ("code", "type", "message")}
    if isinstance(error, str):
        detail["message"] = clean(error)
    if not any(detail.values()) and result is None:
        detail["message"] = clean(getattr(response, "text", ""))
    headers = getattr(response, "headers", {})
    for name in ("x-request-id", "x-vercel-id"):
        if headers.get(name):
            detail[name] = clean(headers[name])
    return {key: value for key, value in detail.items() if value}


def request_once(session, endpoint, api_key, payload, timeout):
    started = time.monotonic()
    attempt = {}
    try:
        response = session.post(endpoint, headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=timeout, allow_redirects=False)
        attempt["http_status"] = response.status_code
        try:
            result = response.json()
        except ValueError:
            result = None
        attempt.update(usage_fields(result))
        if not 200 <= response.status_code < 300:
            attempt["error"] = "http_error"
            attempt["gateway_error"] = safe_gateway_error(response, result, api_key)
        else:
            try:
                attempt["probability"] = parse_answer(result)
            except ValueError as exc:
                attempt["error"] = str(exc)
    except requests.Timeout:
        attempt["error"] = "timeout"
    except requests.RequestException:
        attempt["error"] = "transport_error"
    attempt["latency_ms"] = (time.monotonic() - started) * 1000
    return attempt
