#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import selectors
import re
import requests
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphabetical_index_db import (
    DEFAULT_DB,
    collect_pending_volume_translations,
    connect_db,
    init_schema,
    upsert_translation_rows,
    volume_already_imported,
)
from patristica_pipeline.common import parse_volume_info
from patristica_pipeline.index_target_locator import parse_ocr_page_xml, resolve_index_targets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILTERED_PAGES_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_filtered_pages.py"
PROMPT_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_prompt.py"
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_alphabetical_index_json.py"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "alphabetical_index_payloads"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "alphabetical_index_logs"
DEFAULT_INTERMEDIATE_ROOT = PROJECT_ROOT / "data" / "intermediate_payloads"
HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 2
INDEX_LINE_RE = re.compile(r"\d{1,4}")
PAGE_HINT_TOKEN_RE = re.compile(r"\b\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\b(?:\s*(?:seq\.?|seqq\.?))?", re.IGNORECASE)
EDITORIAL_TITLE_RE = re.compile(
    r"^(?:"
    r"CAP\.\s*[IVXLCDM0-9]+"
    r"|INDEX(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|INDICES(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|TABLE(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|ORDO(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|ELENCHUS(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r")\b",
    re.IGNORECASE,
)


def run_cmd(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def resolve_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser().resolve()


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _is_index_line(stripped: str) -> bool:
    if not stripped or len(stripped) > 240:
        return False
    if not re.search(r"[A-Za-zÆŒÀ-ÿ]", stripped):
        return False
    if EDITORIAL_TITLE_RE.match(stripped):
        return False
    if not INDEX_LINE_RE.search(stripped):
        return False
    if re.match(r"^\s*\d{1,4}\s+[A-ZÆŒ]", stripped):
        return False
    if stripped.upper().startswith(("INDEX ", "TABLE ", "ELENCHUS ", "ORDO ", "NOTES ", "OBS ", "OCR ")):
        return False
    return True


def _extract_page_hints(text: str) -> list[str]:
    return [match.group(0).strip() for match in PAGE_HINT_TOKEN_RE.finditer(text or "")]


def _derive_lemma_raw(text: str) -> str:
    lemma = re.split(r"\b\d{1,4}\b", text, maxsplit=1)[0].strip()
    lemma = re.sub(r"\s+", " ", lemma)
    lemma = lemma.strip(" ,;:.–—-")
    return lemma


def _derive_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    candidates: list[str] = []
    cleaned = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
    if cleaned:
        candidates.append(cleaned)
    if lemma_raw and lemma_raw not in candidates:
        candidates.append(lemma_raw)
    first_clause = re.split(r"\s*[;,]\s*|\s{2,}", cleaned or lemma_raw, maxsplit=1)[0].strip()
    if first_clause and first_clause not in candidates:
        candidates.append(first_clause)
    context_prefix = context_raw.strip()
    if context_prefix and context_prefix not in candidates:
        candidates.append(context_prefix)
    return _unique_preserve_order(candidates)[:4]


def _looks_structural_title(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    if EDITORIAL_TITLE_RE.match(compact):
        return True
    if compact.upper().startswith(("CAP. ", "INDEX ", "INDICES ", "TABLE ", "ORDO ", "ELENCHUS ")):
        return True
    return False


def _build_context_window(lines: list[str], index: int, *, width: int = 1) -> str:
    start = max(0, index - width)
    end = min(len(lines), index + width + 1)
    window = [line.strip() for line in lines[start:end] if line.strip()]
    return "\n".join(window)


def build_helper_request_artifact(
    *,
    volume_id: str,
    source_root: Path,
    filtered_pages: dict[str, Any],
    helper_request_json: Path,
) -> dict[str, Any]:
    candidate_files = [Path(item) for item in (filtered_pages.get("candidate_files") or [])]
    if not candidate_files:
        candidate_files = [Path(item) for item in (filtered_pages.get("tail_files") or [])]
    if not candidate_files:
        candidate_files = sorted(source_root.glob("*.txt"))

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    for file_path in candidate_files:
        if not file_path.exists():
            continue
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_ocr_page_xml(raw_text)
        lines = [line.strip() for line in parsed["all_text"].splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            if not _is_index_line(line):
                continue
            if not _extract_page_hints(line):
                continue
            lemma_raw = _derive_lemma_raw(line)
            if not lemma_raw:
                continue
            if _looks_structural_title(lemma_raw):
                continue
            context_raw = _build_context_window(lines, idx, width=1)
            if _looks_structural_title(context_raw.splitlines()[0] if context_raw else ""):
                if lemma_raw == (context_raw.splitlines()[0] if context_raw else ""):
                    continue
            request_key = (str(file_path), idx + 1, lemma_raw)
            if request_key in seen:
                continue
            seen.add(request_key)
            entries.append(
                {
                    "entry_id": f"{volume_id.lower()}_{file_path.stem}_{idx + 1:04d}",
                    "lemma_raw": lemma_raw,
                    "query_names": _derive_query_names(lemma_raw, context_raw),
                    "page_hints": _extract_page_hints(line),
                    "page_hint_ints": [int(match.group(0)) for match in INDEX_LINE_RE.finditer(line)],
                    "context_raw": context_raw or line,
                }
            )

    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": HELPER_TOP_K,
            "adjacency_window": HELPER_ADJACENCY_WINDOW,
        },
        "entries": entries,
    }
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


def run_helper_locator(request: dict[str, Any], helper_output_json: Path) -> dict[str, Any]:
    result = resolve_index_targets(request)
    helper_output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def select_volume_ids(root: Path, blob: str, limit: int | None) -> list[str]:
    patterns = [part.strip().upper() for part in blob.split(",") if part.strip()]
    prefixes = [part[:-1] if part.endswith("*") else part for part in patterns]
    volume_ids: list[str] = []
    if not root.exists():
        return volume_ids
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        info = parse_volume_info(child)
        if info is None:
            continue
        if prefixes and not any(info.volume_id.upper().startswith(prefix) for prefix in prefixes):
            continue
        if not (child / "text").exists():
            continue
        volume_ids.append(info.volume_id)
    if limit is not None and limit > 0:
        volume_ids = volume_ids[:limit]
    return volume_ids


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def intermediate_dir_for_volume(root: Path, volume_id: str) -> Path:
    return root / volume_id


def summarize_intermediate_dir(intermediate_dir: Path) -> list[dict[str, Any]]:
    if not intermediate_dir.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in intermediate_dir.iterdir() if p.is_file()):
        files.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
            }
        )
    return files


def inspect_payload_coverage(payload_file: Path) -> tuple[bool, str | None, str | None]:
    payload = read_json(payload_file)
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        return False, None, None
    status = coverage.get("entries_status")
    if not isinstance(status, str):
        return False, None, None
    reason = coverage.get("entries_status_reason")
    reason_text = reason.strip() if isinstance(reason, str) and reason.strip() else None
    return status.startswith("partial"), status, reason_text


def previous_failure_path(output_dir: Path, volume_id: str) -> Path:
    return output_dir / f"{volume_id}_last_failure.json"


def output_checkpoint_error_path(output_dir: Path, volume_id: str) -> Path:
    return output_dir / f"{volume_id}_output_checkpoint_error.txt"


def read_previous_failure(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def write_previous_failure(
    *,
    path: Path,
    volume_id: str,
    stage: str,
    error_summary: str,
    error_detail: str,
    payload_file: Path,
    last_message_file: Path,
    stdout_log_file: Path,
    stderr_log_file: Path,
    stream_log_file: Path,
) -> None:
    payload = {
        "volume_id": volume_id,
        "stage": stage,
        "error_summary": error_summary,
        "error_detail": error_detail,
        "payload_file": str(payload_file),
        "last_message_file": str(last_message_file),
        "stdout_log_file": str(stdout_log_file),
        "stderr_log_file": str(stderr_log_file),
        "stream_log_file": str(stream_log_file),
        "recorded_at": now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_previous_failure(path: Path) -> None:
    path.unlink(missing_ok=True)


def write_output_checkpoint_error(path: Path, error_text: str | None) -> Path | None:
    if not error_text:
        path.unlink(missing_ok=True)
        return None
    path.write_text(error_text, encoding="utf-8")
    return path


def resolve_previous_payload_path(
    *,
    args: argparse.Namespace,
    volume_id: str,
    default_payload_file: Path,
) -> tuple[Path, str]:
    if args.previous_result_json is not None:
        return args.previous_result_json, "explicit_file"
    if args.previous_result_dir is not None:
        candidate = args.previous_result_dir / f"{volume_id}_alphabetical_indices.json"
        if candidate.exists():
            return candidate, "explicit_dir"
        return candidate, "explicit_dir_missing"
    return default_payload_file, "default_output"


def run_codex(
    codex_bin: str,
    prompt: str,
    last_message_path: Path,
    cwd: Path,
    use_json: bool,
    model: str | None,
    verbose: bool,
    stdout_log_path: Path | None = None,
    stderr_log_path: Path | None = None,
    stream_log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        codex_bin,
        "exec",
        "--full-auto",
        "--output-last-message",
        str(last_message_path),
    ]
    if model:
        command.extend(["--model", model])
    if use_json:
        command.append("--json")
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    proc.stdin.write(prompt)
    proc.stdin.close()

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_log = stdout_log_path.open("w", encoding="utf-8") if stdout_log_path else None
    stderr_log = stderr_log_path.open("w", encoding="utf-8") if stderr_log_path else None
    stream_log = stream_log_path.open("w", encoding="utf-8") if stream_log_path else None

    try:
        while selector.get_map():
            for key, _ in selector.select():
                stream = key.fileobj
                label = key.data
                line = stream.readline()
                if line == "":
                    selector.unregister(stream)
                    continue
                if label == "stdout":
                    stdout_parts.append(line)
                    if stdout_log is not None:
                        stdout_log.write(line)
                else:
                    stderr_parts.append(line)
                    if stderr_log is not None:
                        stderr_log.write(line)
                if stream_log is not None:
                    stream_log.write(f"[{label}] {line}")
                if verbose:
                    sys.stdout.write(f"[codex {label}] {line}")
                    sys.stdout.flush()
    finally:
        if stdout_log is not None:
            stdout_log.close()
        if stderr_log is not None:
            stderr_log.close()
        if stream_log is not None:
            stream_log.close()

    return subprocess.CompletedProcess(
        args=command,
        returncode=proc.wait(),
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def validate_payload_file(payload_file: Path) -> None:
    cmd = [
        sys.executable,
        str(IMPORT_SCRIPT),
        "--input",
        str(payload_file),
        "--validate-only",
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"import_alphabetical_index_json.py validation failed for {payload_file.name}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def inspect_output_checkpoint(payload_file: Path) -> tuple[str, str | None]:
    if not payload_file.exists():
        return "missing", None
    try:
        validate_payload_file(payload_file)
    except SystemExit as exc:
        return "invalid", str(exc)
    return "done", None


def build_filtered_pages_artifact(
    *,
    args: argparse.Namespace,
    volume_id: str,
    canonical_path: Path,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(FILTERED_PAGES_SCRIPT),
        "--volume",
        volume_id,
        "--root",
        str(args.root),
        "--output",
        str(canonical_path),
        "--pretty",
    ]
    if args.filtered_pages_json is not None:
        cmd.extend(["--filtered-pages-json", str(args.filtered_pages_json)])
    if args.filtered_pages_dir is not None:
        cmd.extend(["--filtered-pages-dir", str(args.filtered_pages_dir)])
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_alphabetical_filtered_pages.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return read_json(canonical_path)


def build_prompt(
    *,
    volume_id: str,
    source_root: Path,
    collection: str,
    filtered_pages_file: Path,
    previous_payload: Path,
    previous_result_source: str,
    output_checkpoint_status: str,
    output_checkpoint_error_file: Path | None,
    previous_failure_json: Path | None,
    helper_request_json: Path,
    helper_output_json: Path,
    pipeline_scripts_dir: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> str:
    cmd = [
        sys.executable,
        str(PROMPT_SCRIPT),
        "--volume",
        volume_id,
        "--source-root",
        str(source_root),
        "--collection",
        collection,
        "--filtered-pages-json",
        str(filtered_pages_file),
        "--previous-payload",
        str(previous_payload),
        "--previous-result-source",
        previous_result_source,
        "--output-checkpoint-status",
        output_checkpoint_status,
        "--helper-request-json",
        str(helper_request_json),
        "--helper-output-json",
        str(helper_output_json),
        "--pipeline-scripts-dir",
        str(pipeline_scripts_dir),
        "--intermediate-dir",
        str(intermediate_dir),
        "--output-file",
        str(output_file),
    ]
    if output_checkpoint_error_file is not None and output_checkpoint_error_file.exists():
        cmd.extend(["--output-checkpoint-error-file", str(output_checkpoint_error_file)])
    if previous_failure_json is not None and previous_failure_json.exists():
        cmd.extend(["--previous-failure-json", str(previous_failure_json)])
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_alphabetical_prompt.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout


def import_payload(payload_file: Path, db_path: Path, replace: bool) -> None:
    cmd = [
        sys.executable,
        str(IMPORT_SCRIPT),
        "--input",
        str(payload_file),
        "--db",
        str(db_path),
    ]
    if replace:
        cmd.append("--replace")
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"import_alphabetical_index_json.py failed for {payload_file.name}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    print(result.stdout.strip())


def emit_volume_failure(
    *,
    volume_id: str,
    collection: str | None,
    error: BaseException,
) -> None:
    print(
        json.dumps(
            {
                "status": "failed",
                "volume_id": volume_id,
                "collection": collection,
                "error_type": type(error).__name__,
                "error": str(error),
            },
            ensure_ascii=False,
        )
    )


def make_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=0)))
    session.mount("http://", HTTPAdapter(max_retries=Retry(total=0)))
    return session


def normalize_language_list(value: str) -> list[str]:
    seen: set[str] = set()
    languages: list[str] = []
    for part in value.split(","):
        lang = part.strip().lower()
        if not lang or lang in seen:
            continue
        seen.add(lang)
        languages.append(lang)
    return languages


def build_translation_prompt(
    *,
    source_text: str,
    source_kind: str,
    section_kind: str,
    contexts: list[str],
    languages: list[str],
) -> tuple[str, str]:
    language_lines = "\n".join(f"- {language}" for language in languages)
    context_block = "\n".join(contexts[:3]).strip() or "(no extra context)"
    system_prompt = (
        "You are a careful multilingual translator for patristic editorial indexes. "
        "Your output is for human-facing editorial index display, not for paraphrase or explanation. "
        "Translate conservatively. Preserve proper names, numbering, established bibliographic conventions, "
        "and editorial abbreviations unless the target language clearly requires a different established form. "
        "If the original string is already natural and correct in the target language, return it unchanged. "
        "Do not add notes, explanations, expansions, or disambiguation not present in the source. "
        "If OCR seems uncertain or ambiguous, prefer faithful preservation over correction or invention. "
        "Return a JSON object with exactly the requested language keys and string values only."
    )
    user_prompt = (
        "Translate the string using the context as guide.\n\n"
        "Metadata:\n"
        f"- source_kind: {source_kind}\n"
        f"- section_kind: {section_kind or '(none)'}\n\n"
        "Return translations only for these language codes:\n"
        f"{language_lines}\n\n"
        "<context>\n"
        f"{context_block}\n"
        "</context>\n\n"
        "<original_string>\n"
        f"{source_text}\n"
        "</original_string>"
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


def validate_translation_payload(payload: dict[str, Any], languages: list[str]) -> dict[str, str]:
    expected = set(languages)
    actual = set(payload.keys())
    if actual != expected:
        raise ValueError(f"translation keys mismatch: expected={sorted(expected)} actual={sorted(actual)}")
    out: dict[str, str] = {}
    for language in languages:
        value = payload.get(language)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"translation for {language!r} is missing or empty")
        out[language] = re.sub(r"\s+", " ", value).strip()
    return out


def format_translation_progress(
    *,
    volume_id: str,
    completed: int,
    total: int,
    result: dict[str, Any],
) -> str:
    source_text = str(result.get("source_text") or "")[:80]
    source_kind = str(result.get("source_kind") or "")
    section_kind = str(result.get("section_kind") or "")
    translations = dict(result.get("translations") or {})
    attempts = result.get("attempts")
    elapsed_s = result.get("elapsed_s")
    sample_context = str(result.get("sample_context") or "")[:100]
    return (
        f"[INFO] translation {completed}/{total} volume={volume_id} "
        f"kind={source_kind} section_kind={section_kind or '-'} "
        f"langs={sorted(translations.keys())} attempts={attempts} elapsed_s={elapsed_s} "
        f"source={source_text!r} context={sample_context!r}"
    )


def translate_candidate_worker(task: dict[str, Any]) -> dict[str, Any]:
    source_text = str(task["source_text"])
    source_kind = str(task["source_kind"])
    section_kind = str(task.get("section_kind") or "")
    contexts = [str(item) for item in task.get("contexts", []) if str(item).strip()]
    languages = [str(item).strip().lower() for item in task.get("languages", []) if str(item).strip()]
    model = str(task["model"])
    base_url = str(task["base_url"]).rstrip("/")
    api_key = str(task["api_key"])
    timeout = int(task["timeout"])
    retries = max(1, int(task["retries"]))

    system_prompt, user_prompt = build_translation_prompt(
        source_text=source_text,
        source_kind=source_kind,
        section_kind=section_kind,
        contexts=contexts,
        languages=languages,
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "top_p": 1.0,
    }
    if "gpt-5" in model:
        payload["reasoning_effort"] = "medium"
        payload["service_tier"] = "flex"
    else:
        payload["temperature"] = 0.1

    url = base_url + "/chat/completions"
    last_error: str | None = None
    started = time.time()
    for attempt in range(1, retries + 1):
        try:
            with make_session() as session:
                response = session.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    data=json.dumps(payload),
                    timeout=timeout,
                )
            if response.status_code != 200:
                raise ValueError(f"http {response.status_code}: {response.text}")
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                raise ValueError("openai returned no choices")
            content = choices[0].get("message", {}).get("content")
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            translations = validate_translation_payload(extract_json_object(str(content or "")), languages)
            return {
                "source_text": source_text,
                "source_kind": source_kind,
                "section_kind": section_kind,
                "sample_context": contexts[0] if contexts else None,
                "translations": translations,
                "model_name": model,
                "attempts": attempt,
                "elapsed_s": round(time.time() - started, 3),
            }
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(f"translation failed for {source_text!r}: {last_error}")


def run_translation_stage(
    *,
    db_path: Path,
    volume_id: str,
    languages: list[str],
    model: str,
    base_url: str,
    api_key: str,
    workers: int,
    timeout: int,
    retries: int,
    verbose: bool,
) -> dict[str, Any]:
    with connect_db(db_path) as con:
        init_schema(con)
        pending = collect_pending_volume_translations(con, volume_id, languages)

    if not pending:
        return {
            "ran": False,
            "pending_strings": 0,
            "written_rows": 0,
            "completed_strings": 0,
        }

    tasks = [
        {
            "source_text": item["source_text"],
            "source_kind": item["source_kind"],
            "section_kind": item["section_kind"],
            "contexts": item["contexts"],
            "languages": item["missing_languages"],
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "timeout": timeout,
            "retries": retries,
        }
        for item in pending
    ]

    written_rows = 0
    completed_strings = 0
    started = time.time()
    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    if verbose:
        print(
            f"[INFO] translation-start volume={volume_id} pending_strings={len(tasks)} "
            f"workers={min(max(1, workers), len(tasks))} languages={languages} model={model}"
        )
    with connect_db(db_path) as con:
        init_schema(con)
        if workers <= 1:
            results = map(translate_candidate_worker, tasks)
            for result in results:
                written_rows += upsert_translation_rows(
                    con,
                    source_text=str(result["source_text"]),
                    source_kind=str(result["source_kind"]),
                    section_kind=str(result["section_kind"]),
                    sample_context=str(result["sample_context"]) if result["sample_context"] is not None else None,
                    translations=dict(result["translations"]),
                    model_name=str(result["model_name"]),
                )
                completed_strings += 1
                if verbose:
                    print(
                        format_translation_progress(
                            volume_id=volume_id,
                            completed=completed_strings,
                            total=len(tasks),
                            result=result,
                        )
                    )
        else:
            with ctx.Pool(processes=min(workers, len(tasks))) as pool:
                for result in pool.imap_unordered(translate_candidate_worker, tasks):
                    written_rows += upsert_translation_rows(
                        con,
                        source_text=str(result["source_text"]),
                        source_kind=str(result["source_kind"]),
                        section_kind=str(result["section_kind"]),
                        sample_context=str(result["sample_context"]) if result["sample_context"] is not None else None,
                        translations=dict(result["translations"]),
                        model_name=str(result["model_name"]),
                    )
                    completed_strings += 1
                    if verbose:
                        print(
                            format_translation_progress(
                                volume_id=volume_id,
                                completed=completed_strings,
                                total=len(tasks),
                                result=result,
                            )
                        )

    summary = {
        "ran": True,
        "pending_strings": len(tasks),
        "written_rows": written_rows,
        "completed_strings": completed_strings,
        "elapsed_s": round(time.time() - started, 3),
    }
    if verbose:
        print(
            f"[INFO] translation-complete volume={volume_id} completed_strings={summary['completed_strings']} "
            f"written_rows={summary['written_rows']} elapsed_s={summary['elapsed_s']} "
            f"languages={languages} model={model}"
        )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run one Codex extraction pass for one PG/PL/PO volume focused on alphabetical indexes."
    )
    ap.add_argument("--volume-id", help="Volume id, e.g. PG003 or PO025")
    ap.add_argument("--all-volumes", action="store_true", help="Run one Codex pass for every matching volume under --root")
    ap.add_argument("--blob", help="Prefix/glob-style filter for --all-volumes, e.g. PL or PL*")
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste", help="Root directory containing volume folders")
    ap.add_argument("--limit", type=int, default=None, help="Optional max volume count for --all-volumes")
    ap.add_argument("--filtered-pages-json", type=Path, help="Pre-filtered pages JSON for a single-volume run")
    ap.add_argument("--filtered-pages-dir", type=Path, help="Directory with <VOLUME>_filtered_pages.json files for batch runs")
    ap.add_argument("--previous-result-json", type=Path, help="Previous payload JSON for a single-volume rerun")
    ap.add_argument("--previous-result-dir", type=Path, help="Directory with prior <VOLUME>_alphabetical_indices.json payloads")
    ap.add_argument("--codex-bin", default="codex", help="Codex CLI binary")
    ap.add_argument("--model", default=None, help="Optional Codex model override")
    ap.add_argument("--use-json", action="store_true", help="Pass --json to codex exec")
    ap.add_argument("--replace", action="store_true", help="Allow overwriting an existing output payload file and replacing existing DB rows")
    ap.add_argument(
        "--skip-done",
        action="store_true",
        help="Skip volumes already imported into the alphabetical SQLite DB",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database used to detect and store imported alphabetical payloads")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for payload and helper JSON artifacts")
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Directory for persistent Codex logs")
    ap.add_argument("--intermediate-root", type=Path, default=DEFAULT_INTERMEDIATE_ROOT, help="Root directory for per-volume intermediate JSON checkpoints")
    ap.add_argument("--translate", action="store_true", help="Translate frontend-facing alphabetical index strings after import")
    ap.add_argument("--translation-languages", default="en,it,pt-br,fr", help="Comma-separated target language codes for translation")
    ap.add_argument("--translation-model", default=None, help="OpenAI model used for translation")
    ap.add_argument("--translation-openai-url", default="https://api.openai.com/v1", help="OpenAI-compatible base URL for translation")
    ap.add_argument("--translation-openai-api-key", default=None, help="OpenAI-compatible API key for translation")
    ap.add_argument("--translation-workers", type=int, default=1, help="Worker count for translation requests")
    ap.add_argument("--translation-timeout", type=int, default=180, help="Per-request timeout in seconds for translation")
    ap.add_argument("--translation-retries", type=int, default=3, help="Retry count per translation request")
    ap.add_argument("--keep-temp", action="store_true", help="Keep the last-message artifact after a successful run")
    ap.add_argument("--dry-run", action="store_true", help="Build filtered pages and prompt, then stop before calling Codex")
    ap.add_argument("--verbose", action="store_true", help="Stream Codex stdout/stderr live to the terminal")
    ap.add_argument(
        "--continue-on-error",
        action="store_true",
        help="For batch runs, emit a failure result for the current volume and continue to the next one.",
    )
    args = ap.parse_args()

    args.root = resolve_path(args.root) or args.root
    args.filtered_pages_json = resolve_path(args.filtered_pages_json)
    args.filtered_pages_dir = resolve_path(args.filtered_pages_dir)
    args.previous_result_json = resolve_path(args.previous_result_json)
    args.previous_result_dir = resolve_path(args.previous_result_dir)
    args.db = resolve_path(args.db) or args.db
    args.output_dir = resolve_path(args.output_dir) or args.output_dir
    args.log_dir = resolve_path(args.log_dir) or args.log_dir
    args.intermediate_root = resolve_path(args.intermediate_root) or args.intermediate_root
    args.translation_languages = normalize_language_list(args.translation_languages)
    args.translation_openai_api_key = args.translation_openai_api_key or os.getenv("OPENAI_API_KEY")
    args.translation_model = args.translation_model or os.getenv("OPENAI_MODEL") or "gpt-5-mini"

    if args.filtered_pages_json and args.all_volumes:
        raise SystemExit("--filtered-pages-json is only valid for a single-volume run.")
    if args.previous_result_json and args.all_volumes:
        raise SystemExit("--previous-result-json is only valid for a single-volume run.")
    if args.translate and not args.translation_languages:
        raise SystemExit("Use at least one language in --translation-languages when --translate is enabled.")
    if args.translate and not args.translation_openai_api_key:
        raise SystemExit("Translation requires --translation-openai-api-key or OPENAI_API_KEY.")

    if args.blob and not args.all_volumes:
        args.all_volumes = True

    if args.all_volumes:
        blob = args.blob or "PG*,PL*,PO*"
        volume_ids = select_volume_ids(args.root, blob, args.limit)
        if not volume_ids:
            raise SystemExit(f"No volumes selected for blob {blob!r}.")
    else:
        if not args.volume_id:
            raise SystemExit("Use --volume-id or --all-volumes/--blob.")
        volume_ids = [args.volume_id]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.intermediate_root.mkdir(parents=True, exist_ok=True)

    failed_volume_ids: list[str] = []

    for idx, volume_id in enumerate(volume_ids, start=1):
        volume_root = args.root / volume_id
        collection: str | None = None
        try:
            text_root = volume_root / "text"
            if not text_root.exists():
                raise SystemExit(f"Text directory not found: {text_root}")

            info = parse_volume_info(volume_root)
            if info is None:
                raise SystemExit(f"Could not parse volume info from {volume_root}")
            collection = info.series

            payload_file = args.output_dir / f"{volume_id}_alphabetical_indices.json"
            previous_payload_file, previous_result_source = resolve_previous_payload_path(
                args=args,
                volume_id=volume_id,
                default_payload_file=payload_file,
            )
            output_checkpoint_status, output_checkpoint_error = inspect_output_checkpoint(payload_file)
            db_imported = volume_already_imported(args.db, volume_id)
            skip_extraction = args.skip_done and db_imported and not args.replace

            if skip_extraction and not args.translate:
                print(
                    json.dumps(
                        {
                            "status": "skipped",
                            "reason": "already_imported",
                            "volume_id": volume_id,
                            "collection": collection,
                            "db": str(args.db),
                            "payload_file": str(payload_file),
                            "output_checkpoint_status": output_checkpoint_status,
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            if payload_file.exists() and output_checkpoint_status == "invalid" and not args.replace and not args.dry_run:
                if args.verbose:
                    print(
                        f"[WARN] existing payload for {volume_id} is invalid but will be reused as previous-result checkpoint: "
                        f"{output_checkpoint_error}"
                    )

            print(f"[INFO] volume {idx}/{len(volume_ids)}: {volume_id} ({collection})")

            filtered_pages_file = args.output_dir / f"{volume_id}_filtered_pages.json"
            helper_request_json = args.output_dir / f"{volume_id}_helper_request.json"
            helper_output_json = args.output_dir / f"{volume_id}_helper_output.json"
            pipeline_scripts_dir = PROJECT_ROOT / "scripts" / "pipeline_index_extraction"
            intermediate_dir = intermediate_dir_for_volume(args.intermediate_root, volume_id)
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            intermediate_files = summarize_intermediate_dir(intermediate_dir)
            last_message_path = args.output_dir / f"{volume_id}_last_message.txt"
            stdout_log_path = args.log_dir / f"{volume_id}_codex_stdout.log"
            stderr_log_path = args.log_dir / f"{volume_id}_codex_stderr.log"
            stream_log_path = args.log_dir / f"{volume_id}_codex_stream.log"
            failure_path = previous_failure_path(args.output_dir, volume_id)
            checkpoint_error_file = output_checkpoint_error_path(args.output_dir, volume_id)
            previous_failure = read_previous_failure(failure_path)
            checkpoint_error_file = write_output_checkpoint_error(checkpoint_error_file, output_checkpoint_error)
            filtered_source = "skipped_existing_import"
            has_partial_coverage = False
            coverage_status = None
            coverage_reason = None

            if skip_extraction and args.dry_run:
                print(
                    json.dumps(
                        {
                            "status": "dry-run",
                            "volume_id": volume_id,
                            "collection": collection,
                            "db": str(args.db),
                            "db_imported": db_imported,
                            "extraction_skipped": True,
                            "would_run_translation": args.translate,
                            "translation_languages": args.translation_languages if args.translate else [],
                            "translation_model": args.translation_model if args.translate else None,
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            if not skip_extraction:
                filtered_pages = build_filtered_pages_artifact(
                    args=args,
                    volume_id=volume_id,
                    canonical_path=filtered_pages_file,
                )
                filtered_source = str(filtered_pages.get("source") or "unknown")
                if args.verbose:
                    print(f"[INFO] filtered pages source: {filtered_source}")
                    print(f"[INFO] filtered pages written to {filtered_pages_file}")
                    print(f"[INFO] previous result source: {previous_result_source}")
                    print(f"[INFO] previous result path: {previous_payload_file}")
                    print(f"[INFO] output checkpoint status: {output_checkpoint_status}")
                    print(f"[INFO] db imported status: {db_imported}")
                if (
                    filtered_source == "fallback_internal"
                    and args.filtered_pages_dir is not None
                    and args.filtered_pages_json is None
                ):
                    print(
                        f"[WARN] no external filtered-pages file found for {volume_id} in {args.filtered_pages_dir}; "
                        "falling back to internal heuristic prefilter."
                    )

                prompt = build_prompt(
                    volume_id=volume_id,
                    source_root=text_root,
                    collection=collection,
                    filtered_pages_file=filtered_pages_file,
                    previous_payload=previous_payload_file,
                    previous_result_source=previous_result_source,
                    output_checkpoint_status=output_checkpoint_status,
                    output_checkpoint_error_file=checkpoint_error_file,
                    previous_failure_json=failure_path if previous_failure is not None else None,
                    helper_request_json=helper_request_json,
                    helper_output_json=helper_output_json,
                    pipeline_scripts_dir=pipeline_scripts_dir,
                    intermediate_dir=intermediate_dir,
                    output_file=payload_file,
                )

                if args.dry_run:
                    prompt_path = args.output_dir / f"{volume_id}_prompt.txt"
                    prompt_path.write_text(prompt, encoding="utf-8")
                    print(
                        json.dumps(
                            {
                                "status": "dry-run",
                                "volume_id": volume_id,
                                "collection": collection,
                                "filtered_pages_source": filtered_source,
                                "previous_result_source": previous_result_source,
                                "previous_result_file": str(previous_payload_file),
                                "previous_result_exists": previous_payload_file.exists(),
                                "output_checkpoint_status": output_checkpoint_status,
                                "output_checkpoint_error": output_checkpoint_error,
                                "output_checkpoint_error_file": str(checkpoint_error_file) if checkpoint_error_file else None,
                                "db": str(args.db),
                                "db_imported": db_imported,
                                "filtered_pages_file": str(filtered_pages_file),
                                "prompt_file": str(prompt_path),
                                "payload_file": str(payload_file),
                                "helper_request_file": str(helper_request_json),
                                "helper_output_file": str(helper_output_json),
                                "pipeline_scripts_dir": str(pipeline_scripts_dir),
                                "intermediate_dir": str(intermediate_dir),
                                "intermediate_files": intermediate_files,
                                "last_message_file": str(last_message_path),
                                "stdout_log_file": str(stdout_log_path),
                                "stderr_log_file": str(stderr_log_path),
                                "stream_log_file": str(stream_log_path),
                                "would_run_codex": True,
                                "would_run_translation": args.translate,
                            },
                            ensure_ascii=False,
                        )
                    )
                    continue

                if args.verbose:
                    print(f"[INFO] payload file: {payload_file}")
                    print(f"[INFO] last message file: {last_message_path}")
                    print(f"[INFO] intermediate dir: {intermediate_dir}")
                    if previous_payload_file.exists():
                        print(f"[INFO] previous payload checkpoint will be available to Codex: {previous_payload_file}")
                result = run_codex(
                    codex_bin=args.codex_bin,
                    prompt=prompt,
                    last_message_path=last_message_path,
                    cwd=PROJECT_ROOT,
                    use_json=args.use_json,
                    model=args.model,
                    verbose=args.verbose,
                    stdout_log_path=stdout_log_path,
                    stderr_log_path=stderr_log_path,
                    stream_log_path=stream_log_path,
                )
                if result.returncode != 0:
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="codex_exec",
                        error_summary="codex exec failed",
                        error_detail=f"codex exec failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise SystemExit(
                        f"codex exec failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                    )

                last_message = last_message_path.read_text(encoding="utf-8").strip()
                try:
                    ack = json.loads(last_message)
                except json.JSONDecodeError as exc:
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="ack_check",
                        error_summary="Last message is not valid JSON",
                        error_detail=f"Last message is not valid JSON: {exc}\n{last_message}",
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise SystemExit(f"Last message is not valid JSON: {exc}\n{last_message}") from exc

                if ack.get("status") != "ok":
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="ack_check",
                        error_summary="Codex did not return ok",
                        error_detail=f"Codex did not return ok: {ack}",
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise SystemExit(f"Codex did not return ok: {ack}")
                if ack.get("volume_id") != volume_id:
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="ack_check",
                        error_summary="volume_id mismatch in ack",
                        error_detail=f"volume_id mismatch in ack: {ack}",
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise SystemExit(f"volume_id mismatch in ack: {ack}")
                if ack.get("written_file") != str(payload_file):
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="ack_check",
                        error_summary="written_file mismatch in ack",
                        error_detail=f"written_file mismatch in ack: {ack}",
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise SystemExit(f"written_file mismatch in ack: {ack}")

                try:
                    validate_payload_file(payload_file)
                except SystemExit as exc:
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="validate_payload",
                        error_summary="payload validation failed",
                        error_detail=str(exc),
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise
                has_partial_coverage, coverage_status, coverage_reason = inspect_payload_coverage(payload_file)
                try:
                    import_payload(payload_file, args.db, replace=args.replace)
                except SystemExit as exc:
                    write_previous_failure(
                        path=failure_path,
                        volume_id=volume_id,
                        stage="import_payload",
                        error_summary="payload import failed",
                        error_detail=str(exc),
                        payload_file=payload_file,
                        last_message_file=last_message_path,
                        stdout_log_file=stdout_log_path,
                        stderr_log_file=stderr_log_path,
                        stream_log_file=stream_log_path,
                    )
                    raise

                clear_previous_failure(failure_path)

            translation_summary = {
                "ran": False,
                "pending_strings": 0,
                "written_rows": 0,
                "completed_strings": 0,
            }
            if args.translate:
                translation_summary = run_translation_stage(
                    db_path=args.db,
                    volume_id=volume_id,
                    languages=args.translation_languages,
                    model=args.translation_model,
                    base_url=args.translation_openai_url,
                    api_key=args.translation_openai_api_key,
                    workers=max(1, args.translation_workers),
                    timeout=max(1, args.translation_timeout),
                    retries=max(1, args.translation_retries),
                    verbose=args.verbose,
                )

            if has_partial_coverage:
                warning = (
                    f"[WARN] partial coverage for {volume_id}: {coverage_status}"
                    + (f" - {coverage_reason}" if coverage_reason else "")
                )
                print(warning, file=sys.stderr)

            print(
                json.dumps(
                    {
                        "status": "ok",
                        "volume_id": volume_id,
                        "collection": collection,
                        "db": str(args.db),
                        "imported": not skip_extraction,
                        "extraction_skipped": skip_extraction,
                        "filtered_pages_source": filtered_source,
                        "previous_result_source": previous_result_source,
                        "previous_result_file": str(previous_payload_file),
                        "output_checkpoint_status_before_run": output_checkpoint_status,
                        "filtered_pages_file": str(filtered_pages_file),
                        "payload_file": str(payload_file),
                        "helper_request_file": str(helper_request_json),
                        "helper_output_file": str(helper_output_json),
                        "pipeline_scripts_dir": str(pipeline_scripts_dir),
                        "intermediate_dir": str(intermediate_dir),
                        "intermediate_files": intermediate_files,
                        "last_message_file": str(last_message_path),
                        "stdout_log_file": str(stdout_log_path),
                        "stderr_log_file": str(stderr_log_path),
                        "stream_log_file": str(stream_log_path),
                        "coverage_warning": has_partial_coverage,
                        "coverage_status": coverage_status,
                        "coverage_reason": coverage_reason,
                        "translation_requested": args.translate,
                        "translation_ran": translation_summary["ran"],
                        "translation_pending_strings": translation_summary["pending_strings"],
                        "translation_completed_strings": translation_summary["completed_strings"],
                        "translation_written_rows": translation_summary["written_rows"],
                        "translation_languages": args.translation_languages if args.translate else [],
                        "translation_model": args.translation_model if args.translate else None,
                    },
                    ensure_ascii=False,
                )
            )

            if not args.keep_temp:
                last_message_path.unlink(missing_ok=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if not args.continue_on_error:
                raise
            if args.verbose and not isinstance(exc, SystemExit):
                traceback.print_exc()
            emit_volume_failure(volume_id=volume_id, collection=collection, error=exc)
            failed_volume_ids.append(volume_id)
            continue

    if failed_volume_ids:
        print(
            json.dumps(
                {
                    "status": "batch-complete-with-errors",
                    "failed_volumes": failed_volume_ids,
                    "failed_count": len(failed_volume_ids),
                    "total_volumes": len(volume_ids),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
