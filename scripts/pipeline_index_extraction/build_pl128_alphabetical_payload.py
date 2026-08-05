#!/usr/bin/env python3
"""Usage: build the PL128 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl128_alphabetical_payload.py
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
VOLUME_ID = "PL128"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL128/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL128_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL128_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL128_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL128"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SECTIONS_JSON = INTERMEDIATE_DIR / "sections.json"
ENTRIES_JSON = INTERMEDIATE_DIR / "entries.json"
REFS_JSON = INTERMEDIATE_DIR / "refs.json"
PAYLOAD_JSON = INTERMEDIATE_DIR / "volume.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

ALPHA_START_SEQ = 718
ALPHA_END_SEQ = 737
ORDO_SEQ = 738

ALPHA_SECTION_KEY = f"{VOLUME_ID}:alpha:alphabetical_general:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"

TITLE_RE = re.compile(r"INDEX IN VITAS PONTIFICUM ROMANORUM\.?", re.IGNORECASE)
HEADER_ONLY_RE = re.compile(r"^\d{1,4}$")
LETTER_ONLY_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_LOC_RE = re.compile(
    r"(?<!\d)(?P<start>\d{1,4})(?:\s*(?:[-–—]|à)\s*(?P<end>\d{1,4}))?(?!\d)"
)
ENTRY_SPLIT_RE = re.compile(r"(?<=[0-9])\.\s+(?=[A-ZÆŒ(])")
SECONDARY_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-ZÆŒ][^.\n]*\d)")
NOISE_RE = re.compile(r"^(?:Digitized by Google|—+)$", re.IGNORECASE)
ROMAN_ONLY_RE = re.compile(r"^(?:[IVXLCDM]+\.?|[IVXLCDM]+\.)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def norm_sort(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def parse_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in parsed.get("all_text", "").splitlines():
        text = normalize(raw)
        if not text:
            continue
        if HEADER_ONLY_RE.fullmatch(text):
            continue
        if NOISE_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def extract_header_numbers(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text") or "")
    numbers: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
        value = int(match.group(1))
        if value not in numbers:
            numbers.append(value)
    return numbers


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in discover_text_files(source_root):
        for number in extract_header_numbers(path):
            page_map.setdefault(number, str(path))
    return page_map


def lookup_target(page: int, page_map: dict[int, str]) -> tuple[str | None, str]:
    if page in page_map:
        return page_map[page], "exact"
    for delta in (1, -1, 2, -2, 3, -3):
        candidate = page + delta
        if candidate in page_map:
            return page_map[candidate], f"fuzzy_{delta:+d}"
    return None, "missing"


def section_files(source_root: Path, start_seq: int, end_seq: int) -> list[Path]:
    return [p for p in discover_text_files(source_root) if start_seq <= file_seq(p) <= end_seq]


def cleanup_fragment(fragment: str) -> str:
    value = normalize(fragment).strip(" \t\r\n")
    value = value.strip(" .;:")
    value = re.sub(r"\s+", " ", value)
    return value


def split_fragments_from_text(text: str) -> list[str]:
    if "Abba Cyrus" in text:
        text = text[text.index("Abba Cyrus") :]
    parts = [text]
    for splitter in (ENTRY_SPLIT_RE, SECONDARY_SPLIT_RE):
        new_parts: list[str] = []
        for part in parts:
            new_parts.extend(splitter.split(part))
        parts = new_parts
    fragments: list[str] = []
    for part in parts:
        frag = cleanup_fragment(part)
        if not frag:
            continue
        if LETTER_ONLY_RE.fullmatch(frag):
            continue
        if ROMAN_ONLY_RE.fullmatch(frag):
            continue
        if TITLE_RE.fullmatch(frag):
            continue
        fragments.append(frag)
    return fragments


def lemma_from_fragment(fragment: str) -> str | None:
    if not re.search(r"\d", fragment) and not re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.|ibid\.)\b", fragment, re.I):
        return None
    match = PAGE_LOC_RE.search(fragment)
    if match:
        lemma = cleanup_fragment(fragment[: match.start()])
    else:
        lemma = cleanup_fragment(fragment)
    lemma = lemma.lstrip("+").strip()
    lemma = re.sub(r"\bnum\.?$", "", lemma, flags=re.IGNORECASE).strip(" ,;:.")
    if not lemma:
        return None
    return lemma


def entry_kind_from_fragment(fragment: str) -> str:
    if re.match(r"^(?:vide|vid\.|voir|cf\.|id\.|ibid\.)\b", fragment, re.I):
        return "cross_reference"
    if not re.search(r"\d", fragment) and re.search(r"\b(?:vide|vid\.|voir|cf\.|id\.|ibid\.)\b", fragment, re.I):
        return "cross_reference"
    return "lemma"


def extract_locs(fragment: str) -> list[dict[str, Any]]:
    locs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in PAGE_LOC_RE.finditer(fragment):
        start = int(match.group("start"))
        end = match.group("end")
        key = (start, int(end) if end else None)
        if key in seen:
            continue
        seen.add(key)
        locs.append(
            {
                "raw": match.group(0).strip(" .;:,"),
                "start": start,
                "end": int(end) if end else None,
            }
        )
    return locs


def build_alpha_section(page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    files = section_files(SOURCE_ROOT, ALPHA_START_SEQ, ALPHA_END_SEQ)
    text = " ".join(normalize(line) for path in files for line in parse_lines(path))
    fragments = split_fragments_from_text(text)

    section = {
        "section_key": ALPHA_SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX IN VITAS PONTIFICUM ROMANORUM.",
        "heading_norm": "index in vitas pontificum romanorum",
        "heading_letter": None,
        "page_start": 1427,
        "page_end": 1466,
        "file_start": str(files[0]) if files else None,
        "file_end": str(files[-1]) if files else None,
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Repeated INDEX IN VITAS PONTIFICUM ROMANORUM headers across the OCR tail identify the alphabetical index.",
            "files": [str(p) for p in files],
        },
    }

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files = [str(p) for p in files]

    for idx, fragment in enumerate(fragments, start=1):
        lemma = lemma_from_fragment(fragment)
        if not lemma:
            continue
        if lemma == "INDEX IN VITAS PONTIFICUM ROMANORUM":
            continue
        if not re.match(r"^[A-ZÆŒ]", lemma):
            continue
        entry_kind = entry_kind_from_fragment(fragment)
        locs = extract_locs(fragment)
        target_file_best = None
        target_source = "missing"
        inferred_page = locs[0]["start"] if locs else None
        if locs:
            target_file_best, target_source = lookup_target(locs[0]["start"], page_map)
        entry_key = f"{VOLUME_ID}:entry:{idx:05d}"
        source_file = evidence_files[0] if evidence_files else str(SOURCE_ROOT)
        entry = {
            "entry_key": entry_key,
            "section_key": ALPHA_SECTION_KEY,
            "parent_node_key": None,
            "entry_order": idx,
            "entry_kind": entry_kind,
            "lemma_raw": None if entry_kind == "cross_reference" else lemma,
            "lemma_display": None if entry_kind == "cross_reference" else lemma,
            "lemma_norm": None if entry_kind == "cross_reference" else norm_sort(lemma),
            "lemma_sort": None if entry_kind == "cross_reference" else norm_sort(lemma),
            "entry_raw": fragment,
            "context_raw": fragment,
            "heading_letter": lemma[:1].upper(),
            "inferred_printed_page": inferred_page,
            "section_start_file": str(files[0]) if files else None,
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.84 if locs else 0.68,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "alphabetical_general",
                "fragment_reason": "split_after_page_ref_boundary",
                "page_locs": locs,
                "target_resolution": target_source,
            },
        }
        entries.append(entry)
        if locs:
            for ref_order, loc in enumerate(locs, start=1):
                target_file, target_source_ref = lookup_target(loc["start"], page_map)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_range" if loc["end"] is not None else "editorial_page",
                        "ref_raw": loc["raw"],
                        "page_ref_raw": loc["raw"],
                        "page_ref_int": loc["start"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(loc["start"]) if loc["end"] is not None else None,
                        "range_end_raw": str(loc["end"]) if loc["end"] is not None else None,
                        "target_file": target_file,
                        "target_file_probability": 1.0 if target_source_ref == "exact" else 0.85 if target_file else None,
                        "section_start_file": str(files[0]) if files else None,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.96 if target_file else 0.68,
                        "raw_json": {
                            "source_file": source_file,
                            "target_resolution": target_source_ref,
                        },
                    }
                )
        if len(helper_entries) < 6:
            page_hint_ints = [loc["start"] for loc in locs[:3]]
            helper_entries.append(
                {
                    "entry_id": f"pl128_alpha_{idx:04d}",
                    "lemma_raw": lemma,
                    "query_names": [lemma],
                    "page_hints": [str(v) for v in page_hint_ints],
                    "page_hint_ints": page_hint_ints,
                    "context_raw": fragment,
                }
            )
    return section, entries, refs, helper_entries, evidence_files


def build_ordo_section(page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    files = section_files(SOURCE_ROOT, ORDO_SEQ, ORDO_SEQ)
    if not files:
        return (
            {
                "section_key": ORDO_SECTION_KEY,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 2,
                "section_kind": "ordo_rerum",
                "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": "ordo rerum quae in hoc tomo continentur",
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": None,
                "file_end": None,
                "confidence": 0.8,
                "raw_json": {"section_kind_reason": "ORDO RERUM heading not found."},
            },
            [],
            [],
            [],
            [],
        )

    ordo_text = " ".join(normalize(line) for path in files for line in parse_lines(path))
    roman_start = re.search(r"\b[IVXLCDM]{1,8}\.\s+", ordo_text)
    if roman_start:
        ordo_text = ordo_text[roman_start.start() :]
    else:
        ordo_text = ""
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files = [str(p) for p in files]

    starts = [match.start() for match in re.finditer(r"\b[IVXLCDM]{1,8}\.\s+", ordo_text)]
    if not starts:
        starts = []
    starts.append(len(ordo_text))

    for index, start in enumerate(starts[:-1]):
        end = starts[index + 1]
        item = normalize(ordo_text[start:end])
        item = re.sub(r"\s*\.+\s*(\d{1,4})$", r" \1", item)
        item = cleanup_fragment(item)
        if not item:
            continue
        locs = extract_locs(item)
        lemma = re.sub(r"^[IVXLCDM]{1,8}\.\s*", "", item)
        lemma = re.sub(r"\s+\d{1,4}$", "", lemma)
        lemma = cleanup_fragment(lemma)
        if not lemma:
            continue
        idx = len(entries) + 1
        entry_key = f"{VOLUME_ID}:ordo:{idx:04d}"
        source_file = evidence_files[0]
        target_file_best = None
        target_source = "missing"
        if locs:
            target_file_best, target_source = lookup_target(locs[0]["start"], page_map)
        entry = {
            "entry_key": entry_key,
            "section_key": ORDO_SECTION_KEY,
            "parent_node_key": None,
            "entry_order": idx,
            "entry_kind": "heading_group",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": norm_sort(lemma),
            "lemma_sort": norm_sort(lemma),
            "entry_raw": item,
            "context_raw": item,
            "heading_letter": None,
            "inferred_printed_page": locs[0]["start"] if locs else None,
            "section_start_file": str(files[0]),
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": 0.9 if locs else 0.72,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "ordo_rerum",
                "fragment_reason": "toc_line",
                "page_locs": locs,
                "target_resolution": target_source,
            },
        }
        entries.append(entry)
        if locs:
            for ref_order, loc in enumerate(locs, start=1):
                target_file, target_source_ref = lookup_target(loc["start"], page_map)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page" if loc["end"] is None else "editorial_range",
                        "ref_raw": loc["raw"],
                        "page_ref_raw": loc["raw"],
                        "page_ref_int": loc["start"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(loc["start"]) if loc["end"] is not None else None,
                        "range_end_raw": str(loc["end"]) if loc["end"] is not None else None,
                        "target_file": target_file,
                        "target_file_probability": 1.0 if target_source_ref == "exact" else 0.85 if target_file else None,
                        "section_start_file": str(files[0]),
                        "editorial_anchor_file": source_file,
                        "confidence": 0.95 if target_file else 0.68,
                        "raw_json": {
                            "source_file": source_file,
                            "target_resolution": target_source_ref,
                        },
                    }
                )
        if len(helper_entries) < 2:
            page_hints = [str(locs[0]["start"])] if locs else []
            page_hint_ints = [locs[0]["start"]] if locs else []
            helper_entries.append(
                {
                    "entry_id": f"pl128_ordo_{idx:04d}",
                    "lemma_raw": lemma,
                    "query_names": [lemma],
                    "page_hints": page_hints,
                    "page_hint_ints": page_hint_ints,
                    "context_raw": item,
                }
            )

    section = {
        "section_key": ORDO_SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.92,
        "raw_json": {
            "section_kind_reason": "Detected from the ORDO RERUM heading at the end of the OCR tail.",
            "files": [str(p) for p in files],
            "page_numbers_not_captured": True,
        },
    }
    return section, entries, refs, helper_entries, evidence_files


def build_page_summaries() -> list[dict[str, Any]]:
    pages = [
        {
            "entry_id": "pl128_alpha_abba_cyrus",
            "lemma_raw": "Abba Cyrus, sive abbas Cyrus",
            "query_names": ["Abba Cyrus", "abbas Cyrus"],
            "page_hints": ["221"],
            "page_hint_ints": [221],
            "context_raw": "Abba Cyrus, sive abbas Cyrus, num. 221.",
        },
        {
            "entry_id": "pl128_alpha_adrianus_i",
            "lemma_raw": "Adrianus I. Romanus, de regione via Lata",
            "query_names": ["Adrianus I.", "Adrianus I Romanus", "via Lata"],
            "page_hints": ["290", "292"],
            "page_hint_ints": [290, 292],
            "context_raw": "Adrianus I. Romanus, de regione via Lata 290. Creatur pontifex 292.",
        },
        {
            "entry_id": "pl128_alpha_anastasius_bibliotecarius",
            "lemma_raw": "Anastasius Bibliotecarius",
            "query_names": ["Anastasius Bibliotecarius", "Anastasius cardinalis", "Anastasius"],
            "page_hints": ["621", "650", "658"],
            "page_hint_ints": [621, 650, 658],
            "context_raw": "Anastasius Bibliotecarius in lingua Græca et Latina habetur eloquentissimus ... 621, 650, 658.",
        },
        {
            "entry_id": "pl128_ordo_sanctus_marcas",
            "lemma_raw": "XXV. Sanctus Marcas",
            "query_names": ["Sanctus Marcas", "Sanctus Marcus"],
            "page_hints": ["9"],
            "page_hint_ints": [9],
            "context_raw": "XXV. Sanctus Marcas. .................................. 9",
        },
    ]
    return pages


def run_helper_request() -> dict[str, Any]:
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": build_page_summaries(),
    }
    write_json(HELPER_REQUEST_JSON, helper_request)
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        check=True,
    )
    if HELPER_OUTPUT_JSON.exists():
        return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))
    return {}


def build_payload(source_root: Path) -> dict[str, Any]:
    page_map = build_page_map(source_root)
    alpha_section, alpha_entries, alpha_refs, alpha_helper_entries, alpha_evidence = build_alpha_section(page_map)
    ordo_section, ordo_entries, ordo_refs, ordo_helper_entries, ordo_evidence = build_ordo_section(page_map)
    helper_output = run_helper_request()

    sections = [alpha_section, ordo_section]
    entries = alpha_entries + ordo_entries
    refs = alpha_refs + ordo_refs
    evidence_files = list(dict.fromkeys(alpha_evidence + ordo_evidence))

    write_json(SECTIONS_JSON, sections)
    write_json(ENTRIES_JSON, entries)
    write_json(REFS_JSON, refs)
    write_json(PAYLOAD_JSON, {
        "sections": sections,
        "entries": entries,
        "refs": refs,
    })
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PL128 alphabetical payload after helper verification.",
            "completed": [
                "OCR tail inspected",
                "alphabetical and ordo sections segmented",
                "helper request run",
            ],
            "pending": [
                "validate final payload file",
            ],
            "blocked": [],
            "notes": [
                "Alphabetical section covers printed pages 1427-1466.",
                "ORDO RERUM is preserved as editorial closure.",
                f"Helper status: {helper_output.get('status', 'unknown')}.",
            ],
        },
    )

    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": "Patrologia Latina 128",
        },
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the PL128 alphabetical index tail and the closing ORDO RERUM section from OCR, with target files resolved from header page numbers when possible.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "The alphabetical index begins on OCR file 718 and ends before ORDO RERUM on file 738.",
            "Helper output was run for representative citations spanning early, middle, late, and ORDO entries.",
            f"Helper status: {helper_output.get('status', 'unknown')}.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(SOURCE_ROOT))
    parser.add_argument("--output-file", default=str(OUTPUT_FILE))
    args = parser.parse_args()

    payload = build_payload(Path(args.source_root))
    write_json(Path(args.output_file), payload)


if __name__ == "__main__":
    main()
