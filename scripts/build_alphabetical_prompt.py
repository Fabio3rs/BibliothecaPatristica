#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXISTING_PAYLOAD_SNIPPET_BYTES = 120_000
FILTERED_PAGES_SNIPPET_BYTES = 80_000
PREVIOUS_FAILURE_SNIPPET_BYTES = 40_000
OUTPUT_CHECKPOINT_ERROR_SNIPPET_BYTES = 40_000
PROMPT_MAX_BYTES = 900_000


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return path.read_text(encoding="utf-8")


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


def summarize_json_block(value: Any, *, max_bytes: int) -> str:
    pretty = json.dumps(value, ensure_ascii=False, indent=2)
    snippet, was_truncated = truncate_utf8(pretty, max_bytes)
    if was_truncated:
        return "\n".join(
            [
                snippet.rstrip(),
                "... [TRUNCATED]",
            ]
        )
    return snippet


def summarize_text_block(text: str, *, max_bytes: int) -> str:
    snippet, was_truncated = truncate_utf8(text, max_bytes)
    if was_truncated:
        return "\n".join(
            [
                snippet.rstrip(),
                "... [TRUNCATED]",
            ]
        )
    return snippet


def summarize_existing_payload(payload_file: Path, volume_id: str) -> str:
    raw_text = payload_file.read_text(encoding="utf-8")
    summary = [
        f"### Existing alphabetical payload for {volume_id}",
        f"- File: {payload_file}",
        f"- Size: {len(raw_text.encode('utf-8'))} bytes",
    ]
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        snippet, was_truncated = truncate_utf8(raw_text, EXISTING_PAYLOAD_SNIPPET_BYTES)
        summary.extend(
            [
                f"- Existing file is not valid JSON: {exc}",
                f"- Embedded excerpt bytes: {len(snippet.encode('utf-8'))} / {EXISTING_PAYLOAD_SNIPPET_BYTES}",
                "- The excerpt below may be truncated. Read the file directly if needed, then rewrite it.",
                "",
                "```json",
                snippet.rstrip(),
                "... [TRUNCATED]" if was_truncated else "",
                "```",
                f"Check and fix/add what is missing/what is wrong following the real files for volume {volume_id}.",
            ]
        )
        return "\n".join(line for line in summary if line != "")

    sections = payload.get("sections") if isinstance(payload, dict) else []
    nodes = payload.get("nodes") if isinstance(payload, dict) else []
    entries = payload.get("entries") if isinstance(payload, dict) else []
    refs = payload.get("refs") if isinstance(payload, dict) else []
    scripture_refs = payload.get("scripture_refs") if isinstance(payload, dict) else []
    summary.extend(
        [
            f"- sections: {len(sections) if isinstance(sections, list) else 'invalid'}",
            f"- nodes: {len(nodes) if isinstance(nodes, list) else 'invalid'}",
            f"- entries: {len(entries) if isinstance(entries, list) else 'invalid'}",
            f"- refs: {len(refs) if isinstance(refs, list) else 'invalid'}",
            f"- scripture_refs: {len(scripture_refs) if isinstance(scripture_refs, list) else 'invalid'}",
            "- Read the existing file directly if you need the full prior payload.",
            "- The embedded excerpt below is intentionally truncated to keep the prompt under the input limit.",
        ]
    )
    snippet = summarize_json_block(payload, max_bytes=EXISTING_PAYLOAD_SNIPPET_BYTES)
    summary.extend(
        [
            "",
            "Embedded excerpt:",
            "```json",
            snippet.rstrip(),
            "```",
            f"Check and fix/add what is missing/what is wrong following the real files for volume {volume_id}.",
        ]
    )
    return "\n".join(line for line in summary if line != "")


def summarize_previous_result_status(payload_file: Path) -> list[str]:
    if not payload_file.exists():
        return [
            "- rerun_checkpoint_guidance: none",
        ]
    try:
        payload = read_json(payload_file)
    except Exception:
        return [
            "- rerun_checkpoint_guidance: existing payload is invalid JSON; use it only as a rough checkpoint and rebuild carefully.",
        ]
    if not isinstance(payload, dict):
        return [
            "- rerun_checkpoint_guidance: existing payload is not a JSON object; use it only as a rough checkpoint and rebuild carefully.",
        ]

    entries = payload.get("entries")
    refs = payload.get("refs")
    if not isinstance(entries, list) or not isinstance(refs, list):
        return [
            "- rerun_checkpoint_guidance: existing payload has incomplete top-level lists; verify the structure before reusing any part of it.",
        ]

    null_target_file_best = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get("target_file_best")
        if value is None or not str(value).strip():
            null_target_file_best += 1

    null_ref_target = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        value = ref.get("target_file")
        if value is None or not str(value).strip():
            null_ref_target += 1

    lines = [
        "- rerun_checkpoint_guidance: treat the previous payload as a checkpoint only; verify it against OCR before carrying anything forward.",
        f"- previous_entries_missing_target_file_best: {null_target_file_best}/{len(entries)}",
        f"- previous_refs_missing_target_file: {null_ref_target}/{len(refs)}",
    ]
    if null_target_file_best or null_ref_target:
        lines.extend(
            [
                "- This rerun should actively try to resolve material locators that remained null in the previous payload.",
                "- Re-check `target_file_best` and `refs[*].target_file` instead of preserving nulls by inertia when OCR and helper evidence now allow a better resolution.",
            ]
        )
    else:
        lines.append(
            "- The previous payload already resolved most material locators; reuse it carefully and focus on correctness, not blind rewrites."
        )
    return lines


def summarize_previous_failure(
    *,
    volume_id: str,
    previous_failure: dict[str, Any] | None,
    output_checkpoint_status: str,
    output_checkpoint_error: str | None,
) -> str:
    lines = ["PREVIOUS FAILURE"]
    if previous_failure is None and output_checkpoint_status != "invalid":
        lines.append("- status: none")
        return "\n".join(lines)

    if previous_failure is not None:
        lines.extend(
            [
                "- status: present",
                f"- stage: {previous_failure.get('stage') or 'unknown'}",
                f"- payload_file: {previous_failure.get('payload_file') or 'unknown'}",
            ]
        )
        summary = previous_failure.get("error_summary")
        detail = previous_failure.get("error_detail")
        if isinstance(summary, str) and summary.strip():
            lines.append(f"- error_summary: {summary.strip()}")
        if isinstance(detail, str) and detail.strip():
            lines.extend(
                [
                    "- exact_failure:",
                    "```text",
                    summarize_text_block(detail.strip(), max_bytes=PREVIOUS_FAILURE_SNIPPET_BYTES),
                    "```",
                ]
            )

    if output_checkpoint_status == "invalid" and output_checkpoint_error:
        lines.extend(
            [
                f"- output_checkpoint_status: {output_checkpoint_status}",
                "- current_validation_failure:",
                "```text",
                summarize_text_block(
                    output_checkpoint_error.strip(),
                    max_bytes=OUTPUT_CHECKPOINT_ERROR_SNIPPET_BYTES,
                ),
                "```",
            ]
        )

    lines.extend(
        [
            f"- Fix the exact failure for {volume_id} before finalizing the replacement payload.",
            "- If the failure message points to a specific object such as `refs[2]`, `entries[15]`, or a key mismatch, inspect that exact object in the existing payload and verify the corrected version against OCR.",
        ]
    )
    return "\n".join(lines)


def summarize_intermediate_dir(intermediate_dir: Path) -> str:
    lines = [
        "WORKSPACE PATHS",
        f"- intermediate_dir: {intermediate_dir}",
    ]
    if not intermediate_dir.exists():
        lines.append("- intermediate_status: missing")
        return "\n".join(lines)

    files = sorted(path for path in intermediate_dir.iterdir() if path.is_file())
    lines.append("- intermediate_status: present")
    if not files:
        lines.append("- intermediate_files: []")
        return "\n".join(lines)

    lines.append("- intermediate_files:")
    for path in files:
        lines.append(f"  - {path.name} ({path.stat().st_size} bytes)")
    return "\n".join(lines)


def build_work_instructions(source_root: Path) -> str:
    lines = [
        "- Stay inside the current `source_root`.",
        "- Use the filtered pages as hints, then inspect the OCR directly.",
        "- `entry_raw` must represent one logical index entry only; never copy an entire OCR page, full column, or long continuous block containing multiple independent entries.",
        "- If an index entry spans multiple OCR lines, merge only the lines that belong to that same entry; split neighboring entries instead of concatenating them.",
        "- Treat extremely long `entry_raw` values as a segmentation failure signal; if the text looks like many subentries or a whole page, re-segment before serializing.",
        "- `context_raw` is optional support context, not a second copy of `entry_raw`.",
        "- Use `context_raw` only when it materially helps locate or disambiguate the entry in the target text.",
        "- Keep `context_raw` short: usually one to three brief sentences, or a compact local excerpt around the key citation.",
        "- If there is no additional context beyond the entry itself, set `context_raw` to null instead of duplicating `entry_raw` mechanically.",
        "- Preserve the literal OCR in `entry_raw`, but avoid carrying redundant surrounding matter that belongs to adjacent entries.",
        "- Use `raw_json` for evidence, ambiguity, helper decisions, and editorial notes; do not dump large OCR blocks there when the structured fields already capture the entry.",
        "- You may write reusable helper scripts under `scripts/pipeline_index_extraction/` and execute them in the same run.",
        "- Any new helper script must start with a short top-of-file usage comment explaining what the script does and how to run it.",
        "- You may write persistent per-volume checkpoint JSON files under the runtime `intermediate_dir`.",
        "- Prefer building large payloads from intermediate JSON fragments plus small assembly scripts instead of rewriting the full final JSON manually.",
        "- Reuse existing intermediate files for the current volume when they remain compatible with OCR and the current failure context.",
        "- Use divide-and-conquer when the volume is large or irregular: split by section, letter group, entry range, or refs/scripture_refs pass, then merge carefully.",
        "- Keep a short TODO list in the intermediate workspace when the job spans multiple chunks or retries.",
        "- Use the standard TODO path `intermediate_dir/todo.json`.",
        "- Use this TODO shape: `volume_id`, `updated_at`, `current_focus`, `completed`, `pending`, `blocked`, `notes`.",
        "- Update TODO notes and intermediate artifacts together so saved state reflects the true current status of the volume.",
        "- Confirm which candidate sections are true alphabetical, analytical, onomastic, scripture, concordance, or editorial-closure sections.",
        "- Keep `section_kind` inside the importer enum only; do not invent narrower ad hoc values such as `onomastic_veterum` or `onomastic_recentiorum`.",
        "- Map headings like `INDEX AUCTORUM ...` to `author_index`; keep distinctions such as `veterum` versus `recentiorum` in `heading_raw`, `section_key`, and `raw_json.section_kind_reason`, not in `section_kind`.",
        "- Letters, divider headings, rubrics, and ordinal groupings such as `A`, `B`, `SUMMI PONTIFICES`, `I.`, `II.` should normally be represented through `nodes` first, not flattened into ordinary lemma entries.",
        "- Do not promote a structural heading to `lemma` unless the printed material truly treats it as a logical index entry.",
        "- Build one helper request JSON for the current volume, following the shape used in `docs/examples/index_target_locator/*.json`.",
        "- The helper request should include entries for every index line whose target location needs material resolution.",
        "- Structural titles such as `CAP. ...`, `INDEX SCRIPTORUM ...`, `INDEX SCRIPTURÆ SACRÆ`, `INDICES IN VITAS PATRUM`, `TABLE ...`, and `ORDO ...` are not ordinary entries by themselves and should not be promoted to helper-request lemma candidates unless the printed material truly uses them as navigable entries.",
        "- Include `expected_manual` only if the runtime input explicitly provides manual validation data. Otherwise omit it.",
        "- Run: `python scripts/index_target_locator.py --input <helper_request_json> --output <helper_output_json> --pretty`.",
        "- Use helper output to support material anchors, not to replace editorial judgment.",
        "- Preserve helper evidence in `raw_json` whenever it materially affects `section_start_file`, `editorial_anchor_file`, `target_file_best`, or `target_file`.",
        "- Keep OCR literals.",
        "- Distinguish OCR file suffixes from editorial page numbers and cited references.",
        "- If real index material exists but a locator is still ambiguous, do not stop at the first mismatch between the printed page and the OCR file suffix.",
        "- When the best candidate file does not clearly confirm the printed page or the lemma, inspect the immediate neighboring OCR files before concluding that the reference is unresolved.",
        "- If the local page-number signal is weak because of CER, page drift, inherited dashes, or OCR corruption, expand the check to a small local window and look for better corroborating evidence.",
        "- You are explicitly allowed to run local OCR searches inside the current volume with `rg -n -S` and volume-specific regex patterns derived from the material you see.",
        "- Prefer regexes that match the actual editorial pattern of the current volume, including forms such as `450_3-5`, `55₁₀`, `464 n. 1`, `37 à 39`, `174, 175`, or inherited `—` lines.",
        "- If a lemma is distinctive, search the lemma directly in the current `source_root` with normalized and OCR-tolerant variants before leaving the entry partial.",
        "- Before declaring `partial_*`, `ambiguous`, or leaving a section under-extracted, try direct OCR inspection, neighboring-page checks, and at least one pattern search that fits the volume.",
        "- When these extra checks still do not resolve the case, preserve in `raw_json` which searches or page-window checks were attempted and why they were insufficient.",
        "- In `refs`, `ref_order` must be unique within each `entry_key` and should normally be sequential starting at 1 for that entry.",
        "- In `scripture_refs`, `ref_order` must be unique within each `entry_key` and should normally be sequential starting at 1 for that entry.",
        "- Do not emit duplicate `refs` or duplicate `scripture_refs` objects.",
        "- Every entry should have at least one material `ref` or parsed `scripture_ref` whenever the printed evidence is sufficient to recover one.",
        "- Never use an editorial section title or page title as `book_raw` or `book_norm` inside `scripture_refs`.",
        "- `book_raw` / `book_norm` must come from an explicit biblical book label or from safe inheritance from the nearest real biblical heading, such as `GENESIS.`, `EXODUS.`, `Psal. ...`, `EX PSALMO I.`, or equivalent localized forms.",
        "- In scripture tables, a heading like `INDEX SCRIPTURÆ SACRÆ.` or `TABLE DES CITATIONS DE LA BIBLE` announces the section only; it does not identify the biblical book for each row.",
        "- When a scripture line contains only chapter/verse or verse-level material such as `II, 7.` or `v. 2.`, inherit the biblical book only from the nearest explicit biblical heading and only until the next explicit biblical heading.",
        "- If a scripture-like line does not have a safe explicit or inherited biblical book context, omit that `scripture_ref` instead of inventing one.",
        "- Use PT-BR canonical book names for `book_norm` and `ref_norm`, compatible with `scripture_ref_normalizer.py`; preserve the original printed form in `book_raw` and `ref_raw`.",
        "- Treat verse-apparatus sections such as `LOCA EX PSALMIS` / `VARIANTIA IN PSALTERIIS` as a dedicated scripture-apparatus pattern, not as an ordinary alphabetical subject index.",
        "- In those apparatus sections, references like `v. 2.` and `v. 5.` inherit the current psalm/book from the local heading, for example `EX PSALMO I.`.",
        "- Do not leave an entry anchorless if a page number, column, line, range, target locator, or biblical citation can still be recovered from the printed material.",
        "- Do not serialize bare `vid.`, `vide`, `voir`, `v.`, `cf.`, or `id.` remissions as `refs` unless they also carry a real material locator.",
        "- Cross-references without a page, range, line, or target locator belong in the entry semantics, usually as `entry_kind: cross_reference`, or they should remain documented in `entry_raw` / `raw_json`.",
        "- When OCR is ambiguous, lower confidence and keep the ambiguity explicit instead of normalizing it away.",
        "- When segmentation is doubtful, prefer multiple smaller conservative entries over one oversized synthetic block.",
        "- Avoid empty `entries` whenever real line items can be recovered from OCR.",
        "- Do not invent placeholder entries just to avoid an empty list.",
        "- If `sections` is non-empty and `entries` must remain empty, set `coverage.entries_status` to `unrecoverable_ocr` or `no_line_items`, add a non-empty `coverage.entries_status_reason`, and include non-empty `coverage.evidence_files`.",
        "- When rerunning after a previous validation or import failure, read the exact failure message first and verify the specific offending objects before rewriting the payload.",
        "- When rerunning with an existing payload checkpoint, do not treat previously null `target_file_best` or `refs[*].target_file` values as final by default.",
        "- On reruns, actively retry unresolved material locators whenever neighboring-page inspection, helper evidence, estimator hints, or direct OCR searches now make a better resolution possible.",
        "- Keep prior good structure when it remains supported, but use the rerun to improve locator completeness rather than merely reproducing the earlier nulls.",
        "- Before writing the final payload, validate key relationships and keep all searches scoped to the current volume only.",
        f"- Good local-search pattern: `rg -n -S \"STRING\" {source_root}`.",
    ]
    return "\n".join(lines)


def build_prompt(
    *,
    volume_id: str,
    source_root: Path,
    collection: str,
    filtered_pages: dict[str, Any],
    previous_payload: Path,
    previous_result_source: str,
    output_checkpoint_status: str,
    output_checkpoint_error: str | None,
    previous_failure: dict[str, Any] | None,
    helper_request_json: Path,
    helper_output_json: Path,
    pipeline_scripts_dir: Path,
    intermediate_dir: Path,
    output_file: Path,
) -> str:
    previous_status = "present" if previous_payload.exists() else "absent"
    filtered_pages_text = summarize_json_block(
        filtered_pages,
        max_bytes=FILTERED_PAGES_SNIPPET_BYTES,
    )
    prompt = f"""$alphabetical-index-extractor volume {volume_id} located at {source_root}

TASK
You are building the alphabetical-index payload for one OCR volume.

VOLUME
- volume_id: {volume_id}
- source_root: {source_root}
- collection: {collection}

NUMBERING GLOSSARY
- OCR file / physical OCR file / file suffix: the numeric suffix in `...-NNN.txt`; this identifies the OCR text file only.
- Editorial page / printed page: the number printed in the source and cited by the index.
- Cited reference: the number, column, line, or range printed inside the index entry.
- Do not collapse these numbering systems.

FILTERED PAGES
{filtered_pages_text}

PREVIOUS RESULT
- path: {previous_payload}
- status: {previous_status}
- source: {previous_result_source}
- output_checkpoint_status: {output_checkpoint_status}
{chr(10).join(summarize_previous_result_status(previous_payload))}

{summarize_previous_failure(
    volume_id=volume_id,
    previous_failure=previous_failure,
    output_checkpoint_status=output_checkpoint_status,
    output_checkpoint_error=output_checkpoint_error,
)}

HELPER PATHS
- helper_request_json: {helper_request_json}
- helper_output_json: {helper_output_json}

{summarize_intermediate_dir(intermediate_dir)}
- pipeline_scripts_dir: {pipeline_scripts_dir}

WORK INSTRUCTIONS
{build_work_instructions(source_root)}

OUTPUT FILE
Write the full JSON payload to the output file named below.
The payload must follow `.codex/skills/alphabetical-index-extractor/references/output-format.md`.

{output_file}

FINAL RESPONSE
{{"status":"ok","volume_id":"{volume_id}","written_file":"{output_file}"}}
"""
    if previous_payload.exists():
        prompt += "\n\n" + summarize_existing_payload(previous_payload, volume_id)
    prompt_bytes = len(prompt.encode("utf-8"))
    if prompt_bytes > PROMPT_MAX_BYTES:
        raise ValueError(
            f"Prompt for {volume_id} exceeds the maximum safe size after truncation: "
            f"{prompt_bytes} bytes > {PROMPT_MAX_BYTES} bytes."
        )
    return prompt


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the runtime prompt for alphabetical index extraction.")
    ap.add_argument("--volume", required=True, help="Volume id, e.g. PG003 or PO025")
    ap.add_argument("--source-root", type=Path, required=True, help="OCR text root for the volume")
    ap.add_argument("--collection", required=True, help="Volume collection, e.g. PG/PL/PO")
    ap.add_argument("--filtered-pages-json", type=Path, required=True, help="Canonical filtered-pages JSON artifact")
    ap.add_argument("--previous-payload", type=Path, required=True, help="Path to a prior payload used as checkpoint")
    ap.add_argument("--previous-result-source", required=True, help="How the previous payload path was resolved")
    ap.add_argument("--output-checkpoint-status", required=True, help="Current status of the output payload path")
    ap.add_argument(
        "--output-checkpoint-error-file",
        type=Path,
        default=None,
        help="File containing current validation/import failure detail for the output payload path",
    )
    ap.add_argument("--previous-failure-json", type=Path, help="Structured failure sidecar for the same volume")
    ap.add_argument("--helper-request-json", type=Path, required=True, help="Helper request JSON path")
    ap.add_argument("--helper-output-json", type=Path, required=True, help="Helper output JSON path")
    ap.add_argument("--pipeline-scripts-dir", type=Path, required=True, help="Reusable helper scripts directory")
    ap.add_argument("--intermediate-dir", type=Path, required=True, help="Per-volume intermediate checkpoint directory")
    ap.add_argument("--output-file", type=Path, required=True, help="Final payload JSON path")
    args = ap.parse_args()

    filtered_pages = read_json(args.filtered_pages_json)
    previous_failure = read_json(args.previous_failure_json) if args.previous_failure_json else None
    output_checkpoint_error = read_text(args.output_checkpoint_error_file)
    print(
        build_prompt(
            volume_id=args.volume,
            source_root=args.source_root,
            collection=args.collection,
            filtered_pages=filtered_pages,
            previous_payload=args.previous_payload,
            previous_result_source=args.previous_result_source,
            output_checkpoint_status=args.output_checkpoint_status,
            output_checkpoint_error=output_checkpoint_error,
            previous_failure=previous_failure,
            helper_request_json=args.helper_request_json,
            helper_output_json=args.helper_output_json,
            pipeline_scripts_dir=args.pipeline_scripts_dir,
            intermediate_dir=args.intermediate_dir,
            output_file=args.output_file,
        )
    )


if __name__ == "__main__":
    main()
