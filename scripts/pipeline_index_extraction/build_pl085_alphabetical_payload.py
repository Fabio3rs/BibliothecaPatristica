#!/usr/bin/env python3
"""Usage: build the PL085 alphabetical-index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl085_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL085/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL085_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL085_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL085 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL085_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

VOLUME_ID = "PL085"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, volume 85"

SECTION1_FILES = list(range(534, 543))
SECTION2_FILES = list(range(543, 546))

SECTION1_HEADING = "INDEX ANALYTICUS."
SECTION2_HEADING = "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR."

SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])|(?<=\d)\s+(?=[A-ZÆŒ])")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:-|à)\s*(\d{1,4}))?(?=[\s\.,;:\)]|$)")
XREF_RE = re.compile(r"^(?:Vide|Vid\.|Voir|v\.|cf\.|id\.)\b", re.IGNORECASE)
HEADING_RE = re.compile(
    r"^(?:INDEX ANALYTICUS\.|ORDO RERUM(?: QU[AEÆ] IN HOC TOMO CONTINENTUR\.)?|MISSALE MISTUM\. — PARS SECUNDA\.|SANCTORALE\.|FESTA [A-Z]+\.|COMMUNE\.|APPENDIX\.|TABULA\.)$",
    re.IGNORECASE,
)
CONTINUATION_END_RE = re.compile(
    r"(?:archiepisc\.|episc\.|episcop\.|presbyt\.|confess\.|abb\.|mart\.|martyr\.|virg\.|apost\.|levit\.|Beatiss\.)$",
    re.IGNORECASE,
)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
HEADER_RE = re.compile(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', re.DOTALL | re.IGNORECASE)
TEXT_BLOCK_RE = re.compile(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', re.DOTALL | re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_block_texts(raw_text: str, block_re: re.Pattern[str]) -> list[str]:
    return [match.group(1) for match in block_re.finditer(raw_text or "")]


def extract_page_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block_text in extract_block_texts(raw, TEXT_BLOCK_RE):
        for raw_line in block_text.splitlines():
            line = norm(raw_line)
            if not line or line == "Digitized by Google":
                continue
            if SINGLE_LETTER_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def extract_header_numbers(path: Path) -> list[int]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    headers = extract_block_texts(raw, HEADER_RE)
    if not headers:
        return []
    header_text = norm(" ".join(headers[0].splitlines())) or ""
    return [int(match.group(1)) for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header_text)]


def section_lines(source_root: Path, file_nums: list[int]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for num in file_nums:
        path = next(source_root.glob(f"*-{num}.txt"))
        for line in extract_page_lines(path):
            lines.append({"file": str(path), "file_num": num, "text": line})
    return lines


def segment_text(raw_text: str) -> list[str]:
    text = " ".join(raw_text.split())
    for pat in [
        r"\bSS\.",
        r"\bS\.",
        r"\bepiscop\.",
        r"\bepisc\.",
        r"\bmartyr\.",
        r"\bmart\.",
        r"\bvirg\.",
        r"\bconfess\.",
        r"\babb\.",
        r"\bapost\.",
        r"\bpresbyt\.",
        r"\blevit\.",
    ]:
        text = re.sub(pat, lambda m: m.group(0).replace(".", "§"), text, flags=re.IGNORECASE)
    parts = [part.strip().replace("§", ".") for part in SPLIT_RE.split(text) if part and part.strip()]
    merged: list[str] = []
    for part in parts:
        if (
            merged
            and not PAGE_REF_RE.search(merged[-1])
            and CONTINUATION_END_RE.search(merged[-1])
            and not XREF_RE.match(part)
            and len(merged[-1]) < 28
        ):
            merged[-1] = f"{merged[-1]} {part}"
            continue
        merged.append(part)
    return merged


def split_lines_into_entries(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in lines:
        for segment in segment_text(item["text"]):
            text = norm(segment) or ""
            if not text:
                continue
            page_matches = [match for match in PAGE_REF_RE.finditer(text) if int(match.group(1)) <= 1060]
            page_refs = []
            for match in page_matches:
                start = int(match.group(1))
                end = match.group(2)
                page_refs.append(
                    {
                        "ref_kind": "editorial_range" if end is not None else "editorial_page",
                        "ref_raw": match.group(0).strip(),
                        "page_ref_raw": match.group(0).strip(),
                        "page_ref_int": start,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": str(start) if end is not None else None,
                        "range_end_raw": str(int(end)) if end is not None else None,
                    }
                )
            inferred_page = page_refs[0]["page_ref_int"] if page_refs else None
            if XREF_RE.match(text):
                entry_kind = "cross_reference"
                lemma_raw = None
            elif HEADING_RE.fullmatch(text):
                entry_kind = "heading_group"
                lemma_raw = text.rstrip(".")
            else:
                entry_kind = "lemma"
                lemma_raw = text.split(",", 1)[0].strip(" .;:")
            entries.append(
                {
                    "source_file": item["file"],
                    "source_file_num": item["file_num"],
                    "entry_kind": entry_kind,
                    "lemma_raw": lemma_raw,
                    "entry_raw": text,
                    "context_raw": text,
                    "inferred_printed_page": inferred_page,
                    "refs": page_refs,
                }
            )
    return entries


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        page_hints = [str(ref["page_ref_int"]) for ref in entry["refs"]]
        page_hint_ints = [ref["page_ref_int"] for ref in entry["refs"]]
        lemma = entry["lemma_raw"] or entry["entry_raw"]
        helper_entries.append(
            {
                "entry_id": f"pl085_{idx:04d}",
                "lemma_raw": lemma,
                "query_names": [lemma, entry["entry_raw"]],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
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


def helper_lookup(helper_output: dict[str, Any] | None) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    if not helper_output:
        return lookup
    for item in helper_output.get("entries", []):
        lookup[str(item.get("entry_id"))] = item
    return lookup


def page_to_file_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in discover_text_files(source_root):
        for num in extract_header_numbers(path)[:2]:
            mapping.setdefault(num, str(path))
    return mapping


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    section1_lines = section_lines(source_root, SECTION1_FILES)
    section2_lines = section_lines(source_root, SECTION2_FILES)

    section1_entries = split_lines_into_entries(section1_lines)
    section2_entries = split_lines_into_entries(section2_lines)

    all_entries = section1_entries + section2_entries
    helper_request = build_helper_request(source_root, all_entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_by_id = helper_lookup(helper_output)
    file_map = page_to_file_map(source_root)

    sections = [
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION1_HEADING,
            "heading_norm": SECTION1_HEADING.lower().rstrip("."),
            "heading_letter": None,
            "page_start": 1057,
            "page_end": 1072,
            "file_start": str(next(source_root.glob("*-534.txt"))),
            "file_end": str(next(source_root.glob("*-542.txt"))),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": "Tail analytic index with the ORDO RERUM transition absorbed into the same index run; the OCR tail keeps the analytic index through file 542.",
                "source_files": [str(next(source_root.glob(f"*-{num}.txt"))) for num in SECTION1_FILES],
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION2_HEADING,
            "heading_norm": SECTION2_HEADING.lower().rstrip("."),
            "heading_letter": None,
            "page_start": 1073,
            "page_end": 1080,
            "file_start": str(next(source_root.glob("*-543.txt"))),
            "file_end": str(next(source_root.glob("*-545.txt"))),
            "confidence": 0.91,
            "raw_json": {
                "section_kind_reason": "Closing ORDO RERUM contents table and appendix list.",
                "source_files": [str(next(source_root.glob(f"*-{num}.txt"))) for num in SECTION2_FILES],
            },
        },
    ]

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for idx, src in enumerate(all_entries, start=1):
        entry_key = f"{VOLUME_ID}:entry:{idx:06d}"
        helper_entry_id = f"pl085_{idx:04d}"
        helper_item = helper_by_id.get(helper_entry_id, {})
        candidate = helper_item.get("best_candidate") or {}
        target_file = candidate.get("file") or None
        if target_file is None and src["inferred_printed_page"] is not None:
            target_file = file_map.get(src["inferred_printed_page"])
        confidence = 0.86 if src["entry_kind"] == "lemma" else 0.78 if src["entry_kind"] == "cross_reference" else 0.74
        entry_payload = {
            "entry_key": entry_key,
            "section_key": sections[0]["section_key"] if idx <= len(section1_entries) else sections[1]["section_key"],
            "parent_node_key": None,
            "entry_order": idx,
            "entry_kind": src["entry_kind"],
            "lemma_raw": src["lemma_raw"],
            "lemma_display": src["lemma_raw"],
            "lemma_norm": src["lemma_raw"].lower() if src["lemma_raw"] else None,
            "lemma_sort": sort_norm(src["lemma_raw"]),
            "entry_raw": src["entry_raw"],
            "context_raw": src["context_raw"],
            "heading_letter": (src["lemma_raw"][0].upper() if src["lemma_raw"] else (src["entry_raw"][0].upper() if src["entry_raw"] else None)),
            "inferred_printed_page": src["inferred_printed_page"],
            "section_start_file": sections[0]["file_start"] if idx <= len(section1_entries) else sections[1]["file_start"],
            "editorial_anchor_file": src["source_file"],
            "target_file_best": target_file,
            "confidence": confidence,
            "raw_json": {
                "source_file": src["source_file"],
                "helper_entry_id": helper_entry_id,
                "helper_status": helper_item.get("status"),
                "helper_best_candidate": candidate or None,
                "page_hint_ints": [ref["page_ref_int"] for ref in src["refs"]],
            },
        }
        entries.append(entry_payload)
        for ref_order, ref in enumerate(src["refs"], start=1):
            target = file_map.get(ref["page_ref_int"]) if ref["page_ref_int"] is not None else None
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": ref["line_ref_raw"],
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": target,
                    "target_file_probability": candidate.get("probability") if candidate else None,
                    "section_start_file": entry_payload["section_start_file"],
                    "editorial_anchor_file": entry_payload["editorial_anchor_file"],
                    "confidence": 0.72 if ref["page_ref_int"] is not None else 0.55,
                    "raw_json": {
                        "source_file": src["source_file"],
                        "helper_entry_id": helper_entry_id,
                        "helper_status": helper_item.get("status"),
                    },
                }
            )

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "Tail OCR includes the analytic index and the closing ORDO RERUM contents block. OCR headers are noisy around the transition, so file-level evidence was kept conservative.",
    }

    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": "Recovered both tail sections conservatively from the OCR files. Some long entries are still grouped by OCR line fragments, and the helper was used only as locator support.",
        "evidence_files": [str(next(source_root.glob(f"*-{num}.txt"))) for num in [534, 535, 536, 537, 538, 539, 540, 541, 542, 543, 544, 545]],
    }

    notes = [
        "Section 1 covers the analytic index tail in files 534-542.",
        "Section 2 covers the ORDO RERUM contents table in files 543-545.",
        "Helper output was retained in raw_json for locator support; exact page-to-file alignment remains OCR-noisy in a few tail rows.",
    ]

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", [])
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
        },
    )
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Validate PL085 helper resolution and stabilize the tail index payload.",
            "completed": [
                "analytic index tail segmented",
                "ordo rerum contents block segmented",
                "helper request written and helper executed",
                "intermediate fragments assembled",
            ],
            "pending": [
                "spot-check a few mixed OCR fragments",
                "confirm final JSON shape",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not collapse OCR file suffixes with printed page numbers.",
            ],
        },
    )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL085 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
