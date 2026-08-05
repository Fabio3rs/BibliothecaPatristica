#!/usr/bin/env python3
"""Usage: build the PG058 alphabetical payload from the OCR index tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/pg058_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG058/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG058_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG058_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG058 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG058_alphabetical_indices.json
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
VOLUME_ID = "PG058"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 58"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_FILES = [417, 418, 419, 420, 421, 422]
ORDO_FILES = [464, 465, 466, 467]

HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(
    r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}|[IVXLCDM]+))?(?!\d)",
    re.IGNORECASE,
)
LETTER_RE = re.compile(r"^(?:[A-ZÆŒ]|[ΨΩΨΩΖ])$")
NOISE_RE = re.compile(
    r"^(?:Digitized by Google|INDEX RERUM MEMORABILIUM\.?|ORDO RERUM\.?|ORDO RERUM|"
    r"ANIANI INTERPRETATIO\.?|Indices\.\s*975|FINIS TOMI QUINQUAGESIMI OCTAVI\.?|"
    r"Parisiis\. — Ex typis L\. MIGNE\.?)$",
    re.IGNORECASE,
)
INDEX_TITLE_RE = re.compile(r"^INDEX RERUM MEMORABILIUM\.?$", re.IGNORECASE)
ORDO_TITLE_RE = re.compile(r"^ORDO RERUM\.?$", re.IGNORECASE)


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


def discover_files(source_root: Path) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", path.name)
        if not m:
            continue
        files.append((int(m.group(1)), path))
    return [path for _, path in sorted(files)]


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file seq from {path}")
    return int(m.group(1))


def page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(read_text(path))
        header = normalize(parsed.get("header_text") or "")
        if not header:
            continue
        for match in HEADER_RE.finditer(header):
            page = int(match.group(1))
            if page >= 1:
                mapping.setdefault(page, str(path))
    return mapping


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(read_text(path))
    text = parsed.get("all_text") or parsed.get("body_text") or ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = normalize(raw)
        if not line:
            continue
        if NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def split_fragments(line: str) -> list[str]:
    text = re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[\.\;\]])\s+(?=[A-ZÆŒΑ-Ω])", text) if part.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and re.fullmatch(r"(?:[IVXLCDM]+|[A-ZÆŒ])\.\s+\d{1,4}(?:\s*[-–—]\s*(?:\d{1,4}|[IVXLCDM]+))?", part):
            merged[-1] = f"{merged[-1]} {part}"
            continue
        merged.append(part)
    return merged


def is_letter_marker(text: str) -> bool:
    return bool(LETTER_RE.fullmatch(text))


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
        page_ref_int = int(start)
        is_range = end is not None
        refs.append(
            {
                "ref_kind": "editorial_range" if is_range else "editorial_page",
                "ref_raw": match.group(0).strip(),
                "page_ref_raw": match.group(0).strip(),
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": start if is_range else None,
                "range_end_raw": end if is_range else None,
            }
        )
    return refs


def best_target_for_page(page: int | None, page_to_file: dict[int, str]) -> str | None:
    if page is None:
        return None
    if page in page_to_file:
        return page_to_file[page]
    if not page_to_file:
        return None
    nearest = min(page_to_file, key=lambda candidate: abs(candidate - page))
    return page_to_file[nearest]


def make_section(
    *,
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    page_start: int,
    page_end: int,
    file_start: Path,
    file_end: Path,
    confidence: float,
    helper_output: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_json: dict[str, Any] = {
        "section_kind_reason": (
            "Alphabetical subject index headed INDEX RERUM MEMORABILIUM with letter-group dividers and page-locator entries."
            if section_kind == "analytic_subject"
            else "Editorial contents table headed ORDO RERUM with homily headings and page ranges."
        ),
        "source_files": [str(file_start), str(file_end)],
    }
    if helper_output is not None:
        raw_json["helper_output"] = helper_output
    return {
        "section_key": section_key,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": normalize(heading_raw),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": str(file_start),
        "file_end": str(file_end),
        "confidence": confidence,
        "raw_json": raw_json,
    }


def build_helper_request(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [
            {
                "entry_id": "PG058_index_rerum_memorabilium",
                "lemma_raw": "INDEX RERUM MEMORABILIUM",
                "query_names": [
                    "INDEX RERUM MEMORABILIUM",
                    "INDEX RERUM",
                    "memorabilium",
                ],
                "page_hints": ["963", "974"],
                "page_hint_ints": [963, 974],
                "context_raw": "963 INDEX RERUM MEMORABILIUM. 964 ... 973 INDEX RERUM MEMORABILIUM. 974",
            },
            {
                "entry_id": "PG058_ordo_rerum",
                "lemma_raw": "ORDO RERUM",
                "query_names": [
                    "ORDO RERUM",
                    "ORDO RERUM QUÆ IN DUABUS PARTIBUS TOMI VII CONTINENTUR",
                    "HOMILIA I",
                ],
                "page_hints": ["1057", "1061", "1063"],
                "page_hint_ints": [1057, 1061, 1063],
                "context_raw": "1057 ORDO RERUM 1058 ... 1063 ORDO RERUM. 1064",
            },
            {
                "entry_id": "PG058_suffitus_in_ecclesia",
                "lemma_raw": "Suffitus in Ecclesia",
                "query_names": [
                    "Suffitus in Ecclesia",
                    "Ecclesiæ ornatui non nimis studendum",
                    "Colloquia in Ecclesia",
                ],
                "page_hints": ["830"],
                "page_hint_ints": [830],
                "context_raw": "Suffitus in Ecclesia 830 pr. f. Ecclesiæ ornatui non nimis studendum 518 init.",
            },
            {
                "entry_id": "PG058_homilia_xxiv",
                "lemma_raw": "HOM. XXIV ex eodem capite.",
                "query_names": [
                    "HOM. XXIV ex eodem capite",
                    "Non alia Filii, alia Patris voluntas",
                    "321-328",
                ],
                "page_hints": ["321", "328"],
                "page_hint_ints": [321, 328],
                "context_raw": "HOM. XXIV ex eodem capite. — Non alia Filii, alia Patris voluntas; signorum operatio sine virtute nihil prodest operanti. 321-328",
            },
        ],
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
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    if helper_output_json.exists():
        return json.loads(helper_output_json.read_text(encoding="utf-8"))
    return {}


def line_to_entry_text(line: str, section_kind: str) -> list[str]:
    if section_kind == "ordo_rerum":
        return [line]
    fragments = split_fragments(line)
    return fragments or [line]


def lemma_from_fragment(fragment: str) -> str | None:
    text = normalize(fragment) or ""
    if not text:
        return None
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)\b", text, re.IGNORECASE):
        return None
    cut_points = [
        m.start()
        for m in re.finditer(r"(?<!\b[Vv]id)(?<!\b[Vv]ide)(?<!\b[Vv]oir)(?<!\b[Cc]f)(?<!\b[Ii]d)\.", text)
    ]
    if cut_points:
        first = cut_points[0]
        prefix = text[:first].strip()
    else:
        prefix = text
    prefix = re.sub(r"\s+(?:\d|[IVXLCDM])[\dIVXLCDM\-\s,;:.\*]*$", "", prefix).strip(" ,;:")
    return prefix or None


def entry_kind_for(section_kind: str, fragment: str) -> str:
    if section_kind == "ordo_rerum":
        return "heading_group"
    text = normalize(fragment) or ""
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)\b", text, re.IGNORECASE):
        return "cross_reference"
    return "lemma"


def build_entries_for_section(
    *,
    section_kind: str,
    section_key: str,
    section_files: list[Path],
    page_to_file: dict[int, str],
    helper_output: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    node_lookup: dict[str, str] = {}
    node_order = 0
    entry_order = 0
    helper_summary = None
    if helper_output:
        helper_summary = {
            "status": helper_output.get("status"),
            "candidate_role": helper_output.get("candidate_role"),
            "reason_summary": helper_output.get("reason_summary"),
            "best_candidates": helper_output.get("candidates", [])[:3],
        }

    for path in section_files:
        lines = extract_lines(path)
        for raw_line in lines:
            line = normalize(raw_line) or ""
            if not line:
                continue
            if section_kind == "analytic_subject":
                if INDEX_TITLE_RE.fullmatch(line) or re.fullmatch(r"\d{1,4}\s+INDEX RERUM MEMORABILIUM\.?\s+\d{1,4}", line):
                    continue
                if is_letter_marker(line):
                    node_order += 1
                    node_key = f"{VOLUME_ID}:{section_kind}:001:node:{node_order:03d}:{line}"
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": line,
                            "label_norm": normalize(line),
                            "label_sort": sort_norm(line),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {
                                "source_file": str(path),
                                "reason": "Standalone letter divider in the analytical subject index.",
                            },
                        }
                    )
                    node_lookup[line] = node_key
                    continue

            if section_kind == "ordo_rerum":
                if ORDO_TITLE_RE.fullmatch(line) or re.fullmatch(r"\d{1,4}\s+ORDO RERUM\.?\s+\d{1,4}", line):
                    continue
                if line in {"FINIS TOMI QUINQUAGESIMI OCTAVI.", "Parisiis. — Ex typis L. MIGNE."}:
                    continue

            fragments = line_to_entry_text(line, section_kind)
            for fragment in fragments:
                text = normalize(fragment) or ""
                if not text:
                    continue
                if section_kind == "analytic_subject" and (INDEX_TITLE_RE.fullmatch(text) or text in {"Ψ", "Ω"}):
                    continue
                if section_kind == "ordo_rerum" and ORDO_TITLE_RE.fullmatch(text):
                    continue
                if section_kind == "ordo_rerum" and text.startswith("HOM. XXIII ex capite. VII. —") and "Præcepta evan-" in text:
                    pass
                if section_kind == "analytic_subject" and is_letter_marker(text):
                    continue
                lemma_raw = lemma_from_fragment(text)
                ref_list = page_refs(text)
                inferred_page = ref_list[0]["page_ref_int"] if ref_list else None
                target_file_best = best_target_for_page(inferred_page, page_to_file)
                if section_kind == "analytic_subject" and lemma_raw is None and not ref_list:
                    # Preserve bare remissions as cross-reference style entries.
                    lemma_raw = text
                entry_order += 1
                entry_key = f"{VOLUME_ID}:{section_kind}:001:entry:{entry_order:04d}"
                parent_node_key = None
                heading_letter = None
                if section_kind == "analytic_subject":
                    for candidate in reversed(list(node_lookup.items())):
                        if candidate[0] and candidate[0] in text:
                            parent_node_key = candidate[1]
                            heading_letter = candidate[0]
                            break
                entry_kind = entry_kind_for(section_kind, text)
                entry_raw = text
                if section_kind == "ordo_rerum":
                    if text.startswith(("HOM.", "MONITUM", "FRAGMENTUM", "Indices.", "ANIANI")):
                        entry_kind = "heading_group"
                    elif text.startswith(("Frid. FIELD", "D. Bern.")):
                        entry_kind = "heading_group"
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": normalize(lemma_raw) if lemma_raw else None,
                    "lemma_sort": sort_norm(lemma_raw),
                    "entry_raw": entry_raw,
                    "context_raw": None,
                    "heading_letter": heading_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(section_files[0]),
                    "editorial_anchor_file": target_file_best,
                    "target_file_best": target_file_best,
                    "confidence": 0.78 if section_kind == "analytic_subject" else 0.92,
                    "raw_json": {
                        "source_file": str(path),
                        "source_fragment": fragment,
                        "helper_summary": helper_summary,
                        "notes": (
                            "Bare remission preserved at entry level without a material ref."
                            if entry_kind == "cross_reference" and not ref_list
                            else None
                        ),
                    },
                }
                if entry["raw_json"]["notes"] is None:
                    entry["raw_json"].pop("notes")
                entries.append(entry)
                for idx, ref in enumerate(ref_list, start=1):
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
                            "target_file": target_file_best,
                            "target_file_probability": 0.94 if target_file_best else None,
                            "section_start_file": str(section_files[0]),
                            "editorial_anchor_file": target_file_best,
                            "confidence": 0.8,
                            "raw_json": {
                                "source_file": str(path),
                                "source_fragment": fragment,
                            },
                        }
                    )
    return nodes, entries, refs


def build_payload(
    *,
    source_root: Path,
    helper_output: dict[str, Any] | None,
    page_to_file: dict[int, str],
) -> dict[str, Any]:
    index_files = [source_root / f"b3204c14-50c4-47e5-ad2b-3822a1d9c62b-{seq}.txt" for seq in INDEX_FILES]
    ordo_files = [source_root / f"999d9b59-2309-4251-9b2e-f8f42b541eff-{seq}.txt" for seq in ORDO_FILES]

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    sections.append(
        make_section(
            section_key=f"{VOLUME_ID}:alpha:analytic_subject:001",
            section_order=1,
            section_kind="analytic_subject",
            heading_raw="INDEX RERUM MEMORABILIUM.",
            page_start=963,
            page_end=974,
            file_start=index_files[0],
            file_end=index_files[-1],
            confidence=0.99,
            helper_output=helper_output,
        )
    )
    idx_nodes, idx_entries, idx_refs = build_entries_for_section(
        section_kind="analytic_subject",
        section_key=f"{VOLUME_ID}:alpha:analytic_subject:001",
        section_files=index_files,
        page_to_file=page_to_file,
        helper_output=helper_output,
    )
    nodes.extend(idx_nodes)
    entries.extend(idx_entries)
    refs.extend(idx_refs)

    sections.append(
        make_section(
            section_key=f"{VOLUME_ID}:alpha:ordo_rerum:001",
            section_order=2,
            section_kind="ordo_rerum",
            heading_raw="ORDO RERUM.",
            page_start=1057,
            page_end=1064,
            file_start=ordo_files[0],
            file_end=ordo_files[-1],
            confidence=0.96,
            helper_output=helper_output,
        )
    )
    ordo_nodes, ordo_entries, ordo_refs = build_entries_for_section(
        section_kind="ordo_rerum",
        section_key=f"{VOLUME_ID}:alpha:ordo_rerum:001",
        section_files=ordo_files,
        page_to_file=page_to_file,
        helper_output=helper_output,
    )
    nodes.extend(ordo_nodes)
    entries.extend(ordo_entries)
    refs.extend(ordo_refs)

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the analytical subject index and the editorial Ordo Rerum table from the OCR tail pages.",
        "evidence_files": [str(index_files[0]), str(index_files[-1]), str(ordo_files[0]), str(ordo_files[-1])],
    }

    notes = [
        "PG058 contains an analytical subject index headed INDEX RERUM MEMORABILIUM and a separate editorial contents table headed ORDO RERUM.",
        "OCR literal text was preserved; page numbers inside entries remain separate from OCR file suffixes.",
    ]
    if helper_output is not None:
        notes.append("Helper output was run and stored in raw_json for section-level evidence.")

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def build_todo(intermediate_dir: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build and validate PG058 alphabetical payload.",
        "completed": [
            "Read OCR tail pages for INDEX RERUM MEMORABILIUM",
            "Confirmed ORDO RERUM as a separate editorial contents section",
            "Built helper request and payload assembly script",
        ],
        "pending": [
            "Run helper and assemble final JSON payload",
            "Validate the final payload against the importer schema",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals intact.",
            "Use neighboring OCR files only through the current source_root.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--helper-request-json", required=True, type=Path)
    parser.add_argument("--helper-output-json", required=True, type=Path)
    parser.add_argument("--intermediate-dir", required=True, type=Path)
    parser.add_argument("--output-file", required=True, type=Path)
    args = parser.parse_args()

    files = discover_files(args.source_root)
    page_to_file = page_map(files)

    helper_request = build_helper_request(args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    todo = build_todo(args.intermediate_dir)
    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.intermediate_dir / "todo.json", todo)

    payload = build_payload(
        source_root=args.source_root,
        helper_output=helper_output,
        page_to_file=page_to_file,
    )
    write_json(args.output_file, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
