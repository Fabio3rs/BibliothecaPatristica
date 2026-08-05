#!/usr/bin/env python3
"""
Build the PL211 alphabetical-index payload from the OCR tail files.

Run:
  python scripts/pipeline_index_extraction/build_pl211_alpha_payload.py
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL211/text"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL211_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL211_helper_output.json"


def extract_text_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_text = False
    for line in lines:
        if '<bloco tipo="texto_principal"' in line:
            in_text = True
            continue
        if in_text and line.strip().startswith("</bloco>"):
            in_text = False
            continue
        if in_text:
            stripped = line.strip()
            if stripped:
                out.append(stripped)
    return out


def is_heading_or_noise(line: str) -> bool:
    if line.startswith("<") or line.startswith("</"):
        return True
    if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "R", "S", "T", "U", "V", "W", "X"}:
        return True
    if "INDEX IN MARIANUM ADAMI" in line:
        return True
    if "INDEX IN PETRUM PICTAVENSEM" in line:
        return True
    if "INDEX RERUM ET VERBORUM" in line:
        return True
    if "QUE IN QUINQUE LIBRIS SENTENTIARUM PETRI PICTAVINI CONTINENTUR" in line:
        return True
    if "Revocatur Lector ad numeros crassiores textui insertos" in line:
        return True
    if "Digitized by Google" in line:
        return True
    if line.startswith("PATHOL. CCXI."):
        return True
    return False


def merge_continuations(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        if merged and merged[-1].endswith(",") and not is_heading_or_noise(line):
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    return merged


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize_spaces(entry_raw).rstrip(".")
    first_num = re.search(r"\d{1,4}", text)
    first_period = text.find(".")
    if first_period != -1 and (not first_num or first_period < first_num.start()):
        lemma = text[:first_period]
    elif first_num:
        lemma = text[:first_num.start()]
    else:
        lemma = text
    lemma = lemma.rstrip(" ,;:")
    return lemma or None


def entry_kind_for(entry_raw: str, refs: list[str]) -> str:
    if refs:
        return "lemma"
    if re.search(r"\bVide\b|\bvid\.\b|\bV\.", entry_raw, flags=re.IGNORECASE):
        return "cross_reference"
    return "editorial_note"


def parse_refs(entry_raw: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    # Keep only printed page locators; ignore bare remissions like "ibid." or "Vide".
    for match in re.finditer(r"\b\d{1,4}(?:-\d{1,4})?(?:,\s*\d{1,4}(?:-\d{1,4})?)*", entry_raw):
        token = match.group(0)
        for part in [p.strip() for p in token.split(",")]:
            if not part:
                continue
            if part not in seen:
                seen.add(part)
                refs.append(part)
    return refs


def build_section(
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    file_paths: list[Path],
    source_note: str,
    helper_entry_ids: dict[str, str] | None = None,
) -> tuple[dict, list[dict], list[dict]]:
    lines: list[tuple[Path, str]] = []
    for path in file_paths:
        for line in extract_text_lines(path):
            if not is_heading_or_noise(line):
                lines.append((path, line))

    lines = merge_continuations([line for _, line in lines])

    # Rebuild file association after continuation merge. Keep the first file for the merged chunk.
    paired: list[tuple[Path, str]] = []
    idx = 0
    raw_lines = []
    for path in file_paths:
        raw_lines.extend([(path, line) for line in extract_text_lines(path) if not is_heading_or_noise(line)])
    i = 0
    while i < len(raw_lines):
        path, line = raw_lines[i]
        if i + 1 < len(raw_lines) and line.endswith(",") and not is_heading_or_noise(raw_lines[i + 1][1]):
            nxt_path, nxt_line = raw_lines[i + 1]
            merged = f"{line} {nxt_line}"
            # In practice only a handful of continuation joins are needed; keep joining while commas persist.
            j = i + 2
            while merged.endswith(",") and j < len(raw_lines) and not is_heading_or_noise(raw_lines[j][1]):
                merged = f"{merged} {raw_lines[j][1]}"
                j += 1
            paired.append((path, normalize_spaces(merged)))
            i = j
            continue
        paired.append((path, normalize_spaces(line)))
        i += 1

    entries: list[dict] = []
    refs: list[dict] = []
    entry_seq = 1
    for path, raw_line in paired:
        if not raw_line:
            continue
        # Skip obvious remaining headings, but keep editorial notes and entry fragments.
        if raw_line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "R", "S", "T", "U", "V", "W", "X"}:
            continue

        refs_found = parse_refs(raw_line)
        lemma = lemma_from_entry(raw_line)
        kind = entry_kind_for(raw_line, refs_found)
        entry_key = f"{section_key}:entry:{entry_seq:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": None,
            "entry_order": entry_seq,
            "entry_kind": kind,
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": lemma.lower() if lemma else None,
            "lemma_sort": lemma.lower() if lemma else None,
            "entry_raw": raw_line,
            "context_raw": raw_line,
            "heading_letter": None,
            "inferred_printed_page": refs_found[0] if refs_found else None,
            "section_start_file": str(file_paths[0]),
            "editorial_anchor_file": None,
            "target_file_best": None,
            "confidence": 0.78 if refs_found else 0.55,
            "raw_json": {
                "source_file": str(path),
                "source_note": source_note,
                "section_kind": section_kind,
            },
        }
        if helper_entry_ids and entry_key in helper_entry_ids:
            entry["raw_json"]["helper"] = helper_entry_ids[entry_key]
        entries.append(entry)

        for ref_order, ref in enumerate(refs_found, 1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref,
                    "page_ref_raw": ref,
                    "page_ref_int": int(ref.split("-")[0]),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": str(file_paths[0]),
                    "editorial_anchor_file": None,
                    "confidence": 0.0,
                    "raw_json": {
                        "source_entry_id": entry_key,
                        "source_token": ref,
                        "section_kind": section_kind,
                    },
                }
            )
        entry_seq += 1

    section = {
        "section_key": section_key,
        "volume_id": "PL211",
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": re.sub(r"[^a-z0-9]+", "-", heading_raw.lower()).strip("-"),
        "heading_letter": None,
        "page_start": None,
        "page_end": None,
        "file_start": str(file_paths[0]),
        "file_end": str(file_paths[-1]),
        "confidence": 0.94,
        "raw_json": {
            "source_note": source_note,
            "section_kind_reason": source_note,
        },
    }
    return section, entries, refs


def load_helper_evidence() -> dict[str, dict]:
    data = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    mapping: dict[str, dict] = {}
    for entry in data.get("entries", []):
        best = entry.get("best_candidate") or {}
        mapping[entry["entry_id"]] = {
            "status": entry.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_candidate": best,
            "top_candidates": entry.get("candidates", [])[:3],
        }
    return mapping


def main() -> None:
    helper = load_helper_evidence()

    section1, entries1, refs1 = build_section(
        section_key="PL211:alpha:onomastic_mixed:001",
        section_order=1,
        section_kind="onomastic_mixed",
        heading_raw="INDEX IN MARIANUM ADAMI ABBATIS PERSENIÆ",
        file_paths=[
            SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-662.txt",
            SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-663.txt",
            SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-664.txt",
        ],
        source_note="OCR files 662-664; Marianum Adami index with alphabetic head groups and mixed Marian epithets.",
    )

    section2, entries2, refs2 = build_section(
        section_key="PL211:alpha:analytic_subject:002",
        section_order=2,
        section_kind="analytic_subject",
        heading_raw="INDEX RERUM ET VERBORUM QUE IN QUINQUE LIBRIS SENTENTIARUM PETRI PICTAVINI CONTINENTUR",
        file_paths=[
            SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-665.txt",
            SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-666.txt",
            SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-667.txt",
        ],
        source_note="OCR files 665-667; analytical index for the Sentences, excluding the adjacent Ordo Rerum contents pages.",
    )

    # Attach helper evidence to the representative entries that were checked.
    helper_map = {
        "PL211:alpha:onomastic_mixed:001:entry:0079": helper["pl211_marianum_adam_001"],
        "PL211:alpha:onomastic_mixed:001:entry:0161": helper["pl211_marianum_maria_deipara_002"],
        "PL211:alpha:analytic_subject:002:entry:0024": helper["pl211_petrum_christus_imago_003"],
    }
    for entry in entries1 + entries2:
        if entry["entry_key"] in helper_map:
            entry["raw_json"]["helper"] = helper_map[entry["entry_key"]]
            best = helper_map[entry["entry_key"]].get("best_candidate") or {}
            entry["editorial_anchor_file"] = best.get("file")
            entry["target_file_best"] = best.get("file")
            entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PL211",
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PL211",
            "notes": [
                "Marianum Adami and Petrum Pictaviense alphabetical sections were extracted from the OCR tail files.",
                "Ordo Rerum pages were identified but excluded because they are contents rather than alphabetical index material.",
            ],
        },
        "sections": [section1, section2],
        "nodes": [],
        "entries": entries1 + entries2,
        "refs": refs1 + refs2,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Two alphabetical/analytical index sections were recovered from OCR files 662-667; non-index Ordo Rerum pages were excluded.",
            "evidence_files": [
                str(SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-662.txt"),
                str(SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-663.txt"),
                str(SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-664.txt"),
                str(SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-665.txt"),
                str(SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-666.txt"),
                str(SOURCE_ROOT / "11ccdfe8-25ff-42d9-96e2-682a3fb11429-667.txt"),
            ],
        },
        "notes": [
            "Target file anchors were only attached to representative helper-resolved entries; most refs keep target_file null because this pass focused on preserving OCR locators.",
            "OCR file suffixes and printed page numbers are distinct; the file sequence is not monotonic in printed pagination.",
        ],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
