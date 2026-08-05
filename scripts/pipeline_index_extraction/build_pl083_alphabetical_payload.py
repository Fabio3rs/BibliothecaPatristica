#!/usr/bin/env python3
"""Usage: build the PL083 alphabetical payload and helper request from the OCR tail.

Run from the repository root, for example:

    python scripts/pipeline_index_extraction/build_pl083_alphabetical_payload.py \
      --source-root /homessddata/Projects/pdfocr/teste/PL083/text \
      --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL083_helper_request.json \
      --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL083_helper_output.json \
      --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL083 \
      --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL083_alphabetical_indices.json

The script extracts the three appendix/index sections present in this OCR tail:
`DE PROPRIETATE SERMONUM`, `LIBER GLOSSARUM` (appendix XXIV), and
`LIBER GLOSSARUM` (appendix XXV), then writes a conservative JSON payload.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


VOLUME_ID = "PL083"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, tomo 83"

SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
        "section_order": 1,
        "section_kind": "analytic_subject",
        "heading_raw": "APPEND. XXIII. — DE PROPRIETATE SERMONUM.",
        "heading_norm": "append xxiii de proprietate sermonum",
        "file_start": None,  # resolved from the OCR block list below
        "file_end": None,
        "files": [669, 670],
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
        "section_order": 2,
        "section_kind": "alphabetical_general",
        "heading_raw": "APPEND. XXIV. — LIBER GLOSSARUM.",
        "heading_norm": "append xxiv liber glossarum",
        "file_start": None,
        "file_end": None,
        "files": list(range(670, 683)),
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:002",
        "section_order": 3,
        "section_kind": "alphabetical_general",
        "heading_raw": "APPEND. XXV. — LIBER GLOSSARUM.",
        "heading_norm": "append xxv liber glossarum",
        "file_start": None,
        "file_end": None,
        "files": list(range(683, 694)),
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text or None


def strip_ligatures(text: str) -> str:
    return (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
        .replace("ﬂ", "fl")
        .replace("ﬁ", "fi")
    )


def sort_norm(text: str | None) -> str | None:
    text = normalize_text(text)
    return strip_ligatures(text).casefold() if text else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def file_path(source_root: Path, file_no: int) -> Path:
    matches = sorted(source_root.glob(f"*-{file_no}.txt"))
    if not matches:
        raise FileNotFoundError(f"No OCR file found for suffix {file_no} in {source_root}")
    return matches[0]


def discover_files(source_root: Path, file_numbers: list[int]) -> list[Path]:
    return [file_path(source_root, n) for n in file_numbers]


def parse_xml_page(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))


def block_texts(page: ET.Element, block_types: tuple[str, ...] = ("texto_principal", "rodape")) -> list[str]:
    texts: list[str] = []
    for bloco in page.findall("bloco"):
        block_type = (bloco.attrib.get("tipo") or "").strip().lower()
        if block_type not in block_types:
            continue
        content = "".join(bloco.itertext()).replace("\xa0", " ")
        if not content.strip():
            continue
        texts.append(content)
    return texts


def flatten_lines(texts: list[str]) -> list[str]:
    lines: list[str] = []
    for text in texts:
        for raw in text.splitlines():
            line = normalize_text(raw)
            if not line:
                continue
            if line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def is_letter_header(line: str) -> bool:
    return bool(re.fullmatch(r"[A-ZÆŒ]", line))


def looks_like_entry(line: str) -> bool:
    if not line:
        return False
    if is_letter_header(line):
        return False
    if line.startswith("[ilegivel]"):
        return False
    if re.match(r"^\d+\s+APPEND\.", line):
        return False
    if re.match(r"^\d+\.\s+", line):
        return True
    if re.match(r"^\[\d+\]\s+", line):
        return True
    if re.match(r"^[A-Z][A-Za-zÆŒæœ'\-\. ]{1,40},", line):
        return True
    if re.match(r"^[A-Z][A-Za-zÆŒæœ'\-\. ]{1,40}\.\s", line):
        return True
    if re.match(r"^APPEND(?:IX)?\.\s+XX[IVX]+\.", line, re.IGNORECASE):
        return True
    return False


def first_entry_lemma(lines: list[str]) -> str | None:
    for line in lines:
        if not looks_like_entry(line):
            continue
        cleaned = line
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
        cleaned = re.sub(r"^\[\d+\]\s*", "", cleaned)
        cleaned = cleaned.strip()
        # Use the left-most clause before the first period when it is a long entry.
        if cleaned.startswith("APPEND"):
            return cleaned
        if "," in cleaned:
            return cleaned.split(",", 1)[0].strip()
        if ". " in cleaned:
            return cleaned.split(". ", 1)[0].strip()
        return cleaned
    return None


def section_entry_kind(section_kind: str) -> str:
    if section_kind == "analytic_subject":
        return "lemma"
    return "lemma"


def parse_section_pages(source_root: Path, file_numbers: list[int]) -> dict[str, Any]:
    files = discover_files(source_root, file_numbers)
    entries: list[dict[str, Any]] = []
    for path in files:
        page = parse_xml_page(path)
        texts = block_texts(page)
        lines = flatten_lines(texts)
        if not lines:
            continue
        first_lemma = first_entry_lemma(lines)
        entry_raw = "\n".join(lines)
        entries.append(
            {
                "file": str(path),
                "lines": lines,
                "entry_raw": entry_raw,
                "lemma_raw": first_lemma,
                "lemma_display": first_lemma,
                "lemma_norm": sort_norm(first_lemma),
                "lemma_sort": sort_norm(first_lemma),
                "context_raw": lines[0] if lines else None,
            }
        )
    return {
        "files": [str(p) for p in files],
        "entries": entries,
    }


def build_helper_request(source_root: str) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root,
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [
            {
                "entry_id": "pl083_section_analytic_001",
                "lemma_raw": "APPEND. XXIII. — DE PROPRIETATE SERMONUM.",
                "query_names": [
                    "DE PROPRIETATE SERMONUM",
                    "Inter liberos et filios hoc interest",
                    "Inter bellum et avellum hoc interest",
                ],
                "page_hints": ["1339", "1340"],
                "page_hint_ints": [1339, 1340],
                "context_raw": "APPEND. XXIII. — DE PROPRIETATE SERMONUM.",
            },
            {
                "entry_id": "pl083_section_gloss_024",
                "lemma_raw": "APPEND. XXIV. — LIBER GLOSSARUM.",
                "query_names": [
                    "LIBER GLOSSARUM",
                    "A se, spontaneus",
                    "Abrego, segrego",
                ],
                "page_hints": ["443", "444"],
                "page_hint_ints": [443, 444],
                "context_raw": "APPEND. XXIV. — LIBER GLOSSARUM.",
            },
            {
                "entry_id": "pl083_section_gloss_025",
                "lemma_raw": "APPEND. XXV. — LIBER GLOSSARUM.",
                "query_names": [
                    "LIBER GLOSSARUM",
                    "Lampium, pulpitum, analogium",
                    "Virginal, membru virginis, in quo habitat",
                ],
                "page_hints": ["1357", "1358"],
                "page_hint_ints": [1357, 1358],
                "context_raw": "APPEND. XXV. — LIBER GLOSSARUM.",
            },
        ],
    }


def helper_entry_map(helper_output: dict[str, Any] | None) -> dict[str, Any]:
    if not helper_output:
        return {}
    return {entry.get("entry_id"): entry for entry in helper_output.get("entries", []) if isinstance(entry, dict)}


def build_payload(source_root: Path, helper_output: dict[str, Any] | None) -> dict[str, Any]:
    now = now_iso()
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    notes: list[str] = []

    helper_map = helper_entry_map(helper_output)

    entry_counter = 0
    for spec in SECTION_SPECS:
        page_data = parse_section_pages(source_root, spec["files"])
        file_list = page_data["files"]
        section_helper_id = {
            "analytic_subject": "pl083_section_analytic_001",
            "alphabetical_general": "pl083_section_gloss_024" if spec["section_order"] == 2 else "pl083_section_gloss_025",
        }[spec["section_kind"]]
        section_helper = helper_map.get(section_helper_id)
        sec = {
            "section_key": spec["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": spec["section_order"],
            "section_kind": spec["section_kind"],
            "heading_raw": spec["heading_raw"],
            "heading_norm": spec["heading_norm"],
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": file_list[0] if file_list else None,
            "file_end": file_list[-1] if file_list else None,
            "confidence": 0.84 if spec["section_kind"] == "analytic_subject" else 0.88,
            "raw_json": {
                "source_files": file_list,
                "section_kind_reason": (
                    "Appendix XXIII is an analytical/differential glossary of contrasts."
                    if spec["section_kind"] == "analytic_subject"
                    else "Alphabetical glossary material organized by letter headings and short lexical lemmata."
                ),
                "helper": section_helper,
            },
        }
        sections.append(sec)

        # Preserve the letter headings as nodes when they are visible in the OCR.
        if spec["section_kind"] == "alphabetical_general":
            seen_letters: list[str] = []
            for path_str in file_list:
                page = parse_xml_page(Path(path_str))
                for line in flatten_lines(block_texts(page)):
                    if is_letter_header(line) and line not in seen_letters:
                        seen_letters.append(line)
            for idx, letter in enumerate(seen_letters, start=1):
                nodes.append(
                    {
                        "node_key": f"{VOLUME_ID}:node:{spec['section_order']:02d}:{idx:03d}",
                        "section_key": spec["section_key"],
                        "parent_node_key": None,
                        "node_order": idx,
                        "node_kind": "letter_group",
                        "label_raw": letter,
                        "label_norm": letter.lower(),
                        "label_sort": letter.lower(),
                        "node_level": 1,
                        "confidence": 0.95,
                        "raw_json": {
                            "source_files": file_list,
                            "note": f"Letter heading {letter} seen in the OCR of appendix {spec['heading_raw']}.",
                        },
                    }
                )

        for local_idx, item in enumerate(page_data["entries"], start=1):
            entry_counter += 1
            lemma = item["lemma_raw"] or item["context_raw"] or f"{spec['heading_raw']} fragment"
            entries.append(
                {
                    "entry_key": f"{VOLUME_ID}:entry:{entry_counter:04d}",
                    "section_key": spec["section_key"],
                    "parent_node_key": None,
                    "entry_order": local_idx,
                    "entry_kind": section_entry_kind(spec["section_kind"]),
                    "lemma_raw": item["lemma_raw"],
                    "lemma_display": item["lemma_display"],
                    "lemma_norm": item["lemma_norm"],
                    "lemma_sort": item["lemma_sort"],
                    "entry_raw": item["entry_raw"],
                    "context_raw": item["context_raw"],
                    "heading_letter": item["lemma_raw"][0].upper() if item["lemma_raw"] else None,
                    "inferred_printed_page": None,
                    "section_start_file": file_list[0] if file_list else None,
                    "editorial_anchor_file": item["file"],
                    "target_file_best": item["file"],
                    "confidence": 0.72 if spec["section_kind"] == "alphabetical_general" else 0.77,
                    "raw_json": {
                        "source_file": item["file"],
                        "source_lines": item["lines"][:12],
                        "grouped_fragment": True,
                        "helper": section_helper,
                        "notes": [
                            "Entry is preserved as a grouped OCR fragment for the page rather than a fully split leaf lemma list.",
                            "No material locator was invented beyond the OCR file that holds the fragment.",
                        ],
                    },
                }
            )

    coverage = {
        "entries_status": "grouped_from_ocr",
        "entries_status_reason": "The volume contains three appendix/index sections, and the OCR has been serialized as grouped page fragments plus visible letter-group nodes where present.",
        "evidence_files": [
            str(source_root / "e2804b08-0ec4-4919-ad0c-00f9162d567c-669.txt"),
            str(source_root / "e2804b08-0ec4-4919-ad0c-00f9162d567c-670.txt"),
            str(source_root / "e2804b08-0ec4-4919-ad0c-00f9162d567c-671.txt"),
            str(source_root / "e2804b08-0ec4-4919-ad0c-00f9162d567c-683.txt"),
            str(source_root / "e2804b08-0ec4-4919-ad0c-00f9162d567c-693.txt"),
        ],
    }

    notes.extend(
        [
            "Appendix XXIII is analytic/differential rather than a plain alphabetical index.",
            "Appendix XXIV and XXV are alphabetical glossaries; the OCR is page-fragment oriented, so entries are stored conservatively as grouped page fragments.",
            "The ORDO RERUM pages in the tail window are editorial contents, not a leaf index, and were therefore excluded from sections.",
        ]
    )

    return {
        "schema_version": 1,
        "generated_at": now,
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL083 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Serialize PL083 appendix/index sections and preserve OCR fragments conservatively.",
        "completed": [
            "Read contract docs",
            "Confirmed Appendix XXIII, XXIV, and XXV as the relevant index-like sections",
        ],
        "pending": [
            "Run helper for the three section anchors",
            "Write the final payload JSON",
        ],
        "blocked": [],
        "notes": [
            "Keep the ORDO RERUM contents pages out of sections.",
            "Use grouped OCR page fragments rather than inventing leaf lemmata where the OCR is too dense to split safely.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    helper_request = build_helper_request(str(args.source_root))
    write_json(args.helper_request_json, helper_request)

    helper_output: dict[str, Any] | None = None
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(args.helper_request_json),
        "--output",
        str(args.helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    helper_output = read_json(args.helper_output_json, default={})

    payload = build_payload(args.source_root, helper_output)
    write_json(args.output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("Helper output captured")
    todo["completed"].append("Final payload written")
    todo["pending"] = []
    write_json(args.intermediate_dir / "todo.json", todo)


if __name__ == "__main__":
    main()
