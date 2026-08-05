#!/usr/bin/env python3
"""Usage: build the PG127 alphabetical payload and helper request from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG127_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG127/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG127_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG127_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG127 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG127_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG127"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 127"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_1_FILES = list(range(750, 753))
SECTION_2_FILES = list(range(753, 759))
SECTION_1_HEADING = "INDEX IN NICEPHORUM BRYENNIUM."
SECTION_2_HEADING = "INDEX IN MANASSEM."

HEADER_PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
NOISE_RE = re.compile(r"^(?:Digitized by Google|__+|\(Revocatur Lector ad numeros grandiores textui insertos\.\)|\.+)$")
SECTION_TITLE_RE = re.compile(r"^(?:INDEX(?:\s+IN\s+(?:NICEPHORUM\s+BRYENNIUM|MANASSEM))?\.?)$", re.IGNORECASE)
TEXT_BLOCK_RE = re.compile(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', re.S)
MARGIN_BLOCK_RE = re.compile(r'<bloco tipo="nota_marginal"[^>]*>(.*?)</bloco>', re.S)
BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)


@dataclass
class SectionSpec:
    section_key: str
    section_order: int
    heading_raw: str
    page_start: int
    page_end: int
    file_start: Path
    file_end: Path
    section_kind: str = "analytic_subject"


@dataclass
class LineRecord:
    text: str
    source_files: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse sequence from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if m:
            files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files)]


def extract_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header_text = normalize(parsed.get("header_text") or "") or ""
        for match in HEADER_PAGE_RE.finditer(header_text):
            mapping.setdefault(int(match.group(1)), str(path))
    return mapping


def extract_body_lines(path: Path) -> list[str]:
    raw_text = read_text(path)
    lines: list[str] = []
    for block_type, block_text in BLOCK_RE.findall(raw_text):
        if block_type not in {"texto_principal", "nota_marginal"}:
            continue
        for raw in block_text.splitlines():
            line = normalize(raw)
            if not line:
                continue
            if NOISE_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def extract_margin_letters(path: Path) -> list[str]:
    raw_text = read_text(path)
    letters: list[str] = []
    for match in MARGIN_BLOCK_RE.finditer(raw_text):
        text = normalize(match.group(1))
        if text and is_letter_line(text):
            letters.append(text)
    return letters


def is_letter_line(text: str) -> bool:
    return bool(LETTER_RE.fullmatch(text))


def is_continuation_line(text: str) -> bool:
    if not text:
        return False
    if text[0].islower():
        return True
    if text.startswith("-") or text.startswith("—"):
        return True
    if re.match(r"^(?:[a-zæœ])", text):
        return True
    return False


def merge_line_record(left: LineRecord, right: LineRecord) -> LineRecord:
    if left.text.endswith("-"):
        merged_text = left.text[:-1] + right.text.lstrip()
    else:
        merged_text = f"{left.text} {right.text}".strip()
    source_files = list(dict.fromkeys(left.source_files + right.source_files))
    return LineRecord(text=merged_text, source_files=source_files)


def should_merge_line_records(left: LineRecord, right: LineRecord) -> bool:
    if not left.text or not right.text:
        return False
    if is_letter_line(left.text) or is_letter_line(right.text):
        return False
    if left.text.endswith("-"):
        return True
    if is_continuation_line(right.text):
        return True
    if not page_refs(left.text):
        return True
    if left.text.rstrip().endswith(",") and re.match(r"^\d", right.text):
        return True
    return False


def merge_wrapped_records(records: list[LineRecord]) -> list[LineRecord]:
    merged: list[LineRecord] = []
    for record in records:
        if not merged:
            merged.append(record)
            continue
        prev = merged[-1]
        if should_merge_line_records(prev, record):
            merged[-1] = merge_line_record(prev, record)
            continue
        merged.append(record)
    return merged


def extract_section_records(path: Path, *, section_heading: str | None = None) -> list[LineRecord]:
    cleaned: list[LineRecord] = []
    collecting = section_heading is None
    found_heading = False
    heading_needle = None
    if section_heading:
        heading_needle = re.sub(r"^INDEX\s+", "", section_heading, flags=re.IGNORECASE).strip(" .")
    for block_type, block_text in BLOCK_RE.findall(read_text(path)):
        if block_type not in {"cabecalho", "texto_principal"}:
            continue
        block_lines = [normalize(raw) for raw in block_text.splitlines()]
        block_lines = [line for line in block_lines if line and not NOISE_RE.fullmatch(line)]
        block_contains_heading = bool(heading_needle and any(heading_needle.lower() in line.lower() for line in block_lines))
        if block_type == "cabecalho" and not block_contains_heading:
            continue
        if section_heading and not collecting:
            if block_contains_heading:
                collecting = True
                found_heading = True
            continue
        if block_contains_heading:
            found_heading = True
        for line in block_lines:
            if SECTION_TITLE_RE.fullmatch(line):
                continue
            if line.startswith("INDEX") and " " not in line.replace(".", ""):
                continue
            cleaned.append(LineRecord(text=line, source_files=[str(path)]))
    if section_heading and not found_heading:
        cleaned = []
        for block_type, block_text in BLOCK_RE.findall(read_text(path)):
            if block_type not in {"cabecalho", "texto_principal"}:
                continue
            for raw in block_text.splitlines():
                line = normalize(raw)
                if not line or NOISE_RE.fullmatch(line):
                    continue
                if SECTION_TITLE_RE.fullmatch(line):
                    continue
                if line.startswith("INDEX") and " " not in line.replace(".", ""):
                    continue
                cleaned.append(LineRecord(text=line, source_files=[str(path)]))
    return merge_wrapped_records(cleaned)


def collect_section_records(section_files: list[Path], section_heading: str) -> list[LineRecord]:
    records: list[LineRecord] = []
    for idx, path in enumerate(section_files):
        records.extend(
            extract_section_records(
                path,
                section_heading=section_heading if idx == 0 else None,
            )
        )
    return merge_wrapped_records(records)


def page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        start = match.group(1)
        end = match.group(2)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_kind": "editorial_range" if end else "editorial_page",
                "ref_raw": match.group(0).strip(),
                "page_ref_raw": match.group(0).strip(),
                "page_ref_int": int(start),
                "range_start_raw": start if end else None,
                "range_end_raw": end if end else None,
            }
        )
    return refs


def lemma_from_entry(text: str) -> str | None:
    value = normalize(text) or ""
    if not value:
        return None
    if re.match(r"^(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", value, re.IGNORECASE):
        return None
    m = re.search(r"\s+\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*,\s*\d{1,4})*(?:\s*\.|\s*$)", value)
    if m:
        prefix = value[: m.start()].strip(" ,;:")
    else:
        prefix = value.strip(" ,;:")
    return prefix or None


def entry_kind(text: str) -> str:
    if re.match(r"^(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", text, re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def initial_letter(text: str | None) -> str | None:
    value = normalize(text) or ""
    if not value:
        return None
    for ch in value:
        if ch.isalpha():
            return ch.upper()
    return None


def section_page_start_end(files: list[Path]) -> tuple[int, int]:
    pages: list[int] = []
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header_text = normalize(parsed.get("header_text") or "") or ""
        matches = [int(match.group(1)) for match in HEADER_PAGE_RE.finditer(header_text)]
        if matches:
            pages.append(matches[-1])
    if not pages:
        return (None, None)  # type: ignore[return-value]
    return (min(pages), max(pages))


def build_sections(section_specs: list[SectionSpec]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for spec in section_specs:
        sections.append(
            {
                "section_key": spec.section_key,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": spec.section_order,
                "section_kind": spec.section_kind,
                "heading_raw": spec.heading_raw,
                "heading_norm": sort_norm(spec.heading_raw),
                "heading_letter": None,
                "page_start": spec.page_start,
                "page_end": spec.page_end,
                "file_start": str(spec.file_start),
                "file_end": str(spec.file_end),
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": (
                        "Alphabetical lemma index with letter-group dividers and subject/person headings."
                    ),
                    "source_files": [str(spec.file_start), str(spec.file_end)],
                },
            }
        )
    return sections


def build_nodes_and_entries(
    section_key: str,
    section_files: list[Path],
    page_to_file: dict[int, str],
    start_entry_order: int,
    start_node_order: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    node_lookup: dict[str, str] = {}
    node_order = start_node_order
    entry_order = start_entry_order
    current_letter: str | None = None

    def ensure_node(letter: str) -> str:
        nonlocal node_order
        if letter in node_lookup:
            return node_lookup[letter]
        node_order += 1
        node_key = f"{VOLUME_ID}:{section_key}:node:{node_order:03d}"
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
                "confidence": 0.99,
                "raw_json": {"role": "alphabetic divider"},
            }
        )
        node_lookup[letter] = node_key
        return node_key

    section_heading = SECTION_1_HEADING if section_key.endswith(":001") else SECTION_2_HEADING
    section_records = collect_section_records(section_files, section_heading)
    for record in section_records:
        text = normalize(record.text) or ""
        if not text:
            continue
        if is_letter_line(text):
            current_letter = text
            ensure_node(text)
            continue
        if text in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
            current_letter = text
            ensure_node(text)
            continue
        lemma_raw = lemma_from_entry(text)
        ref_list = page_refs(text)
        if current_letter is None:
            current_letter = initial_letter(lemma_raw or text)
        parent_node_key = ensure_node(current_letter) if current_letter else None
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:05d}"
        inferred_page = ref_list[0]["page_ref_int"] if ref_list else None
        target_file_best = page_to_file.get(inferred_page)
        if target_file_best is None and inferred_page is not None and page_to_file:
            nearest_page = min(page_to_file, key=lambda candidate: abs(candidate - inferred_page))
            target_file_best = page_to_file[nearest_page]
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": parent_node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind(text),
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": text,
                "context_raw": None,
                "heading_letter": current_letter,
                "inferred_printed_page": inferred_page,
                "section_start_file": str(section_files[0]),
                "editorial_anchor_file": target_file_best,
                "target_file_best": target_file_best,
                "confidence": 0.82 if ref_list else 0.72,
                "raw_json": {
                    "source_file": record.source_files[0],
                    "source_files": record.source_files,
                    "page_ref_count": len(ref_list),
                },
            }
        )
        for idx, ref in enumerate(ref_list, start=1):
            ref_file = None
            if ref["page_ref_int"] in page_to_file:
                ref_file = page_to_file[ref["page_ref_int"]]
            elif page_to_file:
                nearest_page = min(page_to_file, key=lambda candidate: abs(candidate - ref["page_ref_int"]))
                ref_file = page_to_file[nearest_page]
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": idx,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": ref_file,
                    "target_file_probability": 0.9 if ref_file else None,
                    "section_start_file": str(section_files[0]),
                    "editorial_anchor_file": ref_file,
                    "confidence": 0.82,
                    "raw_json": {
                        "source_file": record.source_files[0],
                        "source_files": record.source_files,
                    },
                }
            )
    return nodes, entries, refs, node_order, entry_order


def build_helper_request(
    *,
    source_root: Path,
    section_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": section_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    if helper_output_json.exists():
        return json.loads(helper_output_json.read_text(encoding="utf-8"))
    return {}


def build_payload(
    *,
    source_root: Path,
    helper_output: dict[str, Any],
) -> dict[str, Any]:
    all_files = discover_files(source_root)
    page_to_file = extract_page_map(all_files)
    section_1_files = [source_root / f"c1597e02-e31f-470d-a1be-12f490c436ee-{seq}.txt" for seq in SECTION_1_FILES]
    section_2_files = [source_root / f"c1597e02-e31f-470d-a1be-12f490c436ee-{seq}.txt" for seq in SECTION_2_FILES]
    sec1_start, sec1_end = section_page_start_end(section_1_files)
    sec2_start, sec2_end = section_page_start_end(section_2_files)
    section_specs = [
        SectionSpec(
            section_key=f"{VOLUME_ID}:alpha:analytic_subject:001",
            section_order=1,
            heading_raw=SECTION_1_HEADING,
            page_start=sec1_start or 1488,
            page_end=sec1_end or 1492,
            file_start=section_1_files[0],
            file_end=section_1_files[-1],
        ),
        SectionSpec(
            section_key=f"{VOLUME_ID}:alpha:analytic_subject:002",
            section_order=2,
            heading_raw=SECTION_2_HEADING,
            page_start=sec2_start or 1491,
            page_end=sec2_end or 1504,
            file_start=section_2_files[0],
            file_end=section_2_files[-1],
        ),
    ]
    sections = build_sections(section_specs)

    # Helper request is based on the leading lines of each logical entry.
    helper_entries: list[dict[str, Any]] = []
    entry_counter = 0
    for section_files in (section_1_files, section_2_files):
        section_heading = SECTION_1_HEADING if section_files is section_1_files else SECTION_2_HEADING
        section_records = collect_section_records(section_files, section_heading)
        for record in section_records:
            text = normalize(record.text) or ""
            if not text or is_letter_line(text):
                continue
            if SECTION_TITLE_RE.fullmatch(text):
                continue
            entry_counter += 1
            ref_list = page_refs(text)
            page_hints = [str(ref["page_ref_int"]) for ref in ref_list[:3]]
            helper_entries.append(
                {
                    "entry_id": f"{VOLUME_ID}_entry_{entry_counter:04d}",
                    "lemma_raw": lemma_from_entry(text) or text[:80],
                    "query_names": [lemma_from_entry(text) or text[:80], text[:120]],
                    "page_hints": page_hints,
                    "page_hint_ints": [ref["page_ref_int"] for ref in ref_list[:3]],
                    "context_raw": text,
                }
            )

    helper_request = build_helper_request(source_root=source_root, section_entries=helper_entries)
    section_1_nodes, section_1_entries, section_1_refs, node_order, entry_order = build_nodes_and_entries(
        section_key=section_specs[0].section_key,
        section_files=section_1_files,
        page_to_file=page_to_file,
        start_entry_order=0,
        start_node_order=0,
    )
    section_2_nodes, section_2_entries, section_2_refs, node_order, entry_order = build_nodes_and_entries(
        section_key=section_specs[1].section_key,
        section_files=section_2_files,
        page_to_file=page_to_file,
        start_entry_order=entry_order,
        start_node_order=node_order,
    )

    nodes = section_1_nodes + section_2_nodes
    entries = section_1_entries + section_2_entries
    refs = section_1_refs + section_2_refs

    notes = [
        "Extracted from the two true index sections: INDEX IN NICEPHORUM BRYENNIUM and INDEX IN MANASSEM.",
        "The final ORDO RERUM / contents block at files 759-762 was inspected but excluded as editorial closure, not an alphabetical index section.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": notes,
    }

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "The OCR contains two recoverable alphabetical index sections with usable lemma lines and material page references.",
        "evidence_files": [str(path) for path in section_1_files + section_2_files],
    }

    payload = {
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

    # Keep a compact helper summary in the section metadata to preserve the run's validation evidence.
    if helper_output:
        summary = {
            "status": helper_output.get("status"),
            "candidate_role": helper_output.get("candidate_role"),
            "reason_summary": helper_output.get("reason_summary"),
            "top_candidates": helper_output.get("candidates", [])[:3],
        }
        for section in payload["sections"]:
            section["raw_json"]["helper_summary"] = summary

    return payload, helper_request


def write_intermediate(intermediate_dir: Path, payload: dict[str, Any], helper_request: dict[str, Any], helper_output: dict[str, Any]) -> None:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    volume = payload["volume"]
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(
        intermediate_dir / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "source_root": volume["source_root"],
            "helper_request_json": str(intermediate_dir.parent.parent / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_request.json"),
            "helper_output_json": str(intermediate_dir.parent.parent / "alphabetical_index_payloads" / f"{VOLUME_ID}_helper_output.json"),
        },
    )
    write_json(intermediate_dir / "todo.json", {
        "volume_id": VOLUME_ID,
        "updated_at": payload["generated_at"],
        "current_focus": "PG127 alphabetical index payload assembled",
        "completed": [
            "two alphabetical sections recovered",
            "helper request generated",
            "refs serialized from printed page numbers",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Keep the final ORDO RERUM block out of the alphabetical index payload.",
        ],
    })


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG127 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    all_files = discover_files(args.source_root)
    page_to_file = extract_page_map(all_files)
    # Build payload once so the helper request can be created from the same segmentation.
    payload, helper_request = build_payload(source_root=args.source_root, helper_output={})
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    # Rebuild with helper summary attached.
    payload, helper_request = build_payload(source_root=args.source_root, helper_output=helper_output)
    write_json(args.helper_request_json, helper_request)
    write_json(args.output_file, payload)
    write_intermediate(args.intermediate_dir, payload, helper_request, helper_output)


if __name__ == "__main__":
    main()
