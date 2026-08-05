#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pl051_alphabetical_payload.py
# Rebuild PL051 from validated fragments, regenerate helper evidence, and write the final payload.

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL051"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
FRAGMENTS_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "assembled_fragments.json"
EXISTING_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
TODO_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "todo.json"

SECTION_ORDO = f"{VOLUME_ID}:candidate-section:001"
SECTION_ANALYTIC = f"{VOLUME_ID}:candidate-section:002"
REF_MARKER_RE = re.compile(
    r"^(?P<head>ibid\.|\d+)\s*,?\s*(?P<marker>vers\.|epigr\.|not\.)\s*(?P<tail>.*)$",
    re.IGNORECASE,
)
IBID_RE = re.compile(r"^ibid\.?", re.IGNORECASE)
SUFFIX_RE = re.compile(r"-(\d+)\.txt$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.lower().replace("æ", "ae").replace("œ", "oe")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def file_seq(path: str | Path) -> int:
    match = SUFFIX_RE.search(str(path))
    if not match:
        return 999999
    return int(match.group(1))


def build_seq_to_file() -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in sorted(SOURCE_ROOT.glob("*.txt"), key=file_seq):
        mapping[file_seq(path)] = str(path)
    return mapping


def file_for_editorial_page(page: int | None, seq_to_file: dict[int, str]) -> str | None:
    if page is None or page <= 0:
        return None
    seq = (page + 9) // 2
    return seq_to_file.get(seq)


def probability_for_page_ref(raw: str, inherited: bool) -> float:
    lowered = raw.lower()
    if inherited or lowered.startswith("ibid"):
        return 0.97
    if "vers." in lowered or "epigr." in lowered or "not." in lowered:
        return 0.98
    return 0.995


def build_old_entry_indexes(entries: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    indexes = {
        "context": defaultdict(list),
        "entry_raw": defaultdict(list),
        "lemma_entry": defaultdict(list),
    }
    for idx, entry in enumerate(entries):
        context_key = normalize_text(entry.get("context_raw"))
        if context_key:
            indexes["context"][context_key].append(idx)
        entry_key = normalize_text(entry.get("entry_raw"))
        if entry_key:
            indexes["entry_raw"][entry_key].append(idx)
        lemma_entry_key = f"{normalize_text(entry.get('lemma_raw'))}||{entry_key}"
        if lemma_entry_key.strip("|"):
            indexes["lemma_entry"][lemma_entry_key].append(idx)
    return indexes


def consume_old_entry(
    new_entry: dict[str, Any],
    old_entries: list[dict[str, Any]],
    indexes: dict[str, dict[str, list[int]]],
    used: set[int],
) -> dict[str, Any] | None:
    candidates: list[int] = []
    context_key = normalize_text(new_entry.get("context_raw"))
    if context_key:
        candidates.extend(indexes["context"].get(context_key, []))
    entry_key = normalize_text(new_entry.get("entry_raw"))
    if entry_key:
        candidates.extend(indexes["entry_raw"].get(entry_key, []))
    lemma_entry_key = f"{normalize_text(new_entry.get('lemma_raw'))}||{entry_key}"
    candidates.extend(indexes["lemma_entry"].get(lemma_entry_key, []))

    seen: set[int] = set()
    for idx in candidates:
        if idx in seen or idx in used:
            continue
        seen.add(idx)
        used.add(idx)
        return old_entries[idx]
    return None


def merge_entry_locator_fields(new_entry: dict[str, Any], old_entry: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(new_entry)
    if old_entry is None:
        return merged

    if old_entry.get("target_file_best"):
        merged["target_file_best"] = old_entry["target_file_best"]
    merged["confidence"] = max(float(merged.get("confidence") or 0.0), float(old_entry.get("confidence") or 0.0))

    raw_json = dict(merged.get("raw_json") or {})
    old_raw = dict(old_entry.get("raw_json") or {})
    for key in (
        "helper_status",
        "helper_candidate_role",
        "helper_reason_summary",
        "helper_best_candidate",
        "helper_top_candidates",
    ):
        if key in old_raw:
            raw_json[key] = old_raw[key]
    raw_json["checkpoint_merged_from_previous_payload"] = True
    merged["raw_json"] = raw_json
    return merged


def merge_existing_ordo(
    fragment_entries: list[dict[str, Any]],
    fragment_refs: list[dict[str, Any]],
    existing_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    old_entries = list(existing_payload.get("entries") or [])
    old_refs = list(existing_payload.get("refs") or [])
    old_refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in old_refs:
        old_refs_by_entry[ref["entry_key"]].append(ref)

    indexes = build_old_entry_indexes(old_entries)
    used_old_indexes: set[int] = set()
    assembled_refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in fragment_refs:
        assembled_refs_by_entry[ref["entry_key"]].append(ref)

    merged_entries: list[dict[str, Any]] = []
    merged_refs: list[dict[str, Any]] = []
    for entry in fragment_entries:
        old_entry = consume_old_entry(entry, old_entries, indexes, used_old_indexes)
        merged_entry = merge_entry_locator_fields(entry, old_entry)
        merged_entries.append(merged_entry)

        if old_entry is not None and old_refs_by_entry.get(old_entry["entry_key"]):
            for ref in old_refs_by_entry[old_entry["entry_key"]]:
                cloned = deepcopy(ref)
                cloned["entry_key"] = merged_entry["entry_key"]
                cloned["section_start_file"] = merged_entry["section_start_file"]
                cloned["editorial_anchor_file"] = merged_entry["editorial_anchor_file"]
                raw_json = dict(cloned.get("raw_json") or {})
                raw_json["checkpoint_merged_from_previous_payload"] = True
                cloned["raw_json"] = raw_json
                merged_refs.append(cloned)
            continue

        for ref in assembled_refs_by_entry.get(entry["entry_key"], []):
            merged_refs.append(deepcopy(ref))

    return merged_entries, merged_refs


def normalize_analytic_refs(
    analytic_entries: list[dict[str, Any]],
    analytic_refs: list[dict[str, Any]],
    seq_to_file: dict[int, str],
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in analytic_refs:
        refs_by_entry[ref["entry_key"]].append(deepcopy(ref))
    for ref_list in refs_by_entry.values():
        ref_list.sort(key=lambda item: int(item.get("ref_order") or 0))

    normalized_refs: list[dict[str, Any]] = []
    best_target_by_entry: dict[str, str | None] = {}

    for entry in analytic_entries:
        entry_key = entry["entry_key"]
        ref_list = refs_by_entry.get(entry_key, [])
        current_page: int | None = None
        first_target: str | None = None
        new_ref_order = 1
        i = 0
        while i < len(ref_list):
            ref = deepcopy(ref_list[i])
            raw = (ref.get("ref_raw") or "").strip()
            lower = raw.lower()
            raw_json = dict(ref.get("raw_json") or {})
            inherited = False
            page = ref.get("page_ref_int")
            line_ref_raw: str | None = None
            ref_kind = ref.get("ref_kind") or "editorial_page"
            consume_next = False

            marker_match = REF_MARKER_RE.match(raw)
            if marker_match:
                head = marker_match.group("head")
                marker = marker_match.group("marker")
                tail = marker_match.group("tail").strip()
                if head.lower().startswith("ibid"):
                    page = current_page
                    inherited = True
                    page_ref_raw = head
                else:
                    page = int(head)
                    page_ref_raw = head
                if not tail and i + 1 < len(ref_list):
                    next_raw = (ref_list[i + 1].get("ref_raw") or "").strip()
                    if next_raw:
                        tail = next_raw
                        consume_next = True
                line_ref_raw = f"{marker} {tail}".strip() if tail else marker
                ref_kind = "editorial_page_line"
                raw_json["sub_locator_kind"] = marker
                if consume_next:
                    raw_json["attached_sub_locator_from_next_ref"] = ref_list[i + 1].get("ref_raw")
                ref["page_ref_raw"] = page_ref_raw
                ref["page_ref_int"] = page
            elif IBID_RE.match(lower):
                inherited = True
                page = current_page
                page_ref_raw = IBID_RE.match(raw).group(0) if IBID_RE.match(raw) else raw
                ref["page_ref_raw"] = page_ref_raw
                ref["page_ref_int"] = page
                rest = raw[len(page_ref_raw):].lstrip(" ,;:")
                if rest:
                    line_ref_raw = rest
                    if any(marker in rest.lower() for marker in ("vers.", "epigr.", "not.")):
                        ref_kind = "editorial_page_line"
            else:
                if page is not None:
                    current_page = int(page)

            if page is not None:
                current_page = int(page)
            target_file = file_for_editorial_page(current_page, seq_to_file) if current_page is not None else None
            if first_target is None and target_file:
                first_target = target_file

            if inherited:
                raw_json["inherited_page_from_previous_ref"] = current_page
            if current_page is not None:
                raw_json["target_file_resolution"] = {
                    "method": "editorial_page_formula",
                    "formula": "page = 2 * file_seq - 9",
                    "resolved_page": current_page,
                    "resolved_file_seq": file_seq(target_file) if target_file else None,
                }
            if line_ref_raw:
                ref["line_ref_raw"] = line_ref_raw
            ref["ref_kind"] = ref_kind
            ref["page_ref_int"] = current_page
            ref["target_file"] = target_file
            ref["target_file_probability"] = probability_for_page_ref(raw, inherited) if target_file else None
            ref["confidence"] = max(float(ref.get("confidence") or 0.0), 0.84 if target_file else 0.58)
            ref["raw_json"] = raw_json
            ref["ref_order"] = new_ref_order
            normalized_refs.append(ref)
            new_ref_order += 1
            i += 2 if consume_next else 1

        best_target_by_entry[entry_key] = first_target or entry.get("target_file_best")

    return normalized_refs, best_target_by_entry


def build_helper_request(
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str]]:
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)
    for ref_list in refs_by_entry.values():
        ref_list.sort(key=lambda item: int(item.get("ref_order") or 0))

    helper_entries: list[dict[str, Any]] = []
    entry_id_by_key: dict[str, str] = {}
    counter = 1
    for entry in entries:
        ref_list = refs_by_entry.get(entry["entry_key"], [])
        page_hints: list[str] = []
        page_hint_ints: list[int] = []
        for ref in ref_list:
            raw = (ref.get("ref_raw") or "").strip()
            page = ref.get("page_ref_int")
            if not raw and page is None:
                continue
            page_hints.append(raw)
            if isinstance(page, int):
                page_hint_ints.append(page)
            if len(page_hints) >= 8:
                break

        if not page_hints and not page_hint_ints:
            continue

        entry_id = f"pl051_helper_{counter:04d}"
        counter += 1
        entry_id_by_key[entry["entry_key"]] = entry_id
        helper_entries.append(
            {
                "entry_id": entry_id,
                "lemma_raw": entry.get("lemma_raw"),
                "query_names": [entry["lemma_raw"]] if entry.get("lemma_raw") else [],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry.get("context_raw") or entry.get("entry_raw"),
            }
        )

    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    return request, entry_id_by_key


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
        cwd=ROOT,
        check=True,
    )


def helper_maps(entry_id_by_key: dict[str, str]) -> dict[str, dict[str, Any]]:
    if not HELPER_OUTPUT_PATH.exists():
        return {}
    helper_output = read_json(HELPER_OUTPUT_PATH)
    by_id = {item["entry_id"]: item for item in helper_output.get("entries", []) if item.get("entry_id")}
    return {
        entry_key: by_id[entry_id]
        for entry_key, entry_id in entry_id_by_key.items()
        if entry_id in by_id
    }


def attach_helper_to_entries(
    entries: list[dict[str, Any]],
    best_target_by_entry: dict[str, str | None],
    helper_by_entry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    updated_entries: list[dict[str, Any]] = []
    for entry in entries:
        updated = deepcopy(entry)
        raw_json = dict(updated.get("raw_json") or {})
        helper_item = helper_by_entry.get(entry["entry_key"])
        if helper_item:
            raw_json["helper_status"] = helper_item.get("status")
            best = helper_item.get("best_candidate") or {}
            if best:
                raw_json["helper_best_candidate"] = {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                }
            top = helper_item.get("candidates") or []
            if top:
                raw_json["helper_top_candidates"] = [
                    {
                        "rank": candidate.get("rank"),
                        "file": candidate.get("file"),
                        "probability": candidate.get("probability"),
                        "candidate_role": candidate.get("candidate_role"),
                        "reason_summary": candidate.get("reason_summary"),
                    }
                    for candidate in top[:5]
                ]
        target = best_target_by_entry.get(entry["entry_key"])
        if target:
            updated["target_file_best"] = target
        updated["raw_json"] = raw_json
        updated_entries.append(updated)
    return updated_entries


def build_volume() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "collection": "PL",
        "source_root": str(SOURCE_ROOT),
        "volume_label": VOLUME_ID,
        "notes": [
            "The final payload was rebuilt from the validated PL051 chunk assembly after the previous final JSON dropped the analytic closing index.",
            "Section 001 preserves the closing Ordo Rerum plus the validated guard-leaf editorial_note entries that were emitted to satisfy chunk-level coverage validation.",
            "Section 002 is the real closing INDEX IN OPERA S. PROSPERI AQUITANI block printed on pages 947-1010.",
            "Analytic-index target files were remapped from the volume-local editorial-page relation page = 2 * file_seq - 9, confirmed by stable headers such as 947 on file 478 and 1011 on file 510.",
            "Previous helper-backed locators from the older Ordo Rerum checkpoint were merged back only where the OCR entry match was safe.",
        ],
    }


def build_coverage() -> dict[str, Any]:
    return {
        "entries_status": "recovered_with_residual_ambiguity",
        "entries_status_reason": (
            "Rebuilt the PL051 closing-index payload from 10 validated chunk fragments, restored the missing "
            "analytic section, merged prior helper-backed Ordo Rerum locators, and remapped analytic refs with "
            "a volume-local editorial page formula. Residual ambiguity remains in some subordinate vers./epigr./not. locators."
        ),
        "evidence_files": [
            str(SOURCE_ROOT / "73942e3c-6358-4846-888f-73378bc918fb-478.txt"),
            str(SOURCE_ROOT / "94a9761f-78aa-4ba2-a538-c4d7fa5252f4-509.txt"),
            str(SOURCE_ROOT / "94a9761f-78aa-4ba2-a538-c4d7fa5252f4-510.txt"),
            str(SOURCE_ROOT / "94a9761f-78aa-4ba2-a538-c4d7fa5252f4-512.txt"),
        ],
    }


def build_payload() -> dict[str, Any]:
    fragments = read_json(FRAGMENTS_PATH)["data"]
    existing_payload = read_json(EXISTING_PATH) if EXISTING_PATH.exists() else {}
    seq_to_file = build_seq_to_file()

    sections = deepcopy(fragments["sections"])
    nodes = deepcopy(fragments["nodes"])

    ordo_entries = [deepcopy(entry) for entry in fragments["entries"] if entry["section_key"] == SECTION_ORDO]
    analytic_entries = [deepcopy(entry) for entry in fragments["entries"] if entry["section_key"] == SECTION_ANALYTIC]
    ordo_refs = [deepcopy(ref) for ref in fragments["refs"] if ref["entry_key"].startswith(f"{SECTION_ORDO}:")]
    analytic_refs = [deepcopy(ref) for ref in fragments["refs"] if ref["entry_key"].startswith(f"{SECTION_ANALYTIC}:")]

    merged_ordo_entries, merged_ordo_refs = merge_existing_ordo(ordo_entries, ordo_refs, existing_payload)
    normalized_analytic_refs, analytic_best_targets = normalize_analytic_refs(analytic_entries, analytic_refs, seq_to_file)

    helper_request, entry_id_by_key = build_helper_request(merged_ordo_entries + analytic_entries, merged_ordo_refs + normalized_analytic_refs)
    write_json(HELPER_REQUEST_PATH, helper_request)
    run_helper()
    helper_by_entry = helper_maps(entry_id_by_key)

    ordo_entries_with_helper = attach_helper_to_entries(
        merged_ordo_entries,
        {entry["entry_key"]: entry.get("target_file_best") for entry in merged_ordo_entries},
        helper_by_entry,
    )
    analytic_entries_with_helper = attach_helper_to_entries(analytic_entries, analytic_best_targets, helper_by_entry)

    notes = list(fragments.get("notes") or [])
    notes.append(
        "The final payload now preserves both validated closing sections from assembled_fragments.json; the previous final JSON had silently dropped the analytic section."
    )
    notes.append(
        "Analytic target_file and target_file_best values were rebuilt from the PL051-local page/file relation page = 2 * file_seq - 9 instead of leaving them anchored to the tail index files."
    )

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": build_volume(),
        "sections": sections,
        "nodes": nodes,
        "entries": ordo_entries_with_helper + analytic_entries_with_helper,
        "refs": merged_ordo_refs + normalized_analytic_refs,
        "scripture_refs": [],
        "coverage": build_coverage(),
        "notes": notes,
    }
    return payload


def write_todo() -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PL051 rebuilt and validated",
        "completed": [
            "loaded validated chunk assembly",
            "restored missing analytic closing index section",
            "merged safe Ordo Rerum locator evidence from the previous checkpoint",
            "regenerated helper request/output for the rebuilt volume payload",
            "rewrote analytic target files from volume-local editorial page mapping",
            "validated final payload with import_alphabetical_index_json.py --validate-only",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Residual ambiguity remains only in subordinate vers./epigr./not. locators, not in the main section coverage.",
        ],
    }
    write_json(TODO_PATH, todo)


def validate_payload() -> None:
    proc = subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(OUTPUT_PATH),
            "--validate-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "import_alphabetical_index_json.py validation failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


def main() -> None:
    payload = build_payload()
    write_json(OUTPUT_PATH, payload)
    validate_payload()
    write_todo()


if __name__ == "__main__":
    main()
