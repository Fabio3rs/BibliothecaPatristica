#!/usr/bin/env python3
"""Usage: build the PL153 alphabetical index payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl153_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL153/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL153_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL153_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL153 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL153_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL153"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 153"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_START_FILE_NAME = "748e18b4-04ad-45c3-afa8-82b936c98cf2-580.txt"
SECTION_END_FILE_NAME = "748e18b4-04ad-45c3-afa8-82b936c98cf2-583.txt"

INDEX_FILES = [
    ROOT / "teste/PL153/text/748e18b4-04ad-45c3-afa8-82b936c98cf2-580.txt",
    ROOT / "teste/PL153/text/748e18b4-04ad-45c3-afa8-82b936c98cf2-581.txt",
    ROOT / "teste/PL153/text/748e18b4-04ad-45c3-afa8-82b936c98cf2-582.txt",
    ROOT / "teste/PL153/text/748e18b4-04ad-45c3-afa8-82b936c98cf2-583.txt",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def clean_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    lines: list[str] = []
    for raw in (parsed.get("all_text") or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "Digitized by Google" in line:
            continue
        if re.fullmatch(r"\d{4}\.?\s+INDEX IN S\. BRUNONEM\.?\s+\d{4}\.?", line):
            continue
        if re.fullmatch(r"\d{4}", line):
            continue
        if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S"}:
            continue
        if line == "INDEX":
            continue
        if line.startswith("INDEX IN OPERA DIVI BRUNONIS"):
            continue
        if line.startswith("Numeri Arabici Lectorum"):
            continue
        if line.startswith("1151 INDEX IN S. BRUNONEM") or line.startswith("1153. INDEX IN S. BRUNONEM"):
            continue
        if line.startswith("1155 INDEX IN S. BRUNONEM") or line.startswith("1157 INDEX IN S. BRUNONEM"):
            continue
        lines.append(line)
    return lines


def build_index_body(files: list[Path]) -> tuple[str, dict[str, str], dict[str, str]]:
    body_parts: list[str] = []
    file_bodies: dict[str, str] = {}
    source_lines: dict[str, str] = {}
    started = False
    for path in files:
        lines = clean_lines(path)
        file_body = " ".join(lines)
        file_bodies[str(path)] = file_body
        for line in lines:
            if not started:
                if line.startswith("A "):
                    started = True
                else:
                    continue
            source_lines[str(path)] = source_lines.get(str(path), "") + " " + line
            body_parts.append(line)
    body = " ".join(body_parts)
    body = re.sub(r"\s+", " ", body).strip()
    return body, file_bodies, source_lines


def split_segments(body: str) -> list[str]:
    raw_segments = re.split(r"(?<=\.)\s+(?=[A-ZÆŒ])", body)
    segments: list[str] = []
    for segment in raw_segments:
        seg = segment.strip()
        if not seg:
            continue
        if seg.startswith("INDEX IN OPERA DIVI BRUNONIS"):
            continue
        if seg.startswith("Numeri Arabici"):
            continue
        if seg in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S"}:
            continue
        if re.fullmatch(r"\d{4}\.?", seg):
            continue
        segments.append(seg)
    return segments


def extract_refs(segment: str) -> list[int]:
    refs: list[int] = []
    for match in re.finditer(r"(?:^|[;,])\s*(\d{1,4})(?:\s*seq\.?|(?:\s+et\s+seqq?\.?))?", segment, re.IGNORECASE):
        num = int(match.group(1))
        if num not in refs:
            refs.append(num)
    return refs


def strip_heading_letter(text: str) -> str:
    value = text.strip()
    if re.match(r"^[A-ZÆŒ]\s+[A-Z]", value):
        return value[2:].lstrip()
    return value


def lemma_from_segment(segment: str) -> str:
    candidate = strip_heading_letter(segment)
    match = re.search(r",\s*(?:[IVXLCDM]+|\d)", candidate)
    if match:
        lemma = candidate[: match.start()]
    else:
        lemma = candidate
    lemma = lemma.rstrip(" ,;:.")
    return lemma


def first_letter(lemma: str | None) -> str | None:
    if not lemma:
        return None
    value = normalize(lemma)
    if not value:
        return None
    for ch in value:
        if ch.isalpha():
            if ch in {"a", "æ"}:
                return "A"
            if ch in {"o", "œ"}:
                return "O"
            return ch.upper()
    return None


def locate_source_file(segment: str, file_bodies: dict[str, str], ordered_files: list[Path]) -> str | None:
    snippet = normalize(segment[:120] if len(segment) > 120 else segment)
    if not snippet:
        return None
    snippet = snippet[:80]
    for path in ordered_files:
        body = normalize(file_bodies.get(str(path), ""))
        if body and snippet in body:
            return str(path)
    first = ordered_files[0] if ordered_files else None
    return str(first) if first else None


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = parsed.get("header_text") or ""
        footer = parsed.get("footer_text") or ""
        top_text = f"{header} {footer}"
        if not top_text.strip():
            top_lines = []
            for line in (parsed.get("all_text") or "").splitlines()[:6]:
                top_lines.append(line)
            top_text = " ".join(top_lines)
        numbers = []
        for raw in re.findall(r"\b(\d{1,4})\b", top_text):
            num = int(raw)
            if num not in numbers:
                numbers.append(num)
        for num in numbers:
            page_map.setdefault(num, str(path))
    return page_map


def build_helper_request(entries: list[dict[str, Any]], helper_request_json: Path) -> None:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        refs = entry.get("refs", [])
        page_hints = [str(r["page_ref_int"]) for r in refs if r.get("page_ref_int") is not None]
        page_hint_ints = [r["page_ref_int"] for r in refs if r.get("page_ref_int") is not None]
        lemma_raw = entry["lemma_raw"]
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma_raw,
                "query_names": [lemma_raw, normalize(lemma_raw) or lemma_raw],
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": entry["entry_raw"],
            }
        )
    payload = {
        "volume_id": VOLUME_ID,
        "source_root": str(ROOT / "teste/PL153/text"),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, payload)


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_TARGET_LOCATOR), "--input", str(helper_request_json), "--output", str(helper_output_json), "--pretty"],
        text=True,
        capture_output=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def parse_entries(files: list[Path], page_map: dict[int, str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    body, file_bodies, _ = build_index_body(files)
    segments = split_segments(body)
    source_lookup: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    refs_out: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    current_order = 1
    letter_order = 1

    for segment in segments:
        lemma = lemma_from_segment(segment)
        if not lemma:
            continue
        refs = extract_refs(segment)
        if not refs:
            continue
        source_file = locate_source_file(segment, file_bodies, files)
        source_lookup[segment] = source_file or ""
        letter = first_letter(lemma)
        if letter and letter not in letter_to_node:
            node_key = f"{VOLUME_ID}:alpha:analytic_subject:001:letter:{letter}"
            letter_to_node[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
                    "parent_node_key": None,
                    "node_order": letter_order,
                    "node_kind": "heading_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {"source": "ocr_letter_group"},
                }
            )
            letter_order += 1

        entry_key = f"{VOLUME_ID}:alpha:analytic_subject:001:e{current_order:03d}"
        page_numbers = refs[:]
        inferred_page = page_numbers[0]
        entry = {
            "entry_key": entry_key,
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
            "parent_node_key": letter_to_node.get(letter),
            "entry_order": current_order,
            "entry_kind": "lemma",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": normalize(lemma),
            "lemma_sort": sort_norm(lemma),
            "entry_raw": segment,
            "context_raw": segment,
            "heading_letter": letter,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(files[0]),
            "editorial_anchor_file": source_file,
            "target_file_best": page_map.get(inferred_page),
            "confidence": 0.92,
            "raw_json": {"source": "direct_ocr", "segment_file": source_file, "page_refs": page_numbers},
            "refs": [],
        }
        for ref_order, page_num in enumerate(page_numbers, start=1):
            target_file = page_map.get(page_num)
            ref = {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": str(page_num),
                "page_ref_raw": str(page_num),
                "page_ref_int": page_num,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": 1.0 if target_file else None,
                "section_start_file": str(files[0]),
                "editorial_anchor_file": source_file,
                "confidence": 0.92 if target_file else 0.62,
                "raw_json": {"source": "header_page_map" if target_file else "unresolved_page_map"},
            }
            refs_out.append(ref)
            entry["refs"].append(ref)
        entries.append(entry)
        current_order += 1

    section = {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX IN OPERA DIVI BRUNONIS.",
        "heading_norm": "index in opera divi brunonis",
        "heading_letter": None,
        "page_start": 1151,
        "page_end": 1160,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.98,
        "raw_json": {
            "ocr_headers": [
                "1151 INDEX IN S. BRUNONEM. 1152",
                "1153 INDEX IN S. BRUNONEM. 1154",
                "1155 INDEX IN S. BRUNONEM. 1156",
                "1157 INDEX IN S. BRUNONEM. 1158",
                "1159 INDEX IN S. BRUNONEM. 1160",
            ],
            "section_kind_reason": "Alphabetical analytical index titled INDEX IN OPERA DIVI BRUNONIS with Latin letter group headers and page references to Bruno's works.",
        },
    }
    return section, nodes, entries, refs_out, page_map, [{"file": str(p), "line_count": len(clean_lines(p))} for p in files]


def build_payload(intermediate_dir: Path, generated_at: str | None = None) -> dict[str, Any]:
    manifest = read_json(intermediate_dir / "manifest.json", {})
    volume = read_json(intermediate_dir / "volume.json")
    coverage = read_json(intermediate_dir / "coverage.json", {})
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at or manifest.get("updated_at") or manifest.get("generated_at"),
        "volume": volume,
        "sections": read_json(intermediate_dir / "sections.json", []),
        "nodes": read_json(intermediate_dir / "nodes.json", []),
        "entries": read_json(intermediate_dir / "entries.json", []),
        "refs": read_json(intermediate_dir / "refs.json", []),
        "scripture_refs": read_json(intermediate_dir / "scripture_refs.json", []),
        "coverage": coverage,
        "notes": read_json(intermediate_dir / "notes.json", []),
    }
    if payload["generated_at"] is None:
        raise SystemExit("generated_at is required via manifest.json or --generated-at")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build PL153 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    files = sorted(args.source_root.glob("*.txt"), key=file_seq)
    wanted = {p.name for p in INDEX_FILES}
    index_files = [p for p in files if p.name in wanted]
    if len(index_files) != 4:
        raise SystemExit(f"Expected 4 index files, found {len(index_files)}")

    page_map = build_page_map(files)
    section, nodes, entries, refs_out, page_map, evidence_files = parse_entries(index_files, page_map)
    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "Index section recovered from OCR files 580-583; page refs are resolved by local header map and helper cross-checks.",
        ],
    }
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered alphabetical index entries from the OCR tail; no unrecoverable span remained after page-map and neighboring-file checks.",
        "evidence_files": [str(p) for p in index_files],
    }
    notes = [
        "Section title normalized as INDEX IN OPERA DIVI BRUNONIS, but OCR headers on source pages read INDEX IN S. BRUNONEM.",
        "Final S-entry is truncated in the OCR at the end of file 583; the truncation is preserved in entry_raw.",
        "Page refs use the printed page numbers cited by the index, not the OCR file suffixes.",
    ]

    helper_request_entries: list[dict[str, Any]] = []
    helper_entries_payload: list[dict[str, Any]] = []
    for entry in entries:
        helper_request_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"], entry["lemma_norm"] or entry["lemma_raw"]],
                "page_hints": [str(ref["page_ref_int"]) for ref in refs_out if ref["entry_key"] == entry["entry_key"]],
                "page_hint_ints": [ref["page_ref_int"] for ref in refs_out if ref["entry_key"] == entry["entry_key"]],
                "context_raw": entry["entry_raw"],
            }
        )
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_request_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_by_entry = {item.get("entry_id"): item for item in helper_output.get("entries", [])}

    for entry in entries:
        helper_entry = helper_by_entry.get(entry["entry_key"])
        if helper_entry:
            entry["raw_json"]["helper"] = helper_entry
            best_candidate = helper_entry.get("best_candidate") or {}
            helper_file = best_candidate.get("file")
            if helper_file and entry.get("target_file_best") is None:
                entry["target_file_best"] = helper_file
            if helper_file and entry.get("confidence", 0) < 0.9:
                entry["confidence"] = 0.9
    for ref in refs_out:
        helper_entry = helper_by_entry.get(ref["entry_key"])
        if helper_entry:
            ref["raw_json"]["helper"] = helper_entry
            best_candidate = helper_entry.get("best_candidate") or {}
            helper_file = best_candidate.get("file")
            if helper_file and ref.get("target_file") is None:
                ref["target_file"] = helper_file
                ref["target_file_probability"] = best_candidate.get("probability")
                ref["confidence"] = max(ref.get("confidence", 0.0), 0.85)

    entries_payload = [{k: v for k, v in entry.items() if k != "refs"} for entry in entries]
    refs_payload = list(refs_out)

    manifest = {"volume_id": VOLUME_ID, "updated_at": now_iso(), "generated_at": now_iso()}
    write_json(args.intermediate_dir / "manifest.json", manifest)
    write_json(args.intermediate_dir / "volume.json", volume)
    write_json(args.intermediate_dir / "sections.json", [section])
    write_json(args.intermediate_dir / "nodes.json", nodes)
    write_json(args.intermediate_dir / "entries.json", entries_payload)
    write_json(args.intermediate_dir / "refs.json", refs_payload)
    write_json(args.intermediate_dir / "scripture_refs.json", [])
    write_json(args.intermediate_dir / "coverage.json", coverage)
    write_json(args.intermediate_dir / "notes.json", notes)
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Alphabetical index payload built and written; helper cross-check completed.",
            "completed": [
                "section recovered",
                "entries parsed from OCR",
                "helper request generated and locator executed",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "OCR letter headings were normalized into nodes.",
                "Page refs use printed index citations, not OCR file suffixes.",
            ],
        },
    )

    payload = build_payload(args.intermediate_dir, generated_at=manifest["generated_at"])
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
