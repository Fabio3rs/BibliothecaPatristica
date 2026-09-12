#!/usr/bin/env python3
"""Usage: build the PL079 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl079_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL079/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL079_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL079_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL079 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL079_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import build_estimator_page_map as estimator_page_map
from tools.indexing.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL079"
COLLECTION = "PL"
INDEX_FILE_NUMBERS = list(range(718, 748))
INDEX_FILE_NUMBERS_EXCLUDED = {720}
ORDO_FILE_NUMBERS = [748]

SECTION1_FILES = [num for num in INDEX_FILE_NUMBERS if num not in INDEX_FILE_NUMBERS_EXCLUDED]
SECTION2_FILES = ORDO_FILE_NUMBERS[:]

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
GREEK_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])|(?<=\d)\s+(?=[A-ZÆŒ])")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)")
BROKEN_TWO_DIGIT_RE = re.compile(r"(?<!\d)(\d)\.(\d)(?!\d)")
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
BARE_REMISSION_RE = re.compile(r"^(?:Ibid\.?|Id\.?)$", re.IGNORECASE)
CROSS_REF_RE = re.compile(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", re.IGNORECASE)
ROMAN_RE = re.compile(r"^[IVXLCDM]+\.?$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def is_single_letter(line: str) -> bool:
    return bool(LETTER_RE.fullmatch(line))


def is_bare_remission(segment: str) -> bool:
    return bool(BARE_REMISSION_RE.fullmatch(segment.strip()))


def is_cross_reference(segment: str) -> bool:
    return bool(CROSS_REF_RE.match(segment.strip()))


def clean_lines(page_text: str) -> list[str]:
    lines: list[str] = []
    for raw in page_text.splitlines():
        text = norm(raw)
        if not text:
            continue
        if text == "Digitized by Google":
            continue
        lines.append(text)
    return lines


def should_skip_page(text: str) -> bool:
    if not text:
        return True
    if GREEK_RE.search(text) and "INDEX IN S. GREGORII" not in text and "INDEX RERUM" not in text and "ORDO RERUM" not in text:
        return True
    return False


def split_page_fragments(lines: list[str]) -> list[tuple[str | None, str]]:
    fragments: list[tuple[str | None, str]] = []
    current_letter: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            fragments.append((current_letter, " ".join(current_lines).strip()))
            current_lines = []

    for line in lines:
        if is_single_letter(line):
            flush()
            current_letter = line
            continue
        current_lines.append(line)
    flush()
    return fragments


def split_segments(fragment_text: str) -> list[str]:
    raw_parts = [part.strip() for part in SPLIT_RE.split(fragment_text) if part and part.strip()]
    merged: list[str] = []
    i = 0
    while i < len(raw_parts):
        part = raw_parts[i].strip()
        if is_bare_remission(part) and merged:
            merged[-1] = f"{merged[-1]} {part}"
            i += 1
            continue
        if (
            len(part) < 15
            and not PAGE_REF_RE.search(part)
            and not is_cross_reference(part)
            and not ROMAN_RE.fullmatch(part.strip())
            and i + 1 < len(raw_parts)
        ):
            nxt = raw_parts[i + 1].strip()
            if nxt and (PAGE_REF_RE.search(nxt) or nxt[0].isupper()):
                merged.append(f"{part} {nxt}".strip())
                i += 2
                continue
        merged.append(part)
        i += 1
    return merged


def infer_lemma(entry_raw: str) -> str | None:
    text = norm(entry_raw) or ""
    if not text or is_cross_reference(text):
        return None
    text = re.sub(r"^[—-]\s*", "", text)
    text = re.sub(r"^\d+\s*", "", text)
    if "," in text:
        candidate = text.split(",", 1)[0].strip()
    else:
        candidate = text
    candidate = re.sub(r"\s+\d+.*$", "", candidate).strip(" .;:")
    if not candidate:
        return None
    return candidate


def parse_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    previous_page: int | None = None
    for match in re.finditer(r"(?<!\d)(\d\.\d|\d{1,4})(?:\s*(?:-|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)", text):
        token = match.group(1)
        end = match.group(2)
        ref_raw = match.group(0).strip()
        start: int | None
        ocr_note: str | None = None
        if BROKEN_TWO_DIGIT_RE.fullmatch(token):
            start = int(token.replace(".", ""))
            ocr_note = "dotted_two_digit_ocr"
        else:
            start = int(token)
            if start > 1600 and previous_page is not None:
                prev_token = str(previous_page)
                if token.startswith(prev_token) and len(token) == len(prev_token) + 1:
                    start = previous_page
                    ocr_note = "joined_repeated_digit_after_page"
        refs.append(
            {
                "ref_kind": "editorial_range" if end is not None else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start) if end is not None else None,
                "range_end_raw": str(int(end)) if end is not None else None,
                "ocr_note": ocr_note,
            }
        )
        if start is not None and start > 0:
            previous_page = start
    return refs


def extract_header_page_numbers(header_text: str) -> list[int]:
    numbers = [int(match.group(0)) for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", header_text or "")]
    return numbers


def first_nonempty_path(files: list[Path]) -> Path:
    if not files:
        raise ValueError("No OCR files found for the requested section.")
    return files[0]


def build_page_map(files: list[Path], source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        for block in (parsed.get("header_text") or "", parsed.get("footer_text") or ""):
            for match in PAGE_HEADER_RE.finditer(block):
                page = int(match.group(1))
                if 0 < page <= 1600:
                    page_map.setdefault(page, str(path))
    for page, target in estimator_page_map(
        volume_id=VOLUME_ID,
        collection=COLLECTION,
        source_root=source_root,
    ).items():
        page_map.setdefault(page, target)
    return page_map


def resolve_target_file(page_ref_int: int | None, page_map: dict[int, str]) -> tuple[str | None, str, float | None]:
    if page_ref_int is None or page_ref_int <= 0:
        return None, "unresolved", None
    exact = page_map.get(page_ref_int)
    if exact:
        return exact, "page_map", 0.99

    pages = sorted(page_map)
    best_pages: list[int] = []
    best_distance: int | None = None
    for page in pages:
        distance = abs(page - page_ref_int)
        if distance == 0 or distance > 3:
            continue
        if best_distance is None or distance < best_distance:
            best_pages = [page]
            best_distance = distance
        elif distance == best_distance:
            best_pages.append(page)

    if best_distance is None or len(best_pages) != 1:
        return None, "unresolved", None

    probability = {1: 0.72, 2: 0.61, 3: 0.52}.get(best_distance, 0.5)
    return page_map[best_pages[0]], f"neighbor_page_map:{best_pages[0]}", probability


def find_helper_entry(entry_raw: str, helper_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = (entry_raw or "").lower()
    for helper in helper_entries:
        helper_id = helper.get("entry_id")
        if helper_id == "pl079_scriptura_sacra" and "scriptura sacra, mons umbrosus" in lowered:
            return helper
        if helper_id == "pl079_saulus_ecclesiae_persecutor" and "saulus ecclesi" in lowered:
            return helper
        if helper_id == "pl079_praelati_carnales" and "praelati carnales jus regis usurpant" in lowered:
            return helper
        if helper_id == "pl079_vide_tribulatio" and "tribulatio" in lowered and ("vide" in lowered or "afflictio" in lowered):
            return helper
    return None


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pl079_scriptura_sacra",
                "lemma_raw": "Scriptura sacra",
                "query_names": [
                    "Scriptura sacra",
                    "mons umbrosus",
                    "picturae similis",
                    "Ignis",
                ],
                "page_hints": ["599", "451", "455"],
                "page_hint_ints": [599, 451, 455],
                "context_raw": "Scriptura sacra, mons umbrosus, 599. Fons et puteus, 135, 454.",
            },
            {
                "entry_id": "pl079_saulus_ecclesiae_persecutor",
                "lemma_raw": "Saulus Ecclesiae persecutor",
                "query_names": ["Saulus Ecclesiae persecutor", "Paulus", "persecutor"],
                "page_hints": ["14", "551"],
                "page_hint_ints": [14, 551],
                "context_raw": "Saulus Ecclesiae persecutor, 14. Vide Paulus.",
            },
            {
                "entry_id": "pl079_praelati_carnales",
                "lemma_raw": "Praelati carnales",
                "query_names": [
                    "Praelati carnales",
                    "a Samuele proponitur",
                    "Praelatorum cura",
                    "Verbis et operibus doceant",
                ],
                "page_hints": ["182", "192", "193", "216"],
                "page_hint_ints": [182, 192, 193, 216],
                "context_raw": "Praelati carnales jus regis usurpant, ut a Samuele proponitur, 182.",
            },
            {
                "entry_id": "pl079_vide_tribulatio",
                "lemma_raw": "Vide Tribulatio",
                "query_names": ["Tribulatio", "Afflictio", "Flagella"],
                "page_hints": ["353", "480", "551"],
                "page_hint_ints": [353, 480, 551],
                "context_raw": "Vide Tribulatio, Afflictio, Flagella.",
            },
        ],
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any] | None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return read_json(helper_output_json)


def page_sort_for_segments(item: tuple[int, Path, dict[str, str]]) -> tuple[int, int]:
    fallback_file_num, path, parsed = item
    header_numbers = extract_header_page_numbers(parsed.get("header_text", ""))
    if header_numbers:
        return (header_numbers[0], fallback_file_num)
    body_numbers = extract_header_page_numbers(parsed.get("body_text", ""))
    if body_numbers:
        return (body_numbers[0], fallback_file_num)
    return (fallback_file_num, fallback_file_num)


def extract_section_entries(
    files: list[Path],
    *,
    section_key: str,
    section_kind: str,
    section_start_file: Path,
    helper_output: dict[str, Any] | None,
    current_letter_nodes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    parsed_pages: list[tuple[int, Path, dict[str, str]]] = []
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        if should_skip_page(parsed.get("all_text", "")):
            continue
        parsed_pages.append((file_num(path), path, parsed))

    parsed_pages.sort(key=page_sort_for_segments)

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    letter_seen: set[str] = set()
    entry_order = 0
    node_order = 0
    helper_status = helper_output.get("status") if isinstance(helper_output, dict) else None

    def ensure_letter_node(letter: str, source_file: Path) -> str:
        nonlocal node_order
        if letter in current_letter_nodes:
            return current_letter_nodes[letter]
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:06d}"
        current_letter_nodes[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.97,
                "raw_json": {
                    "source_file": str(source_file),
                    "section_kind": section_kind,
                },
            }
        )
        return node_key

    for _, path, parsed in parsed_pages:
        body_lines = clean_lines(parsed.get("body_text", ""))
        fragments = split_page_fragments(body_lines)
        for current_letter, fragment_text in fragments:
            if not fragment_text:
                continue
            parts = split_segments(fragment_text)
            for part in parts:
                if not part:
                    continue
                if is_single_letter(part):
                    current_letter = part
                    continue

                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:tmp:{section_kind}:{entry_order:06d}"
                entry_kind = "heading_group" if section_kind == "ordo_rerum" else ("cross_reference" if is_cross_reference(part) else "lemma")
                if entry_kind == "cross_reference":
                    lemma_raw = None
                    lemma_display = None
                    lemma_norm = None
                    lemma_sort = None
                else:
                    lemma_raw = infer_lemma(part)
                    lemma_display = lemma_raw
                    lemma_norm = lemma_raw.lower() if lemma_raw else None
                    lemma_sort = sort_norm(lemma_raw)

                page_refs = [] if entry_kind == "cross_reference" else parse_page_refs(part)
                printed_page = page_refs[0]["page_ref_int"] if page_refs else None
                parent_node_key = None
                if current_letter and current_letter in letter_seen:
                    parent_node_key = current_letter_nodes.get(current_letter)
                elif current_letter:
                    parent_node_key = ensure_letter_node(current_letter, path)
                    letter_seen.add(current_letter)

                entry_payload = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_display,
                    "lemma_norm": lemma_norm,
                    "lemma_sort": lemma_sort,
                    "entry_raw": part,
                    "context_raw": part,
                    "heading_letter": current_letter,
                    "inferred_printed_page": printed_page,
                    "section_start_file": str(section_start_file),
                    "editorial_anchor_file": str(path),
                    "target_file_best": str(path),
                    "confidence": 0.89 if entry_kind != "cross_reference" else 0.75,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section_kind,
                        "helper_status": helper_status,
                        "temp_entry_key": entry_key,
                    },
                }
                entries.append(entry_payload)

                for ref_index, ref in enumerate(page_refs, start=1):
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_index,
                            "ref_kind": ref["ref_kind"],
                            "ref_raw": ref["ref_raw"],
                            "page_ref_raw": ref["page_ref_raw"],
                            "page_ref_int": ref["page_ref_int"],
                            "page_ref_col": ref["page_ref_col"],
                            "line_ref_raw": ref["line_ref_raw"],
                            "range_start_raw": ref["range_start_raw"],
                            "range_end_raw": ref["range_end_raw"],
                            "target_file": None,
                            "target_file_probability": None,
                            "section_start_file": str(section_start_file),
                            "editorial_anchor_file": str(path),
                            "confidence": 0.67 if len(page_refs) == 1 else 0.62,
                            "raw_json": {
                                "source_file": str(path),
                                "section_kind": section_kind,
                                "helper_status": helper_status,
                            },
                        }
                    )

    entries.sort(key=lambda item: (
        item["heading_letter"] or "",
        item["lemma_sort"] or sort_norm(item["entry_raw"]) or "",
        item["entry_raw"].lower(),
    ))
    for idx, entry in enumerate(entries, start=1):
        entry["entry_order"] = idx

    node_by_key = {node["node_key"]: node for node in nodes}
    for idx, node in enumerate(sorted(nodes, key=lambda n: (n["label_sort"] or "", n["label_raw"])), start=1):
        node["node_order"] = idx
    # Rebuild node key ordering for stable output.
    nodes.sort(key=lambda n: (n["node_order"], n["label_sort"]))
    return nodes, entries, refs, sorted(letter_seen)


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    file_map = {file_num(path): path for path in files}
    page_map = build_page_map(files, source_root)

    helper_request = build_helper_request(source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_entries = helper_output.get("entries", []) if isinstance(helper_output, dict) else []

    relevant_index_files = [file_map[num] for num in SECTION1_FILES if num in file_map]
    ordo_files = [file_map[num] for num in SECTION2_FILES if num in file_map]

    section1_start = first_nonempty_path(relevant_index_files)
    section2_start = first_nonempty_path(ordo_files)

    section1_key = f"{VOLUME_ID}:alpha:analytic_subject:001"
    section2_key = f"{VOLUME_ID}:alpha:ordo_rerum:002"

    section1_nodes_map: dict[str, str] = {}
    section1_nodes, section1_entries, section1_refs, section1_letters = extract_section_entries(
        relevant_index_files,
        section_key=section1_key,
        section_kind="analytic_subject",
        section_start_file=section1_start,
        helper_output=helper_output,
        current_letter_nodes=section1_nodes_map,
    )
    section2_nodes, section2_entries, section2_refs, section2_letters = extract_section_entries(
        ordo_files,
        section_key=section2_key,
        section_kind="ordo_rerum",
        section_start_file=section2_start,
        helper_output=helper_output,
        current_letter_nodes={},
    )

    sections = [
        {
            "section_key": section1_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM ET VERBORUM QUAE IN PRIMA PARTE TOMI CONTINENTUR.",
            "heading_norm": "index rerum et verborum quae in prima parte tomi continentur",
            "heading_letter": None,
            "page_start": 1423,
            "page_end": 1482,
            "file_start": str(section1_start),
            "file_end": str(relevant_index_files[-1]),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index in the tail volume; the OCR includes a single Greek interlude at file 720, which was excluded from extraction.",
                "source_files": [str(path) for path in relevant_index_files],
                "excluded_files": [str(file_map[num]) for num in INDEX_FILE_NUMBERS_EXCLUDED if num in file_map],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
                "helper_candidate_files": helper_output.get("entries", []) if isinstance(helper_output, dict) else None,
            },
        },
        {
            "section_key": section2_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 1483,
            "page_end": 1484,
            "file_start": str(section2_start),
            "file_end": str(section2_start),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Editorial closure / contents table after the alphabetical index.",
                "source_files": [str(path) for path in ordo_files],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
            },
        },
    ]

    nodes = section1_nodes + section2_nodes
    entries = section1_entries + section2_entries
    refs = section1_refs + section2_refs

    global_key_map: dict[str, str] = {}
    for idx, entry in enumerate(entries, start=1):
        old_key = entry["entry_key"]
        new_key = f"{VOLUME_ID}:entry:{idx:06d}"
        global_key_map[old_key] = new_key
        entry["entry_key"] = new_key
        entry["entry_order"] = idx

    for ref in refs:
        ref["entry_key"] = global_key_map.get(ref["entry_key"], ref["entry_key"])
    refs_by_entry: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        refs_by_entry.setdefault(ref["entry_key"], []).append(ref)

    for entry in entries:
        first_ref_target: str | None = None
        for ref in refs_by_entry.get(entry["entry_key"], []):
            page_ref_int = ref.get("page_ref_int")
            target_file, locator_method, probability = resolve_target_file(
                page_ref_int if isinstance(page_ref_int, int) else None,
                page_map,
            )
            if target_file is None and ref.get("ocr_note") == "joined_repeated_digit_after_page":
                target_file = entry.get("target_file_best")
                locator_method = "repeated_page_fallback"
                probability = 0.52 if target_file else None
            ref["target_file"] = target_file
            ref["target_file_probability"] = probability if target_file else None
            ref["confidence"] = 0.91 if locator_method == "page_map" else (0.68 if target_file else 0.55)
            ref["raw_json"]["locator_method"] = locator_method
            if ref.get("ocr_note"):
                ref["raw_json"]["ocr_note"] = ref["ocr_note"]
            ref.pop("ocr_note", None)
            if first_ref_target is None and target_file:
                first_ref_target = target_file

        if first_ref_target:
            entry["target_file_best"] = first_ref_target
            entry["raw_json"]["target_file_best_reason"] = "first_resolved_ref_page_map"
        helper_entry = find_helper_entry(entry.get("entry_raw") or "", helper_entries)
        if helper_entry:
            best = helper_entry.get("best_candidate") or {}
            entry["raw_json"]["helper"] = {
                "entry_id": helper_entry.get("entry_id"),
                "status": helper_entry.get("status"),
                "best_candidate": {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                },
                "candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "inferred_printed_page": cand.get("inferred_printed_page"),
                    }
                    for cand in (helper_entry.get("candidates") or [])[:3]
                ],
            }
            if not first_ref_target and best.get("file") and best.get("candidate_role") == "target_candidate":
                entry["target_file_best"] = best["file"]
                entry["raw_json"]["target_file_best_reason"] = "helper_target_candidate"
    scripture_refs: list[dict[str, Any]] = []

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina, volume 79",
        "notes": "Tail alphabetical index and closing table of contents for the volume; OCR file suffixes do not match printed-page order, and file 720 is a Greek interlude that was excluded from the Latin index extraction.",
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the alphabetical index and the closing ORDO RERUM conservatively from the OCR tail. A full-volume page map plus focused helper evidence now resolve the cited target files for nearly all material refs, while noisy line wraps and a few OCR-broken numerals remain explicit in raw_json.",
        "evidence_files": [
            str(file_map[num])
            for num in [718, 719, 721, 722, 723, 724, 745, 746, 747, 748]
            if num in file_map
        ],
    }

    notes = [
        "The alphabetical index is one section even though the OCR file suffixes and printed pages are shuffled.",
        "File 720 is a Greek interlude and was excluded from the Latin index payload.",
        "Bare remissions such as `Vide` and `Ibid.` were preserved at entry level and not serialized as standalone refs.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", scripture_refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": now_iso(),
            "updated_at": now_iso(),
            "source_root": str(source_root),
            "helper_request_json": str(helper_request_json),
            "helper_output_json": str(helper_output_json),
            "output_file": str(Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL079_alphabetical_indices.json")),
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate the PL079 alphabetical payload and keep OCR literals intact.",
            "completed": [
                "tail index section identified",
                "Greek interlude excluded",
                "helper request written and helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "validate final JSON payload",
            ],
            "blocked": [],
            "notes": [
                "Keep `OCR file`, `printed page`, and `cited reference` separate.",
                "Do not invent placeholder refs for bare remissions.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL079 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
