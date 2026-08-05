#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg097_hyphen_artifacts.py
# Repairs PG097 alphabetical payload entries split by OCR line-break hyphenation, remaps refs, and validates the final JSON.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG097"
SOURCE_ROOT = PROJECT_ROOT / "teste/PG097/text"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG097_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG097"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}0-9])")
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")

ENTRY_TEXT_FIELDS = (
    "lemma_raw",
    "lemma_display",
    "lemma_norm",
    "lemma_sort",
    "entry_raw",
    "context_raw",
)
REF_TEXT_FIELDS = (
    "ref_raw",
    "page_ref_raw",
    "line_ref_raw",
    "range_start_raw",
    "range_end_raw",
)
EVIDENCE_FILES = [
    SOURCE_ROOT / "e0ed51a0-ecce-4ee5-a89b-2998ac2b3e70-980.txt",
    SOURCE_ROOT / "e0ed51a0-ecce-4ee5-a89b-2998ac2b3e70-982.txt",
    SOURCE_ROOT / "e0ed51a0-ecce-4ee5-a89b-2998ac2b3e70-993.txt",
    SOURCE_ROOT / "e0ed51a0-ecce-4ee5-a89b-2998ac2b3e70-995.txt",
]


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


def merge_text(left: Any, right: Any) -> Any:
    if not isinstance(left, str):
        return right
    if not isinstance(right, str):
        return left
    return LINEBREAK_HYPHEN_RE.sub(r"\1\2", f"{left} {right}").strip()


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return merged
        updated = merged


def entry_has_terminal_hyphen(entry: dict[str, Any]) -> bool:
    return bool(TRAILING_WORD_HYPHEN_RE.search(str(entry.get("entry_raw") or "")))


def same_ocr_source(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("section_key") == right.get("section_key")
        and left.get("raw_json", {}).get("source_file") == right.get("raw_json", {}).get("source_file")
    )


def compact_norm(value: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+", " ", value.casefold()).strip()


def repair_nested_strings(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changes = 0
        for key, child in list(value.items()):
            repaired, child_changes = repair_nested_strings(child)
            value[key] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, list):
        changes = 0
        for idx, child in enumerate(value):
            repaired, child_changes = repair_nested_strings(child)
            value[idx] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, str):
        repaired = merge_linebreak_hyphens(value)
        return repaired, int(repaired != value)
    return value, 0


def merge_split_entries(payload: dict[str, Any], changed: dict[str, int]) -> dict[str, str]:
    entries = payload["entries"]
    old_to_keep: dict[str, str] = {}
    removed_keys: set[str] = set()
    repaired_entries: list[dict[str, Any]] = []
    i = 0

    while i < len(entries):
        current = entries[i]
        merged_from = [current["entry_key"]]
        nested_changes = 0

        while entry_has_terminal_hyphen(current):
            if i + 1 >= len(entries) or not same_ocr_source(current, entries[i + 1]):
                raise SystemExit(f"Cannot safely merge terminal hyphen entry {current['entry_key']}")
            nxt = entries[i + 1]
            for field in ENTRY_TEXT_FIELDS:
                right = nxt.get(field)
                if right is None and field.startswith("lemma_"):
                    right = nxt.get("entry_raw")
                current[field] = merge_text(current.get(field), right)
            if current.get("inferred_printed_page") is None:
                current["inferred_printed_page"] = nxt.get("inferred_printed_page")
            if not current.get("target_file_best") and nxt.get("target_file_best"):
                current["target_file_best"] = nxt.get("target_file_best")
            current["confidence"] = min(float(current.get("confidence") or 0.8), float(nxt.get("confidence") or 0.8))
            current.setdefault("raw_json", {})
            current["raw_json"]["pg097_rerun_linebreak_hyphen_merge"] = {
                "merged_entry_keys": merged_from + [nxt["entry_key"]],
                "reason": (
                    "Consecutive OCR-derived entries from the same source file were one logical "
                    "index entry split at a line-break hyphen."
                ),
                "source_file": current.get("raw_json", {}).get("source_file"),
            }
            if isinstance(current.get("raw_json"), dict):
                _, child_changes = repair_nested_strings(current["raw_json"])
                nested_changes += child_changes
            old_to_keep[nxt["entry_key"]] = current["entry_key"]
            removed_keys.add(nxt["entry_key"])
            merged_from.append(nxt["entry_key"])
            changed["entries_merged"] += 1
            i += 1

        for field in ENTRY_TEXT_FIELDS:
            repaired = merge_linebreak_hyphens(current.get(field))
            if repaired != current.get(field):
                current[field] = repaired
                changed[f"entries.{field}"] += 1
        if isinstance(current.get("raw_json"), dict):
            _, child_changes = repair_nested_strings(current["raw_json"])
            nested_changes += child_changes
            if current["raw_json"].get("fragment") != current.get("entry_raw"):
                current["raw_json"]["fragment"] = current.get("entry_raw")
                changed["entries.raw_json_fragment_synced"] += 1
        if nested_changes:
            changed["entries.raw_json_nested_strings"] += nested_changes
        repaired_entries.append(current)
        i += 1

    payload["entries"] = [entry for entry in repaired_entries if entry["entry_key"] not in removed_keys]
    return old_to_keep


def repair_refs(payload: dict[str, Any], old_to_keep: dict[str, str], changed: dict[str, int]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in payload["refs"]:
        old_key = ref.get("entry_key")
        if old_key in old_to_keep:
            ref["entry_key"] = old_to_keep[str(old_key)]
            ref.setdefault("raw_json", {})["pg097_rerun_entry_key_remapped_from"] = old_key
            changed["refs.entry_key_remapped"] += 1
        for field in REF_TEXT_FIELDS:
            repaired = merge_linebreak_hyphens(ref.get(field))
            if repaired != ref.get(field):
                ref[field] = repaired
                changed[f"refs.{field}"] += 1
        grouped[str(ref["entry_key"])].append(ref)

    for refs in grouped.values():
        refs.sort(key=lambda item: (int(item.get("ref_order") or 0), str(item.get("ref_raw") or "")))
        for order, ref in enumerate(refs, 1):
            if ref.get("ref_order") != order:
                changed["refs.ref_order_renumbered"] += 1
            ref["ref_order"] = order


def repair_nodes(payload: dict[str, Any], changed: dict[str, int]) -> None:
    replacements = {
        "PG097:node:ordo_rerum:heading:006": (
            "COMMENTARII IN S. GREGORII NAZIANZENI ORATIONES XIX (hic memorantur tantum). 1466"
        ),
        "PG097:node:ordo_rerum:heading:007": "THEODORUS ABUCARA, CARUM EPISCOPUSNotitia. 1476",
    }
    for node in payload["nodes"]:
        node_key = node.get("node_key")
        if node_key in replacements:
            label = replacements[str(node_key)]
            node["label_raw"] = label
            node["label_norm"] = label.rstrip(".")
            node["label_sort"] = compact_norm(label)
            node.setdefault("raw_json", {})["pg097_rerun_linebreak_hyphen_repair"] = {
                "source_file": str(SOURCE_ROOT / "e0ed51a0-ecce-4ee5-a89b-2998ac2b3e70-995.txt"),
                "reason": "Verified against cleaned OCR reader output for the ORDO RERUM page.",
            }
            changed["nodes.repaired"] += 1


def assert_relationships(payload: dict[str, Any]) -> None:
    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing = [(idx, ref["entry_key"]) for idx, ref in enumerate(payload["refs"]) if ref["entry_key"] not in entry_keys]
    if missing:
        raise SystemExit(f"refs still point to missing entries: {missing[:20]}")


def assert_no_validator_hyphen_artifacts(payload: dict[str, Any]) -> None:
    residual: list[str] = []
    scan_entry_fields = set(ENTRY_TEXT_FIELDS)
    scan_ref_fields = set(REF_TEXT_FIELDS)

    def scan(value: Any, path: str, key: str | None = None) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                scan(child, f"{path}.{key}" if path else str(key), str(key))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                scan(child, f"{path}[{idx}]", key)
        elif isinstance(value, str) and (
            LINEBREAK_HYPHEN_RE.search(value) or TRAILING_WORD_HYPHEN_RE.search(value)
        ) and (key in scan_entry_fields or key in scan_ref_fields or key in {"label_raw", "label_norm", "fragment"}):
            residual.append(path)

    scan(payload, "")
    if residual:
        raise SystemExit(f"line-break hyphen artifacts remain: {residual[:50]}")


def update_notes(payload: dict[str, Any], changed: dict[str, int], old_to_keep: dict[str, str]) -> None:
    payload["generated_at"] = now_iso()
    volume_note = (
        "PG097 rerun repaired OCR line-break hyphen splits in the analytical indices and ORDO RERUM; "
        "OCR file suffixes, printed pages, and cited references remain separate."
    )
    notes = payload.setdefault("volume", {}).setdefault("notes", [])
    if volume_note not in notes:
        notes.append(volume_note)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "Fixed PG097 import failure caused by OCR line-break hyphen artifacts and remapped "
                "refs from merged continuation entries."
            ),
            "changed_field_counts": dict(changed),
            "merged_continuation_entry_count": len(old_to_keep),
            "ocr_spot_checks": [str(path) for path in EVIDENCE_FILES],
        }
    )


def update_intermediates(payload: dict[str, Any], changed: dict[str, int]) -> None:
    for name in ("sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        dump_json(INTERMEDIATE_DIR / f"{name}.json", payload[name])
    dump_json(INTERMEDIATE_DIR / "payload.json", payload)
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG097 payload repaired and validated after line-break hyphen import failure.",
            "completed": [
                "Read alphabetical-index skill contract and repository extraction docs",
                "Reproduced import_alphabetical_index_json.py validation failure",
                "Inspected OCR reader output for index files 980 and 995",
                "Merged consecutive entries split by OCR line-break hyphenation",
                "Remapped refs from removed continuation entries and renumbered ref_order per entry",
                "Repaired two ORDO RERUM nodes verified against OCR file 995",
                "Validated final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Changed field counts: {dict(changed)}",
                "No scripture_refs are present for PG097.",
                "Helper evidence and existing target_file locators were preserved unless refs were remapped from a merged continuation entry.",
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
            "--print-summary",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed: dict[str, int] = defaultdict(int)
    old_to_keep = merge_split_entries(payload, changed)
    repair_refs(payload, old_to_keep, changed)
    repair_nodes(payload, changed)
    assert_relationships(payload)
    assert_no_validator_hyphen_artifacts(payload)
    update_notes(payload, changed, old_to_keep)
    dump_json(PAYLOAD_PATH, payload)
    validate_payload()
    update_intermediates(payload, changed)
    print(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "merged_entries": len(old_to_keep),
                "changed": dict(changed),
                "written_file": str(PAYLOAD_PATH),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
