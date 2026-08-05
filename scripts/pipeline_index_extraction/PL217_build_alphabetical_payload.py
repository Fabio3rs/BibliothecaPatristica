#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/PL217_build_alphabetical_payload.py

Build the PL217 alphabetical-index payload from the OCR tail files, reuse the
local helper output when present, and write the canonical JSON payload to
data/alphabetical_index_payloads/PL217_alphabetical_indices.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL217/text"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL217_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PL217_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL217_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL217"

ALPHA_FILES = [SOURCE_ROOT / f"76224dcc-209a-487f-a8c4-586ba27380fe-{n}.txt" for n in range(585, 600)]
ORDO_FILES = [SOURCE_ROOT / f"76224dcc-209a-487f-a8c4-586ba27380fe-{n}.txt" for n in range(600, 611)]

FILE_PAGE_START = {
    585: 1161,
    586: 1162,
    587: 1163,
    588: 1165,
    589: 1167,
    590: 1169,
    591: 1171,
    592: 1173,
    593: 1175,
    594: 1177,
    595: 1179,
    596: 1181,
    597: 1183,
    598: 1185,
    599: 1187,
    600: 1189,
    601: 1191,
    602: 1193,
    603: 1195,
    604: 1197,
    605: 1199,
    606: 1201,
    607: 1203,
    608: 1205,
    609: 1207,
    610: 1209,
}

ALPHA_LETTERS = [
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "L",
    "M",
    "N",
    "O",
    "P",
    "Q",
    "R",
    "S",
    "T",
    "U",
    "V",
]

NOISE_LINES = {
    "Digitized by Google",
    "FINIS TOMI DUCENTESIMI DECIMI SEPTIMI.",
    "EDITORIS PROFESSIO FIDEI.",
    "Hunc igitur Cursum Patrologiæ completum, cum tota prostrati animi demissione, Sanctæ Sedis judicio bens submitto; et, si quid, vel immodica celeritate abreptus, vel scientiæ infirmitate delusus, vel im-",
}

LETTER_RE = re.compile(r"^[A-Z]$")
ARABIC_RE = re.compile(r"\d")
PAGE_END_RE = re.compile(r"(\d{1,4})(?:\s*bis)?\.?$")
ALPHA_REF_PATTERNS = [
    r"Registr\.?\s+de\s+neg\.?\s+imp\.?\s*\d+(?:\s*bis)?\.?",
    r"Registr\.?\s+ad\s+neg\.?\s+imp\.?\s*\d+(?:\s*bis)?\.?",
    r"Supplem\.?\s*\d+(?:\s*bis)?\.?",
    r"Supplement\.?\s*\d+(?:\s*bis)?\.?",
    r"Suppl\.?\s*\d+(?:\s*bis)?\.?",
    r"Append\.?\s*\d+(?:\s*bis)?\.?",
    r"Appen\.?\s*\d+(?:\s*bis)?\.?",
    r"(?:[IVXLCDM]+|[A-Z])\s*,\s*\d+(?:\s*bis)?\.?",
]
ALPHA_REF_RE = re.compile("|".join(f"({p})" for p in ALPHA_REF_PATTERNS), flags=re.I)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def strip_xml_line(line: str) -> str:
    return normalize_spaces(line.replace("\xa0", " "))


def extract_block_text(path: Path, block_type: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(rf'<bloco tipo="{block_type}"[^>]*>(.*?)</bloco>', text, flags=re.S)
    lines: list[str] = []
    for block in blocks:
        for raw_line in block.splitlines():
            line = strip_xml_line(raw_line)
            if line:
                lines.append(line)
    return lines


def extract_heading(path: Path) -> str:
    lines = extract_block_text(path, "cabecalho")
    if not lines:
        return ""
    return " ".join(lines)


def is_noise(line: str) -> bool:
    if not line:
        return True
    if line in NOISE_LINES:
        return True
    if line.startswith("<") and line.endswith(">"):
        return True
    if line in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V"}:
        return True
    if LETTER_RE.fullmatch(line):
        return True
    if line.startswith("INDEX EPISTOLARUM") or line.startswith("SECUNDUM LITTERAM INITIALEM ORDINATUS"):
        return True
    if line.startswith("PRIOR NUMERUS LIBRUM") or line.startswith("POSTERIOR EPISTOLAM INDICAT"):
        return True
    if line.startswith("ORDO RERUM"):
        return True
    if line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
        return True
    if line.startswith("QUÆ IN HOC TOMO CONTINENTUR"):
        return True
    if line.startswith("CAPUT ") and "LIBELLUS" in line:
        return False
    if line.startswith("EDITORIS PROFESSIO") or line.startswith("Hunc igitur Cursum Patrologiæ"):
        return True
    return False


def is_continuation_line(line: str) -> bool:
    if not line:
        return False
    if line[0].islower() or line[0].isdigit():
        return True
    if line.startswith(("et ", "quæ ", "que ", "quae ", "ad ", "de ", "in ", "cum ", "vel ", "ubi ", "quod ", "pro ", "per ", "non ")):
        return True
    return False


def should_join(prev: str, nxt: str) -> bool:
    if prev.endswith("-") or prev.endswith(",") or prev.endswith(";") or prev.endswith(":"):
        return True
    if not ARABIC_RE.search(prev) and is_continuation_line(nxt):
        return True
    if re.search(r"(Registr\.?|Supplem\.?|Supplement\.?|Suppl\.?|Append\.?|Appen\.?|imp\.?|neg\.?|lib\.?|epist\.?)$", prev, flags=re.I):
        return True
    if prev.endswith(" ") or prev.endswith("—"):
        return True
    return False


def merge_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buffer = ""
    for raw in lines:
        line = strip_xml_line(raw)
        if not line or is_noise(line):
            if buffer:
                merged.append(buffer)
                buffer = ""
            continue
        if not buffer:
            buffer = line
            continue
        if should_join(buffer, line):
            joiner = " "
            if buffer.endswith("-"):
                buffer = buffer[:-1].rstrip()
                joiner = ""
            buffer = f"{buffer}{joiner}{line}"
        else:
            merged.append(buffer)
            buffer = line
    if buffer:
        merged.append(buffer)
    return merged


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def load_helper_summary() -> dict[str, Any]:
    if not HELPER_OUTPUT.exists():
        return {}
    data = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    summary: dict[str, Any] = {}
    for item in data.get("entries", []):
        best = item.get("best_candidate") or {}
        summary[item.get("entry_id", "")] = {
            "status": item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "best_file": best.get("file"),
            "best_probability": best.get("probability"),
            "top_candidates": [
                {
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                    "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:4]],
                }
                for cand in (item.get("candidates") or [])[:3]
            ],
        }
    return summary


def first_alpha_ref_pos(text: str) -> int | None:
    matches = list(ALPHA_REF_RE.finditer(text))
    if not matches:
        return None
    return matches[0].start()


def split_alpha_entry(line: str) -> tuple[str | None, list[str]]:
    ref_positions = [m.start() for m in ALPHA_REF_RE.finditer(line)]
    if not ref_positions:
        return normalize_spaces(line.rstrip(".")) or None, []
    split_at = ref_positions[0]
    lemma = normalize_spaces(line[:split_at].rstrip(" ,.;:"))
    refs = [normalize_spaces(m.group(0)) for m in ALPHA_REF_RE.finditer(line)]
    return lemma or None, refs


def split_ordo_entry(line: str) -> tuple[str | None, list[str]]:
    m = PAGE_END_RE.search(line)
    if not m:
        return normalize_spaces(line.rstrip(".")) or None, []
    page = m.group(1)
    ref_raw = m.group(0).strip()
    lemma = normalize_spaces(line[: m.start()].rstrip(" ,.;:"))
    return lemma or None, [ref_raw]


def leading_letter(lemma: str | None) -> str | None:
    if not lemma:
        return None
    m = re.search(r"[A-Za-zÀ-ÿ]", lemma)
    if not m:
        return None
    ch = m.group(0).upper()
    return ch


def clean_sort_key(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.lower()
    value = value.replace("æ", "ae").replace("œ", "oe").replace("j", "j")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return normalize_spaces(value)


def build_section(
    *,
    volume_id: str,
    section_key: str,
    section_order: int,
    section_kind: str,
    heading_raw: str,
    page_start: int,
    page_end: int,
    file_start: Path,
    file_end: Path,
    source_files: list[Path],
    source_kind: str,
    helper_summary: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    if section_kind == "alphabetical_general":
        letter_to_node_key: dict[str, str] = {}
        node_order = 0
        entry_order = 0
        for path in source_files:
            inferred_page = FILE_PAGE_START.get(file_seq(path), page_start)
            merged_lines = merge_lines(extract_block_text(path, "texto_principal"))
            for line in merged_lines:
                if is_noise(line):
                    continue
                if re.fullmatch(r"[A-Z]", line):
                    continue
                if line.startswith("ORDO RERUM"):
                    continue
                lemma_raw, ref_frags = split_alpha_entry(line)
                if lemma_raw is None:
                    continue
                if not ref_frags:
                    # Keep small editorial fragments when OCR has dropped the locator.
                    if not ARABIC_RE.search(line):
                        continue
                letter = leading_letter(lemma_raw)
                parent_node_key = None
                if letter:
                    if letter not in letter_to_node_key:
                        node_order += 1
                        node_key = f"{volume_id}:node:{section_order:02d}:{node_order:03d}"
                        letter_to_node_key[letter] = node_key
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
                                "raw_json": {"role": "alphabetic_letter"},
                            }
                        )
                    parent_node_key = letter_to_node_key[letter]
                entry_order += 1
                entry_key = f"{volume_id}:entry:{section_order:02d}:{entry_order:04d}"
                target_file = str(path)
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": parent_node_key,
                    "entry_order": entry_order,
                    "entry_kind": "lemma" if ref_frags else "editorial_note",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": clean_sort_key(lemma_raw),
                    "lemma_sort": clean_sort_key(lemma_raw),
                    "entry_raw": line,
                    "context_raw": line,
                    "heading_letter": letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(file_start),
                    "editorial_anchor_file": target_file,
                    "target_file_best": target_file,
                    "confidence": 0.95 if ref_frags else 0.75,
                    "raw_json": {
                        "source_file": str(path),
                        "file_seq": file_seq(path),
                        "file_page_start": inferred_page,
                        "section_kind": section_kind,
                        "section_page_start": page_start,
                        "helper_summary": helper_summary,
                    },
                }
                entries.append(entry)
                for ref_order, ref_raw in enumerate(ref_frags, 1):
                    page_match = PAGE_END_RE.search(ref_raw)
                    page_ref_int = int(page_match.group(1)) if page_match else None
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": ref_raw,
                            "page_ref_raw": page_match.group(1) if page_match else None,
                            "page_ref_int": page_ref_int,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target_file,
                            "target_file_probability": 1.0,
                            "section_start_file": str(file_start),
                            "editorial_anchor_file": target_file,
                            "confidence": 0.94,
                            "raw_json": {
                                "source_file": str(path),
                                "segment_raw": line,
                                "ref_fragment_raw": ref_raw,
                            },
                        }
                    )
    else:
        entry_order = 0
        for path in source_files:
            inferred_page = FILE_PAGE_START.get(file_seq(path), page_start)
            merged_lines = merge_lines(extract_block_text(path, "texto_principal"))
            for line in merged_lines:
                if is_noise(line):
                    continue
                if line.startswith("FINIS TOMI"):
                    break
                if line.startswith("EDITORIS PROFESSIO"):
                    break
                if line.startswith("Hunc igitur Cursum Patrologiæ"):
                    break
                if line.startswith("Index epistolarum Innocentii III juxta litteram initia"):
                    # Keep this as the final table entry, not as a separate closure block.
                    pass
                lemma_raw, ref_frags = split_ordo_entry(line)
                if lemma_raw is None:
                    continue
                if not ref_frags and not ARABIC_RE.search(line):
                    # Retain lines like "1. LIBELLUS..." or chapter rubrics even if OCR dropped the page ref.
                    if not line.startswith("CAP.") and not line.startswith("LIBELLUS") and not line.startswith("MYSTERIORUM") and not line.startswith("Ordo missæ"):
                        continue
                entry_order += 1
                entry_key = f"{volume_id}:entry:{section_order:02d}:{entry_order:04d}"
                target_file = str(path)
                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "heading_group",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": clean_sort_key(lemma_raw),
                    "lemma_sort": clean_sort_key(lemma_raw),
                    "entry_raw": line,
                    "context_raw": line,
                    "heading_letter": None,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(file_start),
                    "editorial_anchor_file": target_file,
                    "target_file_best": target_file,
                    "confidence": 0.9 if ref_frags else 0.72,
                    "raw_json": {
                        "source_file": str(path),
                        "file_seq": file_seq(path),
                        "file_page_start": inferred_page,
                        "section_kind": section_kind,
                        "section_page_start": page_start,
                        "helper_summary": helper_summary,
                    },
                }
                entries.append(entry)
                for ref_order, ref_raw in enumerate(ref_frags, 1):
                    page_match = PAGE_END_RE.search(ref_raw)
                    page_ref_int = int(page_match.group(1)) if page_match else None
                    refs.append(
                        {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": ref_raw,
                            "page_ref_raw": page_match.group(1) if page_match else None,
                            "page_ref_int": page_ref_int,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target_file,
                            "target_file_probability": 1.0,
                            "section_start_file": str(file_start),
                            "editorial_anchor_file": target_file,
                            "confidence": 0.94,
                            "raw_json": {
                                "source_file": str(path),
                                "segment_raw": line,
                                "ref_fragment_raw": ref_raw,
                            },
                        }
                    )

    section = {
        "section_key": section_key,
        "volume_id": volume_id,
        "work_key": None,
        "section_order": section_order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": clean_sort_key(heading_raw),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": str(file_start),
        "file_end": str(file_end),
        "confidence": 0.97 if section_kind == "alphabetical_general" else 0.94,
        "raw_json": {
            "section_kind_reason": (
                "Alphabetical index of Innocent III epistles ordered by initial letter."
                if section_kind == "alphabetical_general"
                else "Editorial table of contents (ordo rerum) preserved as non-alphabetical closure material."
            ),
            "source_files": [str(p) for p in source_files],
            "helper_summary": helper_summary,
        },
    }
    return section, nodes, entries, refs


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    helper_summary = load_helper_summary()

    todo = {
        "volume_id": "PL217",
        "updated_at": now_utc(),
        "current_focus": "Build PL217 alphabetical index payload from OCR tail and ordo pages",
        "completed": [
            "Read OCR tail pages and identified the alphabetical index plus ordo rerum",
            "Prepared helper request and local target locator checkpoint",
        ],
        "pending": [
            "Finalize payload assembly",
            "Validate output shape and key relationships",
        ],
        "blocked": [],
        "notes": [
            "OCR file sequence is not the same as printed page order; page anchors are inferred conservatively.",
            "The final editorial profession page is excluded from the canonical payload.",
        ],
    }
    write_json(INTERMEDIATE_DIR / "todo.json", todo)

    helper_request = {
        "volume_id": "PL217",
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pl217_index_epistolarum_start",
                "lemma_raw": "INDEX EPISTOLARUM INNOCENTII III",
                "query_names": [
                    "INDEX EPISTOLARUM INNOCENTII III",
                    "SECUNDUM LITTERAM INITIALEM ORDINATUS",
                    "A memoria vestra",
                ],
                "page_hints": ["1161", "1162", "1187"],
                "page_hint_ints": [1161, 1162, 1187],
                "context_raw": "INDEX EPISTOLARUM INNOCENTII III SECUNDUM LITTERAM INITIALEM ORDINATUS. PRIOR NUMERUS LIBRUM, POSTERIOR EPISTOLAM INDICAT.",
            },
            {
                "entry_id": "pl217_ordo_rerum_start",
                "lemma_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "query_names": [
                    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR",
                    "LIBELLUS DE ELEEMOSYNA",
                    "CAPUT PRIMUM",
                ],
                "page_hints": ["1189", "1190", "1203"],
                "page_hint_ints": [1189, 1190, 1203],
                "context_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            },
        ],
    }
    write_json(HELPER_REQUEST, helper_request)

    alpha_heading = "INDEX EPISTOLARUM INNOCENTII III SECUNDUM LITTERAM INITIALEM ORDINATUS. PRIOR NUMERUS LIBRUM, POSTERIOR EPISTOLAM INDICAT."
    ordo_heading = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

    alpha_section, alpha_nodes, alpha_entries, alpha_refs = build_section(
        volume_id="PL217",
        section_key="PL217:alpha:alphabetical_general:001",
        section_order=1,
        section_kind="alphabetical_general",
        heading_raw=alpha_heading,
        page_start=1161,
        page_end=1188,
        file_start=ALPHA_FILES[0],
        file_end=ALPHA_FILES[-1],
        source_files=ALPHA_FILES,
        source_kind="alpha",
        helper_summary=helper_summary,
    )

    ordo_section, ordo_nodes, ordo_entries, ordo_refs = build_section(
        volume_id="PL217",
        section_key="PL217:alpha:ordo_rerum:002",
        section_order=2,
        section_kind="ordo_rerum",
        heading_raw=ordo_heading,
        page_start=1189,
        page_end=1210,
        file_start=ORDO_FILES[0],
        file_end=ORDO_FILES[-1],
        source_files=ORDO_FILES,
        source_kind="ordo",
        helper_summary=helper_summary,
    )

    volume = {
        "volume_id": "PL217",
        "collection": "PL",
        "source_root": str(SOURCE_ROOT),
        "volume_label": "Innocentii III Papæ",
        "notes": "Recovered the INDEX EPISTOLARUM alphabetical index and the trailing ORDO RERUM material from the OCR tail.",
    }

    sections = [alpha_section, ordo_section]
    nodes = alpha_nodes + ordo_nodes
    entries = alpha_entries + ordo_entries
    refs = alpha_refs + ordo_refs

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the alphabetical index of Innocent III epistles and the trailing ordo rerum from OCR tail files 585-610.",
        "evidence_files": [str(p) for p in ALPHA_FILES + ORDO_FILES],
    }

    notes = [
        "The OCR file suffix order is not the same as the printed pagination; page anchors were inferred conservatively from local headers and sequence.",
        "The alphabetical section is ordered by initial letter and uses the OCR file as the target anchor for each line item.",
        "The final editorial profession page was excluded because it is outside the index/ordo material.",
    ]

    manifest = {
        "volume_id": "PL217",
        "generated_at": now_utc(),
        "updated_at": now_utc(),
        "source_root": str(SOURCE_ROOT),
    }

    write_json(INTERMEDIATE_DIR / "manifest.json", manifest)
    write_json(INTERMEDIATE_DIR / "volume.json", volume)
    write_json(INTERMEDIATE_DIR / "sections.json", sections)
    write_json(INTERMEDIATE_DIR / "nodes.json", nodes)
    write_json(INTERMEDIATE_DIR / "entries.json", entries)
    write_json(INTERMEDIATE_DIR / "refs.json", refs)
    write_json(INTERMEDIATE_DIR / "coverage.json", coverage)
    write_json(INTERMEDIATE_DIR / "notes.json", notes)

    payload = {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    write_json(OUTPUT_PATH, payload)


if __name__ == "__main__":
    main()
