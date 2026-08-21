#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import re
import sqlite3
import subprocess
import sys
import selectors
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.common import parse_volume_info
from patristica_pipeline.editorial_page_estimator import (
    DEFAULT_ESTIMATOR_DB,
    estimate_editorial_pages,
)
from patristica_pipeline.index_localization_helpers import (
    build_helper_request_artifact,
    run_helper_locator,
)
from patristica_pipeline.index_fragment_assembly import (
    assemble_index_fragments,
    verify_payload_consumes_fragments,
)
from patristica_pipeline.index_payload_evidence import verify_index_payload_evidence
from patristica_pipeline.index_pipeline_ownership import general_section_ownership
from patristica_pipeline.index_work_anchor_reconciler import reconcile_work_anchors
from patristica_pipeline.index_workplan import build_index_workplan, reconcile_workplan_progress
from patristica_pipeline.index_chunk_driver import run_index_chunk_agents
from scripts.index_translation.database import (
    collect_pending_translations,
    collect_volume_candidates,
    connect_translation_db,
    ensure_string_rows,
    init_translation_schema,
    load_analysis_summaries,
    prepare_cltk_analyses,
    upsert_translation_rows,
)
from scripts.index_translation.provider import (
    normalize_language_list,
    translate_candidate_worker,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = PROJECT_ROOT / ".codex" / "skills" / "patristic-index-extractor"
SCAN_SCRIPT = SKILL_DIR / "scripts" / "scan_volume.py"
PROMPT_SCRIPT = SKILL_DIR / "scripts" / "build_volume_prompt.py"
IMPORT_SCRIPT = SKILL_DIR / "scripts" / "import_index_json.py"
FILTERED_PAGES_SCRIPT = PROJECT_ROOT / "scripts" / "build_alphabetical_filtered_pages.py"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "index_payloads"
DEFAULT_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "index_logs"
DEFAULT_INTERMEDIATE_ROOT = PROJECT_ROOT / "data" / "index_intermediate_payloads"
DEFAULT_EDITORIAL_PAGE_DB = DEFAULT_ESTIMATOR_DB
DEFAULT_TRANSLATION_DICTIONARY_DIR = Path(
    os.getenv("PATRISTICA_DICTIONARY_DIR", "/mnt/projects/Projects/Dicionarios/dicionarios")
)
DEFAULT_CLTK_PYTHON = Path(
    os.getenv("PATRISTICA_CLTK_PYTHON", PROJECT_ROOT / ".venv-cltk" / "bin" / "python")
)
CLTK_WORKER_SCRIPT = PROJECT_ROOT / "scripts" / "index_translation" / "cltk_worker.py"
DEFAULT_TRANSLATION_WORKERS = 4
DEFAULT_TRANSLATION_BACKOFF_BASE = 1.0
DEFAULT_TRANSLATION_BACKOFF_MAX = 60.0
DEFAULT_TRANSLATION_JITTER = 0.25
EXISTING_PAYLOAD_SNIPPET_BYTES = 120_000
PREVIOUS_FAILURE_DETAIL_MAX_CHARS = 12_000
FAILURE_ARTIFACT_TRACEBACK_MAX_CHARS = 12_000
HYPHEN_SAMPLE_LIMIT = 20
LINEBREAK_HYPHEN_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿÆæŒœ]-\s*(?:$|\n)", re.MULTILINE)
HYPHEN_FAILURE_RE = re.compile(r"(?:line.?break|quebra de linha|hyphen artifact|hífen).{0,120}", re.I)
CANONICAL_HYPHEN_FIELDS = {
    "author_raw",
    "entry_raw",
    "heading_norm",
    "heading_raw",
    "normalized_target",
    "note_raw",
    "target_raw",
    "title_norm",
    "title_raw",
}
LIST_BEARING_SCOPE_KINDS = {
    "work_index",
    "work_index_alphabetical",
    "work_index_analytical",
    "volume_index",
    "volume_index_alphabetical",
    "volume_index_analytical",
    "table_of_contents",
    "chapter_list",
    "book_list",
}
LIST_HEADING_RE = re.compile(
    r"\b(?:INDEX|INDICES|TABLE|TABULA|CAPITULA|CAP\.|LIBER|LIBRI|BOOK|CHAPTER|TITULI)\b",
    re.I,
)
EMPTY_ENTRY_STATUSES = {"unrecoverable_ocr", "no_line_items"}
VOLUME_STAGE_COUNT = 14


def absolute_path_preserving_symlinks(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def emit_stage(
    *,
    verbose: bool,
    volume_id: str,
    volume_index: int,
    volume_total: int,
    stage_index: int,
    status: str,
    label: str,
    started_at: float | None = None,
    detail: str | None = None,
    progress_log: Path | None = None,
) -> None:
    elapsed = f" elapsed={time.monotonic() - started_at:.2f}s" if started_at is not None else ""
    suffix = f" {detail}" if detail else ""
    line = (
        f"[VOLUME {volume_index}/{volume_total} {volume_id}] "
        f"[STAGE {stage_index}/{VOLUME_STAGE_COUNT}] {status} {label}{elapsed}{suffix}"
    )
    if progress_log is not None:
        progress_log.parent.mkdir(parents=True, exist_ok=True)
        with progress_log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    if verbose:
        print(line)


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


def select_db_volume_ids(db_path: Path, blob: str, limit: int | None) -> list[str]:
    prefixes = [
        part.strip().upper().removesuffix("*")
        for part in blob.split(",")
        if part.strip()
    ]
    if not db_path.is_file():
        return []
    with connect_translation_db(db_path) as con:
        rows = con.execute("SELECT volume_id FROM volumes ORDER BY volume_id").fetchall()
    volume_ids = [
        str(row["volume_id"])
        for row in rows
        if not prefixes
        or any(str(row["volume_id"]).upper().startswith(prefix) for prefix in prefixes)
    ]
    if limit is not None and limit > 0:
        volume_ids = volume_ids[:limit]
    return volume_ids


def db_volume_collection(db_path: Path, volume_id: str) -> str | None:
    if not db_path.is_file():
        return None
    with connect_translation_db(db_path) as con:
        row = con.execute(
            "SELECT collection FROM volumes WHERE volume_id = ?", (volume_id,)
        ).fetchone()
    return str(row["collection"]) if row is not None else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_path(path: Path | None) -> Path | None:
    return path.expanduser().resolve() if path is not None else None


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text, False
    clipped = data[:max_bytes]
    while True:
        try:
            return clipped.decode("utf-8"), True
        except UnicodeDecodeError:
            clipped = clipped[:-1]


def compact_failure_text(text: str, max_chars: int) -> tuple[str, int, bool]:
    """Deduplicate repeated diagnostic lines and cap text while retaining both edges."""
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")

    lines: list[str] = []
    seen: set[str] = set()
    duplicate_count = 0
    previous_blank = False
    for line in text.splitlines():
        key = line.strip()
        if not key:
            if previous_blank:
                duplicate_count += 1
                continue
            previous_blank = True
            lines.append("")
            continue
        previous_blank = False
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        lines.append(line)

    compacted = "\n".join(lines).strip()
    if len(compacted) <= max_chars:
        return compacted, duplicate_count, False

    marker_template = "\n... [{omitted} diagnostic characters omitted] ...\n"
    marker = marker_template.format(omitted=0)
    edge_budget = max_chars - len(marker) - 12
    head_chars = max(1, edge_budget * 2 // 3)
    tail_chars = max(1, edge_budget - head_chars)
    omitted = len(compacted) - head_chars - tail_chars
    marker = marker_template.format(omitted=omitted)
    tail_chars = max(1, max_chars - head_chars - len(marker))
    omitted = len(compacted) - head_chars - tail_chars
    marker = marker_template.format(omitted=omitted)
    clipped = compacted[:head_chars].rstrip() + marker + compacted[-tail_chars:].lstrip()
    return clipped[:max_chars], duplicate_count, True


def _collapse_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _scan_hyphen_artifacts(value: Any, path: str, hits: list[dict[str, str]]) -> None:
    if len(hits) >= HYPHEN_SAMPLE_LIMIT:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "raw_json":
                continue
            item_path = f"{path}.{key}" if path else str(key)
            if (
                key in CANONICAL_HYPHEN_FIELDS
                and isinstance(item, str)
                and LINEBREAK_HYPHEN_RE.search(item)
            ):
                hits.append({"path": item_path, "sample": _collapse_ws(item)[:240]})
            elif isinstance(item, (dict, list)):
                _scan_hyphen_artifacts(item, item_path, hits)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_hyphen_artifacts(item, f"{path}[{index}]", hits)


def summarize_hyphen_artifacts(payload: dict[str, Any]) -> list[str]:
    hits: list[dict[str, str]] = []
    _scan_hyphen_artifacts(payload, "", hits)
    return [f"{item['path']}: {item['sample']}" for item in hits]


def payload_has_hyphen_artifacts(payload: dict[str, Any]) -> bool:
    return bool(summarize_hyphen_artifacts(payload))


def build_existing_payload_prompt_block(payload_file: Path, volume_id: str) -> str:
    raw_text = payload_file.read_text(encoding="utf-8")
    payload_summary = [
        f"### Payload that already exists for {volume_id}",
        f"- File: {payload_file}",
        f"- Size: {len(raw_text.encode('utf-8'))} bytes",
    ]
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        snippet, was_truncated = truncate_utf8(raw_text, EXISTING_PAYLOAD_SNIPPET_BYTES)
        payload_summary.extend(
            [
                f"- Existing file is not valid JSON: {exc}",
                f"- Embedded excerpt bytes: {len(snippet.encode('utf-8'))} / {EXISTING_PAYLOAD_SNIPPET_BYTES}",
                "- The excerpt below may be truncated. Read the file directly if needed, then rewrite it.",
                "",
                "```json",
                snippet.rstrip(),
                "... [TRUNCATED]" if was_truncated else "",
                "```",
                f"Check and fix/add what is missing/what is wrong following the volume {volume_id} real files.",
            ]
        )
        return "\n".join(line for line in payload_summary if line != "")

    volume = payload.get("volume") if isinstance(payload, dict) else {}
    works = payload.get("works") if isinstance(payload, dict) else []
    sections = payload.get("sections") if isinstance(payload, dict) else []
    notes = payload.get("notes") if isinstance(payload, dict) else []
    rerun_count = sum(
        1
        for work in works
        if isinstance(work, dict)
        and isinstance(work.get("raw_json"), dict)
        and isinstance(work["raw_json"].get("work_anchor_rerun"), dict)
    ) if isinstance(works, list) else "invalid"
    entry_count = sum(
        len(section.get("entries") or [])
        for section in sections
        if isinstance(section, dict)
    )
    payload_summary.extend(
        [
            f"- volume.volume_id: {volume.get('volume_id') if isinstance(volume, dict) else None}",
            f"- works: {len(works) if isinstance(works, list) else 'invalid'}",
            f"- sections: {len(sections) if isinstance(sections, list) else 'invalid'}",
            f"- entries: {entry_count if isinstance(sections, list) else 'invalid'}",
            f"- notes: {len(notes) if isinstance(notes, list) else 'invalid'}",
            f"- pending work-anchor reruns: {rerun_count}",
            "- Read the existing file directly if you need the full prior payload.",
            "- The embedded excerpt below is intentionally truncated to keep the prompt under the input limit.",
        ]
    )
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    snippet, was_truncated = truncate_utf8(pretty, EXISTING_PAYLOAD_SNIPPET_BYTES)
    payload_summary.extend(
        [
            "",
            "Embedded excerpt:",
            "```json",
            snippet.rstrip(),
            "... [TRUNCATED]" if was_truncated else "",
            "```",
            f"Check and fix/add what is missing/what is wrong following the volume {volume_id} real files.",
        ]
    )
    return "\n".join(line for line in payload_summary if line != "")


def build_work_anchor_rerun_prompt_block(
    payload: dict[str, Any],
    volume_id: str,
) -> str | None:
    pending: list[dict[str, Any]] = []
    for work in payload.get("works") or []:
        if not isinstance(work, dict):
            continue
        raw_json = work.get("raw_json")
        marker = (
            raw_json.get("work_anchor_rerun")
            if isinstance(raw_json, dict)
            else None
        )
        if not isinstance(marker, dict):
            continue
        pending.append(
            {
                "work_key": work.get("work_key"),
                "title_raw": work.get("title_raw"),
                "start_page": work.get("start_page"),
                "start_file": work.get("start_file"),
                "rerun": marker,
            }
        )
    if not pending:
        return None
    return (
        "### WORK ANCHOR RERUN\n"
        f"The previous {volume_id} payload contains {len(pending)} work anchor(s) "
        "marked for agent review.\n"
        "- Process every item below against OCR inside the current source_root.\n"
        "- Inspect competing files and neighboring pages; do not accept helper ranking alone.\n"
        "- A fuzzy/Levenshtein title hit is positive evidence, not a final decision.\n"
        "- Reject target occurrences in ORDO/ELENCHUS lists, catalogues, prefatory inventories, and closing indexes.\n"
        "- Assemble a logical header from all header blocks; page numbers may be attached to title text or emitted in separate OCR/XML blocks.\n"
        "- A similar header on at least four files, allowing up to three missing/corrupt headers, is probable body-range evidence, not an exact title-page or ending boundary.\n"
        "- Distinguish a direct title page/opening from a repeated running header and inspect both edges of any recurring-header range.\n"
        "- Use editorial numbers only as secondary evidence and account for two-page scans, columns, and adjacent works sharing one scan.\n"
        "- Preserve a declared page inside an estimator facing-page pair unless layout or direct text evidence resolves the side.\n"
        "- Do not set end_page to next_start - 1, infer end_file from editorial numbers alone, or accept start_page > end_page.\n"
        "- Do not force a single local anchor for composite containers. Pure external `Vide ... tom.` remissions keep a null local target and their raw reference evidence.\n"
        "- Process any nested anchor_locator_review as part of the same mandatory review.\n"
        "- If resolved, update the work anchor and remove raw_json.work_anchor_rerun.\n"
        "- If direct evidence remains tied, preserve the marker with status=ambiguous.\n\n"
        + json.dumps(pending, ensure_ascii=False, indent=2)
    )


def load_failure_artifact(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_previous_failure_prompt_block(
    volume_id: str,
    failure_payload: dict[str, Any],
) -> str:
    stage = failure_payload.get("stage") or "unknown"
    raw_detail = str(
        failure_payload.get("error_detail")
        or failure_payload.get("error_summary")
        or "unknown"
    )
    detail, duplicate_count, was_truncated = compact_failure_text(
        raw_detail,
        PREVIOUS_FAILURE_DETAIL_MAX_CHARS,
    )
    compaction_notes: list[str] = []
    if duplicate_count:
        compaction_notes.append(f"deduplicated {duplicate_count} repeated diagnostic line(s)")
    if was_truncated:
        compaction_notes.append(
            f"truncated from {len(raw_detail)} to at most "
            f"{PREVIOUS_FAILURE_DETAIL_MAX_CHARS} characters"
        )
    diagnostics = ""
    if compaction_notes:
        diagnostics = (
            "- prompt diagnostic: " + "; ".join(compaction_notes) + ".\n"
            "- Full stdout/stderr remain in the log paths recorded by the failure artifact.\n"
        )
    return (
        "### PREVIOUS FAILURE\n"
        f"- volume_id: {volume_id}\n"
        f"- failed stage: {stage}\n"
        "- This is validator feedback from the previous run. Treat it as a required correction, "
        "then verify the correction against the OCR source.\n"
        f"{diagnostics}"
        f"- exact_failure:\n{detail}\n\n"
        f"Fix the exact failure for {volume_id}; do not merely regenerate the same payload."
    )


def prevalidate_existing_payload(
    payload_file: Path,
    volume_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not payload_file.is_file():
        return None, None
    try:
        payload = read_json(payload_file)
        validate_payload(payload, volume_id, payload_file)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        detail = str(exc) or repr(exc)
        return None, {
            "stage": "prevalidate_existing_payload",
            "error_type": type(exc).__name__,
            "error_summary": detail.splitlines()[0],
            "error_detail": detail,
            "payload_file": str(payload_file),
        }
    return payload, None


def failure_mentions_hyphen_artifact(failure_payload: dict[str, Any] | None) -> bool:
    if not failure_payload:
        return False
    if failure_payload.get("mentions_hyphen_artifact") is True:
        return True
    text = " ".join(
        str(failure_payload.get(key) or "")
        for key in ("error_summary", "error_detail")
    )
    return bool(HYPHEN_FAILURE_RE.search(text))


def build_hyphen_rerun_recovery_block() -> str:
    return """### HYPHEN RERUN RECOVERY
The previous result contains or reported a likely OCR line-break hyphen artifact.
- Re-read every affected source with:
  `python scripts/read_ocr_page_text.py --view xml --show-source <file>`
- Join a trailing hyphen only when the following OCR line/page proves that the same word continues.
- Preserve genuine lexical/editorial hyphens.
- Repair canonical logical fields such as `entry_raw`, `target_raw`, and `normalized_target`.
- Keep exact physical OCR line fragments in `raw_json`; provenance strings are not canonical
  soft-wrap errors and must not be rewritten merely to satisfy the validator.
- `scripts/pipeline_index_extraction/fix_linebreak_hyphens.py` is a repair aid, not OCR evidence.
- Revalidate all changed `entry_raw`, headings, titles, and source spans before writing the payload."""


def volume_already_imported(db_path: Path, volume_id: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM volumes WHERE volume_id = ? LIMIT 1",
            (volume_id,),
        ).fetchone()
        if row is not None:
            return True
        row = con.execute(
            "SELECT 1 FROM runs WHERE volume_id = ? AND status = 'imported' LIMIT 1",
            (volume_id,),
        ).fetchone()
        return row is not None


def scan_volume(volume_id: str, root: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCAN_SCRIPT),
        "--volume",
        volume_id,
        "--root",
        str(root),
        "--json",
    ]
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"scan_volume.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def build_filtered_pages_artifact(
    *,
    root: Path,
    volume_id: str,
    filtered_pages_json: Path | None,
    filtered_pages_dir: Path | None,
    canonical_path: Path,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(FILTERED_PAGES_SCRIPT),
        "--volume",
        volume_id,
        "--root",
        str(root),
        "--output",
        str(canonical_path),
        "--pretty",
        "--profile",
        "general",
    ]
    if filtered_pages_json is not None:
        cmd.extend(["--filtered-pages-json", str(filtered_pages_json)])
    if filtered_pages_dir is not None:
        cmd.extend(["--filtered-pages-dir", str(filtered_pages_dir)])
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_alphabetical_filtered_pages.py failed for {volume_id}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return read_json(canonical_path)


def build_editorial_pages_artifact(
    *,
    volume_id: str,
    source_root: Path,
    collection: str,
    output_path: Path,
    db_path: Path,
    use_cache: bool,
) -> dict[str, Any]:
    if collection not in {"PG", "PL"}:
        payload = {
            "volume_id": volume_id,
            "collection": collection,
            "status": "skipped",
            "reason": "editorial_page_estimator_supports_pg_pl_only",
            "files": [],
        }
    else:
        payload = estimate_editorial_pages(
            volume_id=volume_id,
            source_root=source_root,
            collection=collection,
            db_path=db_path,
            use_cache=use_cache,
        )
    write_json(output_path, payload)
    return payload


def build_prompt(
    volume_id: str,
    source_root: Path,
    collection: str,
    prescan_json: Path,
    output_dir: Path,
    filtered_pages_json: Path | None = None,
    editorial_pages_json: Path | None = None,
    helper_request_json: Path | None = None,
    helper_output_json: Path | None = None,
    workplan_json: Path | None = None,
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
        "--prescan-json",
        str(prescan_json),
        "--output-dir",
        str(output_dir),
    ]
    for option, path in (
        ("--filtered-pages-json", filtered_pages_json),
        ("--editorial-pages-json", editorial_pages_json),
        ("--helper-request-json", helper_request_json),
        ("--helper-output-json", helper_output_json),
        ("--workplan-json", workplan_json),
    ):
        if path is not None:
            cmd.extend([option, str(path)])
    result = run_cmd(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"build_volume_prompt.py failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout


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


def validate_payload(payload: dict[str, Any], volume_id: str, expected_file: Path) -> None:
    if not isinstance(payload, dict):
        raise SystemExit("Payload is not a JSON object.")
    if payload.get("volume", {}).get("volume_id") != volume_id:
        raise SystemExit(f"Payload volume_id mismatch: expected {volume_id}")
    for key in ("volume", "works", "sections", "notes"):
        if key not in payload:
            raise SystemExit(f"Missing top-level key: {key}")
    if not isinstance(payload.get("works"), list):
        raise SystemExit("Payload 'works' must be a list.")
    if not isinstance(payload.get("sections"), list):
        raise SystemExit("Payload 'sections' must be a list.")
    works = payload.get("works") or []
    invalid_work_ranges: list[str] = []
    seen_work_keys: set[str] = set()
    for index, work in enumerate(works):
        if not isinstance(work, dict):
            raise SystemExit("Each item in payload 'works' must be an object.")
        work_key = str(work.get("work_key") or "").strip()
        if not work_key:
            raise SystemExit(f"works[{index}] must include a stable work_key.")
        if work_key in seen_work_keys:
            raise SystemExit(f"Duplicate work_key in payload: {work_key}")
        seen_work_keys.add(work_key)
        start_page = work.get("start_page")
        end_page = work.get("end_page")
        if (
            isinstance(start_page, int)
            and not isinstance(start_page, bool)
            and isinstance(end_page, int)
            and not isinstance(end_page, bool)
            and start_page > end_page
        ):
            invalid_work_ranges.append(
                str(work.get("work_key") or f"works[{index}]")
            )
    if invalid_work_ranges:
        raise SystemExit(
            "Works have impossible editorial page ranges (start_page > end_page): "
            + ", ".join(invalid_work_ranges[:10])
        )
    sections = payload.get("sections") or []
    total_entries = 0
    suspicious_empty_sections: list[str] = []
    missing_start_anchors: list[str] = []
    missing_end_anchors: list[str] = []
    non_owned_sections: list[str] = []
    seen_section_keys: set[str] = set()
    seen_entry_keys: set[str] = set()
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise SystemExit("Each item in payload 'sections' must be an object.")
        section_key = str(section.get("section_key") or "").strip()
        if not section_key:
            raise SystemExit(f"sections[{section_index}] must include a stable section_key.")
        if section_key in seen_section_keys:
            raise SystemExit(f"Duplicate section_key in payload: {section_key}")
        seen_section_keys.add(section_key)
        if section.get("page_start") is None and not str(section.get("file_start") or "").strip():
            missing_start_anchors.append(section_key)
        if section.get("page_end") is None and not str(section.get("file_end") or "").strip():
            missing_end_anchors.append(section_key)
        owned, ownership_reason = general_section_ownership(section)
        if not owned:
            non_owned_sections.append(f"{section_key} ({ownership_reason})")
        entries = section.get("entries")
        if not isinstance(entries, list):
            raise SystemExit("Each section payload must include an 'entries' list.")
        total_entries += len(entries)
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise SystemExit(
                    f"{section_key}.entries[{entry_index}] must be an object."
                )
            entry_key = str(entry.get("entry_key") or "").strip()
            if not entry_key:
                raise SystemExit(
                    f"{section_key}.entries[{entry_index}] must include a stable entry_key."
                )
            if entry_key in seen_entry_keys:
                raise SystemExit(f"Duplicate entry_key in payload: {entry_key}")
            seen_entry_keys.add(entry_key)
        raw_json = section.get("raw_json")
        scope_kind = str(section.get("scope_kind") or "")
        heading = str(section.get("heading_raw") or "")
        list_bearing = scope_kind in LIST_BEARING_SCOPE_KINDS or bool(LIST_HEADING_RE.search(heading))
        if len(entries) == 0 and list_bearing:
            status = raw_json.get("entries_status") if isinstance(raw_json, dict) else None
            reason = raw_json.get("entries_status_reason") if isinstance(raw_json, dict) else None
            evidence = raw_json.get("evidence_files") if isinstance(raw_json, dict) else None
            justified = (
                status in EMPTY_ENTRY_STATUSES
                and isinstance(reason, str)
                and bool(reason.strip())
                and isinstance(evidence, list)
                and bool(evidence)
            )
            if not justified:
                suspicious_empty_sections.append(
                    str(section.get("section_key") or heading or "unknown")
                )
    if missing_start_anchors:
        raise SystemExit(
            "Sections must include at least one start anchor (`page_start` or `file_start`): "
            + ", ".join(missing_start_anchors[:10])
        )
    if missing_end_anchors:
        raise SystemExit(
            "Sections must include at least one end anchor (`page_end` or `file_end`): "
            + ", ".join(missing_end_anchors[:10])
        )
    if non_owned_sections:
        raise SystemExit(
            "General payload contains sections owned by the closing alphabetical/citation "
            "pipeline: " + "; ".join(non_owned_sections[:10])
        )
    if suspicious_empty_sections:
        raise SystemExit(
            "List-bearing sections have no entries and no explicit recovery evidence: "
            + ", ".join(suspicious_empty_sections[:10])
        )
    hyphen_hits = summarize_hyphen_artifacts(payload)
    if hyphen_hits:
        raise SystemExit(
            "Payload contains likely OCR line-break hyphen artifacts: "
            + "; ".join(hyphen_hits[:10])
        )
    if not expected_file.exists():
        raise SystemExit(f"Expected output file not found: {expected_file}")


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
            f"import_index_json.py failed for {payload_file.name}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    print(result.stdout.strip())


def format_translation_progress(
    *, volume_id: str, completed: int, total: int, result: dict[str, Any]
) -> str:
    source = str(result.get("source_text") or "")[:90]
    return (
        f"[INFO] translation {completed}/{total} volume={volume_id} "
        f"langs={sorted(dict(result.get('translations') or {}).keys())} "
        f"attempts={result.get('attempts')} elapsed_s={result.get('elapsed_s')} "
        f"retry_wait_s={result.get('retry_wait_s', 0)} "
        f"tool_calls={result.get('tool_call_count', 0)} source={source!r}"
    )


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
    tools_enabled: bool,
    dictionary_dir: Path,
    max_tool_rounds: int,
    cltk_enabled: bool,
    cltk_python: Path,
    cltk_workers: int,
    cltk_max_tokens: int,
    backoff_base_s: float = DEFAULT_TRANSLATION_BACKOFF_BASE,
    backoff_max_s: float = DEFAULT_TRANSLATION_BACKOFF_MAX,
    jitter_ratio: float = DEFAULT_TRANSLATION_JITTER,
) -> dict[str, Any]:
    with connect_translation_db(db_path) as con:
        init_translation_schema(con)
        candidates = ensure_string_rows(con, collect_volume_candidates(con, volume_id))
        cltk_summary: dict[str, Any] = {
            "status": "disabled",
            "candidate_count": len(candidates),
            "analyzed": 0,
            "cached": 0,
            "errors": 0,
        }
        if cltk_enabled:
            cltk_summary = prepare_cltk_analyses(
                con,
                candidates=candidates,
                python_executable=cltk_python,
                worker_script=CLTK_WORKER_SCRIPT,
                dictionary_dir=dictionary_dir,
                workers=cltk_workers,
                max_tokens=cltk_max_tokens,
            )
        pending = collect_pending_translations(con, candidates, languages)

    if verbose and cltk_enabled:
        print(
            f"[INFO] cltk volume={volume_id} status={cltk_summary.get('status')} "
            f"analyzed={cltk_summary.get('analyzed', 0)} "
            f"cached={cltk_summary.get('cached', 0)} "
            f"errors={cltk_summary.get('errors', 0)} "
            f"model_missing={cltk_summary.get('model_missing', 0)}"
        )
    if cltk_summary.get("status") == "unavailable":
        health = cltk_summary.get("health") or {}
        print(
            f"[WARN] CLTK unavailable for {volume_id}; translation will continue without "
            "linguistic annotations: "
            f"{health.get('error') or health.get('analyzer_version') or health}",
            file=sys.stderr,
        )

    if not pending:
        return {
            "ran": False,
            "candidate_strings": len(candidates),
            "pending_strings": 0,
            "completed_strings": 0,
            "written_rows": 0,
            "cltk": cltk_summary,
        }

    def iter_tasks() -> Iterator[dict[str, Any]]:
        with connect_translation_db(db_path) as task_con:
            for start in range(0, len(pending), 250):
                chunk = pending[start : start + 250]
                analysis_summaries = load_analysis_summaries(
                    task_con, [int(item["string_id"]) for item in chunk]
                )
                for item in chunk:
                    yield {
                        "string_id": item["string_id"],
                        "source_text": item["source_text"],
                        "contexts": item["contexts"],
                        "languages": item["missing_languages"],
                        "linguistic_analysis": analysis_summaries.get(
                            int(item["string_id"])
                        ),
                        "model": model,
                        "base_url": base_url,
                        "api_key": api_key,
                        "timeout": timeout,
                        "retries": retries,
                        "backoff_base_s": backoff_base_s,
                        "backoff_max_s": backoff_max_s,
                        "jitter_ratio": jitter_ratio,
                        "tools_enabled": tools_enabled,
                        "dictionary_dir": str(dictionary_dir),
                        "max_tool_rounds": max_tool_rounds,
                    }

    started = time.time()
    completed = 0
    written = 0
    worker_count = min(max(1, workers), len(pending))
    context = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    if verbose:
        print(
            f"[INFO] translation-start volume={volume_id} pending_strings={len(pending)} "
            f"workers={worker_count} languages={languages} model={model} "
            f"backoff={backoff_base_s:g}..{backoff_max_s:g}s jitter={jitter_ratio:g}"
        )
    with connect_translation_db(db_path) as con:
        init_translation_schema(con)

        def store_result(result: dict[str, Any]) -> None:
            nonlocal completed, written
            written += upsert_translation_rows(
                con,
                string_id=int(result["string_id"]),
                translations=dict(result["translations"]),
                model_name=str(result["model_name"]),
                sample_context=(
                    str(result["sample_context"])
                    if result.get("sample_context") is not None else None
                ),
                commit=False,
            )
            completed += 1
            if completed % 25 == 0:
                con.commit()
            if verbose:
                print(
                    format_translation_progress(
                        volume_id=volume_id,
                        completed=completed,
                        total=len(pending),
                        result=result,
                    )
                )

        try:
            if worker_count == 1:
                for result in map(translate_candidate_worker, iter_tasks()):
                    store_result(result)
            else:
                with context.Pool(processes=worker_count) as pool:
                    for result in pool.imap_unordered(
                        translate_candidate_worker, iter_tasks(), chunksize=1
                    ):
                        store_result(result)
        finally:
            con.commit()
    return {
        "ran": True,
        "candidate_strings": len(candidates),
        "pending_strings": len(pending),
        "completed_strings": completed,
        "written_rows": written,
        "workers": worker_count,
        "elapsed_s": round(time.time() - started, 3),
        "cltk": cltk_summary,
    }


def write_pipeline_quality_reports(
    *,
    payload_file: Path,
    assembled_file: Path | None,
    intermediate_dir: Path,
    evidence_sample_size: int,
    max_unverified_ratio: float,
    skip_evidence_check: bool,
) -> tuple[Path | None, Path | None]:
    payload = read_json(payload_file)
    consumption_path: Path | None = None
    evidence_path: Path | None = None
    if assembled_file is not None and assembled_file.is_file():
        assembled = read_json(assembled_file)
        report = verify_payload_consumes_fragments(payload, assembled)
        consumption_path = intermediate_dir / "fragment_consumption_report.json"
        write_json(consumption_path, report)
        if report.get("status") != "ok":
            raise SystemExit(
                "Final payload omitted stable objects from validated chunk fragments; "
                f"see {consumption_path}"
            )
    if not skip_evidence_check:
        report = verify_index_payload_evidence(payload, sample_size=evidence_sample_size)
        evidence_path = intermediate_dir / "payload_evidence_report.json"
        write_json(evidence_path, report)
        sampled = int(report.get("sampled_entry_count") or 0)
        verified_ratio = report.get("verified_ratio")
        ratio = 1.0 - float(verified_ratio) if verified_ratio is not None else 0.0
        unverified = round(sampled * ratio)
        if int(report.get("unjustified_empty_list_section_count") or 0):
            raise SystemExit(
                "OCR evidence verification found unjustified empty list-bearing sections; "
                f"see {evidence_path}"
            )
        if ratio > max_unverified_ratio:
            raise SystemExit(
                "OCR evidence verification exceeded the limit: "
                f"{unverified}/{sampled} unverified ({ratio:.3f}); see {evidence_path}"
            )
    return consumption_path, evidence_path


def infer_failure_stage(error: BaseException) -> str:
    text = str(error).casefold()
    if "translation failed" in text or "translation keys mismatch" in text:
        return "translation"
    if "cltk" in text:
        return "cltk_analysis"
    if "chunked codex extraction" in text:
        return "chunk_extraction"
    if "omitted stable objects" in text:
        return "fragment_consumption"
    if "evidence verification" in text:
        return "payload_evidence"
    if "import_index_json.py" in text:
        return "import_payload"
    if "scan_volume.py" in text:
        return "scan"
    if "filtered_pages" in text:
        return "filtered_pages"
    if "editorial" in text:
        return "editorial_pages"
    if "helper" in text:
        return "helper"
    if (
        "codex exec" in text
        or "last message" in text
        or "acknowledgment mismatch" in text
        or "invalid acknowledgment" in text
    ):
        return "codex"
    if "validation" in text or "payload" in text and "invalid" in text:
        return "validate_payload"
    return "pipeline"


def failure_artifact_path(output_dir: Path, volume_id: str) -> Path:
    return output_dir / f"{volume_id}_failure.json"


def write_failure_artifact(
    *,
    path: Path,
    volume_id: str,
    collection: str | None,
    stage: str,
    error: BaseException,
    payload_file: Path | None,
    prescan_file: Path | None,
    filtered_pages_file: Path | None,
    editorial_pages_file: Path | None,
    helper_request_file: Path | None,
    helper_output_file: Path | None,
    last_message_file: Path | None,
    stdout_log_file: Path | None,
    stderr_log_file: Path | None,
    stream_log_file: Path | None,
) -> None:
    raw_detail = str(error) or repr(error)
    detail, duplicate_count, detail_truncated = compact_failure_text(
        raw_detail,
        PREVIOUS_FAILURE_DETAIL_MAX_CHARS,
    )
    raw_traceback = "".join(traceback.format_exception(error))
    compact_traceback, traceback_duplicate_count, traceback_truncated = compact_failure_text(
        raw_traceback,
        FAILURE_ARTIFACT_TRACEBACK_MAX_CHARS,
    )
    write_json(
        path,
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "volume_id": volume_id,
            "collection": collection,
            "stage": stage,
            "error_type": type(error).__name__,
            "error_summary": raw_detail.splitlines()[0],
            "error_detail": detail,
            "error_detail_original_chars": len(raw_detail),
            "error_detail_deduplicated_lines": duplicate_count,
            "error_detail_truncated": detail_truncated,
            "traceback": compact_traceback,
            "traceback_original_chars": len(raw_traceback),
            "traceback_deduplicated_lines": traceback_duplicate_count,
            "traceback_truncated": traceback_truncated,
            "mentions_hyphen_artifact": bool(HYPHEN_FAILURE_RE.search(raw_detail)),
            "payload_file": str(payload_file) if payload_file else None,
            "prescan_file": str(prescan_file) if prescan_file else None,
            "filtered_pages_file": str(filtered_pages_file) if filtered_pages_file else None,
            "editorial_pages_file": str(editorial_pages_file) if editorial_pages_file else None,
            "helper_request_file": str(helper_request_file) if helper_request_file else None,
            "helper_output_file": str(helper_output_file) if helper_output_file else None,
            "last_message_file": str(last_message_file) if last_message_file else None,
            "stdout_log_file": str(stdout_log_file) if stdout_log_file else None,
            "stderr_log_file": str(stderr_log_file) if stderr_log_file else None,
            "stream_log_file": str(stream_log_file) if stream_log_file else None,
        },
    )


def emit_volume_failure(
    *,
    volume_id: str,
    collection: str | None,
    error: BaseException,
    artifact_path: Path | None,
) -> None:
    print(
        json.dumps(
            {
                "status": "failed",
                "volume_id": volume_id,
                "collection": collection,
                "error_type": type(error).__name__,
                "error": str(error),
                "failure_artifact": str(artifact_path) if artifact_path else None,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the opening/general works-index pipeline for PG/PL/PO volumes."
    )
    ap.add_argument("--volume-id", help="Volume id, e.g. PG001 or PL099")
    ap.add_argument("--all-volumes", action="store_true", help="Run one Codex pass for every matching volume under --root")
    ap.add_argument("--blob", help="Prefix/glob-style filter for --all-volumes, e.g. PL or PL*")
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT / "teste", help="Root directory containing volume folders")
    ap.add_argument("--limit", type=int, default=None, help="Optional max volume count for --all-volumes")
    ap.add_argument("--codex-bin", default="codex", help="Codex CLI binary")
    ap.add_argument("--model", default=None, help="Optional Codex model override")
    ap.add_argument("--use-json", action="store_true", help="Pass --json to codex exec")
    ap.add_argument("--replace", action="store_true", help="Replace existing rows for the volume on import")
    ap.add_argument("--skip-done", action="store_true", help="Skip volumes already imported into the SQLite DB")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database used to detect already imported volumes")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for the payload JSON file")
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Directory for persistent Codex logs")
    ap.add_argument("--intermediate-root", type=Path, default=DEFAULT_INTERMEDIATE_ROOT)
    ap.add_argument("--filtered-pages-json", type=Path)
    ap.add_argument("--filtered-pages-dir", type=Path)
    ap.add_argument("--editorial-page-db", type=Path, default=DEFAULT_EDITORIAL_PAGE_DB)
    ap.add_argument("--no-editorial-page-cache", action="store_true")
    ap.add_argument("--legacy-single-context", action="store_true")
    ap.add_argument("--max-files-per-chunk", type=int, default=6)
    ap.add_argument("--chunk-overlap", type=int, default=1)
    ap.add_argument("--chunk-workers", type=int, default=1)
    ap.add_argument(
        "--helper-workers",
        type=int,
        default=12,
        help="CPU processes for deterministic target localization (default: 12; no fixed upper limit)",
    )
    ap.add_argument("--evidence-sample-size", type=int, default=200)
    ap.add_argument("--max-unverified-evidence-ratio", type=float, default=0.25)
    ap.add_argument("--skip-evidence-check", action="store_true")
    ap.add_argument(
        "--translate",
        action="store_true",
        help="Translate frontend-facing general-index strings after import",
    )
    ap.add_argument(
        "--translation-only",
        action="store_true",
        help="Translate pending strings already in the database without extraction or import",
    )
    ap.add_argument(
        "--translation-languages",
        default="en,fr,it,pt-br",
        help="Comma-separated target language codes",
    )
    ap.add_argument("--translation-model", default=None)
    ap.add_argument(
        "--translation-openai-url", default="https://api.openai.com/v1"
    )
    ap.add_argument("--translation-openai-api-key", default=None)
    ap.add_argument(
        "--translation-workers",
        type=int,
        default=DEFAULT_TRANSLATION_WORKERS,
        help=f"Parallel API request processes (default: {DEFAULT_TRANSLATION_WORKERS})",
    )
    ap.add_argument("--translation-timeout", type=int, default=180)
    ap.add_argument("--translation-retries", type=int, default=3)
    ap.add_argument(
        "--translation-backoff-base",
        type=float,
        default=DEFAULT_TRANSLATION_BACKOFF_BASE,
        help="Initial retry backoff in seconds",
    )
    ap.add_argument(
        "--translation-backoff-max",
        type=float,
        default=DEFAULT_TRANSLATION_BACKOFF_MAX,
        help="Maximum retry backoff in seconds",
    )
    ap.add_argument(
        "--translation-jitter",
        type=float,
        default=DEFAULT_TRANSLATION_JITTER,
        help="Random jitter ratio added to each exponential backoff",
    )
    ap.add_argument(
        "--translation-tools",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow bounded read-only Latin dictionary tools during translation",
    )
    ap.add_argument(
        "--translation-dictionary-dir",
        type=Path,
        default=DEFAULT_TRANSLATION_DICTIONARY_DIR,
    )
    ap.add_argument("--translation-max-tool-rounds", type=int, default=2)
    ap.add_argument(
        "--translation-cltk",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Cache CLTK lemma/POS/morphology when the isolated worker is available",
    )
    ap.add_argument("--cltk-python", type=Path, default=DEFAULT_CLTK_PYTHON)
    ap.add_argument("--cltk-workers", type=int, default=1)
    ap.add_argument("--cltk-max-tokens", type=int, default=512)
    ap.add_argument(
        "--skip-work-anchor-reconciliation",
        action="store_true",
        help="Do not audit, annotate, or repair suspicious work page/file anchors before validation and import",
    )
    ap.add_argument("--continue-on-error", action="store_true")
    ap.add_argument("--keep-temp", action="store_true", help="Keep the temporary prescan JSON file")
    ap.add_argument("--dry-run", action="store_true", help="Build prescan and prompt, then stop before calling Codex")
    ap.add_argument("--verbose", action="store_true", help="Stream Codex stdout/stderr live to the terminal")
    args = ap.parse_args()
    if args.helper_workers < 1:
        raise SystemExit("--helper-workers must be at least 1")
    if args.translation_only:
        args.translate = True
    if args.translation_only and args.dry_run:
        raise SystemExit("--translation-only and --dry-run cannot be used together.")
    args.translation_languages = normalize_language_list(args.translation_languages)
    args.translation_openai_api_key = (
        args.translation_openai_api_key or os.getenv("OPENAI_API_KEY")
    )
    args.translation_model = (
        args.translation_model or os.getenv("OPENAI_MODEL") or "gpt-5-mini"
    )
    if args.translate and not args.translation_languages:
        raise SystemExit("Use at least one language in --translation-languages.")
    if args.translate and not args.dry_run and not args.translation_openai_api_key:
        raise SystemExit(
            "Translation requires --translation-openai-api-key or OPENAI_API_KEY."
        )
    if args.translation_workers < 1 or args.cltk_workers < 1:
        raise SystemExit("Translation and CLTK worker counts must be at least 1.")
    if args.translation_retries < 1:
        raise SystemExit("--translation-retries must be at least 1.")
    if (
        args.translation_backoff_base < 0
        or args.translation_backoff_max < args.translation_backoff_base
        or args.translation_jitter < 0
    ):
        raise SystemExit(
            "Translation backoff must satisfy 0 <= base <= max and jitter >= 0."
        )
    if args.translation_max_tool_rounds < 0 or args.cltk_max_tokens < 1:
        raise SystemExit("Translation tool rounds and CLTK token limit are invalid.")

    if args.blob and not args.all_volumes:
        args.all_volumes = True

    if args.all_volumes:
        blob = args.blob or "PG*,PL*,PO*"
        volume_ids = (
            select_db_volume_ids(args.db.expanduser().resolve(), blob, args.limit)
            if args.translation_only
            else select_volume_ids(args.root, blob, args.limit)
        )
        if not volume_ids:
            raise SystemExit(f"No volumes selected for blob {blob!r}.")
    else:
        if not args.volume_id:
            raise SystemExit("Use --volume-id or --all-volumes/--blob.")
        volume_ids = [args.volume_id]

    args.root = args.root.resolve()
    args.output_dir = args.output_dir.resolve()
    args.log_dir = args.log_dir.resolve()
    args.intermediate_root = args.intermediate_root.resolve()
    args.db = args.db.resolve()
    args.translation_dictionary_dir = args.translation_dictionary_dir.expanduser().resolve()
    args.cltk_python = absolute_path_preserving_symlinks(args.cltk_python)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.intermediate_root.mkdir(parents=True, exist_ok=True)

    failed_volumes: list[str] = []
    for idx, volume_id in enumerate(volume_ids, start=1):
        volume_root = args.root / volume_id
        text_root = volume_root / "text"
        if args.translation_only:
            collection = db_volume_collection(args.db, volume_id)
            if collection is None:
                raise SystemExit(f"Volume {volume_id} is not present in {args.db}.")
        else:
            if not text_root.exists():
                raise SystemExit(f"Text directory not found: {text_root}")
            info = parse_volume_info(volume_root)
            if info is None:
                raise SystemExit(f"Could not parse volume info from {volume_root}")
            collection = info.series
        print(f"[INFO] volume {idx}/{len(volume_ids)}: {volume_id} ({collection})")
        prescan_path = args.output_dir / f"{volume_id}_prescan.json"
        filtered_path = args.output_dir / f"{volume_id}_filtered_pages.json"
        editorial_path = args.output_dir / f"{volume_id}_editorial_pages.json"
        helper_request_path = args.output_dir / f"{volume_id}_helper_request.json"
        helper_output_path = args.output_dir / f"{volume_id}_helper_output.json"
        payload_file = args.output_dir / f"{volume_id}_indices.json"
        last_message_path = args.output_dir / f"{volume_id}_last_message.txt"
        stdout_log_path = args.log_dir / f"{volume_id}_codex_stdout.log"
        stderr_log_path = args.log_dir / f"{volume_id}_codex_stderr.log"
        stream_log_path = args.log_dir / f"{volume_id}_codex_stream.log"
        progress_log = args.log_dir / f"{volume_id}_progress.log"
        progress_log.unlink(missing_ok=True)
        intermediate_dir = args.intermediate_root / volume_id
        intermediate_dir.mkdir(parents=True, exist_ok=True)
        workplan_path = intermediate_dir / "workplan.json"
        assembled_path = intermediate_dir / "assembled_fragments.json"
        failure_path = failure_artifact_path(args.output_dir, volume_id)

        if args.translation_only:
            try:
                translation_summary = run_translation_stage(
                    db_path=args.db,
                    volume_id=volume_id,
                    languages=args.translation_languages,
                    model=args.translation_model,
                    base_url=args.translation_openai_url,
                    api_key=args.translation_openai_api_key,
                    workers=args.translation_workers,
                    timeout=args.translation_timeout,
                    retries=args.translation_retries,
                    verbose=args.verbose,
                    tools_enabled=args.translation_tools,
                    dictionary_dir=args.translation_dictionary_dir,
                    max_tool_rounds=args.translation_max_tool_rounds,
                    cltk_enabled=args.translation_cltk,
                    cltk_python=args.cltk_python,
                    cltk_workers=args.cltk_workers,
                    cltk_max_tokens=args.cltk_max_tokens,
                    backoff_base_s=args.translation_backoff_base,
                    backoff_max_s=args.translation_backoff_max,
                    jitter_ratio=args.translation_jitter,
                )
                failure_path.unlink(missing_ok=True)
                print(
                    json.dumps(
                        {
                            "status": "ok",
                            "volume_id": volume_id,
                            "collection": collection,
                            "db": str(args.db),
                            "translation_only": True,
                            "translation": translation_summary,
                        },
                        ensure_ascii=False,
                    )
                )
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, GeneratorExit)):
                    raise
                write_failure_artifact(
                    path=failure_path,
                    volume_id=volume_id,
                    collection=collection,
                    stage=infer_failure_stage(error),
                    error=error,
                    payload_file=None,
                    prescan_file=None,
                    filtered_pages_file=None,
                    editorial_pages_file=None,
                    helper_request_file=None,
                    helper_output_file=None,
                    last_message_file=None,
                    stdout_log_file=None,
                    stderr_log_file=None,
                    stream_log_file=None,
                )
                if not args.continue_on_error:
                    raise
                failed_volumes.append(volume_id)
                emit_volume_failure(
                    volume_id=volume_id,
                    collection=collection,
                    error=error,
                    artifact_path=failure_path,
                )
            continue

        existing_payload_for_prompt: dict[str, Any] | None = None
        if payload_file.is_file():
            try:
                candidate_payload = read_json(payload_file)
                if isinstance(candidate_payload, dict):
                    existing_payload_for_prompt = candidate_payload
            except (OSError, json.JSONDecodeError):
                pass
        existing_payload, checkpoint_validation_failure = prevalidate_existing_payload(
            payload_file,
            volume_id,
        )

        if args.skip_done and not args.replace and volume_already_imported(args.db, volume_id):
            print(json.dumps({"status": "skipped", "reason": "already_imported", "volume_id": volume_id}, ensure_ascii=False))
            continue

        try:
            stage_started = time.monotonic()
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=1, status="START", label="prescan OCR", progress_log=progress_log)
            prescan = scan_volume(volume_id, args.root)
            write_json(prescan_path, prescan)
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=1, status="DONE", label="prescan OCR", started_at=stage_started, progress_log=progress_log)

            stage_started = time.monotonic()
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=2, status="START", label="localize candidate pages", progress_log=progress_log)
            filtered = build_filtered_pages_artifact(
                root=args.root,
                volume_id=volume_id,
                filtered_pages_json=resolve_path(args.filtered_pages_json),
                filtered_pages_dir=resolve_path(args.filtered_pages_dir),
                canonical_path=filtered_path,
            )
            if not filtered_path.exists():
                write_json(filtered_path, filtered)
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=2, status="DONE", label="localize candidate pages", started_at=stage_started, progress_log=progress_log)

            previous_workplan = read_json(workplan_path) if workplan_path.is_file() else None
            workplan = build_index_workplan(
                volume_id=volume_id,
                source_root=text_root,
                collection=collection,
                filtered_pages=filtered,
                pipeline_kind="general",
                chunk_output_dir=intermediate_dir / "chunks",
                max_files_per_chunk=args.max_files_per_chunk,
                chunk_overlap=args.chunk_overlap,
            )
            workplan = reconcile_workplan_progress(workplan, previous_workplan)
            write_json(workplan_path, workplan)
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=3, status="DONE", label="build opening-matter workplan", progress_log=progress_log)

            editorial = build_editorial_pages_artifact(
                volume_id=volume_id,
                source_root=text_root,
                collection=collection,
                output_path=editorial_path,
                db_path=args.editorial_page_db,
                use_cache=not args.no_editorial_page_cache,
            )
            if not editorial_path.exists():
                write_json(editorial_path, editorial)
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=4, status="DONE", label="estimate editorial pages", progress_log=progress_log)

            helper_request = build_helper_request_artifact(
                volume_id=volume_id,
                source_root=text_root,
                filtered_pages=filtered,
                helper_request_json=helper_request_path,
                workplan=workplan,
                workers=args.helper_workers,
            )
            if not helper_request_path.exists():
                write_json(helper_request_path, helper_request)
            helper_output = run_helper_locator(helper_request, helper_output_path)
            if not helper_output_path.exists():
                write_json(helper_output_path, helper_output)
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=5, status="DONE", label="build locator evidence", progress_log=progress_log)

            assembled_file: Path | None = None
            chunks = [item for item in workplan.get("chunks") or [] if isinstance(item, dict)]
            if args.dry_run or args.legacy_single_context or not chunks:
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=6, status="SKIP", label="extract semantic chunks", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=7, status="SKIP", label="assemble chunk fragments", progress_log=progress_log)
            else:
                run_index_chunk_agents(
                    workplan_file=workplan_path,
                    codex_bin=args.codex_bin,
                    model=args.model,
                    log_dir=args.log_dir / volume_id / "chunks",
                    workers=args.chunk_workers,
                    verbose=args.verbose,
                )
                workplan = read_json(workplan_path)
                assembled = assemble_index_fragments(workplan, assembled_path)
                assembled_file = assembled_path
                if assembled.get("status") != "complete":
                    raise SystemExit(f"Chunk assembly incomplete for {volume_id}: {assembled}")
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=6, status="DONE", label="extract semantic chunks", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=7, status="DONE", label="assemble chunk fragments", progress_log=progress_log)

            prompt = build_prompt(
                volume_id, text_root, collection, prescan_path, args.output_dir,
                filtered_path, editorial_path, helper_request_path, helper_output_path,
                workplan_path,
            )
            if assembled_file is not None:
                prompt += (
                    "\n\n### VALIDATED CHUNK ASSEMBLY\n"
                    f"Read and consume every stable object from: {assembled_file}\n"
                    "For every object owned by this general pipeline, copy `work_key`, "
                    "`section_key`, and `entry_key` exactly; these identities are immutable, "
                    "including keys containing `candidate-section`. Refine semantic fields or "
                    "`raw_json`, never the stable key. Do not reconstruct entries without their "
                    "`entry_key`. If a fragment clearly contains a closing alphabetical, "
                    "analytical, scripture, citation, names, words, or concordance index, do not "
                    "emit that non-owned section; record its stable key and exclusion reason in "
                    "`notes`. Do not silently drop any owned work, section, or entry."
                )
            previous_failure = load_failure_artifact(failure_path)
            if payload_file.exists():
                prompt += "\n\n" + build_existing_payload_prompt_block(payload_file, volume_id)
            if existing_payload_for_prompt is not None:
                anchor_rerun_block = build_work_anchor_rerun_prompt_block(
                    existing_payload_for_prompt,
                    volume_id,
                )
                if anchor_rerun_block:
                    prompt += "\n\n" + anchor_rerun_block
            if previous_failure:
                prompt += "\n\n" + build_previous_failure_prompt_block(volume_id, previous_failure)
            if checkpoint_validation_failure:
                prompt += "\n\n" + build_previous_failure_prompt_block(
                    volume_id,
                    checkpoint_validation_failure,
                )
            if existing_payload is not None and payload_has_hyphen_artifacts(existing_payload):
                prompt += "\n\n" + build_hyphen_rerun_recovery_block()
            elif failure_mentions_hyphen_artifact(previous_failure):
                prompt += "\n\n" + build_hyphen_rerun_recovery_block()
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=8, status="DONE", label="build final-agent prompt", progress_log=progress_log)

            if args.dry_run:
                prompt_path = args.output_dir / f"{volume_id}_prompt.txt"
                prompt_path.write_text(prompt, encoding="utf-8")
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=9, status="SKIP", label="run final agent", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=10, status="SKIP", label="validate acknowledgment", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=11, status="SKIP", label="validate payload and evidence", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=12, status="SKIP", label="import payload", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=13, status="SKIP", label="cache CLTK analysis", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=14, status="SKIP", label="translate index strings", progress_log=progress_log)
                print(json.dumps({"status": "dry-run", "volume_id": volume_id, "prompt_file": str(prompt_path), "workplan_file": str(workplan_path)}, ensure_ascii=False))
                continue

            result = run_codex(
                codex_bin=args.codex_bin, prompt=prompt, last_message_path=last_message_path,
                cwd=PROJECT_ROOT, use_json=args.use_json, model=args.model, verbose=args.verbose,
                stdout_log_path=stdout_log_path, stderr_log_path=stderr_log_path,
                stream_log_path=stream_log_path,
            )
            if result.returncode != 0:
                raise SystemExit(f"codex exec failed for {volume_id}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=9, status="DONE", label="run final agent", progress_log=progress_log)

            last_message = last_message_path.read_text(encoding="utf-8").strip()
            try:
                ack = json.loads(last_message)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Last message is not valid JSON: {exc}\n{last_message}") from exc
            if ack.get("status") != "ok" or ack.get("volume_id") != volume_id or ack.get("written_file") != str(payload_file):
                raise SystemExit(f"Codex acknowledgment mismatch: {ack}")
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=10, status="DONE", label="validate acknowledgment", progress_log=progress_log)

            payload = read_json(payload_file)
            if not args.skip_work_anchor_reconciliation:
                work_anchor_report = reconcile_work_anchors(
                    payload,
                    editorial_pages=editorial,
                )
                write_json(
                    intermediate_dir / "work_anchor_reconciliation_report.json",
                    work_anchor_report,
                )
                if int(work_anchor_report.get("payload_mutation_count") or 0):
                    write_json(payload_file, payload)
            validate_payload(payload, volume_id, payload_file)
            write_pipeline_quality_reports(
                payload_file=payload_file,
                assembled_file=assembled_file,
                intermediate_dir=intermediate_dir,
                evidence_sample_size=args.evidence_sample_size,
                max_unverified_ratio=args.max_unverified_evidence_ratio,
                skip_evidence_check=args.skip_evidence_check or not chunks,
            )
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=11, status="DONE", label="validate payload and evidence", progress_log=progress_log)
            import_payload(payload_file, args.db, args.replace)
            emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=12, status="DONE", label="import payload", progress_log=progress_log)
            translation_summary: dict[str, Any] = {
                "ran": False,
                "pending_strings": 0,
                "written_rows": 0,
                "cltk": {"status": "disabled"},
            }
            if args.translate:
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=13, status="START", label="cache CLTK analysis", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=14, status="START", label="translate index strings", progress_log=progress_log)
                translation_summary = run_translation_stage(
                    db_path=args.db,
                    volume_id=volume_id,
                    languages=args.translation_languages,
                    model=args.translation_model,
                    base_url=args.translation_openai_url,
                    api_key=args.translation_openai_api_key,
                    workers=args.translation_workers,
                    timeout=args.translation_timeout,
                    retries=args.translation_retries,
                    verbose=args.verbose,
                    tools_enabled=args.translation_tools,
                    dictionary_dir=args.translation_dictionary_dir,
                    max_tool_rounds=args.translation_max_tool_rounds,
                    cltk_enabled=args.translation_cltk,
                    cltk_python=args.cltk_python,
                    cltk_workers=args.cltk_workers,
                    cltk_max_tokens=args.cltk_max_tokens,
                    backoff_base_s=args.translation_backoff_base,
                    backoff_max_s=args.translation_backoff_max,
                    jitter_ratio=args.translation_jitter,
                )
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=13, status="DONE", label="cache CLTK analysis", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=14, status="DONE", label="translate index strings", progress_log=progress_log)
            else:
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=13, status="SKIP", label="cache CLTK analysis", progress_log=progress_log)
                emit_stage(verbose=args.verbose, volume_id=volume_id, volume_index=idx, volume_total=len(volume_ids), stage_index=14, status="SKIP", label="translate index strings", progress_log=progress_log)
            failure_path.unlink(missing_ok=True)
            print(json.dumps({"status": "ok", "volume_id": volume_id, "collection": collection, "payload_file": str(payload_file), "imported": True, "translation": translation_summary}, ensure_ascii=False))
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, GeneratorExit)):
                raise
            stage = infer_failure_stage(error)
            write_failure_artifact(
                path=failure_path, volume_id=volume_id, collection=collection, stage=stage,
                error=error, payload_file=payload_file, prescan_file=prescan_path,
                filtered_pages_file=filtered_path, editorial_pages_file=editorial_path,
                helper_request_file=helper_request_path, helper_output_file=helper_output_path,
                last_message_file=last_message_path, stdout_log_file=stdout_log_path,
                stderr_log_file=stderr_log_path, stream_log_file=stream_log_path,
            )
            if not args.continue_on_error:
                raise
            failed_volumes.append(volume_id)
            emit_volume_failure(volume_id=volume_id, collection=collection, error=error, artifact_path=failure_path)
            print(f"[ERROR] volume={volume_id} {error}", file=sys.stderr)

    if failed_volumes:
        print(json.dumps({"status": "batch-complete-with-errors", "total_volumes": len(volume_ids), "failed_count": len(failed_volumes), "failed_volumes": failed_volumes}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
