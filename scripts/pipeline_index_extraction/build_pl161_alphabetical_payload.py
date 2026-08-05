#!/usr/bin/env python3
"""Usage: build the PL161 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl161_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL161/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL161_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL161_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL161 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL161_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL161"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, volume 161"

SECTION1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION2_KEY = f"{VOLUME_ID}:alpha:analytic_subject:002"
SECTION3_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:003"

SECTION1_HEADING = "INDEX UNIVERSALIS EORUM OMNIUM QUÆ CONTINENTUR IN DECRETO IVONIS."
SECTION2_HEADING = "INDEX LOCUPLETISSIMUS IN PANORMIAM SECUNDUM ORDINEM CAPITULORUM ET LIBRORUM DIGESTUS."
SECTION3_HEADING = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

SECTION1_FILE_START = 723
SECTION1_FILE_END = 740
SECTION2_FILE_START = 740
SECTION2_FILE_END = 761
SECTION3_FILE_START = 764
SECTION3_FILE_END = 764

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
GREEK_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])|(?<=\d)\s+(?=[A-ZÆŒ])")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)")
BARE_REMISSION_RE = re.compile(r"^(?:Ibid\.?|Id\.?)$", re.IGNORECASE)
CROSS_REF_RE = re.compile(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", re.IGNORECASE)
ROMAN_RE = re.compile(r"^[IVXLCDM]+\.?$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    return candidate or None


def parse_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = match.group(2)
        ref_raw = match.group(0).strip()
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
            }
        )
    return refs


def header_page_numbers(header_text: str) -> list[int]:
    return [int(match.group(0)) for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", header_text or "")]


def page_sort_for_segments(item: tuple[int, Path, dict[str, str]]) -> tuple[int, int]:
    fallback_file_num, _path, parsed = item
    header_numbers = header_page_numbers(parsed.get("header_text", ""))
    if header_numbers:
        return (header_numbers[0], fallback_file_num)
    body_numbers = header_page_numbers(parsed.get("body_text", ""))
    if body_numbers:
        return (body_numbers[0], fallback_file_num)
    return (fallback_file_num, fallback_file_num)


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


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pl161_index_universalis_abbas",
                "lemma_raw": "Abbas et abbatissa",
                "query_names": [
                    "Abbas et abbatissa",
                    "Abbas avaritiam detestari debet",
                    "Abbatissa magnan de suo conventu curam habere debet",
                ],
                "page_hints": ["1359", "1360"],
                "page_hint_ints": [1359, 1360],
                "context_raw": "Abbas et abbatissa. Abbas avaritiam detestari debet, pars 7, cap. 130. Venditimes quas potest facere abbas, quae, 3, 165.",
            },
            {
                "entry_id": "pl161_index_locupletissimus_admonitio",
                "lemma_raw": "Admonitio prima",
                "query_names": [
                    "Admonitio prima",
                    "si vis perfectus esse",
                    "Prologi",
                ],
                "page_hints": ["1385", "1386"],
                "page_hint_ints": [1385, 1386],
                "context_raw": "Admonitio prima, ut illa, si vis perfectus esse, non intentat poenam sed praemium pollicetur, cap. 5 Prologi.",
            },
            {
                "entry_id": "pl161_ordo_rerum_d_ivo",
                "lemma_raw": "D. IVO CARNOTENSIS EPISCOPUS",
                "query_names": [
                    "D. IVO CARNOTENSIS EPISCOPUS",
                    "Notitia historico-litteraria",
                    "Panormia. 165?",
                ],
                "page_hints": ["943"],
                "page_hint_ints": [943],
                "context_raw": "D. IVO CARNOTENSIS EPISCOPUS. Notitia historico-litteraria. Dissertatio de Decreto Ivonis ... XLIX",
            },
        ],
    }


def parse_section_entries(
    files: list[Path],
    *,
    section_key: str,
    section_kind: str,
    section_start_file: Path,
    helper_output: dict[str, Any] | None,
    start_after_title: str | None = None,
    stop_before_title: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    parsed_pages: list[tuple[int, Path, dict[str, str]]] = []
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        if GREEK_RE.search(parsed.get("all_text", "")) and section_kind != "ordo_rerum":
            continue
        parsed_pages.append((file_num(path), path, parsed))

    parsed_pages.sort(key=page_sort_for_segments)

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    current_letter_nodes: dict[str, str] = {}
    current_letter = None
    node_order = 0
    entry_order = 0
    letter_seen: set[str] = set()
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
        if not body_lines:
            continue

        if stop_before_title and any(stop_before_title in line for line in body_lines):
            cut = next(i for i, line in enumerate(body_lines) if stop_before_title in line)
            body_lines = body_lines[:cut]
        if start_after_title and any(start_after_title in line for line in body_lines):
            cut = next(i for i, line in enumerate(body_lines) if start_after_title in line)
            body_lines = body_lines[cut + 1 :]

        fragments = split_page_fragments(body_lines)
        for maybe_letter, fragment_text in fragments:
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
                entry_key = f"{VOLUME_ID}:entry:{section_kind}:{entry_order:06d}"
                entry_kind = "heading_group" if part.endswith(".") and not PAGE_REF_RE.search(part) and not is_cross_reference(part) else "lemma"
                if is_cross_reference(part):
                    entry_kind = "cross_reference"

                lemma_raw = None
                lemma_display = None
                lemma_norm = None
                lemma_sort = None
                if entry_kind != "cross_reference":
                    lemma_raw = infer_lemma(part)
                    lemma_display = lemma_raw
                    lemma_norm = lemma_raw.lower() if lemma_raw else None
                    lemma_sort = sort_norm(lemma_raw)

                refs_for_entry = [] if entry_kind == "cross_reference" else parse_page_refs(part)
                inferred_printed_page = refs_for_entry[0]["page_ref_int"] if refs_for_entry else None
                parent_node_key = None
                if current_letter:
                    parent_node_key = ensure_letter_node(current_letter, path)
                    letter_seen.add(current_letter)

                entries.append(
                    {
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
                        "inferred_printed_page": inferred_printed_page,
                        "section_start_file": str(section_start_file),
                        "editorial_anchor_file": str(path),
                        "target_file_best": str(path),
                        "confidence": 0.89 if entry_kind != "cross_reference" else 0.75,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section_kind,
                            "helper_status": helper_status,
                            "candidate_role": "section_entry",
                        },
                    }
                )

                for ref_index, ref in enumerate(refs_for_entry, start=1):
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
                            "confidence": 0.67 if len(refs_for_entry) == 1 else 0.62,
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

    nodes.sort(key=lambda item: (item["node_order"], item["label_sort"]))
    for idx, node in enumerate(nodes, start=1):
        node["node_order"] = idx

    return nodes, entries, refs, sorted(letter_seen)


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    file_map = {file_num(path): path for path in files}

    helper_request = build_helper_request(source_root)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)

    section1_files = [file_map[num] for num in range(723, 741) if num in file_map]
    section2_files = [file_map[num] for num in range(740, 762) if num in file_map]
    section3_files = [file_map[num] for num in range(764, 765) if num in file_map]

    section1_start = section1_files[0]
    section2_start = section2_files[0]
    section3_start = section3_files[0]

    section1_nodes, section1_entries, section1_refs, section1_letters = parse_section_entries(
        section1_files,
        section_key=SECTION1_KEY,
        section_kind="analytic_subject",
        section_start_file=section1_start,
        helper_output=helper_output,
        stop_before_title=SECTION2_HEADING,
    )
    section2_nodes, section2_entries, section2_refs, section2_letters = parse_section_entries(
        section2_files,
        section_key=SECTION2_KEY,
        section_kind="analytic_subject",
        section_start_file=section2_start,
        helper_output=helper_output,
        start_after_title=SECTION2_HEADING,
    )
    section3_nodes, section3_entries, section3_refs, section3_letters = parse_section_entries(
        section3_files,
        section_key=SECTION3_KEY,
        section_kind="ordo_rerum",
        section_start_file=section3_start,
        helper_output=helper_output,
    )

    entries = section1_entries + section2_entries + section3_entries
    refs = section1_refs + section2_refs + section3_refs
    nodes = section1_nodes + section2_nodes + section3_nodes

    global_key_map: dict[str, str] = {}
    for idx, entry in enumerate(entries, start=1):
        old_key = entry["entry_key"]
        new_key = f"{VOLUME_ID}:entry:{idx:06d}"
        global_key_map[old_key] = new_key
        entry["entry_key"] = new_key
        entry["entry_order"] = idx

    for ref in refs:
        ref["entry_key"] = global_key_map.get(ref["entry_key"], ref["entry_key"])

    sections = [
        {
            "section_key": SECTION1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION1_HEADING,
            "heading_norm": "index universalis eorum omnium quae continentur in decreto ivonis",
            "heading_letter": None,
            "page_start": 1359,
            "page_end": 1384,
            "file_start": str(section1_start),
            "file_end": str(section1_files[-1]),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index for the Decretum portion of the volume.",
                "source_files": [str(path) for path in section1_files],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
                "candidate_role": "section",
            },
        },
        {
            "section_key": SECTION2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION2_HEADING,
            "heading_norm": "index locupletissimus in panormiam secundum ordinem capitulorum et librorum digestus",
            "heading_letter": None,
            "page_start": 1385,
            "page_end": 1422,
            "file_start": str(section2_start),
            "file_end": str(section2_files[-1]),
            "confidence": 0.94,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index for the Panormia portion of the volume.",
                "source_files": [str(path) for path in section2_files],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
                "candidate_role": "section",
            },
        },
        {
            "section_key": SECTION3_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION3_HEADING,
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 943,
            "page_end": 943,
            "file_start": str(section3_start),
            "file_end": str(section3_files[-1]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Editorial closure / table of contents at the end of the volume.",
                "source_files": [str(path) for path in section3_files],
                "helper_status": helper_output.get("status") if isinstance(helper_output, dict) else None,
                "candidate_role": "section",
            },
        },
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "PL161 tail contains two analytical subject indexes (Decretum and Panormia) followed by a closing Ordo rerum table; OCR file suffixes do not align with printed pages, and the Panormia section begins inside file 740.",
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered the two analytical subject indexes and the closing Ordo rerum table from the OCR tail with conservative line grouping. The Panormia section begins in the same OCR file that closes the Decretum index, and several entries are split across OCR line wraps.",
        "evidence_files": [
            str(file_map[num])
            for num in [723, 740, 743, 749, 761, 764]
            if num in file_map
        ],
    }

    notes = [
        "The Decretum index and Panormia index are separate sections but share the same OCR tail window.",
        "Section 2 begins inside OCR file 740 immediately after the end of section 1.",
        "The closing Ordo rerum table is stored as a separate `ordo_rerum` section.",
        "Refs keep OCR literals and material locators separate; `target_file` is left null when the volume-local locator is not resolved.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
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
            "output_file": str(Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL161_alphabetical_indices.json")),
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate the PL161 alphabetical payload and keep OCR literals intact.",
            "completed": [
                "Decretum index window identified",
                "Panormia index window identified",
                "closing Ordo rerum table identified",
                "helper request written and helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "review final JSON for section boundaries and OCR line splits",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR file, printed page, and cited reference separate.",
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
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL161 alphabetical payload.")
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
