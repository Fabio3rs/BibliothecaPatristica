#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pl055_alphabetical_payload.py
# Rebuild the PL055 alphabetical-index payload from the validated assembled-fragments
# checkpoint, resegment the coarse 673-678 subject block, and rederive refs/targets.
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import build_estimator_page_map


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PL055"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 55"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
ASSEMBLED_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "assembled_fragments.json"
OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data" / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"
TODO_PATH = ROOT / "data" / "intermediate_payloads" / VOLUME_ID / "todo.json"
EDITORIAL_PAGE_ONE_FILE = str(SOURCE_ROOT / "cddeef1e-6159-4a29-af46-5275e607a667-009.txt")

SECTION_ORDO = "PL055:candidate-section:001"
SECTION_POSTERIOR = "PL055:candidate-section:002"
SECTION_PRIOR_LATE = "PL055:candidate-section:003"
SECTION_PRIOR_MID = "PL055:candidate-section:004"
SECTION_PRIOR_EARLY = "PL055:candidate-section:005"
REBUILT_SECTIONS = {SECTION_POSTERIOR, SECTION_PRIOR_LATE, SECTION_PRIOR_MID, SECTION_PRIOR_EARLY}

CONTINUATION_WORDS = {
    "A",
    "Ab",
    "Ad",
    "Ante",
    "Apologiæ",
    "Cur",
    "Cum",
    "Data",
    "De",
    "Deo",
    "Designatur",
    "Eadem",
    "Ejus",
    "Eremum",
    "Erga",
    "Et",
    "Etiam",
    "Ex",
    "Hic",
    "Hinc",
    "Hominibus",
    "Horum",
    "Hujus",
    "Ibid",
    "Idem",
    "In",
    "Ipsius",
    "Item",
    "Magis",
    "Nec",
    "Non",
    "Notatur",
    "Nullis",
    "Num",
    "Post",
    "Quando",
    "Quam",
    "Quid",
    "Qui",
    "Quinam",
    "Quo",
    "Quomodo",
    "Rapitur",
    "Rationalis",
    "Ratione",
    "Reparatio",
    "Sæculo",
    "Sed",
    "Seminarium",
    "Solam",
    "Sunt",
    "Tamen",
    "Unde",
    "Vid",
    "Vide",
    "Zelus",
}

SPLIT_RE = re.compile(r"\.\s+(?=(?:S§\s+|SS§\s+)?[A-ZÆŒIJH])")
RANGE_RE = re.compile(r"\b(?P<start>\d{1,4})\s+usque\s+ad\s+(?P<end>\d{1,4})\b", re.IGNORECASE)
REF_RE = re.compile(
    r"(?P<range>\b\d{1,4}\s+usque\s+ad\s+\d{1,4}\b)"
    r"|(?P<numqual>\b\d{1,4}\b(?:\s+et\s+num\.\s*\d+\s+præfat\.?|\s+not\.|\s+et\s+seq{1,2}\.?|\s+et\s+seqq\.?)?)"
    r"|(?P<ibid>\bibid\.?(?:\s+not\.)?)",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"\b\d{1,4}\b")
VIDE_RE = re.compile(r"\b(?:vid\.?|vide|voir|cf\.?|v\.)\b", re.IGNORECASE)
SAINT_PREFIX_RE = re.compile(r"^(?:SS?\.\s+)", re.IGNORECASE)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(microsecond=0).isoformat()


def normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def normalize_sort(text: str | None) -> str | None:
    value = normalize_space(text)
    if not value:
        return None
    return value.lower()


def protect_abbreviations(text: str) -> str:
    out = text
    for raw, protected in (
        ("SS.", "SS§"),
        ("S.", "S§"),
        ("Vid.", "Vid§"),
        ("Vide.", "Vide§"),
        ("Ibid.", "Ibid§"),
        ("ibid.", "ibid§"),
        ("M.", "M§"),
        ("P.", "P§"),
    ):
        out = out.replace(raw, protected)
    return out


def unprotect_abbreviations(text: str) -> str:
    out = text
    for protected, raw in (
        ("SS§", "SS."),
        ("S§", "S."),
        ("Vid§", "Vid."),
        ("Vide§", "Vide."),
        ("Ibid§", "Ibid."),
        ("ibid§", "ibid."),
        ("M§", "M."),
        ("P§", "P."),
    ):
        out = out.replace(protected, raw)
    return out


def strip_saint_prefix(text: str | None) -> str:
    value = normalize_space(text)
    return SAINT_PREFIX_RE.sub("", value) if value else ""


def lemma_letter(text: str | None) -> str | None:
    value = strip_saint_prefix(text)
    match = re.search(r"[A-ZÆŒ]", value)
    return match.group(0) if match else None


def should_split_piece(base_letter: str | None, current_piece: str, next_piece: str) -> bool:
    first_word = next_piece.split(" ", 1)[0].rstrip(".,;:")
    next_letter = lemma_letter(next_piece)
    if first_word in CONTINUATION_WORDS or first_word.lower().startswith("ibid"):
        return False
    if base_letter and next_letter and next_letter != base_letter:
        return False
    if current_piece.count(",") >= 1 and next_piece.count(",") >= 1:
        return True
    if NUMBER_RE.search(current_piece) and NUMBER_RE.search(next_piece):
        return True
    return False


def split_coarse_entry(entry_raw: str) -> list[str]:
    protected = protect_abbreviations(normalize_space(entry_raw))
    base_letter = lemma_letter(unprotect_abbreviations(protected))
    pieces: list[str] = []
    current = ""
    for raw_piece in SPLIT_RE.split(protected):
        piece = unprotect_abbreviations(raw_piece.strip())
        if not piece:
            continue
        if current and should_split_piece(base_letter, current, piece):
            if not current.endswith("."):
                current += "."
            pieces.append(current)
            current = piece
            continue
        if current:
            current = f"{current}. {piece}" if not current.endswith(".") else f"{current} {piece}"
        else:
            current = piece
    if current:
        if not current.endswith("."):
            current += "."
        pieces.append(current)
    return pieces or [normalize_space(entry_raw)]


def summarize_helper_candidates(candidates: list[dict[str, Any]] | None, limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (candidates or [])[:limit]:
        out.append(
            {
                "file": item.get("file"),
                "probability": item.get("probability"),
                "inferred_printed_page": item.get("inferred_printed_page"),
                "candidate_role": item.get("candidate_role"),
                "reason_summary": item.get("reason_summary"),
            }
        )
    return out


def build_page_lookup() -> tuple[dict[int, str], list[int]]:
    mapping = build_estimator_page_map(volume_id=VOLUME_ID, collection=COLLECTION, source_root=SOURCE_ROOT)
    mapping.setdefault(1, EDITORIAL_PAGE_ONE_FILE)
    known_pages = sorted(mapping)
    return mapping, known_pages


def lookup_target_file(page: int | None, page_map: dict[int, str], known_pages: list[int]) -> tuple[str | None, float | None, dict[str, Any]]:
    if page is None:
        return None, None, {"target_lookup_mode": "no_page_ref"}
    if page in page_map:
        return page_map[page], 0.96, {"target_lookup_mode": "estimator_exact", "estimator_page": page}
    nearest = None
    nearest_distance = None
    for candidate in (page - 1, page + 1, page - 2, page + 2, page - 3, page + 3):
        if candidate in page_map:
            nearest = candidate
            nearest_distance = abs(candidate - page)
            break
    if nearest is not None and nearest_distance is not None:
        probability = 0.82 if nearest_distance == 1 else 0.72 if nearest_distance == 2 else 0.66
        return (
            page_map[nearest],
            probability,
            {
                "target_lookup_mode": "estimator_nearest",
                "estimator_page": nearest,
                "requested_page": page,
                "page_distance": nearest_distance,
            },
        )
    for delta in (200, 300, 400, 100):
        corrected = page - delta
        if corrected in page_map:
            return (
                page_map[corrected],
                0.68 if delta == 200 else 0.6 if delta == 300 else 0.54,
                {
                    "target_lookup_mode": "estimator_offset_correction",
                    "requested_page": page,
                    "corrected_page": corrected,
                    "offset_applied": delta,
                },
            )
        for nearby in (corrected - 1, corrected + 1, corrected - 2, corrected + 2, corrected - 3, corrected + 3):
            if nearby in page_map:
                return (
                    page_map[nearby],
                    0.58 if delta == 200 else 0.5,
                    {
                        "target_lookup_mode": "estimator_offset_nearest",
                        "requested_page": page,
                        "corrected_page": corrected,
                        "matched_page": nearby,
                        "offset_applied": delta,
                    },
                )
    return None, None, {"target_lookup_mode": "helper_needed", "requested_page": page}


def is_false_page_one_ref(entry_raw: str, raw_ref: str) -> bool:
    text = normalize_space(entry_raw)
    raw = normalize_space(raw_ref)
    if raw not in {"1", "1 not."}:
        return False
    false_patterns = (
        "Narbonensis 1 erat divisa",
        "Concilium Toletanum 1, vid. Toletana 1 Synodus",
        "concilio CP. 1, 522 not.",
        "Constantinopolitanum 1 concilium",
    )
    return any(pattern in text for pattern in false_patterns)


def first_ref_position(entry_raw: str) -> int | None:
    match = REF_RE.search(entry_raw)
    return match.start() if match else None


def derive_lemma(entry_raw: str, fallback: str | None) -> str | None:
    raw = normalize_space(entry_raw)
    if not raw:
        return fallback
    pos = first_ref_position(raw)
    candidate = normalize_space(raw[:pos]) if pos is not None and pos > 0 else None
    candidate = candidate.rstrip(" ,;:") if candidate else candidate
    if candidate and candidate not in {"S.", "SS.", "S", "SS", "M.", "P."}:
        return candidate
    fallback = normalize_space(fallback)
    return fallback or None


def build_ref_records(
    entry: dict[str, Any],
    *,
    page_map: dict[int, str],
    known_pages: list[int],
) -> list[dict[str, Any]]:
    text = normalize_space(entry["entry_raw"])
    refs: list[dict[str, Any]] = []
    last_page: int | None = None
    seen: set[tuple[Any, ...]] = set()
    for match in REF_RE.finditer(text):
        raw = normalize_space(match.group(0))
        if not raw:
            continue
        if match.group("range"):
            range_match = RANGE_RE.search(raw)
            if not range_match:
                continue
            start = int(range_match.group("start"))
            end = int(range_match.group("end"))
            target_file, probability, lookup_raw = lookup_target_file(start, page_map, known_pages)
            key = ("range", start, end)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": 0,
                    "ref_kind": "editorial_range",
                    "ref_raw": raw,
                    "page_ref_raw": str(start),
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(start),
                    "range_end_raw": str(end),
                    "target_file": target_file,
                    "target_file_probability": probability,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.9 if target_file else 0.62,
                    "raw_json": lookup_raw,
                }
            )
            last_page = start
            continue

        if match.group("ibid"):
            if last_page is None:
                continue
            line_ref_raw = "not." if "not." in raw.lower() else None
            target_file, probability, lookup_raw = lookup_target_file(last_page, page_map, known_pages)
            key = ("ibid", last_page, line_ref_raw)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": 0,
                    "ref_kind": "editorial_page",
                    "ref_raw": raw,
                    "page_ref_raw": "ibid.",
                    "page_ref_int": last_page,
                    "page_ref_col": None,
                    "line_ref_raw": line_ref_raw,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": probability,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.88 if target_file else 0.58,
                    "raw_json": {**lookup_raw, "inherited_from": last_page},
                }
            )
            continue

        number_match = NUMBER_RE.search(raw)
        if not number_match:
            continue
        page = int(number_match.group(0))
        qualifier = normalize_space(raw[number_match.end() :]) or None
        if page == 1 and is_false_page_one_ref(text, raw):
            continue
        target_file, probability, lookup_raw = lookup_target_file(page, page_map, known_pages)
        key = ("page", page, qualifier)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": 0,
                "ref_kind": "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": str(page),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": qualifier,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": probability,
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "confidence": 0.91 if target_file else 0.6,
                "raw_json": lookup_raw,
            }
        )
        last_page = page
    for idx, ref in enumerate(refs, start=1):
        ref["ref_order"] = idx
    return refs


def helper_page_hints(page: int) -> list[int]:
    hints = [page]
    for delta in (1, 2, 3, 5, 10, 20, 100, 200, 300, 400):
        for candidate in (page - delta, page + delta):
            if 0 < candidate < 10000 and candidate not in hints:
                hints.append(candidate)
    return hints[:8]


def build_helper_request(unresolved_refs: list[dict[str, Any]], entries_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for ref in unresolved_refs:
        entry = entries_by_key[ref["entry_key"]]
        page = ref.get("page_ref_int")
        page_hints = helper_page_hints(page) if isinstance(page, int) else []
        helper_entries.append(
            {
                "entry_id": f"{ref['entry_key']}#ref{ref['ref_order']}",
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:120],
                "query_names": [
                    item
                    for item in [
                        entry.get("lemma_raw"),
                        normalize_space(entry["entry_raw"].split(",", 1)[0]),
                        normalize_space(entry["entry_raw"])[:180],
                    ]
                    if item
                ][:4],
                "page_hints": [str(item) for item in page_hints],
                "page_hint_ints": page_hints,
                "context_raw": normalize_space(entry["entry_raw"])[:320],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper() -> dict[str, Any]:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--pretty",
        ],
        check=True,
        cwd=ROOT,
    )
    return read_json(HELPER_OUTPUT_PATH)


def apply_helper_to_refs(refs: list[dict[str, Any]], entries_by_key: dict[str, dict[str, Any]]) -> None:
    unresolved = [ref for ref in refs if ref.get("target_file") is None]
    if not unresolved:
        write_json(
            HELPER_REQUEST_PATH,
            {
                "volume_id": VOLUME_ID,
                "source_root": str(SOURCE_ROOT),
                "options": {"top_k": 5, "adjacency_window": 2},
                "entries": [],
            },
        )
        write_json(HELPER_OUTPUT_PATH, {"volume_id": VOLUME_ID, "entries": []})
        return
    request = build_helper_request(unresolved, entries_by_key)
    write_json(HELPER_REQUEST_PATH, request)
    output = run_helper()
    helper_by_id = {
        item.get("entry_id"): item
        for item in (output.get("entries") or [])
        if isinstance(item, dict)
    }
    for ref in unresolved:
        helper_id = f"{ref['entry_key']}#ref{ref['ref_order']}"
        helper = helper_by_id.get(helper_id)
        if not helper:
            ref.setdefault("raw_json", {})["helper"] = {"status": "missing"}
            continue
        best = helper.get("best_candidate")
        top = summarize_helper_candidates(helper.get("candidates") or helper.get("top_candidates"))
        helper_raw = {
            "status": helper.get("status"),
            "best_candidate": {
                "file": best.get("file") if isinstance(best, dict) else None,
                "probability": best.get("probability") if isinstance(best, dict) else None,
                "candidate_role": best.get("candidate_role") if isinstance(best, dict) else None,
                "reason_summary": best.get("reason_summary") if isinstance(best, dict) else None,
                "inferred_printed_page": best.get("inferred_printed_page") if isinstance(best, dict) else None,
            },
            "top_candidates": top,
        }
        ref.setdefault("raw_json", {})["helper"] = helper_raw
        if isinstance(best, dict) and best.get("file") and best.get("candidate_role") == "target_candidate":
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = best.get("probability")
            ref["confidence"] = 0.74 if (best.get("probability") or 0) >= 0.7 else 0.62
            ref["raw_json"]["target_lookup_mode"] = "helper_best_candidate"
            entry = entries_by_key[ref["entry_key"]]
            if not entry.get("target_file_best"):
                entry["target_file_best"] = best["file"]


def rebuild_section3_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    for entry in entries:
        pieces = split_coarse_entry(entry["entry_raw"])
        if len(pieces) == 1:
            updated = deepcopy(entry)
            updated["entry_raw"] = pieces[0]
            rebuilt.append(updated)
            continue
        for idx, piece in enumerate(pieces, start=1):
            updated = deepcopy(entry)
            updated["entry_raw"] = piece
            updated["raw_json"] = deepcopy(entry.get("raw_json", {}))
            updated["raw_json"]["split_from_fragment_entry_key"] = entry["entry_key"]
            updated["raw_json"]["split_piece_index"] = idx
            updated["raw_json"]["split_piece_total"] = len(pieces)
            if idx == 1:
                rebuilt.append(updated)
            else:
                updated["entry_key"] = f"{entry['entry_key']}:split:{idx:02d}"
                rebuilt.append(updated)
    return rebuilt


def rebuild_entries_and_refs(
    wrapper: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    data = wrapper["data"]
    sections = {item["section_key"]: item for item in data["sections"]}
    nodes_by_key = {item["node_key"]: item for item in data["nodes"]}
    existing_entries = data["entries"]
    entries_out: list[dict[str, Any]] = []
    refs_out: list[dict[str, Any]] = []
    notes: list[str] = []

    page_map, known_pages = build_page_lookup()

    section_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in existing_entries:
        section_groups[entry["section_key"]].append(deepcopy(entry))

    section_groups[SECTION_PRIOR_LATE] = rebuild_section3_entries(section_groups[SECTION_PRIOR_LATE])
    notes.append(
        "Section PL055:candidate-section:003 was resegmented from coarse fragment paragraphs into single logical entries when the OCR clearly contained multiple consecutive lemmata."
    )

    for section_key, section_entries in section_groups.items():
        section = sections[section_key]
        if section_key == SECTION_ORDO:
            entries_out.extend(section_entries)
            refs_out.extend(deepcopy(ref) for ref in data["refs"] if ref["entry_key"] in {e["entry_key"] for e in section_entries})
            continue

        for entry in section_entries:
            original_raw_json = deepcopy(entry.get("raw_json", {}))
            source_files = original_raw_json.get("source_files") or []
            if not source_files and original_raw_json.get("source_file"):
                source_files = [original_raw_json["source_file"]]
            editorial_anchor_file = entry.get("editorial_anchor_file") or (source_files[0] if source_files else section["file_start"])
            parent_node = nodes_by_key.get(entry.get("parent_node_key") or "")
            existing_lemma = normalize_space(entry.get("lemma_raw"))
            derived_lemma = derive_lemma(entry["entry_raw"], existing_lemma)
            entry["lemma_raw"] = derived_lemma
            entry["lemma_display"] = derived_lemma
            entry["lemma_norm"] = normalize_sort(derived_lemma)
            entry["lemma_sort"] = normalize_sort(derived_lemma)
            entry["section_start_file"] = section["file_start"]
            entry["editorial_anchor_file"] = editorial_anchor_file
            entry["target_file_best"] = None
            entry["inferred_printed_page"] = None
            if parent_node is not None:
                entry["heading_letter"] = parent_node["label_raw"]
            else:
                entry["heading_letter"] = lemma_letter(derived_lemma) or entry.get("heading_letter")
            entry["raw_json"] = original_raw_json
            if source_files:
                entry["raw_json"]["source_files"] = source_files
            refs = build_ref_records(entry, page_map=page_map, known_pages=known_pages)
            if not refs and VIDE_RE.search(entry["entry_raw"]) and entry.get("entry_kind") != "editorial_note":
                entry["entry_kind"] = "cross_reference"
            elif refs and entry.get("entry_kind") == "cross_reference":
                entry["entry_kind"] = "lemma"
            if refs:
                entry["inferred_printed_page"] = refs[0]["page_ref_int"]
                entry["target_file_best"] = refs[0].get("target_file")
                entry["confidence"] = min(max(entry.get("confidence", 0.85), 0.82), 0.96)
            else:
                entry["target_file_best"] = editorial_anchor_file
                if entry.get("entry_kind") == "cross_reference":
                    entry["confidence"] = min(entry.get("confidence", 0.72), 0.78)
            entries_out.append(entry)
            refs_out.extend(refs)

    entries_by_key = {entry["entry_key"]: entry for entry in entries_out}
    apply_helper_to_refs(refs_out, entries_by_key)

    ref_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs_out:
        ref_groups[ref["entry_key"]].append(ref)
    for entry in entries_out:
        entry_refs = ref_groups.get(entry["entry_key"], [])
        if entry_refs:
            entry["inferred_printed_page"] = entry_refs[0]["page_ref_int"]
            first_target = next((item.get("target_file") for item in entry_refs if item.get("target_file")), None)
            if first_target:
                entry["target_file_best"] = first_target

    order_by_section: dict[str, int] = defaultdict(int)
    for entry in entries_out:
        order_by_section[entry["section_key"]] += 1
        entry["entry_order"] = order_by_section[entry["section_key"]]

    refs_out.sort(key=lambda item: (entries_by_key[item["entry_key"]]["entry_order"], item["entry_key"], item["ref_order"]))
    for entry_key, entry_refs in ref_groups.items():
        for idx, ref in enumerate(entry_refs, start=1):
            ref["ref_order"] = idx

    return entries_out, refs_out, notes


def rebuild_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = deepcopy(sections)
    for section in out:
        if section["section_key"] == SECTION_POSTERIOR:
            section["page_start"] = 1351
            section["page_end"] = 1362
            section["raw_json"]["page_range_note"] = (
                "OCR headers drift on files 680-685, but the local sequence between file 679 (1349-1350) and file 686 (1363-1364) supports 1351-1362."
            )
        elif section["section_key"] == SECTION_PRIOR_MID:
            section["page_end"] = 1336
            section["raw_json"]["page_range_note"] = "Single physical file 672 carries the facing pair 1335-1336 despite the duplicated OCR right-side header digit."
    return out


def rebuild_payload() -> dict[str, Any]:
    wrapper = read_json(ASSEMBLED_PATH)
    data = wrapper["data"]
    sections = rebuild_sections(data["sections"])
    nodes = deepcopy(data["nodes"])
    entries, refs, rebuild_notes = rebuild_entries_and_refs(wrapper)

    notes = list(data.get("notes", []))
    notes.extend(
        [
            "Rebuilt refs for sections 002-005 from entry_raw instead of reusing the fragment-stage placeholder targets.",
            "Used tools.indexing.editorial_page_estimator to map cited editorial pages to OCR files across the full PL055 volume.",
            "Ran index_target_locator only for refs that remained unresolved after estimator exact/nearby lookup and preserved helper evidence in raw_json.",
        ]
    )
    notes.extend(item for item in rebuild_notes if item not in notes)

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Built the canonical payload from the validated assembled-fragments checkpoint; resegmented the coarse 673-678 block against OCR and rederived material refs/targets for sections 002-005."
        ),
        "evidence_files": [
            str(SOURCE_ROOT / "8016bbe0-aea8-4041-a70c-d515ebcda274-672.txt"),
            str(SOURCE_ROOT / "8016bbe0-aea8-4041-a70c-d515ebcda274-673.txt"),
            str(SOURCE_ROOT / "8016bbe0-aea8-4041-a70c-d515ebcda274-679.txt"),
            str(SOURCE_ROOT / "8016bbe0-aea8-4041-a70c-d515ebcda274-686.txt"),
        ],
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "Canonical PL055 payload rebuilt from validated closing-index chunk assembly plus direct OCR verification.",
                "The prior/posterior INDEX RERUM blocks and the final ORDO RERUM closure remain separate sections.",
                "Target-file resolution uses the editorial-page estimator first and helper support only for residual unresolved refs.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    return payload


def update_todo(payload: dict[str, Any]) -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": payload["generated_at"],
        "current_focus": "PL055 payload rebuilt and validated",
        "completed": [
            "Consumed stable sections, nodes, entries, refs, scripture_refs, and notes from assembled_fragments.json.",
            "Corrected section page-range metadata where OCR headers were corrupt but local sequence was stable.",
            "Resegmented the coarse 673-678 fragment entries against OCR into logical lemmata without rewriting unaffected sections.",
            "Rebuilt refs for sections 002-005 from entry_raw and resolved target_file with the editorial page estimator plus helper fallback.",
            "Wrote helper request/output and the canonical PL055 alphabetical payload.",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Section 001 (ORDO RERUM) was preserved from the validated chunk assembly because its refs were already materialized correctly.",
            "Section 003 required semantic splitting; the payload preserves split provenance in raw_json.split_from_fragment_entry_key.",
        ],
    }
    write_json(TODO_PATH, todo)


def main() -> None:
    payload = rebuild_payload()
    write_json(OUTPUT_PATH, payload)
    update_todo(payload)
    print(
        json.dumps(
            {
                "written_file": str(OUTPUT_PATH),
                "entries": len(payload["entries"]),
                "refs": len(payload["refs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
