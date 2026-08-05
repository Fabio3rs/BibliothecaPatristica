#!/usr/bin/env python3
"""Usage: build the PL096 ORDO RERUM payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl096_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL096/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL096_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL096 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL096_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BLOCK_RE = re.compile(
    r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>',
    re.IGNORECASE | re.DOTALL,
)
PAGE_TOKEN_RE = re.compile(
    r"(?P<prefix>.*?)(?P<token>(?:col\.?\s*)?\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)(?:\s*[.)])?\s*$",
    re.IGNORECASE,
)
HEADING_ROMAN_RE = re.compile(r"^\s*[IVXLCDM]+\.\s*(?:[-—]\s*)?.*$", re.IGNORECASE)
HEADING_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"APPENDIX(?:ES)?|ANONYMI|S[ÆAE]CULI|ORDO RERUM|"
    r"CYRICIUS|S\. HILDEFONSUS|S\. LEODEGARIUS|LEO PAPA II|BENEDICTUS II|"
    r"JOANNES PAPA V|S\. JULIANUS|S\. LULLUS|ELIPANDUS|RACHIO|ANGELRAMNUS|"
    r"VICBODUS|ADRIANUS PAPA I|ISIDORUS PACENSIS|ABEDOC ET ETHELVOLFUS|"
    r"MARCUS, IDRONTINUS EPISCOPUS|PETRUS ARCHIDIACONUS|CATULFUS|"
    r"CONSTANS SACERDOS|GARNERIVS ABBAS|S[ÆAE]CULI VIII MONUMENTA ECCLESIASTICA|"
    r"PIPPINI ET CAROLOMANNI FRANCORUM REGUM DIPLOMATA"
    r")",
    re.IGNORECASE,
)
NOISE_LINES = {"Digitized by Google", "||", "."}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip()
    return value or None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line and line not in NOISE_LINES]
        if lines:
            blocks.append({"kind": kind, "lines": lines})
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        blocks = extract_blocks(path)
        head_lines: list[str] = []
        for block in blocks[:3]:
            if block["kind"] in {"cabecalho", "outro"}:
                head_lines.extend(block["lines"][:3])
        if not head_lines:
            head_lines = [line for block in blocks[:2] for line in block["lines"][:3]]
        for line in head_lines:
            for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", line):
                page = int(match.group(1))
                page_map.setdefault(page, str(path))
    return page_map


def lookup_target(page: int | None, page_map: dict[int, str]) -> tuple[str | None, float | None]:
    if page is None:
        return None, None
    if page in page_map:
        return page_map[page], 0.99
    candidates: list[tuple[int, int, str]] = []
    for candidate_page, candidate_path in page_map.items():
        distance = abs(candidate_page - page)
        if distance <= 3:
            candidates.append((distance, candidate_page, candidate_path))
        elif str(candidate_page)[-3:] == str(page)[-3:]:
            candidates.append((100 + distance, candidate_page, candidate_path))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2], 0.72


def is_heading_text(text: str) -> bool:
    cleaned = normalize(text) or ""
    if not cleaned:
        return False
    if HEADING_PREFIX_RE.match(cleaned):
        return True
    if HEADING_ROMAN_RE.match(cleaned):
        return True
    letters = [ch for ch in cleaned if ch.isalpha()]
    if letters and all(ch.isupper() or not ch.isalpha() for ch in cleaned):
        return True
    if letters and cleaned.count(" ") <= 8 and cleaned.upper() == cleaned and len(cleaned) <= 140:
        return True
    return False


def heading_level(text: str, parent_exists: bool) -> int:
    cleaned = normalize(text) or ""
    if HEADING_ROMAN_RE.match(cleaned) and parent_exists:
        return 2
    return 1


def parse_entry_line(text: str) -> tuple[str, int | None, str | None, str | None, str | None]:
    cleaned = normalize(text) or ""
    match = PAGE_TOKEN_RE.match(cleaned)
    if not match:
        return cleaned, None, None, None, None
    prefix = normalize(match.group("prefix")) or ""
    token = normalize(match.group("token")) or ""
    page_part = re.search(r"\d{1,4}", token)
    page = int(page_part.group(0)) if page_part else None
    page_col = "col." if token.lower().startswith("col") else None
    if prefix:
        entry_text = prefix
    else:
        entry_text = cleaned[: match.start("token")].rstrip(" .")
    return entry_text, page, token, page_col, cleaned


def is_page_header_line(text: str) -> bool:
    cleaned = normalize(text) or ""
    upper = cleaned.upper()
    if "ORDO RERUM" in upper or "QUAE IN HOC TOMO CONTINENTUR" in upper or "QUÆ IN HOC TOMO CONTINENTUR" in upper:
        if re.search(r"\d{1,4}", cleaned):
            return True
    if re.match(r"^\d{1,4}\s+(?:ORDO RERUM|QU[AEÆ] IN HOC TOMO CONTINENTUR)", cleaned, re.IGNORECASE):
        return True
    return False


def build_payload(
    *,
    volume_id: str,
    source_root: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    file_map = {file_seq(path): str(path) for path in files}
    page_map = build_page_map(files)
    helper_output = {}
    if helper_output_json.exists():
        helper_output = json.loads(helper_output_json.read_text(encoding="utf-8"))

    section_files = [path for path in files if 787 <= file_seq(path) <= 798]
    if not section_files:
        raise SystemExit("No OCR files found for the PL096 ORDO RERUM window.")

    section_start_file = str(section_files[0])
    section_end_file = str(section_files[-1])
    section_key = f"{volume_id}:alpha:ordo_rerum:001"

    sections = [
        {
            "section_key": section_key,
            "volume_id": volume_id,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur.",
            "heading_letter": None,
            "page_start": 1589,
            "page_end": 1612,
            "file_start": section_start_file,
            "file_end": section_end_file,
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "Editorial closing contents list recovered from the OCR tail.",
                "evidence_files": [str(path) for path in section_files],
            },
        }
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    current_node_key: str | None = None
    current_node_label: str | None = None
    node_counter = 0
    entry_counter = 0
    pending_parts: list[str] = []
    section_started = False

    def add_node(label: str, parent_key: str | None, node_level: int) -> str:
        nonlocal node_counter, current_node_key, current_node_label
        node_counter += 1
        node_key = f"{volume_id}:node:{node_counter:03d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent_key,
                "node_order": node_counter,
                "node_kind": "heading_group",
                "label_raw": label,
                "label_norm": normalize(label).lower() if normalize(label) else None,
                "label_sort": normalize(label).lower() if normalize(label) else None,
                "node_level": node_level,
                "confidence": 0.96 if node_level == 1 else 0.92,
                "raw_json": {
                    "source_file": current_source_file,
                    "node_level_reason": "roman-numeral subheading" if node_level == 2 else "top-level heading",
                },
            }
        )
        current_node_key = node_key
        current_node_label = label
        return node_key

    def flush_pending_as_node() -> None:
        nonlocal pending_parts
        if not pending_parts:
            return
        candidate = normalize(" ".join(pending_parts)) or ""
        if is_heading_text(candidate):
            parent = current_node_key if HEADING_ROMAN_RE.match(candidate) and current_node_key else None
            level = heading_level(candidate, parent is not None)
            add_node(candidate, parent, level)
            pending_parts = []

    current_source_file = ""

    for path in section_files:
        current_source_file = str(path)
        blocks = extract_blocks(path)
        for block in blocks:
            if block["kind"] not in {"cabecalho", "texto_principal"}:
                continue
            for raw_line in block["lines"]:
                line = normalize(raw_line) or ""
                if not line or line in NOISE_LINES:
                    continue

                if not section_started:
                    if "ORDO RERUM" in line.upper() and "CONTINENTUR" in line.upper():
                        section_started = True
                    continue

                if is_page_header_line(line):
                    pending_parts = []
                    continue

                entry_text, page, page_token_raw, page_col, cleaned = parse_entry_line(line)
                if page is None:
                    pending_parts.append(line)
                    continue

                # If the lines immediately before this one form a pure heading,
                # materialize them as a node and keep the current entry separate.
                if pending_parts:
                    candidate = normalize(" ".join(pending_parts)) or ""
                    if is_heading_text(candidate):
                        parent_key = None
                        level = heading_level(candidate, False)
                        if HEADING_ROMAN_RE.match(candidate) and current_node_key:
                            parent_key = current_node_key
                            level = 2
                        add_node(candidate, parent_key, level)
                        pending_parts = []

                if pending_parts:
                    combined_entry = normalize(" ".join([*pending_parts, entry_text])) or entry_text
                    pending_parts = []
                else:
                    combined_entry = entry_text

                if not combined_entry:
                    continue

                if current_node_key is None and is_heading_text(combined_entry):
                    add_node(combined_entry, None, 1)
                    continue

                entry_counter += 1
                entry_key = f"{volume_id}:entry:{entry_counter:04d}"
                target_file, target_prob = lookup_target(page, page_map)
                ref_raw = page_token_raw or str(page)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": 1,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref_raw,
                        "page_ref_raw": ref_raw,
                        "page_ref_int": page,
                        "page_ref_col": page_col,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": target_file,
                        "target_file_probability": target_prob,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": current_source_file,
                        "confidence": 0.92 if target_file else 0.66,
                        "raw_json": {
                            "source_file": current_source_file,
                            "page_token_raw": ref_raw,
                            "section_kind": "ordo_rerum",
                            "target_lookup": "exact" if target_prob == 0.99 else ("fuzzy" if target_file else "unresolved"),
                        },
                    }
                )
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": section_key,
                        "parent_node_key": current_node_key,
                        "entry_order": entry_counter,
                        "entry_kind": "heading_group",
                        "lemma_raw": combined_entry,
                        "lemma_display": combined_entry,
                        "lemma_norm": normalize(combined_entry).lower() if normalize(combined_entry) else None,
                        "lemma_sort": normalize(combined_entry).lower() if normalize(combined_entry) else None,
                        "entry_raw": combined_entry,
                        "context_raw": combined_entry,
                        "heading_letter": None,
                        "inferred_printed_page": page,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": current_source_file,
                        "target_file_best": target_file,
                        "confidence": 0.96 if target_file else 0.72,
                        "raw_json": {
                            "source_file": current_source_file,
                            "section_kind": "ordo_rerum",
                            "page_token_raw": ref_raw,
                            "page_ref_source": "explicit",
                            "helper_output_path": str(helper_output_json),
                            "helper_status": helper_output.get("status"),
                        },
                    }
                )

        # Keep any trailing heading lines buffered so the next page-bearing line can decide whether
        # they are a node or a wrapped entry.
        if pending_parts and is_heading_text(" ".join(pending_parts)):
            # Leave the heading in place until the next page-bearing line; this lets the heading
            # become a node while the following numbered line remains the first child entry.
            flush_pending_as_node()

    # Any leftover OCR fragment is too incomplete to treat as an entry.
    pending_parts = []

    if entries and normalize(entries[0]["entry_raw"]).lower().startswith("pro animæ meæ remedio"):
        bad_entry_key = entries[0]["entry_key"]
        entries = entries[1:]
        refs = [ref for ref in refs if ref["entry_key"] != bad_entry_key]

    volume = {
        "volume_id": volume_id,
        "collection": "PL",
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina 96",
        "notes": "Final contents list (ordo rerum) recovered from the OCR tail.",
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the closing ordo rerum contents block from files 787-798. "
            "The OCR is stable enough for line-level extraction, but the payload preserves editorial hierarchy conservatively. "
            "The opening non-index residue before the contents heading was discarded."
        ),
        "evidence_files": [str(path) for path in section_files],
    }
    notes = [
        {
            "note_key": f"{volume_id}:note:1",
            "note_kind": "extraction_note",
            "note_raw": "The section is a closing contents list, not an alphabetical subject index; headings without page numbers were preserved as nodes.",
            "confidence": 0.95,
            "raw_json": {
                "helper_output_path": str(helper_output_json),
                "page_map_size": len(page_map),
                "target_mapping_mode": "exact_header_map_with_fuzzy_fallback",
                "cross_check": "The volume-front contents list in file 010 repeats the same editorial structure and confirms the ordo rerum block.",
            },
        }
    ]
    generated_at = now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    for name, fragment in {
        "volume.json": volume,
        "sections.json": sections,
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": scripture_refs,
        "coverage.json": coverage,
        "notes.json": notes,
        "manifest.json": {"volume_id": volume_id, "generated_at": generated_at, "updated_at": generated_at},
    }.items():
        (intermediate_dir / name).write_text(json.dumps(fragment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL096 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(
        volume_id="PL096",
        source_root=args.source_root,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
    )
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
