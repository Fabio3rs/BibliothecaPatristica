#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg111_alphabetical_payload.py
# Rebuild PG111 refs and target locators from the checkpoint payload, repair the
# verified line-break hyphen artifact, refresh helper input/intermediates, and
# write the canonical alphabetical payload.

from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG111"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
PAYLOAD_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

EXPLICIT_REF_RE = re.compile(r"(?<![A-Za-z])(?P<prefix>[bhn])?\s*(?P<page>\d{1,3})(?=[,.;)])", re.IGNORECASE)
IBID_RE = re.compile(r"\bibid\.", re.IGNORECASE)
HEADER_TEXT_RE = re.compile(r">([^<]{1,180})<")
HEADER_PAGE_PAIR_RE = re.compile(r"\b(\d{1,4})\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_page_map(source_root: Path) -> dict[int, list[str]]:
    page_map: dict[int, list[str]] = defaultdict(list)
    for file_path in sorted(source_root.glob("*.txt")):
        text = file_path.read_text(encoding="utf-8")
        header_texts = HEADER_TEXT_RE.findall(text[:2500])[:8]
        pages: set[int] = set()
        for header in header_texts:
            for match in HEADER_PAGE_PAIR_RE.findall(header):
                page = int(match)
                if 1 <= page <= 1300:
                    pages.add(page)
        for page in sorted(pages):
            page_map[page].append(str(file_path))
    return page_map


def pick_target_file(page_map: dict[int, list[str]], page: int | None) -> tuple[str | None, float | None, dict | None]:
    if page is None:
        return None, None, None
    candidates = page_map.get(page, [])
    if len(candidates) == 1:
        return candidates[0], 0.99, {"method": "header_page_map_exact", "matched_page": page}
    if len(candidates) > 1:
        return candidates[0], 0.6, {
            "method": "header_page_map_ambiguous",
            "matched_page": page,
            "candidates": candidates,
        }
    return None, None, None


def parse_entry_refs(entry_raw: str) -> list[dict]:
    refs: list[dict] = []
    last_explicit: tuple[str, int] | None = None
    occupied: set[tuple[str, int | None]] = set()
    index = 0
    while index < len(entry_raw):
        explicit = EXPLICIT_REF_RE.search(entry_raw, index)
        ibid = IBID_RE.search(entry_raw, index)
        if explicit and (not ibid or explicit.start() < ibid.start()):
            token = explicit.group(0).strip()
            page = int(explicit.group("page"))
            index = explicit.end()
            if page < 10:
                continue
            prefix = (explicit.group("prefix") or "").lower()
            if prefix:
                token = f"{prefix} {page}"
            key = (token.casefold(), page)
            if key in occupied:
                last_explicit = (token, page)
                continue
            occupied.add(key)
            refs.append({
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": page,
                "page_token_kind": "explicit",
            })
            last_explicit = (token, page)
            continue
        if ibid:
            index = ibid.end()
            if last_explicit is None:
                continue
            token = "ibid."
            page = last_explicit[1]
            key = (token, page)
            if key in occupied:
                continue
            occupied.add(key)
            refs.append({
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": page,
                "page_token_kind": "ibid",
                "inherited_from": last_explicit[0],
            })
            continue
        break
    return refs


def load_helper_map() -> dict[str, dict]:
    if not HELPER_OUTPUT_PATH.exists():
        return {}
    helper = load_json(HELPER_OUTPUT_PATH)
    return {item["entry_id"]: item for item in helper.get("entries", [])}


def entry_to_helper_id(entry_key: str) -> str:
    return entry_key.replace(":", "_")


def build_helper_request(payload: dict) -> dict:
    helper_entries = []
    refs_by_entry: dict[str, list[dict]] = defaultdict(list)
    for ref_obj in payload["refs"]:
        refs_by_entry[ref_obj["entry_key"]].append(ref_obj)
    for entry in payload["entries"]:
        page_hints = []
        for ref_obj in refs_by_entry.get(entry["entry_key"], []):
            page = ref_obj.get("page_ref_int")
            if isinstance(page, int) and page not in page_hints:
                page_hints.append(page)
            if len(page_hints) >= 3:
                break
        if not page_hints:
            continue
        helper_entries.append({
            "entry_id": entry_to_helper_id(entry["entry_key"]),
            "lemma_raw": entry.get("lemma_raw"),
            "query_names": [entry.get("lemma_raw"), entry.get("lemma_display")],
            "page_hints": [str(page) for page in page_hints],
            "page_hint_ints": page_hints,
            "context_raw": (entry.get("entry_raw") or "")[:400],
        })
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def rebuild_payload() -> dict:
    payload = load_json(PAYLOAD_PATH)
    payload["generated_at"] = utc_now()
    page_map = build_page_map(SOURCE_ROOT)
    helper_map = load_helper_map()

    payload["entries"][16]["entry_raw"] = payload["entries"][16]["entry_raw"].replace("maledic- tionem", "maledictionem")
    payload["entries"][16]["raw_json"]["verified_hyphen_repair"] = {
        "file": str(SOURCE_ROOT / "204929f2-50ce-4b06-82a4-5605e40815e6-607.txt"),
        "reason": "Merged the OCR line-break continuation 'maledic-' + 'tionem' confirmed in the source XML.",
    }

    new_refs: list[dict] = []
    refs_by_entry: dict[str, list[dict]] = defaultdict(list)

    for entry in payload["entries"]:
        parsed_refs = parse_entry_refs(entry["entry_raw"])
        section_file = entry.get("section_start_file")
        anchor_file = entry.get("editorial_anchor_file")
        for order, parsed in enumerate(parsed_refs, start=1):
            target_file, probability, target_meta = pick_target_file(page_map, parsed["page_ref_int"])
            ref_obj = {
                "entry_key": entry["entry_key"],
                "ref_order": order,
                "ref_kind": "editorial_page",
                "ref_raw": parsed["ref_raw"],
                "page_ref_raw": parsed["page_ref_raw"],
                "page_ref_int": parsed["page_ref_int"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": probability,
                "section_start_file": section_file,
                "editorial_anchor_file": anchor_file,
                "confidence": 0.95 if parsed["page_token_kind"] == "explicit" else 0.91,
                "raw_json": {
                    "page_token_kind": parsed["page_token_kind"],
                },
            }
            if "inherited_from" in parsed:
                ref_obj["raw_json"]["inherited_from"] = parsed["inherited_from"]
            if target_meta is not None:
                ref_obj["raw_json"]["target_resolution"] = target_meta
            helper_entry = helper_map.get(entry_to_helper_id(entry["entry_key"]))
            if helper_entry and helper_entry.get("best_candidate"):
                best = helper_entry["best_candidate"]
                ref_obj["raw_json"]["helper_locator"] = {
                    "status": helper_entry.get("status"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                    "top_candidates": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "evidence_kinds": [item.get("kind") for item in cand.get("evidence", [])[:4]],
                        }
                        for cand in helper_entry.get("candidates", [])[:3]
                    ],
                }
            refs_by_entry[entry["entry_key"]].append(ref_obj)
            new_refs.append(ref_obj)

        entry_refs = refs_by_entry[entry["entry_key"]]
        first_explicit = next((ref_obj for ref_obj in entry_refs if ref_obj["ref_raw"] != "ibid."), None)
        entry["inferred_printed_page"] = first_explicit["page_ref_int"] if first_explicit else None
        entry_target = first_explicit["target_file"] if first_explicit and first_explicit["target_file"] else None
        if entry_target is None:
            helper_entry = helper_map.get(entry_to_helper_id(entry["entry_key"]))
            if helper_entry and helper_entry.get("best_candidate"):
                entry_target = helper_entry["best_candidate"].get("file")
        entry["target_file_best"] = entry_target
        entry["confidence"] = max(entry.get("confidence") or 0.7, 0.84 if entry_refs else 0.72)
        entry["raw_json"]["body_target_status"] = "resolved_by_page_map" if entry_target else "partially_resolved"
        if entry_target:
            entry["raw_json"]["target_file_best_reason"] = "Resolved from the first explicit page reference against the PG111 header page map."
        helper_entry = helper_map.get(entry_to_helper_id(entry["entry_key"]))
        if helper_entry and helper_entry.get("best_candidate"):
            best = helper_entry["best_candidate"]
            entry["raw_json"]["helper_locator"] = {
                "status": helper_entry.get("status"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "evidence_kinds": [item.get("kind") for item in cand.get("evidence", [])[:4]],
                    }
                    for cand in helper_entry.get("candidates", [])[:3]
                ],
            }

    payload["refs"] = new_refs
    payload["coverage"]["entries_status_reason"] = (
        "Recovered logical entries from the verified PG111 index sections, repaired the validation-blocking hyphen artifact, "
        "rebuilt material refs from OCR-backed index lines, and resolved target files whenever the volume page map gave a direct match."
    )
    payload["notes"] = [
        note
        for note in payload.get("notes", [])
        if "Current payload is conservative" not in note
    ]
    payload["notes"].append(
        "PG111 rerun repaired the verified OCR line-break hyphen artifact in Constantinus and rebuilt refs directly from the OCR-backed entry text."
    )
    payload["notes"].append(
        "Target files now use the PG111 header page map when a cited editorial page matches a unique OCR file; unresolved ambiguity remains explicit in raw_json."
    )
    return payload


def write_intermediates(payload: dict) -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": utc_now(),
            "current_focus": "Validate rebuilt PG111 alphabetical payload after OCR-backed ref and target refresh.",
            "completed": [
                "Re-read the validation failure and the offending Constantinus entry against the OCR XML",
                "Merged the verified line-break hyphen artifact in Constantinus",
                "Rebuilt material refs for the 22 extracted PG111 entries from the OCR-backed entry text",
                "Refreshed target_file_best and refs[*].target_file from the PG111 header page map",
                "Rebuilt helper input for entry-level locator checks",
            ],
            "pending": [
                "Run index_target_locator.py for the refreshed helper request",
                "Run import_alphabetical_index_json.py --validate-only",
            ],
            "blocked": [],
            "notes": [
                "The previous missing entry_key error was treated as a cascade from the invalid hyphenated entry/ref layout.",
                "Section structure was preserved because the OCR still supports the existing four-section checkpoint.",
            ],
        },
    )


def main() -> None:
    payload = rebuild_payload()
    helper_request = build_helper_request(payload)
    write_json(HELPER_REQUEST_PATH, helper_request)
    write_intermediates(payload)
    write_json(PAYLOAD_PATH, payload)


if __name__ == "__main__":
    main()
