#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg066_alphabetical_payload.py
# Repairs PG066 alphabetical payload OCR line-break hyphen artifacts, refreshes
# page-map based material locators, writes helper/TODO checkpoints, and validates.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG066"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG066/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG066_alphabetical_indices.json"
HELPER_REQUEST_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG066_helper_request.json"
HELPER_OUTPUT_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG066_helper_output.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG066"
PAGE_MAP_PATH = INTERMEDIATE_DIR / "page_map.json"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
TERMINAL_WORD_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-+$")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return TERMINAL_WORD_HYPHEN_RE.sub(r"\1", merged)
        updated = merged


def clean_text_fields(payload: dict[str, Any]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)
    entry_fields = (
        "lemma_raw",
        "lemma_display",
        "lemma_norm",
        "lemma_sort",
        "entry_raw",
        "context_raw",
    )
    ref_fields = ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw")
    scripture_fields = ("ref_raw", "book_raw", "book_norm")
    for entry in payload.get("entries", []):
        for field in entry_fields:
            old = entry.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                entry[field] = new
                changed[f"entries.{field}"] += 1
        raw_json = entry.setdefault("raw_json", {})
        if any(field.startswith("entries.") for field in changed):
            raw_json.setdefault("rerun_repairs", [])
        if raw_json.get("rerun_repairs") is not None and any(
            LINEBREAK_HYPHEN_RE.search(str(entry.get(field) or "")) for field in entry_fields
        ):
            raw_json["rerun_repairs"].append("Checked for OCR line-break hyphen artifacts during PG066 rerun.")
    for ref in payload.get("refs", []):
        for field in ref_fields:
            old = ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"refs.{field}"] += 1
    for ref in payload.get("scripture_refs", []):
        for field in scripture_fields:
            old = ref.get(field)
            new = merge_linebreak_hyphens(old)
            if new != old:
                ref[field] = new
                changed[f"scripture_refs.{field}"] += 1
    return dict(changed)


def load_page_map() -> dict[int, str]:
    raw = load_json(PAGE_MAP_PATH)
    page_map: dict[int, str] = {}
    for key, value in raw.items():
        try:
            page = int(key)
        except ValueError:
            continue
        path = Path(value)
        if path.exists() and SOURCE_ROOT in path.parents:
            page_map[page] = str(path)
    return page_map


def refresh_page_map_locators(payload: dict[str, Any], page_map: dict[int, str]) -> dict[str, int]:
    changed: dict[str, int] = defaultdict(int)
    for entry in payload.get("entries", []):
        page = entry.get("inferred_printed_page")
        if entry.get("target_file_best") is None and isinstance(page, int) and page in page_map:
            entry["target_file_best"] = page_map[page]
            entry.setdefault("raw_json", {}).setdefault("locator_rerun", {})[
                "target_file_best_source"
            ] = "PG066 intermediate page_map.json by inferred_printed_page"
            changed["entries.target_file_best"] += 1
    for ref in payload.get("refs", []):
        page = ref.get("page_ref_int")
        if ref.get("target_file") is None and isinstance(page, int) and page in page_map:
            ref["target_file"] = page_map[page]
            ref.setdefault("raw_json", {}).setdefault("locator_rerun", {})[
                "target_file_source"
            ] = "PG066 intermediate page_map.json by page_ref_int"
            changed["refs.target_file"] += 1
    return dict(changed)


def renumber_and_dedupe_refs(payload: dict[str, Any]) -> dict[str, int]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in payload.get("refs", []):
        refs_by_entry[ref["entry_key"]].append(ref)

    removed = 0
    for refs in refs_by_entry.values():
        refs.sort(key=lambda item: item.get("ref_order") or 0)
        seen: set[tuple[Any, ...]] = set()
        unique: list[dict[str, Any]] = []
        for ref in refs:
            sig = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("page_ref_int"),
                ref.get("page_ref_col"),
                ref.get("line_ref_raw"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
                ref.get("target_file"),
            )
            if sig in seen:
                removed += 1
                continue
            seen.add(sig)
            unique.append(ref)
        for order, ref in enumerate(unique, start=1):
            ref["ref_order"] = order
        refs[:] = unique

    entry_order = {entry["entry_key"]: i for i, entry in enumerate(payload.get("entries", []))}
    ordered_refs: list[dict[str, Any]] = []
    for entry_key in sorted(refs_by_entry, key=lambda key: entry_order.get(key, 10**9)):
        ordered_refs.extend(sorted(refs_by_entry[entry_key], key=lambda item: item["ref_order"]))
    payload["refs"] = ordered_refs
    return {"refs.removed_duplicates": removed} if removed else {}


def helper_entries_for_unresolved(payload: dict[str, Any]) -> list[dict[str, Any]]:
    helper_entries: list[dict[str, Any]] = []
    for entry in payload.get("entries", []):
        page = entry.get("inferred_printed_page")
        if entry.get("target_file_best") is not None:
            continue
        if not isinstance(page, int):
            continue
        lemma = entry.get("lemma_raw") or entry.get("lemma_display") or ""
        query_names = [lemma] if lemma else []
        words = [word.strip(",.;:()[]") for word in lemma.split()]
        if len(words) > 3:
            query_names.append(" ".join(words[:4]))
        helper_entries.append(
            {
                "entry_id": entry["entry_key"].replace(":", "_").lower(),
                "entry_key": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": [q for q in dict.fromkeys(query_names) if q],
                "page_hints": [str(page)],
                "page_hint_ints": [page],
                "context_raw": (entry.get("entry_raw") or "")[:500],
            }
        )
    return helper_entries


def write_helper_request(payload: dict[str, Any]) -> None:
    dump_json(
        HELPER_REQUEST_PATH,
        {
            "volume_id": VOLUME_ID,
            "source_root": str(SOURCE_ROOT),
            "options": {
                "top_k": 5,
                "adjacency_window": 2,
                "notes": "PG066 rerun request for entries still lacking target_file_best after page-map refresh.",
            },
            "entries": helper_entries_for_unresolved(payload),
        },
    )


def run_helper() -> None:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--pretty",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def apply_helper_output(payload: dict[str, Any]) -> dict[str, int]:
    if not HELPER_OUTPUT_PATH.exists():
        return {}
    output = load_json(HELPER_OUTPUT_PATH)
    results = output.get("entries") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return {}
    entries_by_key = {entry["entry_key"]: entry for entry in payload.get("entries", [])}
    request_by_entry_id = {
        entry["entry_id"]: entry
        for entry in load_json(HELPER_REQUEST_PATH).get("entries", [])
        if isinstance(entry, dict) and entry.get("entry_id")
    }
    changed = 0
    for result in results:
        request_entry = request_by_entry_id.get(result.get("entry_id"))
        entry_key = request_entry.get("entry_key") if request_entry else result.get("entry_key")
        entry = entries_by_key.get(entry_key)
        if not entry or entry.get("target_file_best") is not None:
            continue
        best = result.get("best_candidate") or result.get("best") or result.get("top_candidate")
        candidates = result.get("candidates") or []
        if not best and candidates:
            best = candidates[0]
        if not isinstance(best, dict):
            continue
        target_file = best.get("file") or best.get("target_file")
        if not target_file:
            continue
        target_path = Path(target_file)
        if not target_path.is_absolute():
            target_path = PROJECT_ROOT / target_path
        if not target_path.exists() or SOURCE_ROOT not in target_path.parents:
            continue
        entry["target_file_best"] = str(target_path)
        entry.setdefault("raw_json", {}).setdefault("helper_rerun", {})["target_resolution"] = {
            "status": result.get("status"),
            "reason_summary": result.get("reason_summary"),
            "candidate_role": best.get("candidate_role"),
            "top_file": str(target_path),
            "probability": best.get("probability"),
        }
        changed += 1
    return {"entries.target_file_best_from_helper": changed} if changed else {}


def update_notes(payload: dict[str, Any], counts: dict[str, int]) -> None:
    payload["generated_at"] = now_iso()
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    volume_note = (
        "PG066 rerun repaired OCR line-break hyphen artifacts and refreshed unresolved "
        "material locators with the volume page map before import validation."
    )
    if volume_note not in volume_notes:
        volume_notes.append(volume_note)
    payload.setdefault("coverage", {})["entries_status"] = "complete"
    payload["coverage"]["entries_status_reason"] = (
        "Previous payload reused as checkpoint; rerun fixed validation-blocking line-break "
        "hyphen artifacts and refreshed target_file_best values where the PG066 page map "
        "provided a material locator."
    )
    evidence_files = payload["coverage"].setdefault("evidence_files", [])
    for suffix in (896, 897, 898, 899, 901, 902, 905, 906):
        matches = sorted(SOURCE_ROOT.glob(f"*-{suffix}.txt"))
        for match in matches:
            path = str(match)
            if path not in evidence_files:
                evidence_files.append(path)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation",
            "created_at": now_iso(),
            "message": (
                "Fixed PG066 import failure caused by OCR line-break hyphen artifacts; "
                "missing entry_key diagnostics were cascade errors after rejected entries."
            ),
            "changed_field_counts": counts,
            "helper_request": str(HELPER_REQUEST_PATH),
            "helper_output": str(HELPER_OUTPUT_PATH),
            "ocr_spot_checks": [
                str(SOURCE_ROOT / "da314370-9aaa-43c1-a5df-dfa21fc745b4-896.txt"),
                str(SOURCE_ROOT / "da314370-9aaa-43c1-a5df-dfa21fc745b4-897.txt"),
            ],
        }
    )


def update_todo(counts: dict[str, int]) -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG066 hyphen-artifact import failure.",
            "completed": [
                "Read current validation failure for line-break hyphen artifacts and missing entry-key cascade",
                "Checked OCR reader output for files 896 and 897",
                "Merged validation-blocking OCR line-break hyphen artifacts in payload text fields",
                "Refreshed null target_file_best values from intermediate page_map.json when possible",
                "Ran index_target_locator.py on remaining unresolved entries",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {counts}",
                "OCR file suffixes, printed pages, and cited references remain distinct.",
            ],
        },
    )


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    counts: dict[str, int] = {}
    for part in (
        clean_text_fields(payload),
        refresh_page_map_locators(payload, load_page_map()),
        renumber_and_dedupe_refs(payload),
    ):
        counts.update(part)
    write_helper_request(payload)
    run_helper()
    counts.update(apply_helper_output(payload))
    update_notes(payload, counts)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_todo(counts)
    print(json.dumps({"status": "ok", "volume_id": VOLUME_ID, "changed": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
