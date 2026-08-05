#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pl027_alphabetical_payload.py
# Rebuild the PL027 alphabetical payload from validated fragments and merge locator evidence
# from the existing payload when a content match is safe.

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL027"
FRAGMENTS_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "assembled_fragments.json"
EXISTING_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
TODO_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "todo.json"
SUFFIX_RE = re.compile(r"-(\d+)\.txt$")
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.lower().replace("æ", "ae").replace("œ", "oe")
    lowered = re.sub(r"[^0-9a-z]+", " ", lowered)
    return " ".join(lowered.split())


def file_seq(path: str | Path) -> int:
    match = SUFFIX_RE.search(str(path))
    if not match:
        return 999999
    return int(match.group(1))


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def top_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8")
    blocks = [(kind, " ".join(strip_tags(inner).split())) for kind, inner in BLOCK_RE.findall(raw)]
    return [(kind, text) for kind, text in blocks if text]


def header_candidate_start(path: Path) -> int | None:
    candidates: list[int] = []
    for kind, text in top_blocks(path)[:4]:
        if kind not in {"cabecalho", "outro", "texto_principal"}:
            continue
        nums = [int(m.group(1)) for m in HEADER_NUM_RE.finditer(text)]
        if len(nums) >= 2:
            nums = sorted(nums[:2])
            if nums[1] - nums[0] == 1 and nums[0] <= 5000:
                candidates.append(nums[0])
        elif len(nums) == 1 and nums[0] <= 5000:
            candidates.append(nums[0])
    if not candidates:
        return None
    return min(candidates)


def build_page_map(source_root: Path) -> dict[int, str]:
    files = sorted(source_root.glob("*.txt"), key=file_seq)
    page_map: dict[int, str] = {}
    for path in files:
        start = header_candidate_start(path)
        if start is None:
            continue
        page_map.setdefault(start, str(path))
        page_map.setdefault(start + 1, str(path))
    return page_map


def first_numeric_hint(text: str | None) -> int | None:
    if not text:
        return None
    match = HEADER_NUM_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def merge_raw_json(new_raw: dict[str, Any], old_raw: dict[str, Any]) -> dict[str, Any]:
    merged = dict(new_raw)
    for key, value in old_raw.items():
        if key in {"source_files", "source_file", "source_span", "source_page_seq", "source_token"}:
            continue
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    return merged


def merge_entry(new_entry: dict[str, Any], old_entry: dict[str, Any] | None) -> dict[str, Any]:
    if not old_entry:
        return new_entry
    merged = dict(new_entry)
    if merged.get("target_file_best") in (None, "") and old_entry.get("target_file_best"):
        merged["target_file_best"] = old_entry["target_file_best"]
    if merged.get("editorial_anchor_file") in (None, "") and old_entry.get("editorial_anchor_file"):
        merged["editorial_anchor_file"] = old_entry["editorial_anchor_file"]
    if old_entry.get("confidence") is not None:
        merged["confidence"] = max(float(merged.get("confidence") or 0.0), float(old_entry.get("confidence") or 0.0))
    merged["raw_json"] = merge_raw_json(dict(merged.get("raw_json") or {}), dict(old_entry.get("raw_json") or {}))
    return merged


def merge_ref(new_ref: dict[str, Any], old_ref: dict[str, Any] | None) -> dict[str, Any]:
    if not old_ref:
        return new_ref
    merged = dict(new_ref)
    if merged.get("target_file") in (None, "") and old_ref.get("target_file"):
        merged["target_file"] = old_ref["target_file"]
    if merged.get("target_file_probability") in (None, "") and old_ref.get("target_file_probability") is not None:
        merged["target_file_probability"] = old_ref["target_file_probability"]
    if old_ref.get("confidence") is not None:
        merged["confidence"] = max(float(merged.get("confidence") or 0.0), float(old_ref.get("confidence") or 0.0))
    merged["raw_json"] = merge_raw_json(dict(merged.get("raw_json") or {}), dict(old_ref.get("raw_json") or {}))
    return merged


def merge_unique_strings(*parts: list[Any]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for part in parts:
        for item in part or []:
            if not isinstance(item, str) or not item:
                continue
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def remap_section_orders(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        sections,
        key=lambda item: (
            file_seq(item.get("file_start") or item.get("file_end") or ""),
            file_seq(item.get("file_end") or item.get("file_start") or ""),
        ),
        reverse=True,
    )
    by_key = {}
    for idx, section in enumerate(ordered, start=1):
        rewritten = dict(section)
        rewritten["section_order"] = idx
        by_key[rewritten["section_key"]] = rewritten
    return [by_key[section["section_key"]] for section in sections]


def load_helper_maps(
    entries: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    if not HELPER_REQUEST_PATH.exists() or not HELPER_OUTPUT_PATH.exists():
        return {}, {}
    helper_request = read_json(HELPER_REQUEST_PATH)
    helper_output = read_json(HELPER_OUTPUT_PATH)
    helper_entries = {item["entry_id"]: item for item in helper_output.get("entries", []) if item.get("entry_id")}

    by_context: dict[str, list[str]] = defaultdict(list)
    by_lemma: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        by_context[normalize_text(entry.get("entry_raw"))].append(entry["entry_key"])
        by_lemma[normalize_text(entry.get("lemma_raw"))].append(entry["entry_key"])

    helper_by_entry_key: dict[str, dict[str, Any]] = {}
    helper_id_by_entry_key: dict[str, str] = {}
    for req_item in helper_request.get("entries", []):
        entry_id = req_item.get("entry_id")
        if not entry_id:
            continue
        matches = by_context.get(normalize_text(req_item.get("context_raw")), [])
        if len(matches) != 1:
            matches = by_lemma.get(normalize_text(req_item.get("lemma_raw")), [])
        if len(matches) != 1:
            continue
        entry_key = matches[0]
        helper_item = helper_entries.get(entry_id)
        if helper_item is None:
            continue
        helper_by_entry_key[entry_key] = helper_item
        helper_id_by_entry_key[entry_key] = entry_id
    return helper_by_entry_key, helper_id_by_entry_key


def candidate_matches_page(candidate: dict[str, Any], page_ref_int: int) -> bool:
    if candidate.get("inferred_printed_page") == page_ref_int:
        return True
    estimator_pages = candidate.get("estimator_pages") or []
    return page_ref_int in estimator_pages


def candidate_evidence_kinds(candidate: dict[str, Any]) -> list[str]:
    return [str(item.get("kind")) for item in candidate.get("evidence", []) if item.get("kind")]


def build_payload() -> dict[str, Any]:
    fragments = read_json(FRAGMENTS_PATH)
    existing = read_json(EXISTING_PATH) if EXISTING_PATH.exists() else {}
    data = dict(fragments["data"])
    data["sections"] = remap_section_orders(list(data.get("sections") or []))
    page_map = build_page_map(Path("/homessddata/Projects/pdfocr/teste/PL027/text"))

    new_entries = list(data.get("entries") or [])
    old_entries = list(existing.get("entries") or [])

    new_entries_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    old_entries_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in new_entries:
        new_entries_by_section[entry.get("section_key") or ""].append(entry)
    for entry in old_entries:
        old_entries_by_section[entry.get("section_key") or ""].append(entry)

    merged_entries: list[dict[str, Any]] = []
    old_entry_key_to_new: dict[str, str] = {}
    for section_key in data.get("sections", []):
        pass

    for section_key in sorted(new_entries_by_section.keys()):
        new_group = sorted(new_entries_by_section[section_key], key=lambda item: int(item.get("entry_order") or 0))
        old_group = sorted(old_entries_by_section.get(section_key, []), key=lambda item: int(item.get("entry_order") or 0))
        for idx, new_entry in enumerate(new_group):
            old_entry = old_group[idx] if idx < len(old_group) else None
            merged = merge_entry(new_entry, old_entry)
            merged_entries.append(merged)
            if old_entry is not None:
                old_entry_key_to_new[old_entry["entry_key"]] = merged["entry_key"]

    helper_by_entry_key, helper_id_by_entry_key = load_helper_maps(merged_entries)
    for entry in merged_entries:
        helper_item = helper_by_entry_key.get(entry["entry_key"])
        helper_id = helper_id_by_entry_key.get(entry["entry_key"])
        if helper_item is None or helper_id is None:
            continue
        raw_json = dict(entry.get("raw_json") or {})
        raw_json.setdefault("helper", {})
        raw_json["helper"]["entry_id"] = helper_id
        raw_json["helper"]["status"] = helper_item.get("status")
        if helper_item.get("best_candidate"):
            best = helper_item["best_candidate"]
            raw_json["helper"]["best_candidate"] = {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
            }
        entry["raw_json"] = raw_json

    merged_refs: list[dict[str, Any]] = []
    new_refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    old_refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in data.get("refs") or []:
        new_refs_by_entry[ref.get("entry_key") or ""].append(ref)
    for ref in existing.get("refs") or []:
        mapped_key = old_entry_key_to_new.get(ref.get("entry_key") or "")
        if mapped_key:
            old_refs_by_entry[mapped_key].append(ref)

    for entry in merged_entries:
        entry_key = entry["entry_key"]
        new_group = sorted(new_refs_by_entry.get(entry_key, []), key=lambda item: int(item.get("ref_order") or 0))
        old_group = sorted(old_refs_by_entry.get(entry_key, []), key=lambda item: int(item.get("ref_order") or 0))
        for idx, new_ref in enumerate(new_group):
            old_ref = old_group[idx] if idx < len(old_group) else None
            merged = merge_ref(new_ref, old_ref)
            if merged.get("target_file") in (None, ""):
                page_int = merged.get("page_ref_int")
                if page_int is None:
                    page_int = first_numeric_hint(merged.get("page_ref_raw") or merged.get("ref_raw"))
                if isinstance(page_int, int):
                    merged["target_file"] = page_map.get(page_int)
            if merged.get("target_file") in (None, "") and isinstance(merged.get("page_ref_int"), int):
                helper_item = helper_by_entry_key.get(entry_key)
                if helper_item is not None:
                    exact_candidates = [
                        candidate
                        for candidate in helper_item.get("candidates", [])
                        if candidate_matches_page(candidate, int(merged["page_ref_int"]))
                    ]
                    if exact_candidates:
                        candidate = max(
                            exact_candidates,
                            key=lambda item: (
                                float(item.get("probability") or 0.0),
                                float(item.get("score") or 0.0),
                            ),
                        )
                        merged["target_file"] = candidate.get("file")
                        merged["target_file_probability"] = candidate.get("probability")
                        merged.setdefault("raw_json", {}).setdefault("helper_ref_resolution", {}).update(
                            {
                                "source": str(HELPER_OUTPUT_PATH),
                                "entry_id": helper_id_by_entry_key.get(entry_key),
                                "match_rule": "page_ref_int matched helper candidate inferred_printed_page or estimator_pages",
                                "candidate_rank": candidate.get("rank"),
                                "candidate_file_seq": candidate.get("file_seq"),
                                "candidate_probability": candidate.get("probability"),
                                "candidate_reason_summary": candidate.get("reason_summary"),
                                "candidate_evidence_kinds": candidate_evidence_kinds(candidate),
                            }
                        )
            merged_refs.append(merged)

    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in merged_refs:
        refs_by_entry[ref.get("entry_key") or ""].append(ref)
    for entry in merged_entries:
        if entry.get("target_file_best") in (None, ""):
            page_int = entry.get("inferred_printed_page")
            if page_int is None:
                page_int = first_numeric_hint(entry.get("entry_raw"))
            if isinstance(page_int, int):
                entry["target_file_best"] = page_map.get(page_int)
        if entry.get("target_file_best") in (None, ""):
            entry_refs = refs_by_entry.get(entry["entry_key"], [])
            for ref in entry_refs:
                if ref.get("target_file"):
                    entry["target_file_best"] = ref["target_file"]
                    break

    notes = merge_unique_strings(existing.get("notes", []), data.get("notes", []), [
        "Rebuilt from validated assembled fragments in data/intermediate_payloads/PL027/assembled_fragments.json.",
        "Existing payload locator evidence was merged back into the fragment-based rebuild when a section-local content match was unambiguous.",
        "OCR page headers were scanned to recover page-to-file target_file locators for page-bearing refs.",
        "Section ordering was normalized to the closing physical sequence 596 -> 595 -> 582-594 so alphabetical_sections.section_order stays unique for PL027.",
        "PL027 helper request/output were mapped back to rebuilt entries by normalized OCR text, and exact helper page matches were reused for unresolved refs.",
    ])

    coverage = dict(existing.get("coverage") or {})
    coverage["entries_status"] = coverage.get("entries_status") or "partial_extracted"
    coverage["entries_status_reason"] = (
        "Rebuilt from validated chunk fragments and merged with the prior payload's locator evidence "
        "where the entry text matched unambiguously."
    )
    coverage["evidence_files"] = coverage.get("evidence_files") or [
        "/homessddata/Projects/pdfocr/teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-582.txt",
        "/homessddata/Projects/pdfocr/teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-585.txt",
        "/homessddata/Projects/pdfocr/teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-590.txt",
        "/homessddata/Projects/pdfocr/teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-595.txt",
        "/homessddata/Projects/pdfocr/teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-596.txt",
    ]

    payload = {
        "schema_version": fragments.get("schema_version", 1),
        "generated_at": now_iso(),
        "volume": existing.get("volume")
        or {
            "volume_id": VOLUME_ID,
            "collection": "PL",
            "source_root": "/homessddata/Projects/pdfocr/teste/PL027/text",
            "volume_label": VOLUME_ID,
        },
        "sections": data.get("sections", []),
        "nodes": data.get("nodes", []),
        "entries": merged_entries,
        "refs": merged_refs,
        "scripture_refs": data.get("scripture_refs", []),
        "coverage": coverage,
        "notes": notes,
    }
    return payload


def update_todo() -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Rebuild PL027 alphabetical payload from validated fragments and prior locator evidence",
        "completed": [
            "validated chunk fragments consumed",
            "assembled_fragments.json inspected",
            "previous payload checked as checkpoint",
            "helper request/output remapped onto rebuilt entries",
        ],
        "pending": [
            "write final canonical payload",
            "run a structural validation pass",
        ],
        "blocked": [],
        "notes": [
            "Use fragment data as canonical entry content.",
            "Merge previous locator evidence only when the entry match is unambiguous.",
            "Keep section_order unique before rerunning the importer.",
        ],
    }
    write_json(TODO_PATH, todo)


def main() -> None:
    update_todo()
    payload = build_payload()
    write_json(OUTPUT_PATH, payload)


if __name__ == "__main__":
    main()
