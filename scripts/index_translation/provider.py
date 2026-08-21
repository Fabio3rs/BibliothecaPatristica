"""OpenAI-compatible translation provider for extracted index strings."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.index_translation.augmentation import (
    build_latin_lexicon_tools,
    run_tool_guided_chat,
)


RETRYABLE_HTTP_STATUSES = {408, 409, 425, 429}
NON_RETRYABLE_API_ERROR_CODES = {
    "billing_hard_limit_reached",
    "credit_balance_exhausted",
    "insufficient_quota",
    "organization_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
    "project_spend_limit_exceeded",
}


@dataclass
class TranslationHTTPError(Exception):
    status_code: int
    response_text: str
    retry_after_s: float | None = None
    error_code: str | None = None

    @property
    def retryable(self) -> bool:
        if self.error_code in NON_RETRYABLE_API_ERROR_CODES:
            return False
        return self.status_code in RETRYABLE_HTTP_STATUSES or self.status_code >= 500

    def __str__(self) -> str:
        return f"http {self.status_code}: {self.response_text}"


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            return None
        return max(0.0, retry_at.timestamp() - (time.time() if now is None else now))
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay(
    *,
    attempt: int,
    base_s: float,
    max_s: float,
    jitter_ratio: float,
    retry_after_s: float | None = None,
) -> float:
    exponent = min(60, max(0, attempt - 1))
    exponential = min(max_s, base_s * (2.0**exponent))
    jitter = random.uniform(0.0, exponential * jitter_ratio)
    return max(retry_after_s or 0.0, exponential) + jitter


def normalize_language_list(value: str) -> list[str]:
    seen: set[str] = set()
    languages: list[str] = []
    for part in value.split(","):
        language = part.strip().lower()
        if not language or language in seen:
            continue
        seen.add(language)
        languages.append(language)
    return languages


def make_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=0)))
    session.mount("http://", HTTPAdapter(max_retries=Retry(total=0)))
    return session


def build_translation_prompt(
    *,
    source_text: str,
    contexts: list[str],
    languages: list[str],
    linguistic_analysis: dict[str, Any] | None = None,
    tools_enabled: bool = False,
) -> tuple[str, str]:
    language_lines = "\n".join(f"- {language}" for language in languages)
    context_block = "\n".join(contexts[:3]).strip() or "(no extra context)"
    system_prompt = (
        "You are a careful multilingual translator for patristic editorial indexes. "
        "Translate for human-facing editorial display, not as paraphrase or explanation. "
        "Preserve proper names, numbering, established bibliographic conventions, and editorial "
        "abbreviations unless the target language clearly has an established equivalent. If the "
        "original is already natural in the target language, return it unchanged. Do not add notes, "
        "expansions, or disambiguation absent from the source. Preserve uncertain OCR rather than "
        "inventing a correction. Return a JSON object with exactly the requested language keys and "
        "string values only."
    )
    if tools_enabled:
        system_prompt += (
            " You may consult the local Latin lexicon for genuinely ambiguous Latin words. Treat "
            "its output as evidence, not as mandatory word-for-word equivalents."
        )
    analysis_block = ""
    if linguistic_analysis and linguistic_analysis.get("status") == "ok":
        analysis_block = (
            "\n\n<linguistic_analysis_advisory>\n"
            + json.dumps(linguistic_analysis, ensure_ascii=False, separators=(",", ":"))
            + "\n</linguistic_analysis_advisory>"
        )
        system_prompt += (
            " A bounded CLTK analysis may be supplied as advisory evidence. It can be wrong on OCR "
            "or mixed-language material; the original string always has priority."
        )
    user_prompt = (
        "Translate the string using the context as guide.\n\n"
        "Return translations only for these language codes:\n"
        f"{language_lines}\n\n"
        "<context>\n"
        f"{context_block}\n"
        "</context>\n\n"
        "<original_string>\n"
        f"{source_text}\n"
        "</original_string>"
        f"{analysis_block}"
    )
    return system_prompt, user_prompt


def extract_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty model output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model output does not contain a json object") from None
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def validate_translation_payload(
    payload: dict[str, Any], languages: list[str]
) -> dict[str, str]:
    expected = set(languages)
    actual = set(payload)
    if actual != expected:
        raise ValueError(
            f"translation keys mismatch: expected={sorted(expected)} actual={sorted(actual)}"
        )
    translations: dict[str, str] = {}
    for language in languages:
        value = payload.get(language)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"translation for {language!r} is missing or empty")
        normalized = re.sub(r"\s+", " ", value).strip()
        try:
            nested_value = json.loads(normalized)
        except (json.JSONDecodeError, TypeError):
            nested_value = None
        if isinstance(nested_value, (dict, list)):
            raise ValueError(
                f"translation for {language!r} contains serialized structured data"
            )
        translations[language] = normalized
    return translations


def translate_candidate_worker(task: dict[str, Any]) -> dict[str, Any]:
    source_text = str(task["source_text"])
    contexts = [str(item) for item in task.get("contexts", []) if str(item).strip()]
    languages = [str(item).strip().lower() for item in task.get("languages", [])]
    model = str(task["model"])
    base_url = str(task["base_url"]).rstrip("/")
    api_key = str(task["api_key"])
    timeout = int(task["timeout"])
    retries = max(1, int(task["retries"]))
    backoff_base_s = max(0.0, float(task.get("backoff_base_s", 1.0)))
    backoff_max_s = max(backoff_base_s, float(task.get("backoff_max_s", 60.0)))
    jitter_ratio = max(0.0, float(task.get("jitter_ratio", 0.25)))
    tools_enabled = bool(task.get("tools_enabled", False))
    dictionary_dir = task.get("dictionary_dir")
    max_tool_rounds = max(0, int(task.get("max_tool_rounds", 2)))

    tools: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}
    if tools_enabled:
        if not dictionary_dir:
            raise RuntimeError("translation tools require a dictionary directory")
        tools, handlers = build_latin_lexicon_tools(Path(str(dictionary_dir)))
        if not tools:
            raise RuntimeError(f"no supported translation dictionaries found in {dictionary_dir}")

    system_prompt, user_prompt = build_translation_prompt(
        source_text=source_text,
        contexts=contexts,
        languages=languages,
        linguistic_analysis=task.get("linguistic_analysis"),
        tools_enabled=bool(tools),
    )
    base_payload: dict[str, Any] = {"model": model, "top_p": 1.0}
    if "gpt-5" in model:
        base_payload["reasoning_effort"] = "medium"
        base_payload["service_tier"] = "flex"
    else:
        base_payload["temperature"] = 0.1

    url = base_url + "/chat/completions"
    last_error: str | None = None
    retry_wait_s = 0.0
    started = time.time()
    for attempt in range(1, retries + 1):
        try:
            with make_session() as session:

                def request_chat(request_payload: dict[str, Any]) -> dict[str, Any]:
                    response = session.post(
                        url,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        data=json.dumps(request_payload),
                        timeout=timeout,
                    )
                    if response.status_code != 200:
                        error_code: str | None = None
                        try:
                            error_body = response.json()
                            error = (
                                error_body.get("error")
                                if isinstance(error_body, dict)
                                else None
                            )
                            if isinstance(error, dict):
                                raw_error_code = error.get("code") or error.get("type")
                                if raw_error_code:
                                    error_code = str(raw_error_code).strip().casefold()
                        except (requests.RequestException, ValueError):
                            pass
                        raise TranslationHTTPError(
                            status_code=response.status_code,
                            response_text=response.text[:2000],
                            retry_after_s=parse_retry_after(
                                response.headers.get("Retry-After")
                            ),
                            error_code=error_code,
                        )
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("translation endpoint returned a non-object response")
                    return body

                guided = run_tool_guided_chat(
                    request_chat=request_chat,
                    payload=base_payload,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=tools,
                    handlers=handlers,
                    max_tool_rounds=max_tool_rounds,
                )
            translations = validate_translation_payload(
                extract_json_object(guided.content), languages
            )
            return {
                "string_id": int(task["string_id"]),
                "source_text": source_text,
                "sample_context": contexts[0] if contexts else None,
                "translations": translations,
                "model_name": model,
                "attempts": attempt,
                "retry_wait_s": round(retry_wait_s, 3),
                "elapsed_s": round(time.time() - started, 3),
                "tool_call_count": len(guided.tool_calls),
            }
        except Exception as exc:
            last_error = str(exc)
            if isinstance(exc, TranslationHTTPError) and not exc.retryable:
                break
            if attempt >= retries:
                break
            delay = retry_delay(
                attempt=attempt,
                base_s=backoff_base_s,
                max_s=backoff_max_s,
                jitter_ratio=jitter_ratio,
                retry_after_s=(
                    exc.retry_after_s
                    if isinstance(exc, TranslationHTTPError)
                    else None
                ),
            )
            retry_wait_s += delay
            time.sleep(delay)
    raise RuntimeError(f"translation failed for {source_text!r}: {last_error}")
