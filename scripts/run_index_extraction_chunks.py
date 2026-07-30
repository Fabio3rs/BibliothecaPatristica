#!/usr/bin/env python3
"""Execute an index workplan with one fresh Codex process per OCR chunk.

The script exists because a running Codex session has no reliable "clear context" operation.
Durable fragment JSON replaces conversational memory: each ephemeral process reads a bounded
physical-file scope, performs semantic entry segmentation, and is deterministically validated
before the workplan advances.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import selectors
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from patristica_pipeline.common import page_sort_key


_TERMINAL_WRITE_LOCK = threading.Lock()
HEARTBEAT_INTERVAL_SECONDS = 30.0
STALL_WARNING_SECONDS = 300.0


def _fragment_entry_count(payload: dict[str, Any], pipeline_kind: str) -> int:
    if pipeline_kind == "alphabetical":
        return len(payload.get("entries") or [])
    return sum(
        len(section.get("entries") or [])
        for section in payload.get("sections") or []
        if isinstance(section, dict)
    )


def _validation_failure(path: Path, error: BaseException) -> dict[str, str]:
    return {
        "stage": "validate_fragment",
        "fragment_file": str(path),
        "error_type": type(error).__name__,
        "error_detail": str(error),
        "detected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _existing_fragment_recovery_block(chunk: dict[str, Any]) -> str:
    failure = chunk.get("last_validation_failure")
    validation_block = ""
    if isinstance(failure, dict):
        detail = str(failure.get("error_detail") or "").strip()
        if detail:
            fragment_file = str(
                failure.get("fragment_file") or chunk.get("output_file") or "unknown"
            )
            validation_block = f"""
EXISTING FRAGMENT RECOVERY
- A fragment from an earlier run already exists at: {fragment_file}
- Treat it as a checkpoint, not as ground truth. Read it before doing broad OCR rework.
- The deterministic fragment validator rejected that checkpoint.
- validator_stage: {failure.get("stage") or "validate_fragment"}
- error_type: {failure.get("error_type") or "unknown"}
- exact_validation_failure:
```text
{detail}
```
- Correct the exact reported problem and then re-check the complete fragment contract.
- Preserve unaffected, OCR-supported objects and stable keys. Do not discard valid prior work merely
  because one contract field failed validation.
- The top-level `section_id` is the chunk assignment identifier shown under RUNTIME. Keep semantic
  editorial identifiers in the objects inside the fragment instead of substituting them for that
  top-level value.
"""
    last_error = str(chunk.get("last_error") or "").strip()
    execution_block = ""
    if last_error:
        execution_block = f"""
PREVIOUS CHUNK ATTEMPT FAILURE
- The most recent chunk attempt failed before it could be accepted.
- error_type: {chunk.get("last_error_type") or "unknown"}
- exact_previous_error:
```text
{last_error}
```
- Correct any actionable output/acknowledgment problem before repeating broad OCR work.
"""
    return validation_block + execution_block


def _attempt_number(chunk: dict[str, Any]) -> int:
    return int(chunk.get("attempt_count") or 0) + 1


def _attempt_artifact_path(
    log_dir: Path,
    *,
    order: int,
    attempt: int,
    suffix: str,
) -> Path:
    return log_dir / f"chunk_{order:04d}_attempt_{attempt:03d}_{suffix}"


def _next_available_attempt_number(
    log_dir: Path,
    *,
    order: int,
    chunk: dict[str, Any],
) -> int:
    attempt = max(
        _attempt_number(chunk),
        int(chunk.get("active_attempt_number") or 0),
    )
    while _attempt_artifact_path(
        log_dir,
        order=order,
        attempt=attempt,
        suffix="prompt.txt",
    ).exists():
        attempt += 1
    return attempt


def _emit_progress(
    message: str,
    *,
    verbose: bool,
    progress_log: Path | None,
    terminal: Any = None,
) -> None:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    line = f"{timestamp} {message}"
    with _TERMINAL_WRITE_LOCK:
        if progress_log is not None:
            with progress_log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if verbose:
            print(message, file=terminal or sys.stdout, flush=True)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _coverage_check(entry_count: int, chunk: dict[str, Any]) -> dict[str, Any]:
    estimate = (chunk.get("deterministic_entry_estimate") or {}).get("estimated_entry_count") or {}
    lower = int(estimate.get("lower_bound") or 0)
    likely = int(estimate.get("likely") or 0)
    upper = int(estimate.get("upper_bound") or 0)
    if upper == 0:
        status = "no_deterministic_signal"
    elif entry_count < lower:
        status = "below_lower_bound_review"
    elif entry_count > upper:
        status = "above_upper_bound_review"
    else:
        status = "within_estimated_range"
    return {
        "status": status,
        "agent_segmented_entry_count": entry_count,
        "deterministic_lower_bound": lower,
        "deterministic_likely": likely,
        "deterministic_upper_bound": upper,
        "interpretation": (
            "Review signal only: semantic segmentation may legitimately fall outside line-shape "
            "bounds, but the difference must not be ignored during assembly."
        ),
    }


def _chunk_prompt(workplan_path: Path, workplan: dict[str, Any], chunk: dict[str, Any]) -> str:
    pipeline_kind = str(workplan.get("pipeline_kind") or "")
    if pipeline_kind not in {"general", "alphabetical"}:
        raise ValueError(f"Unsupported pipeline_kind in workplan: {pipeline_kind!r}")
    skill = "alphabetical-index-extractor" if pipeline_kind == "alphabetical" else "patristic-index-extractor"
    output_file = Path(str(chunk["output_file"]))
    physical_files = "\n".join(f"- {value}" for value in chunk.get("physical_files") or [])
    context_files = "\n".join(f"- {value}" for value in chunk.get("context_files") or [])
    overlap_context_files = "\n".join(
        f"- {value}" for value in chunk.get("overlap_context_files") or []
    ) or "- none"
    if pipeline_kind == "alphabetical":
        fragment_fields = "sections, nodes, entries, refs, scripture_refs, notes"
    else:
        fragment_fields = "works, sections (with their entries), notes"
    estimate = chunk.get("deterministic_entry_estimate") or {}
    estimate_counts = estimate.get("estimated_entry_count") or {}
    boundary_evidence = json.dumps(
        [
            item
            for item in chunk.get("page_boundary_evidence") or []
            if item.get("likelihood") in {"high", "medium"}
        ],
        ensure_ascii=False,
        indent=2,
    )
    required_boundaries = json.dumps(
        chunk.get("required_boundary_decisions") or [], ensure_ascii=False, indent=2
    )
    recovery_block = _existing_fragment_recovery_block(chunk)
    return f"""${skill}

TASK
Read bounded OCR chunk {chunk["chunk_id"]} and write one extraction fragment. Do not assemble the volume.

RUNTIME
- workplan: {workplan_path}
- volume_id: {workplan["volume_id"]}
- source_root: {workplan["source_root"]}
- section_id: {chunk["section_id"]}
- chunk_id: {chunk["chunk_id"]}
- pipeline purpose: {workplan.get("pipeline_purpose")}
- extraction direction: {chunk.get("extraction_direction") or workplan.get("extraction_direction")}
- input_fingerprint: {chunk.get("input_fingerprint")}
- output_file: {output_file}

{recovery_block}

PHYSICAL OCR FILES
{physical_files}

ADJACENT CONTEXT FILES
{context_files}

OVERLAP CONTEXT (NOT OWNED BY THIS CHUNK)
{overlap_context_files}

NUMBERING
- `{{uuid}}-NNN.txt`: physical file/scan sequence only.
- Editorial page numbers are printed by the historical volume, commonly as
  `NUMBER  PAGE-TITLE  NUMBER+1` (for example `915  INDEX RERUM  916`).
- Depending on the OCR generator, the two numbers and title may be in one header block, split
  across separate blocks, reduced to only one visible number, corrupted by CER, or absent.
- Infer missing/corrupt editorial numbers from several physical files before and after. Never use
  the `...-NNN.txt` suffix as a replacement editorial page number.
- Numbers printed inside an index entry are non-physical source/reference data. Classify their
  semantics from context: they may be editorial pages/columns/folios/notes, scripture chapter and
  verse, book/chapter numbers, manuscript identifiers, years, or another reference system.
- Fields such as `page_ref_raw`, `page_ref_int`, `page_ref_col`, and numbers inside `refs` are
  editorial data. Only `physical_files` and explicit `*_file` locator fields identify OCR files.
- Never map these systems by numeric equality.

DETERMINISTIC COVERAGE HINT
- estimated logical-entry range: {estimate_counts.get("lower_bound", 0)} to {estimate_counts.get("upper_bound", 0)}
- likely line-boundary count: {estimate_counts.get("likely", 0)}
- This is not a proposed segmentation. Regex sees line shapes but cannot decide editorial semantics.

PHYSICAL PAGE-BOUNDARY EVIDENCE
{boundary_evidence}
- Required high-likelihood decisions: {required_boundaries}

INSTRUCTIONS
- Start with the owned and context files above, using
  `python scripts/read_ocr_page_text.py --view xml --show-source`.
- If CER, corrupted printed page numbers, an uncertain boundary, or a section that appears truncated
  makes the evidence doubtful, inspect additional neighboring files or search anywhere inside this
  volume's `source_root`. The chunk bounds limit what you emit, not what you may investigate.
- Record materially useful extra-file evidence in `raw_json`, including the files or searches checked.
- Follow the listed physical-file order. It encodes this pipeline's discovery direction; it does
  not change the editorial meaning of numbers printed inside entries.
- Context and extra investigation files may establish continuations, headings, numbering drift, or
  target evidence. They do not transfer entry ownership to this chunk.
- Emit entries only for `PHYSICAL OCR FILES`. Never re-emit an entry owned by an overlap/context
  file; another chunk owns it.
- A cross-page entry belongs to the chunk containing the physical file where its text begins in
  ascending scan order, regardless of this pipeline's processing direction.
- When one entry crosses a page, join only the proven continuation, preserve both physical source
  files in its evidence/raw_json, and do not confuse either suffix with its editorial references.
- A repeated `left_footer_catchword` is layout evidence, not entry text. Keep the word at the start
  of the right page and do not duplicate the footer catchword in `entry_raw`.
- Write `boundary_decisions` as a list of objects with `physical_left_file`,
  `physical_right_file`, `decision` (`joined_entry`, `not_same_entry`, or `uncertain_ocr`), and
  `reason`. Include one decision for every required high-likelihood boundary.
- Segment one logical editorial entry per JSON entry. Do not serialize a whole OCR block as one entry.
- Give every entry a globally unique, deterministic `entry_key` and preserve it through final
  assembly. A suitable shape is `<section_key>:<chunk_id>:<entry_order>`.
- Use real semantic reading to join OCR-wrapped lines and distinguish topics, subtopics, inherited
  dash entries, cross-references, and structural headings.
- The OCR reader automatically joins likely within-block wraps such as `pala-\\nvra` -> `palavra`.
  For an uncertain lexical hyphen, compare with the raw source before accepting that cleanup.
- A hyphen at the end of a physical page is different: remove it only when the following page proves
  that the same word continues. Preserve real lexical/editorial hyphens.
- For payload recovery after OCR verification, the optional helper is:
  `python scripts/pipeline_index_extraction/fix_linebreak_hyphens.py INPUT_JSON OUTPUT_JSON`.
  It is not evidence and must not be applied blindly to ambiguous lexical hyphens.
- Do not run the material target locator in this phase.
- Write a JSON object with schema_version, volume_id, section_id, chunk_id, input_fingerprint,
  status, physical_files, numbering_semantics, boundary_decisions, {fragment_fields}.
- Set `input_fingerprint` exactly to `{chunk.get("input_fingerprint")}`. It binds this fragment to
  the current OCR and chunk contract; never copy a fingerprint from an older fragment.
- Set `numbering_semantics` exactly to:
  {{"physical_file_fields":"physical_files_and_explicit_file_locators","entry_number_system":"editorial","numeric_equality_mapping_forbidden":true}}
- Set status to `complete` only after every readable line item in the chunk is represented.

FINAL RESPONSE
{{"status":"ok","volume_id":"{workplan["volume_id"]}","chunk_id":"{chunk["chunk_id"]}","written_file":"{output_file}"}}
"""


def _run_codex_command(
    *,
    command: list[str],
    prompt: str,
    stdout_log: Path,
    stderr_log: Path,
    verbose: bool,
    chunk_id: str,
    progress_log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
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
    output_parts: dict[str, list[str]] = {"stdout": [], "stderr": []}
    process_started_at = time.monotonic()
    last_output_at = process_started_at
    last_heartbeat_at = process_started_at

    with (
        stdout_log.open("w", encoding="utf-8") as stdout_handle,
        stderr_log.open("w", encoding="utf-8") as stderr_handle,
    ):
        log_handles = {"stdout": stdout_handle, "stderr": stderr_handle}
        while selector.get_map():
            events = selector.select(timeout=1.0)
            for key, _ in events:
                stream = key.fileobj
                label = str(key.data)
                line = stream.readline()
                if line == "":
                    selector.unregister(stream)
                    continue
                output_parts[label].append(line)
                last_output_at = time.monotonic()
                log_handles[label].write(line)
                log_handles[label].flush()
                if verbose:
                    terminal = sys.stdout if label == "stdout" else sys.stderr
                    with _TERMINAL_WRITE_LOCK:
                        terminal.write(f"[{chunk_id} codex {label}] {line}")
                        terminal.flush()
            now = time.monotonic()
            if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                silence = now - last_output_at
                state = "STALLED" if silence >= STALL_WARNING_SECONDS else "RUNNING"
                _emit_progress(
                    f"[CHUNK {chunk_id}] {state} pid={proc.pid} "
                    f"elapsed={now - process_started_at:.0f}s no_output={silence:.0f}s",
                    verbose=verbose,
                    progress_log=progress_log,
                    terminal=sys.stderr if state == "STALLED" else sys.stdout,
                )
                last_heartbeat_at = now

    return subprocess.CompletedProcess(
        args=command,
        returncode=proc.wait(),
        stdout="".join(output_parts["stdout"]),
        stderr="".join(output_parts["stderr"]),
    )


def _validate_fragment_reference_orders(
    items: list[Any],
    *,
    field: str,
    entry_keys: set[str],
    path: Path,
) -> None:
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"every {field} item must be an object in {path}")
        entry_key = str(item.get("entry_key") or "").strip()
        if not entry_key or entry_key not in entry_keys:
            raise ValueError(
                f"{field}[{index}] references missing entry_key {entry_key!r} in {path}"
            )
        ref_order = item.get("ref_order")
        if not isinstance(ref_order, int) or ref_order < 1:
            raise ValueError(
                f"{field}[{index}].ref_order must be a positive integer in {path}"
            )
        stable_key = (entry_key, ref_order)
        if stable_key in seen:
            raise ValueError(
                f"duplicate {field} stable key inside fragment {path}: "
                f"entry_key={entry_key!r}, ref_order={ref_order}"
            )
        seen.add(stable_key)


def _validate_fragment(
    path: Path,
    workplan: dict[str, Any],
    chunk: dict[str, Any],
    *,
    require_input_fingerprint: bool = False,
) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("volume_id") != workplan.get("volume_id"):
        raise ValueError(f"volume_id mismatch in {path}")
    if payload.get("chunk_id") != chunk.get("chunk_id"):
        raise ValueError(f"chunk_id mismatch in {path}")
    if payload.get("section_id") != chunk.get("section_id"):
        raise ValueError(f"section_id mismatch in {path}")
    if payload.get("status") != "complete":
        raise ValueError(f"Chunk fragment is not complete: {path}")
    if payload.get("physical_files") != chunk.get("physical_files"):
        raise ValueError(f"physical_files mismatch in {path}")
    if require_input_fingerprint:
        expected_fingerprint = str(chunk.get("input_fingerprint") or "").strip()
        if not expected_fingerprint:
            raise ValueError(
                f"current workplan chunk has no input_fingerprint for {path}"
            )
        if payload.get("input_fingerprint") != expected_fingerprint:
            raise ValueError(
                f"input_fingerprint mismatch in {path}: "
                f"expected {expected_fingerprint!r}, "
                f"got {payload.get('input_fingerprint')!r}"
            )
    expected_numbering = {
        "physical_file_fields": "physical_files_and_explicit_file_locators",
        "entry_number_system": "editorial",
        "numeric_equality_mapping_forbidden": True,
    }
    if payload.get("numbering_semantics") != expected_numbering:
        raise ValueError(f"numbering_semantics missing or invalid in {path}")
    decisions = payload.get("boundary_decisions")
    if not isinstance(decisions, list):
        raise ValueError(f"boundary_decisions must be a list in {path}")
    valid_decisions = {"joined_entry", "not_same_entry", "uncertain_ocr"}
    decision_by_boundary: dict[tuple[str, str], dict[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, dict) or item.get("decision") not in valid_decisions:
            raise ValueError(f"invalid boundary decision in {path}: {item}")
        key = (str(item.get("physical_left_file") or ""), str(item.get("physical_right_file") or ""))
        if not all(key) or not str(item.get("reason") or "").strip():
            raise ValueError(f"incomplete boundary decision in {path}: {item}")
        decision_by_boundary[key] = item
    for required in chunk.get("required_boundary_decisions") or []:
        key = (
            str(required.get("physical_left_file") or ""),
            str(required.get("physical_right_file") or ""),
        )
        if key not in decision_by_boundary:
            raise ValueError(f"missing required page-boundary decision in {path}: {key}")
    pipeline_kind = workplan.get("pipeline_kind")
    if pipeline_kind not in {"general", "alphabetical"}:
        raise ValueError(f"Unsupported pipeline_kind in workplan: {pipeline_kind!r}")
    if pipeline_kind == "alphabetical":
        entries = payload.get("entries") or []
    else:
        entries = []
        sections = payload.get("sections")
        if not isinstance(sections, list):
            raise ValueError(f"sections must be a list in {path}")
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("entries"), list):
                raise ValueError(f"general fragment sections must contain entries lists in {path}")
            entries.extend(section["entries"])
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError(f"every fragment entry must be an object in {path}")
    if int(chunk.get("chunk_contract_version") or 0) >= 2 and any(
        not str(entry.get("entry_key") or "").strip() for entry in entries
    ):
        raise ValueError(f"every v2 fragment entry must have a stable entry_key in {path}")
    stable_keys = [
        str(entry.get("entry_key"))
        for entry in entries
        if entry.get("entry_key") is not None
    ]
    if len(stable_keys) != len(set(stable_keys)):
        raise ValueError(f"duplicate entry_key inside fragment {path}")
    owned_files = {str(value) for value in chunk.get("physical_files") or []}
    for entry in entries:
        raw_json = entry.get("raw_json")
        source_files = raw_json.get("source_files") if isinstance(raw_json, dict) else None
        if not isinstance(source_files, list) or not source_files:
            if int(chunk.get("chunk_contract_version") or 0) >= 2:
                raise ValueError(
                    f"entry {entry.get('entry_key')!r} must declare "
                    f"raw_json.source_files in {path}"
                )
            continue
        ordered_sources = sorted(
            (Path(str(value)) for value in source_files),
            key=page_sort_key,
        )
        if str(ordered_sources[0]) not in owned_files:
            raise ValueError(
                f"entry source ownership violation in {path}: "
                f"ascending first source {ordered_sources[0]} is not owned by the chunk"
            )
    entry_count = len(entries)
    estimate = (chunk.get("deterministic_entry_estimate") or {}).get("estimated_entry_count") or {}
    if entry_count == 0 and int(estimate.get("lower_bound") or 0) > 0:
        raise ValueError(
            f"complete fragment has no entries despite strong deterministic entry signals in {path}"
        )
    if payload.get("schema_version") is None:
        raise ValueError(f"schema_version missing in {path}")
    required_lists = (
        ("sections", "nodes", "entries", "refs", "scripture_refs", "notes")
        if pipeline_kind == "alphabetical"
        else ("works", "sections", "notes")
    )
    for field in required_lists:
        if not isinstance(payload.get(field), list):
            raise ValueError(f"{field} must be a list in {path}")
    if pipeline_kind == "alphabetical":
        entry_keys = {
            str(entry.get("entry_key")).strip()
            for entry in entries
            if entry.get("entry_key") is not None
        }
        _validate_fragment_reference_orders(
            payload["refs"],
            field="refs",
            entry_keys=entry_keys,
            path=path,
        )
        _validate_fragment_reference_orders(
            payload["scripture_refs"],
            field="scripture_refs",
            entry_keys=entry_keys,
            path=path,
        )
    return payload


def _execute_chunk(
    *,
    order: int,
    total: int,
    chunk: dict[str, Any],
    workplan_path: Path,
    workplan: dict[str, Any],
    codex_bin: str,
    model: str | None,
    log_dir: Path,
    progress_log: Path | None,
    verbose: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output_file = Path(str(chunk["output_file"]))
    prompt = _chunk_prompt(workplan_path, workplan, chunk)
    attempt = int(chunk.get("active_attempt_number") or _attempt_number(chunk))
    prompt_file = _attempt_artifact_path(
        log_dir,
        order=order,
        attempt=attempt,
        suffix="prompt.txt",
    )
    prompt_file.write_text(prompt, encoding="utf-8")
    last_message = _attempt_artifact_path(
        log_dir,
        order=order,
        attempt=attempt,
        suffix="last_message.json",
    )
    stdout_log = _attempt_artifact_path(
        log_dir,
        order=order,
        attempt=attempt,
        suffix="stdout.log",
    )
    stderr_log = _attempt_artifact_path(
        log_dir,
        order=order,
        attempt=attempt,
        suffix="stderr.log",
    )
    command = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(last_message),
    ]
    if model:
        command.extend(["--model", model])
    _emit_progress(
        f"[CHUNK {order}/{total}] START id={chunk['chunk_id']} "
        f"files={len(chunk.get('physical_files') or [])}",
        verbose=verbose,
        progress_log=progress_log,
    )
    result = _run_codex_command(
        command=command,
        prompt=prompt,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        verbose=verbose,
        chunk_id=str(chunk["chunk_id"]),
        progress_log=progress_log,
    )
    if result.returncode != 0:
        raise RuntimeError(f"codex exec failed for {chunk['chunk_id']}; see {stderr_log}")
    ack = _read_json(last_message)
    if ack.get("status") != "ok" or ack.get("written_file") != str(output_file):
        raise RuntimeError(f"Invalid acknowledgment for {chunk['chunk_id']}: {ack}")
    return chunk, _validate_fragment(
        output_file,
        workplan,
        chunk,
        require_input_fingerprint=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run each deterministic index workplan chunk in a fresh ephemeral Codex context."
    )
    ap.add_argument("--workplan", type=Path, required=True)
    ap.add_argument("--codex-bin", default="codex")
    ap.add_argument("--model")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip-complete", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-dir", type=Path)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--verbose", action="store_true", help="Stream each Codex process live")
    args = ap.parse_args()

    workplan = _read_json(args.workplan)
    chunks = [item for item in workplan.get("chunks") or [] if isinstance(item, dict)]
    if args.limit is not None and args.limit > 0:
        chunks = chunks[: args.limit]
    log_dir = args.log_dir or args.workplan.parent / "chunk_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_log = log_dir / "progress.log"
    _emit_progress(
        f"[RUN] START workplan={args.workplan}",
        verbose=args.verbose,
        progress_log=progress_log,
    )

    runnable: list[tuple[int, dict[str, Any]]] = []
    skipped_count = 0
    total_chunks = len(chunks)
    workplan_changed = False
    for order, chunk in enumerate(chunks, start=1):
        output_file = Path(str(chunk["output_file"]))
        existing_fragment: dict[str, Any] | None = None
        fingerprint_required = (
            int(chunk.get("chunk_contract_version") or 0) >= 2
            or bool(chunk.get("input_fingerprint"))
        )
        if output_file.exists():
            try:
                existing_fragment = _validate_fragment(
                    output_file,
                    workplan,
                    chunk,
                    require_input_fingerprint=fingerprint_required,
                )
            except Exception as exc:
                chunk["status"] = "pending"
                chunk["last_validation_failure"] = _validation_failure(output_file, exc)
                workplan_changed = True
                _emit_progress(
                    f"[CHUNK {order}/{total_chunks}] PREVALIDATION_FAIL "
                    f"id={chunk['chunk_id']} error={str(exc).splitlines()[0]}",
                    verbose=args.verbose,
                    progress_log=progress_log,
                    terminal=sys.stderr,
                )
        elif "last_validation_failure" in chunk:
            chunk.pop("last_validation_failure", None)
            workplan_changed = True
        fingerprint_matches = (
            existing_fragment is not None
            and bool(chunk.get("input_fingerprint"))
            and existing_fragment.get("input_fingerprint") == chunk.get("input_fingerprint")
        )
        trusted_legacy_complete = (
            chunk.get("status") == "complete" and not fingerprint_required
        )
        if (
            args.skip_complete
            and existing_fragment is not None
            and (trusted_legacy_complete or fingerprint_matches)
        ):
            if chunk.get("status") != "complete":
                chunk["status"] = "complete"
                chunk["recovered_existing_fragment_at"] = (
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                )
                chunk["entry_count"] = _fragment_entry_count(
                    existing_fragment,
                    str(workplan.get("pipeline_kind") or ""),
                )
                chunk["coverage_check"] = _coverage_check(chunk["entry_count"], chunk)
                workplan_changed = True
            if "last_validation_failure" in chunk:
                chunk.pop("last_validation_failure", None)
                workplan_changed = True
            skipped_count += 1
            _emit_progress(
                f"[CHUNK {order}/{total_chunks}] SKIP id={chunk['chunk_id']} "
                "reason=existing-fragment-valid",
                verbose=args.verbose,
                progress_log=progress_log,
            )
            continue
        if (
            args.skip_complete
            and existing_fragment is not None
            and not trusted_legacy_complete
            and not fingerprint_matches
        ):
            fingerprint_error = ValueError(
                "Existing pending fragment cannot be reused because input_fingerprint "
                f"does not match the current chunk: expected "
                f"{chunk.get('input_fingerprint')!r}, got "
                f"{existing_fragment.get('input_fingerprint')!r}"
            )
            chunk["last_validation_failure"] = _validation_failure(
                output_file,
                fingerprint_error,
            )
            workplan_changed = True
            _emit_progress(
                f"[CHUNK {order}/{total_chunks}] PREVALIDATION_FAIL "
                f"id={chunk['chunk_id']} error={str(fingerprint_error)}",
                verbose=args.verbose,
                progress_log=progress_log,
                terminal=sys.stderr,
            )
        if args.skip_complete and chunk.get("status") == "complete":
            if not output_file.exists():
                chunk["status"] = "pending"
                workplan_changed = True
            else:
                skipped_count += 1
                _emit_progress(
                    f"[CHUNK {order}/{total_chunks}] SKIP id={chunk['chunk_id']} "
                    "reason=already-complete",
                    verbose=args.verbose,
                    progress_log=progress_log,
                )
                continue
        prompt = _chunk_prompt(args.workplan, workplan, chunk)
        active_attempt = _next_available_attempt_number(
            log_dir,
            order=order,
            chunk=chunk,
        )
        chunk["active_attempt_number"] = active_attempt
        prompt_file = _attempt_artifact_path(
            log_dir,
            order=order,
            attempt=active_attempt,
            suffix="prompt.txt",
        )
        prompt_file.write_text(prompt, encoding="utf-8")
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "dry-run",
                        "chunk_id": chunk["chunk_id"],
                        "prompt_file": str(prompt_file),
                        "output_file": str(output_file),
                        "prompt_bytes": len(prompt.encode("utf-8")),
                    },
                    ensure_ascii=False,
                )
            )
            continue
        runnable.append((order, chunk))

    if args.dry_run:
        for chunk in chunks:
            chunk.pop("active_attempt_number", None)
        if workplan_changed:
            if workplan.get("chunks") and all(
                isinstance(item, dict) and item.get("status") == "complete"
                for item in workplan.get("chunks") or []
            ):
                workplan["manifest_status"] = "complete"
            else:
                workplan["manifest_status"] = "candidate"
            _write_json(args.workplan, workplan)
        return

    if workplan_changed:
        if workplan.get("chunks") and all(
            isinstance(item, dict) and item.get("status") == "complete"
            for item in workplan.get("chunks") or []
        ):
            workplan["manifest_status"] = "complete"
        else:
            workplan["manifest_status"] = "candidate"
        _write_json(args.workplan, workplan)

    queued_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for _, chunk in runnable:
        chunk["status"] = "queued"
        chunk["queued_at"] = queued_at
    if runnable:
        workplan["manifest_status"] = "running"
        _write_json(args.workplan, workplan)

    _emit_progress(
        f"[CHUNKS] READY total={total_chunks} runnable={len(runnable)} "
        f"skipped={skipped_count} "
        f"workers={min(max(1, args.workers), max(1, len(runnable)))} "
        "retry_policy=none "
        f"progress_log={progress_log}",
        verbose=args.verbose,
        progress_log=progress_log,
    )

    position_by_chunk_id = {
        str(chunk["chunk_id"]): order
        for order, chunk in enumerate(chunks, start=1)
    }

    def emit_chunk_summary() -> None:
        _emit_progress(
            f"[CHUNKS] DONE total={total_chunks} completed={len(runnable)} "
            f"skipped={skipped_count}",
            verbose=args.verbose,
            progress_log=progress_log,
        )

    def execute_chunk(
        order: int,
        chunk: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            return _execute_chunk(
                order=order,
                total=total_chunks,
                chunk=chunk,
                workplan_path=args.workplan,
                workplan=workplan,
                codex_bin=args.codex_bin,
                model=args.model,
                log_dir=log_dir,
                progress_log=progress_log,
                verbose=args.verbose,
            )
        except BaseException as exc:
            _emit_progress(
                f"[CHUNK {order}/{total_chunks}] FAIL id={chunk['chunk_id']} "
                f"error={str(exc).splitlines()[0]}",
                verbose=args.verbose,
                progress_log=progress_log,
                terminal=sys.stderr,
            )
            raise

    def record_completion(chunk: dict[str, Any], fragment: dict[str, Any]) -> None:
        output_file = Path(str(chunk["output_file"]))
        position = position_by_chunk_id[str(chunk["chunk_id"])]
        chunk["status"] = "complete"
        chunk["attempt_count"] = int(chunk.get("attempt_count") or 0) + 1
        chunk["completed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        chunk["entry_count"] = _fragment_entry_count(
            fragment,
            str(workplan.get("pipeline_kind") or ""),
        )
        chunk.pop("active_attempt_number", None)
        chunk.pop("last_validation_failure", None)
        chunk.pop("last_error", None)
        chunk.pop("last_error_type", None)
        chunk["coverage_check"] = _coverage_check(chunk["entry_count"], chunk)
        if all(
            str(item.get("status") or "") == "complete"
            for item in workplan.get("chunks") or []
            if isinstance(item, dict)
        ):
            workplan["manifest_status"] = "complete"
        _write_json(args.workplan, workplan)
        result_line = json.dumps(
            {
                "status": "ok",
                "chunk_id": chunk["chunk_id"],
                "output_file": str(output_file),
                "entry_count": chunk["entry_count"],
                "coverage_check": chunk["coverage_check"],
            },
            ensure_ascii=False,
        )
        _emit_progress(
            f"[CHUNK {position}/{total_chunks}] DONE id={chunk['chunk_id']} "
            f"entries={chunk['entry_count']} "
            f"coverage={chunk['coverage_check']['status']} output={output_file}",
            verbose=args.verbose,
            progress_log=progress_log,
        )
        with _TERMINAL_WRITE_LOCK:
            print(result_line, flush=True)

    def record_failure(chunk: dict[str, Any], error: BaseException) -> None:
        chunk["status"] = "failed"
        chunk["attempt_count"] = int(chunk.get("attempt_count") or 0) + 1
        chunk["failed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        chunk["last_error"] = str(error) or repr(error)
        chunk["last_error_type"] = type(error).__name__
        chunk.pop("active_attempt_number", None)
        workplan["manifest_status"] = "failed"
        _write_json(args.workplan, workplan)

    worker_count = min(max(1, args.workers), max(1, len(runnable)))
    if worker_count == 1:
        for order, chunk in runnable:
            try:
                completed_chunk, fragment = execute_chunk(order, chunk)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                record_failure(chunk, exc)
                raise
            record_completion(completed_chunk, fragment)
        emit_chunk_summary()
        return

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                execute_chunk,
                order,
                chunk,
            ): chunk
            for order, chunk in runnable
        }
        for future in as_completed(futures):
            try:
                completed_chunk, fragment = future.result()
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                failed_chunk = futures[future]
                record_failure(failed_chunk, exc)
                failures.append(f"{failed_chunk['chunk_id']}: {exc}")
                continue
            record_completion(completed_chunk, fragment)
    if failures:
        _emit_progress(
            f"[CHUNKS] FAIL failed={len(failures)} completed={len(runnable) - len(failures)} "
            f"skipped={skipped_count}",
            verbose=args.verbose,
            progress_log=progress_log,
            terminal=sys.stderr,
        )
        raise SystemExit("; ".join(failures))
    emit_chunk_summary()


if __name__ == "__main__":
    main()
