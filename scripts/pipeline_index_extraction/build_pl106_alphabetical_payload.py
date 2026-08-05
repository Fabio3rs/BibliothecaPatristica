#!/usr/bin/env python3
"""Usage: build the PL106 alphabetical-index payload from the OCR tail and front matter.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl106_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL106/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL106_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL106_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL106 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL106_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

VOLUME_ID = "PL106"
COLLECTION = "PL"
HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 2

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:001",
        "section_order": 1,
        "section_kind": "author_index",
        "heading_raw": "ELENCHUS AUCTORUM ET OPERUM QUI IN HOC TOMO CVI CONTINENTUR.",
        "heading_norm": "elenchus auctorum et operum qui in hoc tomo cvi continentur",
        "files": [9],
        "notes": "Front-matter author/work contents list.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "files": list(range(768, 782)),
        "notes": "Closing table of contents for the volume.",
    },
]

DECO_SKIP = {
    "TRADITIO CATHOLICA.",
    "SÆCULUM IX, ANNI 840 851.",
    "SÆCULUM IX. ANNI 840 851.",
    "SÆCULUM IX, ANNI 840 851.",
    "ELENCHUS",
    "AUCTORUM ET OPERUM QUI IN HOC TOMO CVI CONTINENTUR.",
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINEANTUR.",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "------------------------------------------------------------",
}

PAGE_REF_RE = re.compile(r"(?:(Col\.)\s*)?(Ibid\.|ibid\.|\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?\s*$")
HYPHEN_RE = re.compile(r"\s*[-–—]\s*")
NUM_RE = re.compile(r"\b\d{1,4}\b")
ROMAN_RE = re.compile(r"^[IVXLCDM]+\.?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    cleaned = norm(text)
    return cleaned.lower() if cleaned else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_num(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def file_by_num(source_root: Path, number: int) -> Path:
    matches = [path for path in source_root.glob(f"*-{number:03d}.txt")]
    if not matches:
        raise FileNotFoundError(f"Missing OCR file for suffix {number:03d}")
    return matches[0]


def extract_lines(path: Path) -> list[str]:
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for raw in raw_text.splitlines():
        text = norm(raw)
        if not text:
            continue
        if text.startswith("<") or text.endswith(">") and text.startswith("</"):
            continue
        if text.startswith("</") or text.startswith("<pagina") or text.startswith("<bloco") or text.startswith("<notas"):
            continue
        if FOOTER_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def is_decorative(line: str) -> bool:
    if line in DECO_SKIP:
        return True
    if set(line) == {"-"}:
        return True
    return False


def is_major_heading(line: str) -> bool:
    if not line or NUM_RE.search(line):
        return False
    if is_decorative(line):
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if not letters:
        return False
    upper = sum(1 for ch in letters if ch.upper() == ch)
    ratio = upper / len(letters)
    return ratio >= 0.7 and len(line) <= 120


def strip_trailing_locator(text: str, last_page_int: int | None = None) -> tuple[str, list[dict[str, Any]]]:
    cleaned = norm(text)
    if not cleaned:
        return "", []
    refs: list[dict[str, Any]] = []
    match = PAGE_REF_RE.search(cleaned)
    if not match:
        return cleaned, refs
    col_token, raw_page, raw_range_end = match.groups()
    locator_raw = match.group(0).strip()
    if raw_page.lower().startswith("ibid"):
        start_int = last_page_int
    else:
        start_int = int(raw_page)
    if raw_range_end is not None:
        refs.append(
            {
                "ref_raw": locator_raw,
                "page_ref_raw": locator_raw,
        "page_ref_int": int(raw_page),
                "page_ref_col": col_token,
                "line_ref_raw": None,
                "range_start_raw": raw_page,
                "range_end_raw": raw_range_end,
                "ref_kind": "editorial_page_column" if col_token else "editorial_range",
            }
        )
    else:
        if start_int is None:
            start_int = last_page_int or 1
        refs.append(
            {
                "ref_raw": locator_raw,
                "page_ref_raw": locator_raw,
                "page_ref_int": start_int,
                "page_ref_col": col_token,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "ref_kind": "editorial_column" if col_token else "editorial_page",
            }
        )
    prefix = cleaned[: match.start()].rstrip(" ,;:.")
    return prefix, refs


def build_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    candidates: list[str] = []
    cleaned = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
    if cleaned:
        candidates.append(cleaned)
    if lemma_raw and lemma_raw not in candidates:
        candidates.append(lemma_raw)
    first_clause = re.split(r"\s*[;,]\s*|\s{2,}", cleaned or lemma_raw, maxsplit=1)[0].strip()
    if first_clause and first_clause not in candidates:
        candidates.append(first_clause)
    if context_raw and context_raw not in candidates:
        candidates.append(context_raw)
    return candidates[:4]


def derive_lemma(entry_raw: str, section_kind: str) -> str | None:
    text, _ = strip_trailing_locator(entry_raw)
    text = re.sub(r"^\s*(?:[IVXLCDM]+\.|[IVXLCDM]+)\s*[—.-]?\s*", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:CAP\.|CAPUT|LIBER|PROLOGUS|NOTITIA|VITA|EPISTOLA|EPISTOLA|DISERTATIO|DISERTATION|OBSERVATIO|OBSERVATIONES)\s*", "", text, count=1, flags=re.IGNORECASE)
    text = text.strip(" .;:")
    if not text:
        return None
    if section_kind == "ordo_rerum":
        return text
    return text


def parse_page_lines(
    lines: list[str],
    *,
    section_kind: str,
    section_key: str,
    section_heading: str,
    section_file: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    current_node_key: str | None = None
    current_node_label: str | None = None
    current_node_order = 0
    entry_order = 0
    block: list[str] = []
    last_page_int: int | None = None

    def flush_block(end_file: Path) -> None:
        nonlocal block, entry_order, last_page_int
        if not block:
            return
        entry_raw = norm(" ".join(block))
        text_without_locator, extracted_refs = strip_trailing_locator(entry_raw, last_page_int=last_page_int)
        if not extracted_refs:
            block = []
            return
        lemma_raw = derive_lemma(entry_raw, section_kind)
        entry_kind = "heading_group" if section_kind == "ordo_rerum" else "lemma"
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{section_kind}:{entry_order:04d}"
        inferred_page = extracted_refs[0]["page_ref_int"]
        if inferred_page is None and last_page_int is not None:
            inferred_page = last_page_int
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lemma_raw.lower() if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": None,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(section_file),
            "editorial_anchor_file": str(end_file),
            "target_file_best": None,
            "confidence": 0.86 if section_kind == "author_index" else 0.84,
            "raw_json": {
                "source_file": str(end_file),
                "section_kind": section_kind,
                "section_file": str(section_file),
            },
        }
        entries.append(entry)
        helper_entries.append(
            {
                "entry_id": entry_key.replace(":", "_"),
                "lemma_raw": lemma_raw or entry_raw,
                "query_names": build_query_names(lemma_raw or entry_raw, entry_raw),
                "page_hints": [ref["page_ref_raw"] for ref in extracted_refs if ref["page_ref_raw"]],
                "page_hint_ints": [ref["page_ref_int"] for ref in extracted_refs if isinstance(ref["page_ref_int"], int)],
                "context_raw": entry_raw,
            }
        )
        for ref_order, ref in enumerate(extracted_refs, start=1):
            ref_entry = {
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
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": str(section_file),
                "editorial_anchor_file": str(end_file),
                "confidence": 0.79 if ref["page_ref_int"] is not None else 0.55,
                "raw_json": {
                    "source_file": str(end_file),
                    "section_kind": section_kind,
                },
            }
            refs.append(ref_entry)
        if isinstance(inferred_page, int):
            last_page_int = inferred_page
        block = []

    for path in [section_file]:
        for line in lines:
            if is_decorative(line):
                continue
            if line == section_heading:
                continue
            if not block and is_major_heading(line):
                current_node_order += 1
                current_node_key = f"{VOLUME_ID}:node:{section_kind}:{current_node_order:03d}"
                current_node_label = line
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": current_node_order,
                        "node_kind": "heading_group",
                        "label_raw": line,
                        "label_norm": line.lower(),
                        "label_sort": line.lower(),
                        "node_level": 1,
                        "confidence": 0.97,
                        "raw_json": {"source_file": str(path), "section_kind": section_kind},
                    }
                )
                continue
            block.append(line)
            if PAGE_REF_RE.search(line):
                flush_block(path)

    if block:
        flush_block(section_file)

    # Backfill node relationships for the author index so that grouped works stay under the right heading.
    if section_kind == "author_index":
        current_author_node = None
        node_lookup: dict[str, dict[str, Any]] = {node["label_raw"]: node for node in nodes}
        for entry in entries:
            text = entry["entry_raw"]
            if text and not NUM_RE.search(text) and is_major_heading(text):
                current_author_node = node_lookup.get(text)
                continue
            if current_author_node is not None:
                entry["parent_node_key"] = current_author_node["node_key"]
    return nodes, entries, refs, helper_entries


def build_section_payload(section_def: dict[str, Any], source_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    files = [file_by_num(source_root, number) for number in section_def["files"]]
    lines: list[str] = []
    for path in files:
        lines.extend(extract_lines(path))
    nodes, entries, refs, helper_entries = parse_page_lines(
        lines,
        section_kind=section_def["section_kind"],
        section_key=section_def["section_key"],
        section_heading=section_def["heading_raw"],
        section_file=files[0],
    )
    section = {
        "section_key": section_def["section_key"],
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": section_def["section_order"],
        "section_kind": section_def["section_kind"],
        "heading_raw": section_def["heading_raw"],
        "heading_norm": section_def["heading_norm"],
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(files[0]),
        "file_end": str(files[-1]),
        "confidence": 0.96 if section_def["section_kind"] == "author_index" else 0.95,
        "raw_json": {
            "source_files": [str(path) for path in files],
            "section_kind_reason": section_def["notes"],
        },
    }
    return section, nodes, entries, refs, helper_entries


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json)


def align_helper_results(entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any]) -> None:
    helper_map = {item["entry_id"]: item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    ref_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        ref_map[ref["entry_key"]].append(ref)

    for entry in entries:
        entry_id = entry["entry_key"].replace(":", "_")
        helper_item = helper_map.get(entry_id)
        if not helper_item:
            entry["raw_json"]["helper_status"] = "missing"
            continue
        entry["raw_json"]["helper_status"] = helper_item.get("status")
        entry["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
        entry["raw_json"]["helper_candidates"] = helper_item.get("candidates", [])[:3]
        best = helper_item.get("best_candidate") or {}
        best_file = best.get("file")
        best_prob = best.get("probability")
        entry["target_file_best"] = best_file
        if best_file:
            entry["editorial_anchor_file"] = best_file
        if isinstance(best_prob, (int, float)):
            entry["confidence"] = max(entry["confidence"], min(0.99, float(best_prob)))
        for ref in ref_map.get(entry["entry_key"], []):
            ref["target_file"] = best_file
            ref["target_file_probability"] = best_prob
            if isinstance(best_prob, (int, float)):
                ref["confidence"] = max(ref["confidence"], min(0.99, float(best_prob)))
            ref["raw_json"]["helper_status"] = helper_item.get("status")
            ref["raw_json"]["helper_best_candidate"] = best
            ref["raw_json"]["helper_candidates"] = helper_item.get("candidates", [])[:3]


def make_todo(intermediate_dir: Path, completed: list[str], pending: list[str]) -> dict[str, Any]:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PL106 alphabetical payload from front-matter author index and closing ordo rerum.",
        "completed": completed,
        "pending": pending,
        "blocked": [],
        "notes": [
            "PL106 has both a front-matter ELENCHUS and a closing ORDO RERUM block.",
            "Use helper output only to anchor OCR pages; keep OCR literals in the payload."
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)
    return todo


def assemble_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for section_def in SECTION_DEFS:
        section, section_nodes, section_entries, section_refs, section_helper_entries = build_section_payload(section_def, source_root)
        sections.append(section)
        nodes.extend(section_nodes)
        entries.extend(section_entries)
        refs.extend(section_refs)
        helper_entries.extend(section_helper_entries)
        evidence_files.extend(section["raw_json"]["source_files"])

    request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": HELPER_TOP_K,
            "adjacency_window": HELPER_ADJACENCY_WINDOW,
        },
        "entries": helper_entries,
    }
    write_json(helper_request_json, request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    align_helper_results(entries, refs, helper_output)

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the front-matter ELENCHUS author/work list and the closing ORDO RERUM table of contents from OCR, then anchored the cited pages with the helper.",
        "evidence_files": sorted(set(evidence_files)),
    }
    notes = [
        "PL106 contains two index-like blocks: a front-matter author/work inventory and a closing ordo rerum list.",
        "OCR page numbers and physical file suffixes do not align literally; helper evidence was used only to anchor the target files.",
    ]
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": "Patrologia Latina 106",
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
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"], "generated_at": payload["generated_at"]})

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL106 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    make_todo(
        args.intermediate_dir,
        completed=["Read OCR tail/front matter and identified the two index-like sections."],
        pending=["Run helper, align targets, and validate the final payload."],
    )
    assemble_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir, args.output_file)


if __name__ == "__main__":
    main()
