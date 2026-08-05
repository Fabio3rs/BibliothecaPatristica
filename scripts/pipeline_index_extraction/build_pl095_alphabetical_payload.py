#!/usr/bin/env python3
"""Build the PL095 alphabetical-index payload from the OCR tail.

Usage:
  python scripts/pipeline_index_extraction/build_pl095_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL095/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL095_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL095_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL095 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL095_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patristica_pipeline.editorial_page_estimator import build_estimator_page_map as estimator_page_map

VOLUME_ID = "PL095"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, Tomus XCV"

INDEX_FILES = [
    867, 868, 869, 870, 871, 872, 873, 874, 875, 876,
]
ORDO_FILES = [
    877, 878, 879, 880, 881, 882, 883, 884, 885,
]

BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
HEADER_PAGE_RE = re.compile(r"^\s*(?P<p1>\d{3,4})\s+(?P<title>.+?)\s+(?P<p2>\d{3,4})\s*$")
SINGLE_LETTER_RE = re.compile(r"^[A-Z]$")
PAGE_AT_END_RE = re.compile(r"(?:,|\s)\s*(?P<token>(?:Ibid\.?|ibid\.?|[0-9]{1,4}|[IVXLCDM]{1,6}))\s*$")
REF_PATTERN_RE = re.compile(r"(?P<roman>[IVXLCDM]{1,6})\s*,\s*(?P<nums>\d{1,4}(?:\s*,\s*\d{1,4})*)|(?P<page>\d{1,4})")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_sort(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def discover_files(source_root: Path) -> list[Path]:
    files = sorted(source_root.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"no OCR files found in {source_root}")
    return files


def extract_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines: list[str] = []
    for block_type, block_text in BLOCK_RE.findall(text):
        if block_type not in {"cabecalho", "texto_principal"}:
            continue
        for raw_line in block_text.splitlines():
            line = raw_line.strip()
            if line:
                lines.append(line)
    return lines


def file_page_header(path: Path) -> int | None:
    for line in extract_lines(path):
        m = HEADER_PAGE_RE.match(line)
        if m:
            try:
                return int(m.group("p1"))
            except ValueError:
                return None
    return None


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        page = file_page_header(path)
        if page is not None:
            page_map.setdefault(page, str(path))
    if files:
        for page, target in estimator_page_map(
            volume_id=VOLUME_ID,
            collection=COLLECTION,
            source_root=files[0].parent,
        ).items():
            page_map.setdefault(page, target)
    return page_map


def section_kind_for_heading(heading: str) -> str:
    if heading.startswith("ORDO RERUM"):
        return "ordo_rerum"
    if heading.startswith("INDEX AUCTORUM"):
        return "author_index"
    if heading.startswith("INDEX RERUM"):
        return "alphabetical_general"
    return "analytic_subject"


def split_sections(files: list[Path]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for path in files:
        lines = extract_lines(path)
        heading = None
        for line in lines[:8]:
            if "INDEX RERUM" in line:
                heading = "INDEX RERUM."
                break
            if "ORDO RERUM" in line:
                heading = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
                break
            if "INDEX GLOSSARUM" in line:
                heading = "INDEX GLOSSARUM, VERBORUM ET RERUM MEMORABILIUM."
                break
            if "INDEX AUCTOR" in line:
                heading = "INDEX AUCTORUM ET MONUMENTORUM QUÆ IN FESTI FRAGMENTO CITANTUR."
                break
        if heading is None:
            continue
        if current and current["heading_raw"] == heading:
            current["file_end"] = str(path)
            current["page_end"] = file_page_header(path)
            current["raw_json"]["source_files"].append(str(path))
            continue
        current = {
            "section_key": f"{VOLUME_ID}:section:{len(sections)+1:03d}",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": len(sections) + 1,
            "section_kind": section_kind_for_heading(heading),
            "heading_raw": heading,
            "heading_norm": normalize_sort(heading),
            "heading_letter": None,
            "page_start": file_page_header(path),
            "page_end": file_page_header(path),
            "file_start": str(path),
            "file_end": str(path),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": f"Detected visible heading '{heading}' in OCR tail.",
                "source_files": [str(path)],
            },
        }
        sections.append(current)
    return sections


def parse_reference_tokens(line: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in REF_PATTERN_RE.finditer(line):
        if match.group("roman"):
            roman = match.group("roman")
            nums = [n.strip() for n in match.group("nums").split(",")]
            refs.append({
                "ref_raw": f"{roman}, {', '.join(nums)}",
                "page_ref_raw": f"{roman}, {', '.join(nums)}",
                "page_ref_int": None,
            })
            continue
        if match.group("page"):
            page = int(match.group("page"))
            refs.append({
                "ref_raw": match.group("page"),
                "page_ref_raw": match.group("page"),
                "page_ref_int": page,
            })
    if not refs and PAGE_AT_END_RE.search(line):
        token = PAGE_AT_END_RE.search(line).group("token")  # type: ignore[union-attr]
        refs.append({
            "ref_raw": token,
            "page_ref_raw": token,
            "page_ref_int": int(token) if token.isdigit() else None,
        })
    return refs


def is_letter_heading(line: str) -> bool:
    return bool(SINGLE_LETTER_RE.match(line))


def build_entries(files: list[Path], page_map: dict[int, str], sections: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    section_by_file: dict[str, dict[str, Any]] = {}
    for section in sections:
        for file_path in section["raw_json"]["source_files"]:
            section_by_file[file_path] = section

    letter_node_index: dict[tuple[str, str], str] = {}

    entry_counter = 1
    for path in files:
        section = section_by_file.get(str(path))
        if section is None:
            continue
        section_key = section["section_key"]
        lines = extract_lines(path)
        current_letter = None
        printed_page = file_page_header(path)
        for line in lines:
            if line.startswith("Digitized by Google"):
                continue
            if line.startswith("PATROL.") or line.startswith("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.") or line.startswith("INDEX RERUM.") or line.startswith("INDEX GLOSSARUM, VERBORUM ET RERUM MEMORABILIUM.") or line.startswith("INDEX AUCTORUM"):
                continue
            if is_letter_heading(line):
                current_letter = line
                node_key = f"{VOLUME_ID}:node:{len(nodes)+1:03d}"
                nodes.append({
                    "node_key": node_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "node_order": len(nodes) + 1,
                    "node_kind": "letter_group",
                    "label_raw": line,
                    "label_norm": normalize_sort(line),
                    "label_sort": normalize_sort(line),
                    "node_level": 1,
                    "confidence": 0.98,
                    "raw_json": {"source_file": str(path)},
                })
                letter_node_index[(section_key, line)] = node_key
                continue

            if section["section_kind"] == "ordo_rerum" and line.startswith("HISTORIÆ ECCLESIASTICÆ LIBER"):
                entry_kind = "heading_group"
            elif section["section_kind"] == "ordo_rerum" and (line.startswith("CAP.") or line.startswith("CAPUT") or line.startswith("Hom.")):
                entry_kind = "lemma"
            elif line.startswith(("V.", "Vid.", "Vid ", "V. ", "De ")):
                entry_kind = "cross_reference" if line.startswith(("V.", "Vid.", "Vid ")) else "lemma"
            else:
                entry_kind = "lemma"

            entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            entry_counter += 1
            entry_obj = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": letter_node_index.get((section_key, current_letter)) if current_letter else None,
                "entry_order": len(entries) + 1,
                "entry_kind": entry_kind,
                "lemma_raw": line,
                "lemma_display": line,
                "lemma_norm": normalize_sort(line),
                "lemma_sort": normalize_sort(line),
                "entry_raw": line,
                "context_raw": line,
                "heading_letter": current_letter,
                "inferred_printed_page": printed_page,
                "section_start_file": section["file_start"],
                "editorial_anchor_file": str(path),
                "target_file_best": str(path),
                "confidence": 0.86 if printed_page is not None else 0.78,
                "raw_json": {
                    "source_file": str(path),
                    "section_kind": section["section_kind"],
                    "page_header": printed_page,
                },
            }
            entries.append(entry_obj)

            ref_tokens = parse_reference_tokens(line)
            if not ref_tokens:
                continue
            for idx_ref, ref_token in enumerate(ref_tokens, start=1):
                target_file = page_map.get(ref_token["page_ref_int"]) if ref_token["page_ref_int"] is not None else None
                ref_obj = {
                    "entry_key": entry_key,
                    "ref_order": idx_ref,
                    "ref_kind": "editorial_page" if ref_token["page_ref_int"] is not None else "target_locator",
                    "ref_raw": ref_token["ref_raw"],
                    "page_ref_raw": ref_token["page_ref_raw"],
                    "page_ref_int": ref_token["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 0.76 if target_file else None,
                    "section_start_file": section["file_start"],
                    "editorial_anchor_file": str(path),
                    "confidence": 0.82 if target_file else 0.64,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section["section_kind"],
                    },
                }
                refs.append(ref_obj)

    return entries, refs, nodes


def helper_request(entries: list[dict[str, Any]], files: list[Path], helper_request_json: Path) -> list[dict[str, Any]]:
    sample_entries = []
    for entry in entries:
        if entry["section_key"].endswith(":section:002") and len(sample_entries) < 4:
            sample_entries.append({
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"],
                "query_names": [entry["lemma_raw"]],
                "page_hints": [str(entry["inferred_printed_page"])] if entry["inferred_printed_page"] is not None else [],
                "page_hint_ints": [entry["inferred_printed_page"]] if entry["inferred_printed_page"] is not None else [],
                "context_raw": entry["entry_raw"],
            })
    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(files[0].parent),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": sample_entries,
    }
    write_json(helper_request_json, request)
    return sample_entries


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
    sections = split_sections(files)
    entries, refs, nodes = build_entries(files, page_map, sections)
    helper_request(entries, files, helper_request_json)

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True)
    helper_status = "not_run"
    helper_map: dict[str, Any] = {}
    if proc.returncode == 0 and helper_output_json.exists():
        helper_status = "ok"
        helper_data = read_json(helper_output_json, {"entries": []})
        for item in helper_data.get("entries", []):
            helper_map[str(item.get("entry_id"))] = item
    else:
        helper_status = f"failed: {proc.returncode}"

    for entry in entries:
        item = helper_map.get(entry["entry_key"])
        if not item:
            continue
        best = item.get("best_candidate") or {}
        entry["raw_json"]["helper_locator"] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best.get("file"):
            entry["raw_json"]["helper_best_file"] = best["file"]

    for ref in refs:
        if ref["target_file"] is None and ref["page_ref_int"] is not None:
            ref["raw_json"]["unresolved_reason"] = "no OCR page header mapped for cited page"

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the INDEX RERUM tail and the closing ORDO RERUM block from the OCR window. "
            "Material locator fields are resolved conservatively where page headers permit, and left explicit where the cited reference is not a simple page."
        ),
        "evidence_files": [str(path) for path in files if int(path.stem.rsplit("-", 1)[-1]) >= 867],
    }

    notes = [
        {
            "note_key": "pl095_tail_scope",
            "note_raw": "This payload covers the OCR tail beginning with INDEX RERUM and ending with ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.; earlier appendix indexes in files 860-866 were inspected but not serialized in this pass.",
            "confidence": 0.92,
        },
        {
            "note_key": "pl095_helper_scope",
            "note_raw": f"Helper request included a small sample from the ORDO RERUM section only; locator output was used as a check, not as the main source of structure.",
            "confidence": 0.81,
        },
    ]

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Tail index material for the volume, including INDEX RERUM and ORDO RERUM.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "todo.json", {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PL095 tail-index payload and preserve unresolved cited references explicitly",
        "completed": [
            "inspected OCR tail files 867-885",
            "detected INDEX RERUM and ORDO RERUM sections",
            "built helper request and ran locator on a small sample",
        ],
        "pending": [
            "review unresolved roman-numeral references if deeper locator work is needed",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR file suffixes separate from cited references.",
            "Leave non-page cited references explicit when they cannot be mapped to a page header.",
        ],
    })
    write_json(intermediate_dir / "manifest.json", {
        "volume_id": VOLUME_ID,
        "generated_at": payload["generated_at"],
        "entries_count": len(entries),
        "refs_count": len(refs),
        "nodes_count": len(nodes),
        "helper_status": helper_status,
    })
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL095 alphabetical payload.")
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
