#!/usr/bin/env python3
"""Build the PL135 alphabetical-index payload from OCR tail files.
Usage: python scripts/pipeline_index_extraction/pl135_generate_payload.py --source-root /homessddata/Projects/pdfocr/teste/PL135/text --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL135_helper_output.json --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL135_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any


SECTION_INDEX_FILES = [
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-550.txt",
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-551.txt",
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-552.txt",
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-553.txt",
]

SECTION_ORDO_FILES = [
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-554.txt",
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-555.txt",
    "ed25e51c-f4cf-45cc-adc2-2da84c8556e8-556.txt",
]


def ascii_norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", ascii_norm(text).lower()).strip()


def slug_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_norm(text).lower()).strip("-")


def is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s in {"Digitized by Google", "PATROL. CXXXV. 35 Digitized by Google"}:
        return True
    if re.fullmatch(r"-{5,}", s):
        return True
    if s in {
        "INDEX",
        "IN",
        "FLODOARDI HISTORIAM ECCLESIÆ REMENSIS.",
        "ORDO RERUM",
        "QUÆ IN HOC TOMO CONTINENTUR.",
        "FLODOARDUS CANONICUS REMENSIS.",
        "HISTORIÆ REMENSIS ECCLESIÆ LIBRI QUATUOR.",
        "LIBER PRIMUS.",
        "LIBER SECUNDUS.",
        "LIBER TERTIUS.",
        "LIBER QUARTUS.",
        "AUCTARIUM FLODOARDI. 327",
    }:
        return True
    if re.fullmatch(r"\d{3,4}", s):
        return True
    if s.startswith("PATROL.") or s.startswith("Ex typis"):
        return True
    if s.startswith("<") or s.startswith("[!--"):
        return True
    return False


def is_single_letter(line: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]", line.strip()))


def extract_blocks(text: str) -> list[tuple[str, str]]:
    return re.findall(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', text, flags=re.S)


def extract_lines(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    items: list[dict[str, Any]] = []
    for block_type, block_text in extract_blocks(text):
        raw_lines = [ln.strip() for ln in block_text.splitlines()]
        for line in raw_lines:
            if not line:
                continue
            items.append({"file": str(path), "block_type": block_type, "line": line})
    return items


def header_pages(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8")
    pages: list[int] = []
    for block_type, block_text in extract_blocks(text):
        if block_type != "cabecalho":
            continue
        for num in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", block_text):
            pages.append(int(num))
    return pages


def choose_entry_kind(section_id: str, entry_raw: str) -> str:
    s = entry_raw.strip()
    if section_id == "index" and s.startswith("INDEX IN HIST. REM. FLODOARDI"):
        return "heading_group"
    if section_id == "ordo":
        if re.match(r"^(FLODOARDUS|HISTORIÆ|LIBER |AUCTARIUM|SCHOLIA|APPENDIX|ANNALS|FLODOARDI OPUSCULA|SANCTA |GUMPOLDUS |ERACLIUS |JOANNES |\b[IVX]+\b)", s):
            return "heading_group"
        if s.startswith("Cap. ") or re.match(r"^[IVX]+\.", s):
            return "lemma"
        return "lemma"
    return "lemma"


def parse_lemma(entry_raw: str) -> str | None:
    s = entry_raw.strip()
    s = re.sub(r"^\d+\.\s+(?=[A-Z])", "", s)
    m = re.search(r"(?:[;,]|\.\s+)\s*\d{1,4}(?:\s*(?:et\s*seqq?\.?|et\s*seq\.?|seqq?\.?|seq\.?))?", s)
    if m:
        lemma = s[: m.start()].strip()
        if lemma:
            return lemma
    nums = list(re.finditer(r"\b\d{1,4}\b", s))
    if nums:
        first = nums[0]
        prefix = s[: first.start()].strip(" ,.;")
        if prefix and not re.fullmatch(r"\d+", prefix):
            return prefix
        tail = s[first.end():].strip(" ,.;")
        if tail:
            return tail.split(".")[0].strip()
    return s.rstrip(".")


def extract_refs(entry_raw: str, target_map: dict[int, str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for m in re.finditer(r"\b(\d{1,4})(?:\s*(et\s*seqq?\.?|et\s*seq\.?|seqq?\.?|seq\.?))?", entry_raw, flags=re.I):
        num = int(m.group(1))
        raw = m.group(0).strip()
        if re.fullmatch(r"\d{3,4}", entry_raw.strip()) and raw == entry_raw.strip():
            continue
        key = (raw.lower(), num)
        if key in seen:
            continue
        seen.add(key)
        target = target_map.get(num)
        refs.append(
            {
                "ref_kind": "editorial_range" if re.search(r"seqq?|seq\.?", raw, flags=re.I) else "editorial_page",
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": num,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target,
                "target_file_probability": 1.0 if target else 0.0,
                "confidence": 0.95 if target else 0.5,
            }
        )
    return refs


def normalize_entry_text(lines: list[str]) -> str:
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_target_map(source_root: Path) -> dict[int, str]:
    target_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        pages = header_pages(path)
        for line in text.splitlines()[:20]:
            s = line.strip()
            if re.fullmatch(r"\d{1,4}", s):
                pages.append(int(s))
        for page in pages:
            target_map.setdefault(page, str(path))
    return target_map


def load_helper_map(helper_output: Path) -> dict[str, dict[str, Any]]:
    if not helper_output.exists():
        return {}
    data = json.loads(helper_output.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("entries", []):
        best = item.get("best_candidate") or {}
        out[item["entry_id"]] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best,
            "candidates": item.get("candidates", [])[:3],
        }
    return out


def make_section(
    *,
    section_key: str,
    volume_id: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    heading_norm: str,
    page_start: int | None,
    page_end: int | None,
    file_start: str,
    file_end: str,
    confidence: float,
    section_kind_reason: str,
) -> dict[str, Any]:
    return {
        "section_key": section_key,
        "volume_id": volume_id,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": heading_norm,
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": file_start,
        "file_end": file_end,
        "confidence": confidence,
        "raw_json": {
            "section_kind_reason": section_kind_reason,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-output", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    volume_id = "PL135"
    collection = "PL"
    source_root = args.source_root
    target_map = build_target_map(source_root)
    helper_map = load_helper_map(args.helper_output)

    section_index_key = f"{volume_id}-index-in-hist-rem-flodoardi"
    section_ordo_key = f"{volume_id}-ordo-rerum"
    sections = [
        make_section(
            section_key=section_index_key,
            volume_id=volume_id,
            section_order=1,
            section_kind="onomastic_mixed",
            heading_raw="INDEX IN HIST. REM. FLODOARDI.",
            heading_norm=norm_key("INDEX IN HIST. REM. FLODOARDI."),
            page_start=1091,
            page_end=1098,
            file_start=str(source_root / SECTION_INDEX_FILES[0]),
            file_end=str(source_root / SECTION_INDEX_FILES[-1]),
            confidence=0.98,
            section_kind_reason="Alphabetical index of persons, places, institutions, and events in Flodoard's history; mixed onomastic material with analytic cross-entries.",
        ),
        make_section(
            section_key=section_ordo_key,
            volume_id=volume_id,
            section_order=2,
            section_kind="ordo_rerum",
            heading_raw="ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            heading_norm=norm_key("ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."),
            page_start=1099,
            page_end=1102,
            file_start=str(source_root / SECTION_ORDO_FILES[0]),
            file_end=str(source_root / SECTION_ORDO_FILES[-1]),
            confidence=0.99,
            section_kind_reason="Editorial closing contents table (ordo rerum), separate from the alphabetical index.",
        ),
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    letter_node_keys: dict[str, str] = {}

    def add_letter_node(letter: str, section_key: str, node_order: int) -> str:
        if letter in letter_node_keys:
            return letter_node_keys[letter]
        node_key = f"{section_key}-{letter.lower()}"
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
                "confidence": 0.98,
                "raw_json": {"source": "implicit_letter_transition" if letter not in {"A", "B", "C", "D", "I", "J", "L", "M", "N", "O", "P", "R", "S", "T", "U", "V", "W", "Y", "Z"} else "explicit_or_implicit"},
            }
        )
        letter_node_keys[letter] = node_key
        return node_key

    current_letter = None
    node_order = 0
    entry_order = 0

    def flush_buffer(section_key: str, current_file: str, buffer_lines: list[str], source_lines: list[str], section_id: str) -> None:
        nonlocal entry_order, refs, entries, current_letter, node_order
        if not buffer_lines:
            return
        entry_raw = normalize_entry_text(buffer_lines)
        if is_noise_line(entry_raw):
            buffer_lines.clear()
            source_lines.clear()
            return

        lemma_raw = parse_lemma(entry_raw)
        entry_kind = choose_entry_kind(section_id, entry_raw)
        first_alpha = re.search(r"[A-Za-zÀ-ÿ]", lemma_raw or entry_raw)
        letter = first_alpha.group(0).upper() if first_alpha else None
        if section_id == "index" and letter:
            if letter != current_letter:
                current_letter = letter
                node_order += 1
                add_letter_node(letter, section_key, node_order)

        entry_order += 1
        entry_key = f"{section_key}-{entry_order:04d}"
        inferred_page = None
        num_matches = [int(m.group(1)) for m in re.finditer(r"\b(\d{1,4})\b", entry_raw)]
        if num_matches:
            inferred_page = num_matches[0]
        helper = None
        lemma_norm = norm_key(lemma_raw) if lemma_raw else None
        if lemma_norm:
            if lemma_norm.startswith("abbo antissiodorensis"):
                helper = helper_map.get("pl135_abbo_antissiodorensis_207")
            elif lemma_norm.startswith("aegidius archiepiscopus remensis"):
                helper = helper_map.get("pl135_aegidius_remensis_96")
            elif lemma_norm.startswith("artoldus arch"):
                helper = helper_map.get("pl135_artoldus_remensis_73")
            elif lemma_norm.startswith("hugo heroldi f arch rem"):
                helper = helper_map.get("pl135_hugo_heroldi_295")

        entry_file = current_file
        entry_confidence = 0.92
        if entry_raw and entry_raw[0].isdigit():
            entry_confidence = 0.7
        if "ilegivel" in entry_raw.lower() or "?" in entry_raw:
            entry_confidence = min(entry_confidence, 0.62)
        if helper:
            entry_confidence = max(entry_confidence, 0.88)

        entry_obj = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": f"{section_key}-{current_letter.lower()}" if section_id == "index" and current_letter else None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_norm,
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_letter if section_id == "index" else None,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(source_root / (SECTION_INDEX_FILES[0] if section_id == "index" else SECTION_ORDO_FILES[0])),
            "editorial_anchor_file": entry_file,
            "target_file_best": None,
            "confidence": entry_confidence,
            "raw_json": {
                "source_lines": source_lines[:],
                "section_id": section_id,
            },
        }
        if helper:
            entry_obj["raw_json"]["helper"] = helper

        entry_refs = extract_refs(entry_raw, target_map)
        if entry_refs:
            entry_obj["target_file_best"] = entry_refs[0]["target_file"]
            for idx, ref in enumerate(entry_refs, start=1):
                ref["entry_key"] = entry_key
                ref["ref_order"] = idx
                ref["section_start_file"] = entry_obj["section_start_file"]
                ref["editorial_anchor_file"] = entry_file
                ref["raw_json"] = {
                    "entry_raw": entry_raw,
                    "source_lines": source_lines[:],
                }
                if helper:
                    ref["raw_json"]["helper"] = helper
                refs.append(ref)

        entries.append(entry_obj)
        buffer_lines.clear()
        source_lines.clear()

    for section_id, files in (("index", SECTION_INDEX_FILES), ("ordo", SECTION_ORDO_FILES)):
        buffer_lines: list[str] = []
        source_lines: list[str] = []
        current_file = ""
        for fname in files:
            fpath = source_root / fname
            for item in extract_lines(fpath):
                line = item["line"]
                current_file = item["file"]
                if is_noise_line(line):
                    flush_buffer(section_index_key if section_id == "index" else section_ordo_key, current_file, buffer_lines, source_lines, section_id)
                    continue
                if is_single_letter(line):
                    flush_buffer(section_index_key if section_id == "index" else section_ordo_key, current_file, buffer_lines, source_lines, section_id)
                    letter = line.strip()
                    if section_id == "index":
                        if letter != current_letter:
                            current_letter = letter
                            if letter not in letter_node_keys:
                                node_order += 1
                                add_letter_node(letter, section_index_key, node_order)
                    continue
                if buffer_lines and (line[0].islower() or line[0] in "—-.,;:)]"):
                    buffer_lines.append(line)
                    source_lines.append(line)
                    continue
                if buffer_lines:
                    flush_buffer(section_index_key if section_id == "index" else section_ordo_key, current_file, buffer_lines, source_lines, section_id)
                buffer_lines.append(line)
                source_lines.append(line)
        flush_buffer(section_index_key if section_id == "index" else section_ordo_key, current_file, buffer_lines, source_lines, section_id)

    # Reorder nodes by insertion order and maintain entry/ref ordering already created.
    volume = {
        "volume_id": volume_id,
        "collection": collection,
        "source_root": str(source_root),
        "volume_label": "Flodoardus, Historia ecclesiae Remensis",
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": "2026-07-20T00:00:00Z",
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the alphabetical index and the closing ORDO RERUM block from the OCR tail with conservative line-level grouping and per-page target resolution.",
            "evidence_files": [str(source_root / name) for name in SECTION_INDEX_FILES + SECTION_ORDO_FILES],
        },
        "notes": [
            "OCR line wraps and interleaved TOC columns were preserved as grouped fragments rather than normalized away.",
            "Helper-backed anchors were retained in raw_json for a few ambiguous line fragments, but direct OCR still drives the final payload structure.",
        ],
    }

    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
