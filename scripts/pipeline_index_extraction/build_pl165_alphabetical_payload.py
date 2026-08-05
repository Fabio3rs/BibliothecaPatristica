#!/usr/bin/env python3
"""Usage:
  Build the PL165 alphabetical index payload and helper request from the OCR tail.

  Request phase:
    python scripts/pipeline_index_extraction/build_pl165_alphabetical_payload.py \
      --source-root /homessddata/Projects/pdfocr/teste/PL165/text \
      --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL165_helper_request.json \
      --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL165_helper_output.json \
      --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL165 \
      --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL165_alphabetical_indices.json \
      --mode request

  Final phase:
    python scripts/pipeline_index_extraction/build_pl165_alphabetical_payload.py \
      --source-root /homessddata/Projects/pdfocr/teste/PL165/text \
      --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL165_helper_request.json \
      --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL165_helper_output.json \
      --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL165 \
      --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL165_alphabetical_indices.json \
      --mode final
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


VOLUME_ID = "PL165"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 165"
SECTION_KEY = f"{VOLUME_ID}:section:1"
SECTION_HEADING_RAW = "INDEX RERUM ET SENTENTIARUM MEMORABILIUM."
SECTION_HEADING_NORM = "index rerum et sententiarum memorabilium"
SECTION_START_FILE = None

INDEX_START_RE = re.compile(r"^INDEX\s+RERUM\s+ET\s+SENTENTIARUM\s+MEMORABILIUM\.?$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-Z]$")
PAGE_RE = re.compile(r"(?<!\d)([IVXLCDM]+|\d{1,4})(?:\s*,\s*|\s+)(\d{1,4})(?:\s*(?:et\s+seqq?\.?|et\s+\d{1,4}|seqq?\.|seq\.))?", re.IGNORECASE)
HEADING_RE = re.compile(
    r"^(?:[A-ZÆŒ]{2,}(?:\s+[A-Z0-9][^.]*)?|[A-Z][A-Za-zÆŒæœ][^.]*)\.$"
)
CONTINUATION_START_RE = re.compile(r"^(?:Ead\.|Ibid\.|Idem\.|Illum\b|Illud\b|Item\b|Quia\b|Quod\b|Quae\b|Quam\b|Cum\b|Sed\b|Sicut\b|De\b|Et\b|Hic\b|Hoc\b|In\b|Ubi\b|Ut\b|Vide\b)", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value is not None else None


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[str] = []
    for match in re.finditer(r'<bloco[^>]*tipo="texto_principal"[^>]*>(.*?)</bloco>', text, re.I | re.S):
        body = re.sub(r"<[^>]+>", " ", match.group(1))
        body = body.replace("\r", "\n")
        blocks.append(body)
    return blocks


def clean_lines(files: list[Path]) -> list[str]:
    lines: list[str] = []
    started = False
    for path in files:
        for block in extract_blocks(path):
            for raw in block.splitlines():
                line = norm(raw)
                if not line:
                    lines.append("")
                    continue
                if line == "INDEX" or INDEX_START_RE.match(line):
                    started = True
                    continue
                if line.startswith("RERUM ET SENTENTIARUM"):
                    started = True
                    continue
                if line.startswith("Revocantur lector ad numeros crassiori charactere textui insertos"):
                    continue
                if not started:
                    continue
                if line == "Digitized by Google":
                    continue
                lines.append(line)
    return lines


def split_into_parts(lines: list[str]) -> list[list[str]]:
    parts: list[list[str]] = []
    current: list[str] = []
    current_letter = None

    def flush() -> None:
        nonlocal current
        if current:
            parts.append(current)
            current = []

    for line in lines:
        if not line:
            flush()
            continue
        if LETTER_RE.fullmatch(line):
            flush()
            current_letter = line
            parts.append([line])
            continue
        if line.startswith("INDEX ") or line.startswith("RERUM ET SENTENTIARUM"):
            continue
        if current_letter and not current:
            current.append(current_letter)
            current_letter = None
        if current and HEADING_RE.match(line) and not CONTINUATION_START_RE.match(line):
            flush()
        current.append(line)
    flush()
    return parts


def make_entry_text(part: list[str]) -> str:
    lines = [line for line in part if line and not LETTER_RE.fullmatch(line)]
    return norm(" ".join(lines)) or ""


def split_headings(text: str) -> list[str]:
    if not text:
        return []
    pieces: list[str] = []
    # Split only on obvious heading transitions after a material locator or a line break.
    parts = re.split(
        r"(?<=\d\.)\s+(?=(?:[A-ZÆŒ]{2,}(?:\s+[A-Z0-9][^.]*)?|[A-Z][A-Za-zÆŒæœ][^.]*?)\.)",
        text,
    )
    for part in parts:
        part = norm(part) or ""
        if part:
            pieces.append(part)
    return pieces


def extract_entries(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    lines = clean_lines(files)
    raw_parts = split_into_parts(lines)
    evidence_files = [str(p) for p in files]

    # Merge the small false split between Confirmatio and the follow-on Deus entry.
    merged_parts: list[str] = []
    for part in raw_parts:
        text = make_entry_text(part)
        if not text:
            continue
        merged_parts.append(text)

    entries_raw: list[str] = []
    i = 0
    while i < len(merged_parts):
        text = merged_parts[i]
        if text.startswith("Confirmatio.") and i + 1 < len(merged_parts) and merged_parts[i + 1].startswith("Ead. pag."):
            text = f"{text} {merged_parts[i + 1]}"
            i += 1
        entries_raw.append(text)
        i += 1

    # Expand the obvious multi-heading OCR runs into separate logical entries.
    expanded: list[str] = []
    for text in entries_raw:
        if text.startswith("Luxuria.") and "Manichæi et Ebionitæ." in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Vide Arca.") and "Nomen Domini." in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Oratio.") and "Paradisus cœlestis." in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Sacrificium.") and "Sanctorum vita" in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Scandulum.") and "Scripturae divinae." in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Avaritia.") and "BALAAM." in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Beatitudines.") and "Blasphemia." in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        if text.startswith("Confirmatio.") and "CONSTANTINUS M" in text:
            expanded.extend([p for p in split_headings(text) if p])
            continue
        expanded.append(text)

    entries: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []

    def ensure_letter_node(letter: str) -> str:
        if letter not in letter_to_node:
            node_key = f"{VOLUME_ID}:section:1:node:{len(nodes)+1:03d}"
            letter_to_node[letter] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "node_order": len(nodes) + 1,
                    "node_kind": "letter_group",
                    "label_raw": letter,
                    "label_norm": letter.lower(),
                    "label_sort": letter.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {},
                }
            )
        return letter_to_node[letter]

    for idx, text in enumerate(expanded, 1):
        entry_key = f"{VOLUME_ID}:entry:{idx:04d}"
        first_heading = text.split(".", 1)[0].strip()
        lemma_raw = first_heading if first_heading else text
        entry_kind = "cross_reference" if lemma_raw.lower().startswith("vide ") else "lemma"
        heading_letter = None
        if lemma_raw:
            lead = re.sub(r"^[^A-Za-zÆŒæœ]+", "", lemma_raw)
            heading_letter = lead[:1].upper() if lead else None
            if heading_letter and heading_letter.isalpha():
                parent_node_key = ensure_letter_node(heading_letter)
            else:
                parent_node_key = None
        else:
            parent_node_key = None

        page_refs = []
        for match in PAGE_RE.finditer(text):
            raw = match.group(0).strip()
            page_refs.append(raw)
        # De-duplicate while preserving order.
        seen_refs: set[str] = set()
        unique_refs: list[str] = []
        for ref in page_refs:
            if ref not in seen_refs:
                unique_refs.append(ref)
                seen_refs.add(ref)

        if unique_refs:
            helper_entries.append(
                {
                    "entry_id": f"{VOLUME_ID.lower()}_{idx:04d}",
                    "lemma_raw": lemma_raw,
                    "query_names": [lemma_raw] if lemma_raw else [text[:80]],
                    "page_hints": [r.split(",")[-1].strip().split()[-1] for r in unique_refs[:4] if re.search(r"\d", r)],
                    "page_hint_ints": [int(re.search(r"(\d{1,4})(?!.*\d)", r).group(1)) for r in unique_refs[:4] if re.search(r"(\d{1,4})(?!.*\d)", r)],
                    "context_raw": text,
                }
            )

        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": parent_node_key,
                "entry_order": idx,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw or None,
                "lemma_display": lemma_raw or None,
                "lemma_norm": norm(lemma_raw).lower() if lemma_raw else None,
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": text,
                "context_raw": text,
                "heading_letter": heading_letter,
                "inferred_printed_page": int(re.search(r"(\d{1,4})(?!.*\d)", unique_refs[0]).group(1)) if unique_refs else None,
                "section_start_file": str(files[0]) if files else None,
                "editorial_anchor_file": str(files[min(idx - 1, len(files) - 1)]) if files else None,
                "target_file_best": None,
                "confidence": 0.78 if unique_refs else 0.66,
                "raw_json": {
                    "source_file_hint": str(files[min(idx - 1, len(files) - 1)]) if files else None,
                    "entry_kind_reason": "cross-reference without a material locator" if entry_kind == "cross_reference" else "alphabetical index entry recovered from OCR tail",
                    "secondary_headings": [m.group(0).rstrip(".") for m in re.finditer(r"(?m)^(?:[A-ZÆŒ]{2,}|[A-Z][A-Za-zÆŒæœ][^.]*)\.", text) if m.group(0).rstrip(".") != lemma_raw],
                },
            }
        )

    return entries, nodes, helper_entries, evidence_files


def build_helper_request(source_root: Path, helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--mode", choices={"request", "final"}, required=True)
    args = ap.parse_args()

    files = [p for p in discover_files(args.source_root) if 661 <= file_seq(p) <= 677]
    entries, nodes, helper_entries, evidence_files = extract_entries(files)

    todo_path = args.intermediate_dir / "todo.json"
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve helper anchors and finalize PL165 alphabetical index payload",
        "completed": [
            "ocr tail inspected",
            "preliminary alphabetical entries segmented",
        ],
        "pending": [
            "run index_target_locator helper",
            "review helper output and write final payload",
        ],
        "blocked": [],
        "notes": [
            "The index section runs from file 661 through 677 and stops before the separate ORDO RERUM spread at 678.",
            "Target files are left unresolved unless the helper or OCR gives a stable anchor.",
        ],
    }
    write_json(todo_path, todo)

    helper_request = build_helper_request(args.source_root, helper_entries)
    write_json(args.helper_request_json, helper_request)

    if args.mode == "request":
        return

    helper_output = load_json(args.helper_output_json, {})
    helper_by_entry_id: dict[str, Any] = {}
    if isinstance(helper_output, dict):
        for key in ("entries", "results", "items"):
            if key in helper_output and isinstance(helper_output[key], list):
                for item in helper_output[key]:
                    eid = item.get("entry_id") or item.get("id")
                    if eid:
                        helper_by_entry_id[eid] = item

    # Rebuild entries and refs with helper evidence when available.
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    final_entries: list[dict[str, Any]] = []
    for entry in entries:
        entry_id = entry["entry_key"].split(":")[-1].replace("entry", VOLUME_ID.lower())
        helper_item = helper_by_entry_id.get(f"{VOLUME_ID.lower()}_{int(entry['entry_order']):04d}")
        if helper_item:
            entry["raw_json"]["helper_output"] = helper_item
            best = helper_item.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best["file"]
                entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0))
        for ref_idx, match in enumerate(PAGE_RE.finditer(entry["entry_raw"]), 1):
            raw = match.group(0).strip()
            digits = re.findall(r"(\d{1,4})", raw)
            page_int = int(digits[-1]) if digits else None
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": raw,
                    "page_ref_raw": raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": entry["target_file_best"],
                    "target_file_probability": helper_item.get("best_candidate", {}).get("probability") if helper_item else None,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.78,
                    "raw_json": {"source": "ocr_tail"},
                }
            )
        final_entries.append(entry)

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(files[0]) if files else None,
        "file_end": str(files[-1]) if files else None,
        "confidence": 0.96,
        "raw_json": {
            "section_kind_reason": "Alphabetical subject index with memorabilium entries and remissions.",
            "evidence_files": evidence_files,
            "notes": [
                "The alphabetical index occupies files 661-677.",
                "File 678 begins the separate ORDO RERUM spread and is excluded from the index section.",
            ],
        },
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section],
        "nodes": nodes,
        "entries": final_entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": {
            "entries_status": "recovered_with_residual_ambiguity",
            "entries_status_reason": "Recovered the PL165 alphabetical subject index from OCR files 661-677; several entries remain materially ambiguous because the OCR tail merges adjacent lemmas and the helper cannot always distinguish the physical OCR file from the printed-page locator.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "Index section recovered from the OCR tail window only.",
            "Helper output is preserved in entry raw_json where available, but unresolved material locators remain null when the OCR evidence is weak.",
        ],
    }

    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
