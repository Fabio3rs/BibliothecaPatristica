#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXISTING_PAYLOAD_SNIPPET_BYTES = 120_000


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    snippet, was_truncated = truncate_utf8(pretty, EXISTING_PAYLOAD_SNIPPET_BYTES)
    summary.extend(
        [
            "",
            "Embedded excerpt:",
            "```json",
            snippet.rstrip(),
            "... [TRUNCATED]" if was_truncated else "",
            "```",
            f"Check and fix/add what is missing/what is wrong following the real files for volume {volume_id}.",
        ]
    )
    return "\n".join(line for line in summary if line != "")


def build_work_instructions(source_root: Path) -> str:
    lines = [
        "- Stay inside the current `source_root`.",
        "- Use the filtered pages as hints, then inspect the OCR directly.",
        "- Confirm which candidate sections are true alphabetical, analytical, onomastic, scripture, concordance, or editorial-closure sections.",
        "- Build one helper request JSON for the current volume, following the shape used in `docs/examples/index_target_locator/*.json`.",
        "- The helper request should include entries for every index line whose target location needs material resolution.",
        "- Include `expected_manual` only if the runtime input explicitly provides manual validation data. Otherwise omit it.",
        "- Run: `python scripts/index_target_locator.py --input <helper_request_json> --output <helper_output_json> --pretty`.",
        "- Use helper output to support material anchors, not to replace editorial judgment.",
        "- Preserve helper evidence in `raw_json` whenever it materially affects `section_start_file`, `editorial_anchor_file`, `target_file_best`, or `target_file`.",
        "- Keep OCR literals.",
        "- Distinguish OCR file suffixes from editorial page numbers and cited references.",
        "- In `refs`, `ref_order` must be unique within each `entry_key` and should normally be sequential starting at 1 for that entry.",
        "- In `scripture_refs`, `ref_order` must be unique within each `entry_key` and should normally be sequential starting at 1 for that entry.",
        "- Do not emit duplicate `refs` or duplicate `scripture_refs` objects.",
        "- When OCR is ambiguous, lower confidence and keep the ambiguity explicit instead of normalizing it away.",
        "- Avoid empty `entries` whenever real line items can be recovered from OCR.",
        "- Do not invent placeholder entries just to avoid an empty list.",
        "- If `sections` is non-empty and `entries` must remain empty, set `coverage.entries_status` to `unrecoverable_ocr` or `no_line_items`, add a non-empty `coverage.entries_status_reason`, and include non-empty `coverage.evidence_files`.",
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
    helper_request_json: Path,
    helper_output_json: Path,
    output_file: Path,
) -> str:
    previous_status = "present" if previous_payload.exists() else "absent"
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
{json.dumps(filtered_pages, ensure_ascii=False, indent=2)}

PREVIOUS RESULT
- path: {previous_payload}
- status: {previous_status}
- source: {previous_result_source}
- output_checkpoint_status: {output_checkpoint_status}

HELPER PATHS
- helper_request_json: {helper_request_json}
- helper_output_json: {helper_output_json}

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
    ap.add_argument("--helper-request-json", type=Path, required=True, help="Helper request JSON path")
    ap.add_argument("--helper-output-json", type=Path, required=True, help="Helper output JSON path")
    ap.add_argument("--output-file", type=Path, required=True, help="Final payload JSON path")
    args = ap.parse_args()

    filtered_pages = read_json(args.filtered_pages_json)
    print(
        build_prompt(
            volume_id=args.volume,
            source_root=args.source_root,
            collection=args.collection,
            filtered_pages=filtered_pages,
            previous_payload=args.previous_payload,
            previous_result_source=args.previous_result_source,
            output_checkpoint_status=args.output_checkpoint_status,
            helper_request_json=args.helper_request_json,
            helper_output_json=args.helper_output_json,
            output_file=args.output_file,
        )
    )


if __name__ == "__main__":
    main()
