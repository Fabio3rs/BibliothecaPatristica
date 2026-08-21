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
    get_volume_quality,
    init_schema,
    upsert_translation_rows,
    volume_already_imported,
)
from patristica_pipeline.common import parse_volume_info
from patristica_pipeline.alphabetical_compact_driver import run_compact_extraction
from patristica_pipeline.alphabetical_analysis_db import (
    DEFAULT_ANALYSIS_DB,
    connect_analysis_db,
    export_seed_rows,
    init_analysis_schema,
    review_occurrences,
    volume_status,
)
from patristica_pipeline.alphabetical_analysis_pipeline import (
    assemble_stage,
    discover_stage,
    extract_stage,
    locate_stage,
    verify_stage,
)
from patristica_pipeline.index_localization_helpers import (
    build_helper_request_artifact,
    run_helper_locator,
)
from patristica_pipeline.index_payload_evidence import verify_index_payload_evidence
from patristica_pipeline.index_workplan import build_index_workplan, reconcile_workplan_progress
from patristica_pipeline.translation_augmentation import (
    build_latin_lexicon_tools,
    run_tool_guided_chat,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILTERED_PAGES_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_filtered_pages.py"
PROMPT_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_prompt.py"
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_alphabetical_index_json.py"
EXPORT_WEB_SCRIPT = PROJECT_ROOT / "tools" / "export_alphabetical_indices.py"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "alphabetical_index_payloads"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "alphabetical_index_logs"
DEFAULT_INTERMEDIATE_ROOT = PROJECT_ROOT / "data" / "intermediate_payloads"
DEFAULT_WEB_ALPHA_OUT = PROJECT_ROOT / "web" / "public" / "alpha"
DEFAULT_TRANSLATION_DICTIONARY_DIR = Path(
    os.getenv(
        "PATRISTICA_DICTIONARY_DIR",
        "/mnt/projects/Projects/Dicionarios/dicionarios",
    )
)
COMPLETE_COVERAGE_STATUSES = {"ok", "complete", "extracted", "recovered"}
ANALYSIS_COMMANDS = {
    "discover",
    "extract",
    "locate",
    "verify",
    "assemble",
    "run",
    "status",
    "review",
    "export-seeds",
}


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


def volume_extraction_policy(
    *,
    db_imported: bool,
    quality_status: str | None,
    skip_done: bool,
    redo_invalid: bool,
    replace: bool,
) -> tuple[bool, bool, str]:
    if redo_invalid:
        if quality_status == "needs_reextract":
            return False, True, "quality_needs_reextract"
        if quality_status is None:
            return True, False, "quality_unassessed"
        return True, False, f"quality_{quality_status}"
    if skip_done and db_imported and not replace:
        if quality_status == "needs_reextract":
            return False, True, "quality_needs_reextract"
        if quality_status == "partial":
            return False, True, "quality_partial"
        return True, False, "already_imported"
    return False, replace, "selected"


def export_web_indices(*, db_path: Path, output_dir: Path) -> None:
    cmd = [
        sys.executable,
        str(EXPORT_WEB_SCRIPT),
        "--db",
        str(db_path),
        "--out",
        str(output_dir),
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            "export_alphabetical_indices.py failed\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    print(result.stdout.strip())


def maybe_export_web_indices(args: argparse.Namespace) -> bool:
    if args.no_export_web or args.dry_run:
        return False
    export_web_indices(db_path=args.db, output_dir=args.web_alpha_out)
    return True


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
    entries_status = coverage.get("entries_status")
    locator_status = coverage.get("locator_status")
    status = (
        entries_status
        if isinstance(entries_status, str)
        else locator_status
        if isinstance(locator_status, str)
        else None
    )
    if status is None:
        return False, None, None
    reason = coverage.get("entries_status_reason")
    reason_text = reason.strip() if isinstance(reason, str) and reason.strip() else None
    return (
        status.startswith("partial") or locator_status == "partial",
        status,
        reason_text,
    )


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


def infer_failure_stage(error: BaseException) -> str:
    text = str(error).casefold()
    if "chunked codex extraction" in text:
        return "chunk_extraction"
    if "omitted stable objects" in text:
        return "fragment_consumption"
    if "evidence verification" in text:
        return "payload_evidence"
    if "compact repair" in text or "repair result" in text:
        return "compact_repair"
    if "compact codex phase" in text or "codex exec" in text:
        return "codex_exec"
    if "last message" in text or "ack" in text:
        return "ack_check"
    if "import_alphabetical_index_json.py" in text:
        return "import_payload"
    if "validation" in text or "validate" in text:
        return "validate_payload"
    return "pipeline"


def write_pipeline_quality_reports(
    *,
    payload_file: Path,
    intermediate_dir: Path,
    evidence_sample_size: int,
    max_unverified_ratio: float,
    skip_evidence_check: bool,
) -> Path | None:
    payload = read_json(payload_file)
    evidence_path: Path | None = None
    if not skip_evidence_check:
        evidence = verify_index_payload_evidence(payload, sample_size=evidence_sample_size)
        evidence_path = intermediate_dir / "payload_evidence_report.json"
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        sampled = int(evidence.get("sampled_entry_count") or 0)
        verified_ratio = evidence.get("verified_ratio")
        ratio = 1.0 - float(verified_ratio) if verified_ratio is not None else 0.0
        unverified = round(sampled * ratio)
        if int(evidence.get("unjustified_empty_list_section_count") or 0):
            raise SystemExit(
                "OCR evidence verification found unjustified empty list-bearing sections; "
                f"see {evidence_path}"
            )
        if ratio > max_unverified_ratio:
            raise SystemExit(
                "OCR evidence verification exceeded the limit: "
                f"{unverified}/{sampled} unverified ({ratio:.3f}); see {evidence_path}"
            )
    return evidence_path


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
        "--ephemeral",
        "--sandbox",
        "workspace-write",
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
        "--profile",
        "alphabetical",
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
    workplan_json: Path,
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
        "--workplan-json",
        str(workplan_json),
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
    tools_enabled: bool = False,
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
    if tools_enabled:
        system_prompt += (
            " You may consult the local Latin lexicon tool when a Latin word is genuinely "
            "ambiguous or materially affects the translation. Query only a small set of likely "
            "dictionary lemmas. Treat dictionary results as contextual evidence, not mandatory "
            "word-for-word equivalents. Do not use the Latin tool for Greek, proper names, page "
            "references, or text already written naturally in a target language."
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
    tool_call_count = result.get("tool_call_count", 0)
    tool_names = result.get("tool_names") or []
    sample_context = str(result.get("sample_context") or "")[:100]
    return (
        f"[INFO] translation {completed}/{total} volume={volume_id} "
        f"kind={source_kind} section_kind={section_kind or '-'} "
        f"langs={sorted(translations.keys())} attempts={attempts} elapsed_s={elapsed_s} "
        f"tool_calls={tool_call_count} tools={tool_names} "
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
    tools_enabled = bool(task.get("tools_enabled", False))
    dictionary_dir_raw = task.get("dictionary_dir")
    max_tool_rounds = max(0, int(task.get("max_tool_rounds", 2)))

    tools: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}
    if tools_enabled:
        if not dictionary_dir_raw:
            raise RuntimeError("translation tools require a dictionary directory")
        tools, handlers = build_latin_lexicon_tools(Path(str(dictionary_dir_raw)))
        if not tools:
            raise RuntimeError(
                f"no supported translation dictionaries found in {dictionary_dir_raw}"
            )

    system_prompt, user_prompt = build_translation_prompt(
        source_text=source_text,
        source_kind=source_kind,
        section_kind=section_kind,
        contexts=contexts,
        languages=languages,
        tools_enabled=bool(tools),
    )
    payload = {
        "model": model,
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
                        raise ValueError(f"http {response.status_code}: {response.text}")
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("openai returned a non-object response")
                    return body

                guided = run_tool_guided_chat(
                    request_chat=request_chat,
                    payload=payload,
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
                "source_text": source_text,
                "source_kind": source_kind,
                "section_kind": section_kind,
                "sample_context": contexts[0] if contexts else None,
                "translations": translations,
                "model_name": model,
                "attempts": attempt,
                "elapsed_s": round(time.time() - started, 3),
                "tool_call_count": len(guided.tool_calls),
                "tool_names": sorted(
                    {
                        str(item.get("name") or "")
                        for item in guided.tool_calls
                        if item.get("name")
                    }
                ),
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
    tools_enabled: bool = False,
    dictionary_dir: Path | None = None,
    max_tool_rounds: int = 2,
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
            "tools_enabled": tools_enabled,
            "dictionary_dir": str(dictionary_dir) if dictionary_dir is not None else None,
            "max_tool_rounds": max_tool_rounds,
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


def _compact_agent_runner(
    *,
    args: argparse.Namespace,
    volume_id: str,
    prompt: str,
    expected_output: Path,
    phase_id: str,
) -> None:
    phase_dir = args.log_dir / volume_id / Path(phase_id)
    phase_dir.mkdir(parents=True, exist_ok=True)
    attempt = len(list(phase_dir.glob("attempt_*_prompt.txt"))) + 1
    prefix = phase_dir / f"attempt_{attempt:03d}"
    prompt_file = prefix.with_name(prefix.name + "_prompt.txt")
    last_message_file = prefix.with_name(prefix.name + "_last_message.json")
    stdout_file = prefix.with_name(prefix.name + "_stdout.log")
    stderr_file = prefix.with_name(prefix.name + "_stderr.log")
    stream_file = prefix.with_name(prefix.name + "_stream.log")
    prompt_file.write_text(prompt, encoding="utf-8")
    result = run_codex(
        codex_bin=args.codex_bin,
        prompt=prompt,
        last_message_path=last_message_file,
        cwd=PROJECT_ROOT,
        use_json=args.use_json,
        model=args.model,
        verbose=args.verbose,
        stdout_log_path=stdout_file,
        stderr_log_path=stderr_file,
        stream_log_path=stream_file,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compact Codex phase {phase_id} failed for {volume_id}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    if not last_message_file.is_file():
        raise RuntimeError(f"compact Codex phase {phase_id} wrote no acknowledgment")
    try:
        ack = json.loads(last_message_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"compact Codex phase {phase_id} returned invalid acknowledgment: {exc}"
        ) from exc
    written_file = Path(str(ack.get("written_file") or "")).expanduser()
    if not written_file.is_absolute():
        written_file = PROJECT_ROOT / written_file
    if (
        ack.get("status") != "ok"
        or ack.get("volume_id") != volume_id
        or written_file.resolve() != expected_output.resolve()
    ):
        raise RuntimeError(
            f"compact Codex phase {phase_id} acknowledgment mismatch: {ack}"
        )
    if not expected_output.is_file():
        raise RuntimeError(
            f"compact Codex phase {phase_id} did not write {expected_output}"
        )


def run_compact_batch(args: argparse.Namespace, volume_ids: list[str]) -> None:
    failed_volume_ids: list[str] = []
    for idx, volume_id in enumerate(volume_ids, start=1):
        collection: str | None = None
        try:
            volume_root = args.root / volume_id
            text_root = volume_root / "text"
            if not text_root.exists():
                raise SystemExit(f"Text directory not found: {text_root}")
            info = parse_volume_info(volume_root)
            if info is None:
                raise SystemExit(f"Could not parse volume info from {volume_root}")
            collection = info.series
            payload_file = args.output_dir / f"{volume_id}_alphabetical_indices.json"
            translation_only = getattr(args, "translation_only", False)
            if translation_only:
                db_imported = False
                quality_status = None
                skip_extraction = True
                replace_for_volume = False
                selection_reason = "translation_only"
            else:
                db_imported = volume_already_imported(args.db, volume_id)
                quality = get_volume_quality(args.db, volume_id)
                quality_status = (
                    str(quality.get("status") or "").strip() or None
                    if quality is not None
                    else None
                )
                skip_extraction, replace_for_volume, selection_reason = (
                    volume_extraction_policy(
                        db_imported=db_imported,
                        quality_status=quality_status,
                        skip_done=args.skip_done,
                        redo_invalid=args.redo_invalid,
                        replace=args.replace,
                    )
                )
            print(f"[INFO] volume {idx}/{len(volume_ids)}: {volume_id} ({collection})")
            if args.redo_invalid and skip_extraction and not translation_only:
                if quality_status is None:
                    print(
                        f"[WARN] {volume_id} has no volume quality assessment; "
                        "--redo-invalid skips unassessed volumes.",
                        file=sys.stderr,
                    )
                print(
                    json.dumps(
                        {
                            "status": "skipped",
                            "reason": selection_reason,
                            "volume_id": volume_id,
                            "collection": collection,
                            "db": str(args.db),
                            "quality_status": quality_status,
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            filtered_pages_file = args.output_dir / f"{volume_id}_filtered_pages.json"
            intermediate_dir = intermediate_dir_for_volume(
                args.intermediate_root, volume_id
            )
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            compact_summary: dict[str, Any] = {
                "status": "skipped",
                "reason": selection_reason,
            }
            has_partial_coverage = False
            coverage_status: str | None = None
            coverage_reason: str | None = None
            filtered_source = "skipped_existing_import"

            if not skip_extraction:
                filtered_pages = build_filtered_pages_artifact(
                    args=args,
                    volume_id=volume_id,
                    canonical_path=filtered_pages_file,
                )
                filtered_source = str(filtered_pages.get("source") or "unknown")

                def agent_runner(
                    prompt: str,
                    expected_output: Path,
                    phase_id: str,
                ) -> None:
                    _compact_agent_runner(
                        args=args,
                        volume_id=volume_id,
                        prompt=prompt,
                        expected_output=expected_output,
                        phase_id=phase_id,
                    )

                compact_summary = run_compact_extraction(
                    volume_id=volume_id,
                    collection=collection,
                    source_root=text_root,
                    filtered_pages_file=filtered_pages_file,
                    intermediate_dir=intermediate_dir,
                    output_file=payload_file,
                    agent_runner=agent_runner,
                    semantic_validator=validate_payload_file,
                    locator_chunk_size=args.locator_chunk_size,
                    locator_workers=args.chunk_workers,
                    scripture_db_path=getattr(
                        args,
                        "scripture_db",
                        PROJECT_ROOT / "data" / "scripture_citations.db",
                    ),
                    deterministic_text_locator=not getattr(
                        args,
                        "no_deterministic_text_locator",
                        False,
                    ),
                    deterministic_locator_workers=max(
                        1,
                        getattr(args, "deterministic_locator_workers", 1),
                    ),
                    dry_run=args.dry_run,
                )
                if args.dry_run:
                    print(
                        json.dumps(
                            {
                                **compact_summary,
                                "collection": collection,
                                "filtered_pages_source": filtered_source,
                                "filtered_pages_file": str(filtered_pages_file),
                                "payload_file": str(payload_file),
                                "intermediate_dir": str(intermediate_dir),
                                "would_run_translation": args.translate,
                            },
                            ensure_ascii=False,
                        )
                    )
                    continue

                validate_payload_file(payload_file)
                write_pipeline_quality_reports(
                    payload_file=payload_file,
                    intermediate_dir=intermediate_dir,
                    evidence_sample_size=args.evidence_sample_size,
                    max_unverified_ratio=args.max_unverified_evidence_ratio,
                    skip_evidence_check=args.skip_evidence_check,
                )
                has_partial_coverage, coverage_status, coverage_reason = (
                    inspect_payload_coverage(payload_file)
                )
                import_payload(payload_file, args.db, replace=replace_for_volume)
                clear_previous_failure(
                    previous_failure_path(args.output_dir, volume_id)
                )

            translation_summary = {
                "ran": False,
                "pending_strings": 0,
                "written_rows": 0,
                "completed_strings": 0,
            }
            if args.translate and not args.dry_run:
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
                    tools_enabled=getattr(args, "translation_tools", False),
                    dictionary_dir=getattr(args, "translation_dictionary_dir", None),
                    max_tool_rounds=max(
                        0, getattr(args, "translation_max_tool_rounds", 2)
                    ),
                )
            if has_partial_coverage:
                print(
                    f"[WARN] partial coverage for {volume_id}: {coverage_status}"
                    + (f" - {coverage_reason}" if coverage_reason else ""),
                    file=sys.stderr,
                )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "pipeline": "compact_divide_and_conquer",
                        "volume_id": volume_id,
                        "collection": collection,
                        "db": str(args.db),
                        "imported": not skip_extraction and not args.dry_run,
                        "extraction_skipped": skip_extraction,
                        "selection_reason": selection_reason,
                        "quality_status_before_run": quality_status,
                        "replace_for_volume": replace_for_volume,
                        "filtered_pages_source": filtered_source,
                        "filtered_pages_file": str(filtered_pages_file),
                        "payload_file": str(payload_file),
                        "intermediate_dir": str(intermediate_dir),
                        "locator_chunk_size": args.locator_chunk_size,
                        "locator_workers": args.chunk_workers,
                        "compact_summary": compact_summary,
                        "coverage_warning": has_partial_coverage,
                        "coverage_status": coverage_status,
                        "coverage_reason": coverage_reason,
                        "translation_ran": translation_summary["ran"],
                        "translation_written_rows": translation_summary["written_rows"],
                        "translation_only": getattr(args, "translation_only", False),
                        "translation_tools": getattr(args, "translation_tools", False),
                    },
                    ensure_ascii=False,
                )
            )
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            if isinstance(exc, GeneratorExit):
                raise
            failure_path = previous_failure_path(args.output_dir, volume_id)
            write_previous_failure(
                path=failure_path,
                volume_id=volume_id,
                stage=infer_failure_stage(exc),
                error_summary=(str(exc).splitlines() or [type(exc).__name__])[0],
                error_detail=str(exc) or repr(exc),
                payload_file=args.output_dir
                / f"{volume_id}_alphabetical_indices.json",
                last_message_file=args.log_dir / volume_id / "last_message.json",
                stdout_log_file=args.log_dir / volume_id / "stdout.log",
                stderr_log_file=args.log_dir / volume_id / "stderr.log",
                stream_log_file=args.log_dir / volume_id / "stream.log",
            )
            if not args.continue_on_error:
                raise
            if args.verbose and not isinstance(exc, SystemExit):
                traceback.print_exc()
            emit_volume_failure(
                volume_id=volume_id,
                collection=collection,
                error=exc,
            )
            failed_volume_ids.append(volume_id)
    if not failed_volume_ids:
        maybe_export_web_indices(args)
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


def build_analysis_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Run named, independently resumable stages of the alphabetical-index "
            "analysis pipeline."
        )
    )
    ap.add_argument("command", choices=sorted(ANALYSIS_COMMANDS))
    ap.add_argument("--volume-id")
    ap.add_argument("--all-volumes", action="store_true")
    ap.add_argument("--blob")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste")
    ap.add_argument("--analysis-db", type=Path, default=DEFAULT_ANALYSIS_DB)
    ap.add_argument(
        "--scripture-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "scripture_citations.db",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    ap.add_argument(
        "--intermediate-root",
        type=Path,
        default=DEFAULT_INTERMEDIATE_ROOT,
    )
    ap.add_argument("--filtered-pages-json", type=Path)
    ap.add_argument("--filtered-pages-dir", type=Path)
    ap.add_argument("--codex-bin", default="codex")
    ap.add_argument("--model")
    ap.add_argument("--use-json", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--locator-chunk-size", type=int, default=40)
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--skip-fingerprint",
        action="store_true",
        help=(
            "Reuse completed checkpoints even if input fingerprint changed. "
            "Useful for resuming long runs after non-impactful contract changes."
        ),
    )
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--status", dest="review_status")
    ap.add_argument("--text", dest="text_query")
    ap.add_argument("--review-limit", type=int, default=100)
    ap.add_argument("--kind", choices=("name", "scripture"))
    ap.add_argument("--output", type=Path)
    return ap


def _analysis_selected_volumes(args: argparse.Namespace) -> list[str]:
    if args.blob and not args.all_volumes:
        args.all_volumes = True
    if args.all_volumes:
        blob = args.blob or "PG*,PL*,PO*"
        volume_ids = select_volume_ids(args.root, blob, args.limit)
        if not volume_ids:
            raise SystemExit(f"No volumes selected for blob {blob!r}.")
        return volume_ids
    if args.volume_id:
        return [args.volume_id]
    if args.command in {"status", "review", "export-seeds"}:
        return []
    raise SystemExit("Use --volume-id or --all-volumes/--blob.")


def _analysis_agent_runner(
    args: argparse.Namespace,
    volume_id: str,
) -> Any:
    def runner(prompt: str, expected_output: Path, phase_id: str) -> None:
        _compact_agent_runner(
            args=args,
            volume_id=volume_id,
            prompt=prompt,
            expected_output=expected_output,
            phase_id=phase_id,
        )

    return runner


def _analysis_read_command(
    args: argparse.Namespace,
    volume_ids: list[str],
) -> bool:
    if args.command not in {"status", "review", "export-seeds"}:
        return False
    if not args.analysis_db.is_file():
        raise SystemExit(f"Analysis database not found: {args.analysis_db}")
    with connect_analysis_db(args.analysis_db, read_only=True) as con:
        if args.command == "status":
            selected = volume_ids
            if not selected:
                selected = [
                    str(row["volume_id"])
                    for row in con.execute(
                        "SELECT volume_id FROM analysis_volumes ORDER BY volume_id"
                    ).fetchall()
                ]
            print(
                json.dumps(
                    [volume_status(con, volume_id) for volume_id in selected],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return True
        if args.command == "review":
            rows = review_occurrences(
                con,
                volume_id=args.volume_id,
                status=args.review_status,
                text_query=args.text_query,
                limit=args.review_limit,
            )
        else:
            if not args.kind:
                raise SystemExit("export-seeds requires --kind name|scripture.")
            rows = export_seed_rows(
                con,
                kind=args.kind,
                volume_id=args.volume_id,
            )
    serialized = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "command": args.command,
                    "row_count": len(rows),
                    "written_file": str(args.output),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(serialized, end="")
    return True


def run_analysis_cli(argv: list[str]) -> None:
    args = build_analysis_arg_parser().parse_args(argv)
    for field in (
        "root",
        "analysis_db",
        "scripture_db",
        "db",
        "output_dir",
        "log_dir",
        "intermediate_root",
        "filtered_pages_json",
        "filtered_pages_dir",
        "output",
    ):
        value = getattr(args, field)
        if isinstance(value, Path):
            setattr(args, field, value.expanduser().resolve())
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1.")
    if args.locator_chunk_size < 1:
        raise SystemExit("--locator-chunk-size must be at least 1.")
    volume_ids = _analysis_selected_volumes(args)
    if _analysis_read_command(args, volume_ids):
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.intermediate_root.mkdir(parents=True, exist_ok=True)
    with connect_analysis_db(args.analysis_db) as con:
        init_analysis_schema(con)

    commands = (
        ["discover", "extract", "locate", "verify", "assemble"]
        if args.command == "run"
        else [args.command]
    )
    failed: list[str] = []
    for volume_id in volume_ids:
        try:
            volume_root = args.root / volume_id
            source_root = volume_root / "text"
            info = parse_volume_info(volume_root)
            if info is None or not source_root.is_dir():
                raise SystemExit(f"Invalid volume source: {volume_root}")
            collection = info.series
            filtered_pages_file = (
                args.output_dir / f"{volume_id}_filtered_pages.json"
            )
            intermediate_dir = intermediate_dir_for_volume(
                args.intermediate_root,
                volume_id,
            )
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            output_file = (
                args.output_dir / f"{volume_id}_alphabetical_indices.json"
            )
            agent_runner = _analysis_agent_runner(args, volume_id)
            summaries: list[dict[str, Any]] = []
            for command in commands:
                if command == "discover":
                    filtered_pages = build_filtered_pages_artifact(
                        args=args,
                        volume_id=volume_id,
                        canonical_path=filtered_pages_file,
                    )
                    summary = discover_stage(
                        analysis_db=args.analysis_db,
                        volume_id=volume_id,
                        collection=collection,
                        source_root=source_root,
                        filtered_pages=filtered_pages,
                        filtered_pages_file=filtered_pages_file,
                        intermediate_dir=intermediate_dir,
                        agent_runner=agent_runner,
                        force=args.force,
                        skip_fingerprint=args.skip_fingerprint,
                    )
                elif command == "extract":
                    if not filtered_pages_file.is_file():
                        raise SystemExit(
                            f"discover checkpoint missing: {filtered_pages_file}"
                        )
                    summary = extract_stage(
                        analysis_db=args.analysis_db,
                        volume_id=volume_id,
                        collection=collection,
                        source_root=source_root,
                        filtered_pages_file=filtered_pages_file,
                        intermediate_dir=intermediate_dir,
                        agent_runner=agent_runner,
                        semantic_validator=validate_payload_file,
                        force=args.force,
                        skip_fingerprint=args.skip_fingerprint,
                    )
                elif command == "locate":
                    summary = locate_stage(
                        analysis_db=args.analysis_db,
                        scripture_db=args.scripture_db,
                        volume_id=volume_id,
                        collection=collection,
                        source_root=source_root,
                        intermediate_dir=intermediate_dir,
                        agent_runner=agent_runner,
                        force=args.force,
                        skip_fingerprint=args.skip_fingerprint,
                    )
                elif command == "verify":
                    summary = verify_stage(
                        analysis_db=args.analysis_db,
                        volume_id=volume_id,
                        source_root=source_root,
                        intermediate_dir=intermediate_dir,
                        agent_runner=agent_runner,
                        workers=args.workers,
                        shard_size=args.locator_chunk_size,
                        force=args.force,
                        skip_fingerprint=args.skip_fingerprint,
                    )
                else:
                    summary = assemble_stage(
                        analysis_db=args.analysis_db,
                        volume_id=volume_id,
                        source_root=source_root,
                        output_file=output_file,
                        semantic_validator=validate_payload_file,
                        payload_importer=lambda path: import_payload(
                            path,
                            args.db,
                            replace=args.replace,
                        ),
                        force=args.force,
                        skip_fingerprint=args.skip_fingerprint,
                    )
                summaries.append(summary)
                print(json.dumps(summary, ensure_ascii=False))
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "pipeline": "divide_to_analyze",
                        "volume_id": volume_id,
                        "commands": commands,
                        "analysis_db": str(args.analysis_db),
                        "summaries": summaries,
                    },
                    ensure_ascii=False,
                )
            )
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            if isinstance(exc, GeneratorExit):
                raise
            failed.append(volume_id)
            emit_volume_failure(
                volume_id=volume_id,
                collection=None,
                error=exc,
            )
            if not args.all_volumes:
                raise
    if failed:
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ANALYSIS_COMMANDS:
        run_analysis_cli(sys.argv[1:])
        return
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
        help=(
            "Skip only imported volumes whose quality is valid; partial and "
            "needs_reextract volumes are reprocessed with per-volume replacement"
        ),
    )
    ap.add_argument(
        "--redo-invalid",
        action="store_true",
        help=(
            "Process only volumes whose DB quality status is needs_reextract, "
            "replacing their existing rows"
        ),
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database used to detect and store imported alphabetical payloads")
    ap.add_argument(
        "--scripture-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "scripture_citations.db",
        help="Deterministic scripture-citation SQLite helper",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for canonical payloads and filtered-page artifacts",
    )
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Directory for persistent Codex logs")
    ap.add_argument("--intermediate-root", type=Path, default=DEFAULT_INTERMEDIATE_ROOT, help="Root directory for per-volume intermediate JSON checkpoints")
    ap.add_argument(
        "--legacy-single-context",
        action="store_true",
        help=(
            "Deprecated invocation alias; emits a warning and runs the "
            "checkpointed compact pipeline"
        ),
    )
    ap.add_argument(
        "--max-files-per-chunk",
        type=int,
        default=6,
        help="Deprecated compatibility option used only by the legacy flow",
    )
    ap.add_argument(
        "--chunk-overlap",
        type=int,
        default=1,
        help="Deprecated compatibility option used only by the legacy flow",
    )
    ap.add_argument(
        "--chunk-workers",
        type=int,
        default=1,
        help="Parallel locator agents in compact mode",
    )
    ap.add_argument(
        "--locator-chunk-size",
        type=int,
        default=40,
        help="Citations per compact locator-agent shard (default: 40)",
    )
    ap.add_argument(
        "--no-deterministic-text-locator",
        action="store_true",
        help=(
            "Disable the default OCR text + editorial-page deterministic locator "
            "and send all unresolved material refs to locator agents"
        ),
    )
    ap.add_argument(
        "--deterministic-locator-workers",
        type=int,
        default=1,
        help="Worker processes used by deterministic OCR text localization",
    )
    ap.add_argument("--evidence-sample-size", type=int, default=200)
    ap.add_argument("--max-unverified-evidence-ratio", type=float, default=0.25)
    ap.add_argument("--skip-evidence-check", action="store_true")
    ap.add_argument("--translate", action="store_true", help="Translate frontend-facing alphabetical index strings after import")
    ap.add_argument(
        "--translation-only",
        action="store_true",
        help=(
            "Translate pending strings already present in the database without "
            "running extraction, validation, or import"
        ),
    )
    ap.add_argument("--translation-languages", default="en,it,pt-br,fr", help="Comma-separated target language codes for translation")
    ap.add_argument("--translation-model", default=None, help="OpenAI model used for translation")
    ap.add_argument("--translation-openai-url", default="https://api.openai.com/v1", help="OpenAI-compatible base URL for translation")
    ap.add_argument("--translation-openai-api-key", default=None, help="OpenAI-compatible API key for translation")
    ap.add_argument("--translation-workers", type=int, default=1, help="Worker count for translation requests")
    ap.add_argument("--translation-timeout", type=int, default=180, help="Per-request timeout in seconds for translation")
    ap.add_argument("--translation-retries", type=int, default=3, help="Retry count per translation request")
    ap.add_argument(
        "--translation-tools",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow bounded read-only dictionary tools during translation",
    )
    ap.add_argument(
        "--translation-dictionary-dir",
        type=Path,
        default=DEFAULT_TRANSLATION_DICTIONARY_DIR,
        help=(
            "Directory containing superdb.sqlite or the compatible individual dictionaries "
            "(default: PATRISTICA_DICTIONARY_DIR or the local dictionary project)"
        ),
    )
    ap.add_argument(
        "--translation-max-tool-rounds",
        type=int,
        default=2,
        help="Maximum dictionary tool-call rounds per translated string",
    )
    ap.add_argument("--keep-temp", action="store_true", help="Keep the last-message artifact after a successful run")
    ap.add_argument("--dry-run", action="store_true", help="Build filtered pages and prompt, then stop before calling Codex")
    ap.add_argument(
        "--no-export-web",
        action="store_true",
        help="Do not refresh the public alphabetical-index shards after a successful batch",
    )
    ap.add_argument(
        "--web-alpha-out",
        type=Path,
        default=DEFAULT_WEB_ALPHA_OUT,
        help="Output directory passed to tools/export_alphabetical_indices.py",
    )
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
    args.scripture_db = resolve_path(args.scripture_db) or args.scripture_db
    args.output_dir = resolve_path(args.output_dir) or args.output_dir
    args.log_dir = resolve_path(args.log_dir) or args.log_dir
    args.intermediate_root = resolve_path(args.intermediate_root) or args.intermediate_root
    args.web_alpha_out = resolve_path(args.web_alpha_out) or args.web_alpha_out
    args.translation_dictionary_dir = (
        resolve_path(args.translation_dictionary_dir)
        or args.translation_dictionary_dir
    )
    args.translation_languages = normalize_language_list(args.translation_languages)
    args.translation_openai_api_key = args.translation_openai_api_key or os.getenv("OPENAI_API_KEY")
    args.translation_model = args.translation_model or os.getenv("OPENAI_MODEL") or "gpt-5-mini"

    if args.translation_only:
        args.translate = True

    if args.filtered_pages_json and args.all_volumes:
        raise SystemExit("--filtered-pages-json is only valid for a single-volume run.")
    if args.previous_result_json and args.all_volumes:
        raise SystemExit("--previous-result-json is only valid for a single-volume run.")
    if args.translate and not args.translation_languages:
        raise SystemExit("Use at least one language in --translation-languages when --translate is enabled.")
    if args.translate and not args.translation_openai_api_key:
        raise SystemExit("Translation requires --translation-openai-api-key or OPENAI_API_KEY.")
    if args.translation_max_tool_rounds < 0:
        raise SystemExit("--translation-max-tool-rounds cannot be negative.")
    if args.translate and args.translation_tools:
        supported_dictionaries = (
            "superdb.sqlite",
            "retificado_v2.db",
            "ls_dict.db",
            "gaffiot.db",
        )
        if not args.translation_dictionary_dir.is_dir() or not any(
            (args.translation_dictionary_dir / name).is_file()
            for name in supported_dictionaries
        ):
            raise SystemExit(
                "Translation tools require at least one supported read-only dictionary "
                f"in {args.translation_dictionary_dir}. Use --no-translation-tools to "
                "run without lexicon guidance."
            )

    if args.blob and not args.all_volumes:
        args.all_volumes = True
    if args.redo_invalid and not args.volume_id:
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

    if args.locator_chunk_size < 1:
        raise SystemExit("--locator-chunk-size must be at least 1.")

    if args.legacy_single_context:
        print(
            "[WARN] --legacy-single-context no longer activates the contradictory "
            "monolithic agent prompt; running the checkpointed compact pipeline instead.",
            file=sys.stderr,
        )
    run_compact_batch(args, volume_ids)
    return

    # Retained temporarily as unreachable compatibility code for old result inspection.
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
            quality = get_volume_quality(args.db, volume_id)
            quality_status = (
                str(quality.get("status") or "").strip() or None
                if quality is not None
                else None
            )
            skip_extraction, replace_for_volume, selection_reason = (
                volume_extraction_policy(
                    db_imported=db_imported,
                    quality_status=quality_status,
                    skip_done=args.skip_done,
                    redo_invalid=args.redo_invalid,
                    replace=args.replace,
                )
            )

            if args.redo_invalid and skip_extraction:
                if quality_status is None:
                    print(
                        f"[WARN] {volume_id} has no volume quality assessment; "
                        "--redo-invalid skips unassessed volumes.",
                        file=sys.stderr,
                    )
                print(
                    json.dumps(
                        {
                            "status": "skipped",
                            "reason": selection_reason,
                            "volume_id": volume_id,
                            "collection": collection,
                            "db": str(args.db),
                            "quality_status": quality_status,
                            "payload_file": str(payload_file),
                            "output_checkpoint_status": output_checkpoint_status,
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            if skip_extraction and not args.translate:
                print(
                    json.dumps(
                        {
                            "status": "skipped",
                            "reason": selection_reason,
                            "volume_id": volume_id,
                            "collection": collection,
                            "db": str(args.db),
                            "quality_status": quality_status,
                            "payload_file": str(payload_file),
                            "output_checkpoint_status": output_checkpoint_status,
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            if payload_file.exists() and output_checkpoint_status == "invalid" and not replace_for_volume and not args.dry_run:
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
            workplan_json = intermediate_dir / "workplan.json"
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

                previous_workplan = read_json(workplan_json) if workplan_json.is_file() else None
                workplan = build_index_workplan(
                    volume_id=volume_id,
                    source_root=text_root,
                    collection=collection,
                    filtered_pages=filtered_pages,
                    pipeline_kind="alphabetical",
                    chunk_output_dir=intermediate_dir / "chunks",
                    max_files_per_chunk=args.max_files_per_chunk,
                    chunk_overlap=args.chunk_overlap,
                )
                workplan = reconcile_workplan_progress(workplan, previous_workplan)
                workplan_json.write_text(
                    json.dumps(workplan, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                chunks = [item for item in workplan.get("chunks") or [] if isinstance(item, dict)]

                prompt = build_prompt(
                    volume_id=volume_id,
                    source_root=text_root,
                    collection=collection,
                    filtered_pages_file=filtered_pages_file,
                    workplan_json=workplan_json,
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
                write_pipeline_quality_reports(
                    payload_file=payload_file,
                    intermediate_dir=intermediate_dir,
                    evidence_sample_size=args.evidence_sample_size,
                    max_unverified_ratio=args.max_unverified_evidence_ratio,
                    skip_evidence_check=args.skip_evidence_check or not chunks,
                )
                has_partial_coverage, coverage_status, coverage_reason = inspect_payload_coverage(payload_file)
                try:
                    import_payload(payload_file, args.db, replace=replace_for_volume)
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
                    tools_enabled=getattr(args, "translation_tools", False),
                    dictionary_dir=getattr(args, "translation_dictionary_dir", None),
                    max_tool_rounds=max(
                        0, getattr(args, "translation_max_tool_rounds", 2)
                    ),
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
                        "selection_reason": selection_reason,
                        "quality_status_before_run": quality_status,
                        "replace_for_volume": replace_for_volume,
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
        except BaseException as exc:
            if isinstance(exc, GeneratorExit):
                raise
            failure_path = previous_failure_path(args.output_dir, volume_id)
            write_previous_failure(
                path=failure_path,
                volume_id=volume_id,
                stage=infer_failure_stage(exc),
                error_summary=(str(exc).splitlines() or [type(exc).__name__])[0],
                error_detail=str(exc) or repr(exc),
                payload_file=args.output_dir / f"{volume_id}_alphabetical_indices.json",
                last_message_file=args.output_dir / f"{volume_id}_last_message.txt",
                stdout_log_file=args.log_dir / f"{volume_id}_codex_stdout.log",
                stderr_log_file=args.log_dir / f"{volume_id}_codex_stderr.log",
                stream_log_file=args.log_dir / f"{volume_id}_codex_stream.log",
            )
            if not args.continue_on_error:
                raise
            if args.verbose and not isinstance(exc, SystemExit):
                traceback.print_exc()
            emit_volume_failure(volume_id=volume_id, collection=collection, error=exc)
            failed_volume_ids.append(volume_id)
            continue

    if not failed_volume_ids:
        maybe_export_web_indices(args)
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
