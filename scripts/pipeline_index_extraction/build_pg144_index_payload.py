#!/usr/bin/env python3
"""Build the PG144 alphabetical-index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg144_index_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG144"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 144"
SOURCE_ROOT = ROOT / "teste/PG144/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG144_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG144_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG144_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG144"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"


SECTION_KEY = f"{VOLUME_ID}:alpha:alphabetical_general:001"
SECTION_HEADING_RAW = "INDEX IN PACHYMERÆ HISTORIAM."
SECTION_HEADING_NORM = "index in pachymerae historiam"

SECTION_FILES = [SOURCE_ROOT / f"9d550232-f738-4e58-8a87-fda0b881d146-{i}.txt" for i in range(707, 720)]

NOISE_LINES = {
    "Digitized by Google",
}

TITLE_SKIP_RE = re.compile(
    r"^(?:INDEX(?: IN PACHYMER(?:Æ|AE)? HISTORIAM)?\.?|"
    r"Numeri Romani priorem et posteriorem Historiæ partem indicant, Arabici cifræ grandiores quæ in textu Latino impressæ sunt\. Gentem Palæologinam universam quære sub titulo PALÆOLOGI\.|"
    r"ORDO RERUM|QUÆ IN HOC TOMO CONTINENTUR\.|GEORGIUS PACHYMERES\.|"
    r"Petrus Possimus Lectori\.|GEORGII PACHYMERAE ANDRONICUS PALÆOLO-"
    r"GUS, sive Historia rerum ab Andronico Palæologo seniore"
    r")$",
    re.IGNORECASE,
)

PAGE_REF_RE = re.compile(r"\b([IVX]+)\s*[,\.]\s*([0-9]{1,4}(?:\s*[,\.]\s*[0-9]{1,4})*)")
DIVIDER_RE = re.compile(r"^[A-ZÆŒ]$")
ROMAN_NUM_RE = re.compile(r"\b[IVX]+\b")


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    value = value.strip(" ,;:")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for key in ("header_text", "body_text", "footer_text"):
        text = parsed.get(key) or ""
        for raw_line in text.splitlines():
            line = normalize(raw_line)
            if not line or line in NOISE_LINES:
                continue
            lines.append(line)
    return lines


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    return int(match.group(1)) if match else -1


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
            token = match.group(1)
            if token.startswith("0"):
                continue
            page_map.setdefault(int(token), path.as_posix())
    return page_map


def parse_page_refs(text: str) -> list[tuple[str, int]]:
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in PAGE_REF_RE.finditer(text):
        roman = match.group(1)
        nums = re.split(r"\s*[,\.]\s*", match.group(2).strip())
        for num in nums:
            if not num.isdigit():
                continue
            item = (roman, int(num))
            if item in seen:
                continue
            seen.add(item)
            refs.append(item)
    return refs


def split_on_lemmata(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_letter = None
    line_index = 0

    def flush() -> None:
        nonlocal current
        if not current:
            return
        entry_raw = " ".join(current["parts"])
        entry_raw = re.sub(r"\s+", " ", entry_raw).strip()
        if not entry_raw:
            current = None
            return
        page_refs = parse_page_refs(entry_raw)
        first_roman = page_refs[0][0] if page_refs else None
        first_page = page_refs[0][1] if page_refs else None
        page_hints = [page for _, page in page_refs[:3]]
        split_point = None
        if page_refs:
            first_match = PAGE_REF_RE.search(entry_raw)
            if first_match:
                split_point = first_match.start()
        lemma_raw = normalize(entry_raw[:split_point].rstrip(" ,;:.") if split_point is not None else entry_raw)
        if lemma_raw and " V. " in lemma_raw:
            lemma_raw = normalize(lemma_raw.split(" V. ", 1)[0])
        entry_kind = "cross_reference" if page_refs == [] and re.search(r"\b(?:vid\.?|vide|voir|cf\.?|id\.?|V\.)\b", entry_raw, re.IGNORECASE) else "lemma"
        if page_refs == [] and DIVIDER_RE.fullmatch(entry_raw):
            entry_kind = "heading_group"
        entry = {
            "entry_raw": entry_raw,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_kind": entry_kind,
            "heading_letter": current_letter,
            "page_refs": [
                {
                    "ref_raw": f"{roman}, {page}",
                    "roman": roman,
                    "page": page,
                }
                for roman, page in page_refs
            ],
            "inferred_printed_page": first_page,
            "page_hints": page_hints,
            "first_roman": first_roman,
            "line_index": line_index,
        }
        entries.append(entry)
        current = None

    started = False
    for raw_line in lines:
        line_index += 1
        if DIVIDER_RE.fullmatch(raw_line):
            flush()
            current_letter = raw_line
            entries.append(
                {
                    "entry_raw": raw_line,
                    "lemma_raw": raw_line,
                    "lemma_display": raw_line,
                    "lemma_norm": raw_line.lower(),
                    "lemma_sort": raw_line.lower(),
                    "entry_kind": "heading_group",
                    "heading_letter": raw_line,
                    "page_refs": [],
                    "inferred_printed_page": None,
                    "page_hints": [],
                    "first_roman": None,
                    "line_index": line_index,
                }
            )
            started = True
            continue
        if not started:
            if raw_line.startswith("A ") or raw_line.startswith("Abbas ") or raw_line.startswith("Abdicatio "):
                started = True
            else:
                continue
        if TITLE_SKIP_RE.match(raw_line):
            continue
        if current is None:
            current = {"parts": [raw_line]}
            continue
        if parse_page_refs(raw_line):
            flush()
            current = {"parts": [raw_line]}
        else:
            current["parts"].append(raw_line)
    flush()
    return entries


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        page_hints = entry.get("page_hints") or []
        if not page_hints:
            continue
        lemma_raw = entry.get("lemma_raw") or entry["entry_raw"][:80]
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{idx:04d}",
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw, entry["entry_raw"].split(",", 1)[0]],
                "page_hints": [str(page_hints[0])],
                "page_hint_ints": [page_hints[0]],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": SOURCE_ROOT.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        check=True,
        cwd=str(ROOT),
    )
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def page_to_file(page: int, page_map: dict[int, str]) -> str | None:
    return page_map.get(page)


def build_payload() -> dict[str, Any]:
    page_map = build_page_map(SECTION_FILES)
    parsed_entries = split_on_lemmata([line for path in SECTION_FILES for line in extract_lines(path)])
    helper_request = build_helper_request(parsed_entries)
    write_json(HELPER_REQUEST_JSON, helper_request)
    helper_output = run_helper(HELPER_REQUEST_JSON, HELPER_OUTPUT_JSON) if helper_request["entries"] else {"entries": []}
    helper_by_id = {entry.get("entry_id"): entry for entry in helper_output.get("entries", [])}

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    letters_seen: set[str] = set()
    entry_order = 0
    section_start_file = SECTION_FILES[0].as_posix()
    section_end_file = SECTION_FILES[-1].as_posix()

    for idx, item in enumerate(parsed_entries, start=1):
        if item["entry_kind"] == "heading_group" and item["entry_raw"] in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Y", "Z"}:
            if item["entry_raw"] not in letters_seen:
                letters_seen.add(item["entry_raw"])
                nodes.append(
                    {
                        "node_key": f"{VOLUME_ID}:node:letter:{item['entry_raw']}",
                        "section_key": SECTION_KEY,
                        "parent_node_key": None,
                        "node_order": len(nodes) + 1,
                        "node_kind": "letter_group",
                        "label_raw": item["entry_raw"],
                        "label_norm": item["entry_raw"].lower(),
                        "label_sort": item["entry_raw"].lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source": item["entry_raw"]},
                    }
                )
            continue
        if item["entry_kind"] == "heading_group" and not item["page_refs"]:
            # Skip title/intro lines.
            continue
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        first_page = item["inferred_printed_page"]
        helper_id = f"{VOLUME_ID.lower()}_{idx:04d}"
        helper_entry = helper_by_id.get(helper_id)
        best_candidate = helper_entry.get("best_candidate") if helper_entry else None
        target_file_best = page_to_file(first_page, page_map) if first_page is not None else None
        if best_candidate and best_candidate.get("file"):
            target_file_best = best_candidate.get("file")
        confidence = 0.93 if first_page is not None else 0.72
        if helper_entry and helper_entry.get("status") == "ambiguous":
            confidence = 0.8
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": item["entry_kind"],
            "lemma_raw": item["lemma_raw"],
            "lemma_display": item["lemma_display"],
            "lemma_norm": item["lemma_norm"],
            "lemma_sort": item["lemma_sort"],
            "entry_raw": item["entry_raw"],
            "context_raw": item["entry_raw"],
            "heading_letter": item["heading_letter"] or (item["lemma_raw"][0].upper() if item["lemma_raw"] else None),
            "inferred_printed_page": first_page,
            "section_start_file": section_start_file,
            "editorial_anchor_file": section_start_file,
            "target_file_best": target_file_best,
            "confidence": confidence,
            "raw_json": {
                "source_file": None,
                "page_hints": item["page_hints"],
                "first_roman": item["first_roman"],
                "helper": helper_entry,
            },
        }
        entries.append(entry)

        for ref_order, ref in enumerate(item["page_refs"], start=1):
            target_file = page_to_file(ref["page"], page_map)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": str(ref["page"]),
                    "page_ref_int": ref["page"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 0.95 if target_file else None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": section_start_file,
                    "confidence": 0.92 if target_file else 0.7,
                    "raw_json": {
                        "roman": ref["roman"],
                        "helper": helper_entry,
                    },
                }
            )

    section_pages = [item["inferred_printed_page"] for item in parsed_entries if item["inferred_printed_page"] is not None]
    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": "Georgius Pachymeres, Historia",
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": min(section_pages) if section_pages else None,
        "page_end": max(section_pages) if section_pages else None,
        "file_start": section_start_file,
        "file_end": section_end_file,
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Alphabetical index of names, places, and concepts for Pachymeres; the OCR tail begins with a title page at file 707 and continues through the Z entries before ORDO RERUM starts in file 720.",
            "evidence_files": [p.as_posix() for p in SECTION_FILES],
            "helper_request": HELPER_REQUEST_JSON.as_posix(),
        },
    }

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the alphabetical index tail from files 707-719; file 720 begins the separate ORDO RERUM contents table.",
        "evidence_files": [p.as_posix() for p in SECTION_FILES],
    }
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": SOURCE_ROOT.as_posix(),
        "volume_label": VOLUME_LABEL,
    }
    notes = [
        "The section opens with INDEX IN PACHYMERÆ HISTORIAM on file 707 and runs through Z before ORDO RERUM begins in file 720.",
        "OCR line segmentation is conservative; long multi-clause entries were preserved as single logical entries when they remained in one OCR run-on line.",
        "Bare remissions without a locator were not promoted to refs.",
    ]

    manifest = {
        "volume_id": VOLUME_ID,
        "updated_at": now_utc(),
        "generated_at": now_utc(),
    }
    write_json(INTERMEDIATE_DIR / "manifest.json", manifest)
    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", [section])
    write_json(INTERMEDIATE_DIR / "nodes.json", nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", [])
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_utc(),
            "current_focus": "Validate PG144 alphabetical index payload and close out the volume.",
            "completed": [
                "section located",
                "index pages 707-719 parsed",
                "helper request generated",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "File 720 is a separate ORDO RERUM contents table and not part of the alphabetical index.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    payload = build_payload()
    write_json(OUTPUT_FILE, payload)


if __name__ == "__main__":
    main()
