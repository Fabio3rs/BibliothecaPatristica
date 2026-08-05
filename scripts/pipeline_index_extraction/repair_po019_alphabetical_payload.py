#!/usr/bin/env python3
"""Repair PO019 alphabetical payload locators.

Usage:
  python scripts/pipeline_index_extraction/repair_po019_alphabetical_payload.py

The script patches the existing PO019 payload in place: it restores a few
OCR-confirmed continued index lines, adds missing refs for numeric INDEX RERUM
entries, and fills null ref target files from volume-local locator evidence.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PO019/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PO019_alphabetical_indices.json"
TODO_PATH = ROOT / "data/intermediate_payloads/PO019/todo.json"


MANUAL_ENTRY_RAW = {
    "po019_s3_0128": "Generatio duplex : naturalis et spiritualis, 207.",
    "po019_s3_0164": "Jesus absque peccato originali conceptus, 18.",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_locator_map(payload: dict) -> dict[int, tuple[str, float, str]]:
    votes: dict[int, Counter[str]] = defaultdict(Counter)
    for ref in payload["refs"]:
        page = ref.get("page_ref_int")
        target = ref.get("target_file")
        if isinstance(page, int) and target:
            votes[page][target] += 1

    # Direct logion starts are strong evidence for PO019 INDEX RERUM/LOCORUM
    # locators, whose numbers are logion ordinals rather than printed pages.
    logion_start: dict[int, str] = {}
    pattern = re.compile(r"^\s*(\d{1,3})\.\s+—", re.MULTILINE)
    for path in sorted(SOURCE_ROOT.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            number = int(match.group(1))
            logion_start.setdefault(number, str(path))

    locator_map: dict[int, tuple[str, float, str]] = {}
    for page, counter in votes.items():
        target, count = counter.most_common(1)[0]
        locator_map[page] = (target, 1.0 if count >= 2 else 0.92, "payload_consensus")
    for page, target in logion_start.items():
        locator_map.setdefault(page, (target, 0.95, "direct_logion_start"))
    return locator_map


def parse_numeric_refs(entry_raw: str) -> list[str]:
    if "," not in entry_raw:
        return []
    tail = entry_raw.split(",", 1)[1]
    refs: list[str] = []
    for match in re.finditer(r"\b\d{1,3}(?:bis|ter|quater|quinquies)?\b", tail):
        refs.append(match.group(0))
    return refs


def make_ref(entry: dict, ref_order: int, ref_raw: str, locator_map: dict[int, tuple[str, float, str]]) -> dict:
    page_int = int(re.match(r"\d+", ref_raw).group(0))
    target, probability, source = locator_map.get(page_int, (None, None, "unresolved_after_consensus_and_logion_search"))
    return {
        "entry_key": entry["entry_key"],
        "ref_order": ref_order,
        "ref_kind": "target_locator" if target else "unresolved",
        "ref_raw": ref_raw,
        "page_ref_raw": ref_raw,
        "page_ref_int": page_int,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": None,
        "range_end_raw": None,
        "target_file": target,
        "target_file_probability": probability,
        "section_start_file": entry["section_start_file"],
        "editorial_anchor_file": entry["editorial_anchor_file"],
        "confidence": 0.88 if target else 0.62,
        "raw_json": {
            "source_file": entry["raw_json"].get("source_file"),
            "section_kind": entry["raw_json"].get("section_kind"),
            "repair": "added_missing_numeric_ref_for_po019_rerun",
            "locator_source": source,
        },
    }


def helper_probability_by_entry(payload: dict) -> dict[str, tuple[str, float]]:
    out: dict[str, tuple[str, float]] = {}
    for entry in payload["entries"]:
        helper = entry.get("raw_json", {}).get("helper") or {}
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            out[entry["entry_key"]] = (best["file"], best.get("probability"))
    return out


def repair_payload(payload: dict) -> dict:
    entries = {entry["entry_key"]: entry for entry in payload["entries"]}
    refs_by_entry: dict[str, list[dict]] = defaultdict(list)
    for ref in payload["refs"]:
        refs_by_entry[ref["entry_key"]].append(ref)

    for entry_key, entry_raw in MANUAL_ENTRY_RAW.items():
        entry = entries[entry_key]
        entry["entry_raw"] = entry_raw
        if entry.get("context_raw") and entry["context_raw"].rstrip(",.") != entry_raw.rstrip("."):
            entry["context_raw"] = entry_raw
        entry.setdefault("raw_json", {})["repair_note"] = "OCR continuation verified in ced07bbc-e86b-4202-b8d2-87016ccf3630-633.txt."

    locator_map = build_locator_map(payload)
    added_refs: list[dict] = []
    for entry in payload["entries"]:
        if refs_by_entry.get(entry["entry_key"]):
            continue
        if entry.get("section_key") != "PO019-s3" or entry.get("entry_kind") == "cross_reference":
            continue
        raw_refs = parse_numeric_refs(entry.get("entry_raw") or "")
        if not raw_refs:
            continue
        for order, ref_raw in enumerate(raw_refs, start=1):
            added_refs.append(make_ref(entry, order, ref_raw, locator_map))

    payload["refs"].extend(added_refs)
    for ref in added_refs:
        refs_by_entry[ref["entry_key"]].append(ref)

    helper_best = helper_probability_by_entry(payload)
    filled_existing_refs = 0
    for ref in payload["refs"]:
        if ref.get("target_file"):
            continue
        page = ref.get("page_ref_int")
        if isinstance(page, int) and page in locator_map:
            target, probability, source = locator_map[page]
        else:
            entry = entries[ref["entry_key"]]
            target = entry.get("target_file_best")
            probability = helper_best.get(ref["entry_key"], (None, 0.78))[1] or 0.78
            source = "entry_target_file_best_fallback"
        if not target:
            ref.setdefault("raw_json", {})["repair_note"] = "No locator found after payload consensus and direct logion-start search."
            continue
        ref["target_file"] = target
        ref["target_file_probability"] = probability
        ref["confidence"] = max(ref.get("confidence") or 0.0, 0.82)
        ref.setdefault("raw_json", {})["repair"] = "filled_null_target_file_for_po019_rerun"
        ref["raw_json"]["locator_source"] = source
        filled_existing_refs += 1

    for entry in payload["entries"]:
        if entry.get("target_file_best"):
            continue
        entry_refs = [ref for ref in refs_by_entry.get(entry["entry_key"], []) if ref.get("target_file")]
        if not entry_refs:
            if entry.get("entry_kind") == "cross_reference":
                entry.setdefault("raw_json", {})["target_file_best_note"] = "Left null because this is a bare cross-reference without material locator."
            continue
        entry["target_file_best"] = entry_refs[0]["target_file"]
        entry["confidence"] = max(entry.get("confidence") or 0.0, 0.86)
        entry.setdefault("raw_json", {})["target_file_best_note"] = "Filled from first recovered material ref during PO019 rerun."

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload.setdefault("notes", []).append(
        {
            "type": "repair",
            "message": (
                "PO019 rerun repaired OCR-confirmed continued INDEX RERUM lines, "
                f"added {len(added_refs)} missing numeric refs, and filled {filled_existing_refs} null ref target_file values."
            ),
        }
    )
    return payload


def update_todo() -> None:
    todo = load_json(TODO_PATH) if TODO_PATH.exists() else {"volume_id": "PO019"}
    todo.update(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "current_focus": "Final validation after PO019 locator repair",
            "completed": list(
                dict.fromkeys(
                    todo.get("completed", [])
                    + [
                        "Repaired missing numeric refs for OCR-confirmed INDEX RERUM continuations",
                        "Filled null ref target_file values using payload consensus and direct logion-start search",
                    ]
                )
            ),
            "pending": ["Run import_alphabetical_index_json.py --validate-only"],
            "blocked": todo.get("blocked", []),
            "notes": list(
                dict.fromkeys(
                    todo.get("notes", [])
                    + [
                        "Bare Vide cross-references intentionally keep target_file_best null.",
                        "INDEX RERUM numeric locators are logion ordinals, not printed pages.",
                    ]
                )
            ),
        }
    )
    dump_json(TODO_PATH, todo)


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    repaired = repair_payload(payload)
    dump_json(PAYLOAD_PATH, repaired)
    update_todo()


if __name__ == "__main__":
    main()
