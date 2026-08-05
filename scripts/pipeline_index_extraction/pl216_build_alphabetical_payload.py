#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/pl216_build_alphabetical_payload.py

Build the PL216 alphabetical-index payload from the OCR tail pages, then write
the canonical JSON payload to data/alphabetical_index_payloads/PL216_alphabetical_indices.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL216/text"
OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PL216_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL216_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL216"

FILES = [
    SOURCE_ROOT / "b376783e-c7f1-4f0f-80ed-228c60265edd-643.txt",
    SOURCE_ROOT / "b376783e-c7f1-4f0f-80ed-228c60265edd-644.txt",
    SOURCE_ROOT / "b376783e-c7f1-4f0f-80ed-228c60265edd-645.txt",
]

PAGE_MAP = {
    FILES[0]: [1273, 1274],
    FILES[1]: [1275, 1276],
    FILES[2]: [1277, 1278],
}

ENTRY_SPLITS = {
    "Inter alia. De sent. excomm. Collectio Rainerii, tit. 31, Inter corporalia. De translat. Lib. 1, epist. 532.": [
        "Inter alia. De sent. excomm. Collectio Rainerii, tit. 31.",
        "Inter corporalia. De translat. Lib. 1, epist. 532.",
    ]
}

LETTER_SORT_ORDER = "ABCDEFGIJKLMNOPQRSTUVWXYZ"


def read_helper_summary() -> dict:
    if not HELPER_OUTPUT.exists():
        return {}
    data = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    summary = {}
    for item in data.get("entries", []):
        best = item.get("best_candidate", {}) or {}
        summary[item.get("entry_id")] = {
            "status": item.get("status"),
            "best_file": best.get("file"),
            "best_probability": best.get("probability"),
            "reason_summary": best.get("reason_summary"),
            "candidate_role": best.get("candidate_role"),
        }
    return summary


def extract_text_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(
        r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>',
        text,
        flags=re.S,
    )
    return blocks


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    text = text.replace("œ", "oe").replace("æ", "ae").replace("Æ", "ae").replace("Œ", "oe")
    return text


REF_PATTERNS = [
    r"In quinta compilat\.\s*lib\.?,?\s*\d+[\.,]?\s*tit\.?,?\s*\d+[\.,]?\s*cap\.?,?\s*\d+\.?",
    r"In (?:tertia|quarta|secunda) collectione\.\s*Lib\.?,?\s*\d+[\.,]?\s*epist\.?,?\s*\d+(?:\.\d+)?\.?",
    r"Lib\.?,?\s*\d+[\.,]?\s*epist\.?,?\s*\d+(?:\.\d+)?\.?",
    r"Collectio Raineri[il],?\s*tit\.?,?\s*\d+\.?",
    r"Appendix libri\s*\d+,\s*pag\.?,?\s*\d+\.?",
    r"Gesta Innoc\.?\s*III\.?\s*cap\.?\s*\d+\.?",
    r"Regis tr\. de negotio imperii,\s*epist\.?,?\s*\d+\.?",
    r"Ibid\.?",
]
REF_RE = re.compile("|".join(f"({p})" for p in REF_PATTERNS), flags=re.I)


def first_ref_pos(raw: str) -> int:
    match = REF_RE.search(raw)
    return match.start() if match else len(raw)


def split_lemma(raw: str) -> str:
    cut = first_ref_pos(raw)
    prefix = raw[:cut].strip()
    if " De " in prefix:
        prefix = prefix.split(" De ", 1)[0]
    elif " de " in prefix:
        prefix = prefix.split(" de ", 1)[0]
    prefix = prefix.strip(" ,.;:")
    return prefix


def lemma_norm(raw: str) -> str:
    return normalize_text(raw).lower()


def leading_letter(lemma: str) -> str | None:
    m = re.search(r"[A-Za-zÀ-ÿ]", lemma)
    if not m:
        return None
    ch = m.group(0).upper()
    if ch == "J":
        ch = "I"
    return ch


def extract_ref_fragments(raw: str, previous_explicit: dict | None) -> list[dict]:
    fragments: list[dict] = []
    for match in REF_RE.finditer(raw):
        frag = normalize_text(match.group(0))
        if not frag:
            continue
        if re.fullmatch(r"Ibid\.?", frag, flags=re.I):
            if previous_explicit is None:
                continue
            frag = previous_explicit["ref_raw"]
            page_raw = previous_explicit["page_ref_raw"]
            page_int = previous_explicit["page_ref_int"]
            inherited = True
        else:
            inherited = False
            nums = re.findall(r"\d+", frag)
            page_int = int(nums[-1]) if nums else None
            page_raw = nums[-1] if nums else None

        fragments.append(
            {
                "ref_raw": frag,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "inherited": inherited,
            }
        )

    if not fragments and "Gesta Innoc." in raw:
        tail = normalize_text(raw[raw.index("Gesta Innoc."):])
        nums = re.findall(r"\d+", tail)
        if nums:
            fragments.append(
                {
                    "ref_raw": tail,
                    "page_ref_raw": nums[-1],
                    "page_ref_int": int(nums[-1]),
                    "inherited": False,
                }
            )
    return fragments


def block_page(file_path: Path, block_index: int) -> int:
    return PAGE_MAP[file_path][block_index]


@dataclass
class EntryRecord:
    entry_key: str
    section_key: str
    parent_node_key: str | None
    entry_order: int
    entry_kind: str
    lemma_raw: str | None
    lemma_display: str | None
    lemma_norm: str | None
    lemma_sort: str | None
    entry_raw: str
    context_raw: str
    heading_letter: str | None
    inferred_printed_page: int | None
    section_start_file: str
    editorial_anchor_file: str
    target_file_best: str
    confidence: float
    raw_json: dict


def main() -> None:
    helper_summary = read_helper_summary()
    section_key = "PL216:alpha:alphabetical_general:001"
    section_start_file = str(FILES[0])
    section = {
        "section_key": section_key,
        "volume_id": "PL216",
        "work_key": None,
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "ELENCHUS EPIST. DECRETAL. INNOC. III RELAT. IN CORP. JURIS.",
        "heading_norm": "elenchus epist decretal innoc iii relat in corp juris",
        "heading_letter": None,
        "page_start": 1273,
        "page_end": 1278,
        "file_start": str(FILES[0]),
        "file_end": str(FILES[2]),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "Alphabetical index of decretal epistles cited in the Corpus Juris Canonici, organized by incipit and letter groups A-V.",
            "source_files": [str(p) for p in FILES],
            "helper_summary": helper_summary,
        },
    }

    entries: list[dict] = []
    refs: list[dict] = []
    nodes: list[dict] = []
    seen_letters: list[str] = []

    previous_explicit_ref: dict | None = None
    entry_order = 0

    for file_path in FILES:
        blocks = extract_text_blocks(file_path)
        for block_index, block in enumerate(blocks):
            if file_path == FILES[2] and block_index > 1:
                break
            current_page = block_page(file_path, block_index)
            lines = [normalize_text(line) for line in block.splitlines()]
            for raw in lines:
                if not raw:
                    continue
                if raw in {"A", "B", "C", "D", "E", "F", "G", "I", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V"}:
                    continue
                if raw.startswith("ORDO RERUM") or raw.startswith("INNOCENTIUS III ROMANUS PONTIFEX") or raw.startswith("REGESTORUM SIVE EPISTOLARUM LIBER DUODECIMUS"):
                    break
                split_lines = ENTRY_SPLITS.get(raw, [raw])
                for split_raw in split_lines:
                    entry_order += 1
                    lemma = split_lemma(split_raw)
                    letter = leading_letter(lemma)
                    if letter and letter not in seen_letters:
                        seen_letters.append(letter)
                    entry_key = f"PL216:entry:01:{entry_order:04d}"
                    parent_node_key = None
                    ref_frags = extract_ref_fragments(split_raw, previous_explicit_ref)
                    ref_objs = []
                    ref_order = 0
                    for frag in ref_frags:
                        ref_order += 1
                        ref_obj = {
                            "entry_key": entry_key,
                            "ref_order": ref_order,
                            "ref_kind": "editorial_page",
                            "ref_raw": frag["ref_raw"],
                            "page_ref_raw": frag["page_ref_raw"],
                            "page_ref_int": frag["page_ref_int"],
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": str(file_path),
                            "target_file_probability": 1.0,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": str(file_path),
                            "confidence": 0.94 if not frag["inherited"] else 0.9,
                            "raw_json": {
                                "source_file": str(file_path),
                                "segment_raw": split_raw,
                                "ref_fragment_raw": frag["ref_raw"],
                                "inherited_from_previous_explicit_ref": frag["inherited"],
                            },
                        }
                        if frag["inherited"]:
                            ref_obj["raw_json"]["inherited_locator_source"] = previous_explicit_ref
                        ref_objs.append(ref_obj)
                        if not frag["inherited"]:
                            previous_explicit_ref = frag

                    if not ref_objs and previous_explicit_ref is not None and "Ibid" in split_raw:
                        ref_objs = [
                            {
                                "entry_key": entry_key,
                                "ref_order": 1,
                                "ref_kind": "editorial_page",
                                "ref_raw": "Ibid.",
                                "page_ref_raw": previous_explicit_ref["page_ref_raw"],
                                "page_ref_int": previous_explicit_ref["page_ref_int"],
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                                "target_file": str(file_path),
                                "target_file_probability": 1.0,
                                "section_start_file": section_start_file,
                                "editorial_anchor_file": str(file_path),
                                "confidence": 0.88,
                                "raw_json": {
                                    "source_file": str(file_path),
                                    "segment_raw": split_raw,
                                    "ref_fragment_raw": "Ibid.",
                                    "inherited_from_previous_explicit_ref": True,
                                    "inherited_locator_source": previous_explicit_ref,
                                },
                            }
                        ]

                    entry = {
                        "entry_key": entry_key,
                        "section_key": section_key,
                        "parent_node_key": parent_node_key,
                        "entry_order": entry_order,
                        "entry_kind": "lemma",
                        "lemma_raw": lemma,
                        "lemma_display": lemma,
                        "lemma_norm": lemma_norm(lemma),
                        "lemma_sort": lemma_norm(lemma),
                        "entry_raw": split_raw,
                        "context_raw": split_raw,
                        "heading_letter": letter,
                        "inferred_printed_page": current_page,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": str(file_path),
                        "target_file_best": str(file_path),
                        "confidence": 0.95 if current_page in {1273, 1275, 1277} else 0.94,
                        "raw_json": {
                            "source_file": str(file_path),
                            "block_page": current_page,
                            "entry_split_source": raw,
                            "section_kind": "alphabetical_general",
                            "helper_summary": helper_summary.get(
                                "pl216_abbate_1273" if entry_order == 1 else (
                                    "pl216_inter_alia_1276" if "Inter alia" in split_raw else (
                                        "pl216_tuis_quaestionibus_1277" if split_raw.startswith("Tuis quæstionibus") else (
                                            "pl216_veniens_1277" if split_raw.startswith("Veniens") else None
                                        )
                                    )
                                )
                            ),
                        },
                    }
                    if "Inter corporalia" in split_raw:
                        entry["raw_json"]["split_from_combined_line"] = True
                    entries.append(entry)
                    refs.extend(ref_objs)

    sorted_letters = sorted(
        seen_letters,
        key=lambda ch: LETTER_SORT_ORDER.index(ch) if ch in LETTER_SORT_ORDER else 999,
    )
    letter_to_node_key: dict[str, str] = {}

    for idx, letter in enumerate(sorted_letters, start=1):
        node_key = f"PL216:node:01:{idx:03d}"
        letter_to_node_key[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": idx,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {
                    "role": "alphabetic_letter",
                },
            }
        )

    for entry in entries:
        entry["parent_node_key"] = letter_to_node_key.get(entry["heading_letter"])

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volume": {
            "volume_id": "PL216",
            "collection": "PL",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Innocentii III PP. Regestorum sive epistolarum liber duodecimus",
            "notes": "Recovered the ELENCHUS EPIST. DECRETAL. index from the OCR tail preceding ORDO RERUM.",
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "complete",
            "entries_status_reason": "The alphabetical index entries and material locators were recovered from the OCR tail pages 1273-1277.",
            "evidence_files": [str(p) for p in FILES],
        },
        "notes": [
            "The OCR tail includes both the ELENCHUS index and the start of ORDO RERUM on the same physical OCR file; only the ELENCHUS portion was extracted here.",
            "Ibid. remissions were resolved conservatively against the previous explicit locator in sequence.",
        ],
    }

    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    (INTERMEDIATE_DIR / "entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE_DIR / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE_DIR / "sections.json").write_text(json.dumps([section], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE_DIR / "nodes.json").write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE_DIR / "coverage.json").write_text(json.dumps(payload["coverage"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
