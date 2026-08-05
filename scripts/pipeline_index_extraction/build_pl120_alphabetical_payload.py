#!/usr/bin/env python3
"""Usage: build the PL120 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl120_alphabetical_payload.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL120"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL120/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL120_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL120_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL120_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL120"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"


SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:paschasii_comment_bibl:001",
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX IN PASCHASII COMMENT. BIBL.",
        "start_match": re.compile(r"INDEX\s+RERUM ET VERBORUM NOTABILIUM", re.I),
        "heading_match": re.compile(r"INDEX IN PASCHAS?H?II COMMENT\. BIBL\.|INDEX IN PASCHASH COMMENT\. BIBL\.", re.I),
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:opuscula_dogmatica:001",
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX IN OPUSCULA DOGMATICA.",
        "start_match": re.compile(r"AD PASCHASII RADBERTI OPUSCULA DOGMATICA", re.I),
        "heading_match": re.compile(r"INDEX IN OPUSCULA DOGMATICA\.", re.I),
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:001",
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "start_match": re.compile(r"ORDO RERUM\s+QUÆ IN HOC VOLUMINE CONTINENTUR", re.I),
        "heading_match": re.compile(r"ORDO RERUM", re.I),
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str) -> str:
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def norm_sort(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = strip_accents(text).replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;")
    return cleaned.lower() if cleaned else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def iter_ocr_files(source_root: Path) -> list[Path]:
    return sorted(
        source_root.glob("*.txt"),
        key=lambda p: int(re.search(r"-(\d+)\.txt$", p.name).group(1)),
    )


def file_seq(path: Path) -> int:
    return int(re.search(r"-(\d+)\.txt$", path.name).group(1))


def classify_section(path: Path) -> dict[str, str] | None:
    text = read_text(path)
    for section in SECTION_DEFS:
        if section["heading_match"].search(text):
            return {
                "section_key": section["section_key"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
            }
    return None


def extract_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', text, re.S):
        blocks.append((match.group(1), match.group(2)))
    return blocks


def block_lines(block_text: str) -> list[str]:
    lines: list[str] = []
    for raw in block_text.splitlines():
        line = normalize(raw)
        if not line or line == "Digitized by Google":
            continue
        lines.append(line)
    return lines


LETTER_ONLY = re.compile(r"^[A-ZÆŒ]$")


def is_continuation(prev: str, line: str) -> bool:
    if prev.endswith("-"):
        return True
    if re.match(r"^[a-zà-ÿ]", line):
        return True
    if line.startswith((";", ",", ":", ")", "]")):
        return True
    if line.lower().startswith(("et ", "et seq", "ibid", "idem", "infra", "supra")):
        return True
    return False


def split_fragments(block_text: str) -> list[str]:
    fragments: list[str] = []
    current = ""
    for line in block_lines(block_text):
        if LETTER_ONLY.fullmatch(line):
            if current:
                fragments.append(current.strip())
                current = ""
            continue
        if not current:
            current = line
            continue
        if is_continuation(current, line):
            if current.endswith("-"):
                current = current[:-1] + line.lstrip()
            else:
                current = f"{current} {line}"
        else:
            fragments.append(current.strip())
            current = line
    if current:
        fragments.append(current.strip())
    return fragments


def extract_page_hints(fragment: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", fragment):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def lemma_from_fragment(fragment: str) -> str:
    cleaned = fragment.lstrip("+ ").strip()
    number_match = re.search(r"(?<!\d)(\d{1,4})(?!\d)", cleaned)
    if number_match:
        cleaned = cleaned[: number_match.start()].rstrip(" ,;:.")
    return cleaned.strip()


def entry_kind_from_fragment(fragment: str) -> str:
    cleaned = fragment.lstrip("+ ").strip()
    if re.search(r"\b(vide|vid\.|voir|cf\.|id\.|ibid\.)\b", cleaned, re.I) and not re.search(r"\d", cleaned):
        return "cross_reference"
    if re.search(r"\b(vide|vid\.|voir|cf\.|id\.|ibid\.)\b", cleaned, re.I) and not re.search(r"\d", cleaned):
        return "cross_reference"
    if cleaned.startswith("+"):
        return "editorial_note"
    return "lemma"


def header_page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in iter_ocr_files(source_root):
        head = "\n".join(read_text(path).splitlines()[:10])
        for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", head):
            value = int(match.group(1))
            mapping.setdefault(value, str(path))
    return mapping


def fallback_search_page(source_root: Path, page: int) -> str | None:
    try:
        proc = subprocess.run(
            ["rg", "-l", "-S", rf"^\s*{page}(?:\s|$)", str(source_root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    candidates = [line.strip() for line in proc.stdout.splitlines() if line.strip().endswith(".txt")]
    return candidates[0] if candidates else None


def target_file_for_page(page: int, page_map: dict[int, str], source_root: Path) -> str | None:
    if page in page_map:
        return page_map[page]
    return fallback_search_page(source_root, page)


def section_files(section_key: str, classified: dict[str, dict[str, str]]) -> list[Path]:
    files = [Path(path) for path, meta in classified.items() if meta["section_key"] == section_key]
    return sorted(files, key=file_seq)


def collect_entries(
    source_root: Path,
    section_key: str,
    section_kind: str,
    start_match: re.Pattern[str],
    heading_match: re.Pattern[str],
    page_map: dict[int, str],
    helper_sample_limit: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    classified: dict[str, dict[str, str]] = {}
    for path in iter_ocr_files(source_root):
        meta = classify_section(path)
        if meta:
            classified[str(path)] = meta

    files = [Path(p) for p, meta in classified.items() if meta["section_key"] == section_key]
    files.sort(key=file_seq)
    if not files:
        return [], [], [], []

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files = [str(p) for p in files]

    started = False
    entry_order = 0
    for path in files:
        text = read_text(path)
        blocks = extract_blocks(text)
        for block_kind, block in blocks:
            if not started:
                if block_kind in {"cabecalho", "texto_principal"} and start_match.search(block):
                    started = True
                continue
            if block_kind != "texto_principal":
                continue
            for fragment in split_fragments(block):
                if not fragment:
                    continue
                if fragment in {"INDEX", "Nempe"}:
                    continue
                if LETTER_ONLY.fullmatch(fragment):
                    continue
                if re.fullmatch(r"[IVXLCDM]+\.?", fragment):
                    continue
                if not re.search(r"\d", fragment) and not re.search(r"\b(vide|vid\.|voir|cf\.|id\.|ibid\.)\b", fragment, re.I) and not fragment.startswith("+"):
                    continue

                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                lemma_raw = lemma_from_fragment(fragment)
                if not lemma_raw or not re.match(r"^[A-ZÆŒ]", lemma_raw):
                    entry_order -= 1
                    continue
                kind = entry_kind_from_fragment(fragment)
                page_hints = extract_page_hints(fragment)
                inferred_page = page_hints[0] if page_hints else None

                target_file_best = str(path)
                confidence = 0.92 if page_hints else 0.78
                if kind == "cross_reference" and not page_hints:
                    confidence = 0.70

                entry = {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": kind,
                    "lemma_raw": None if kind == "cross_reference" else lemma_raw,
                    "lemma_display": None if kind == "cross_reference" else lemma_raw,
                    "lemma_norm": None if kind == "cross_reference" else norm_sort(lemma_raw),
                    "lemma_sort": None if kind == "cross_reference" else norm_sort(lemma_raw),
                    "entry_raw": fragment,
                    "context_raw": fragment,
                    "heading_letter": lemma_raw[:1].upper() if lemma_raw else None,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(files[0]),
                    "editorial_anchor_file": str(path),
                    "target_file_best": target_file_best,
                    "confidence": confidence,
                    "raw_json": {
                        "source_file": str(path),
                        "section_kind": section_kind,
                        "page_hints": page_hints,
                        "fragment_reason": "line_or_grouped_fragment",
                    },
                }
                entries.append(entry)

                if page_hints:
                    ref_order = 0
                    seen_page: set[int] = set()
                    for page in page_hints:
                        if page in seen_page:
                            continue
                        seen_page.add(page)
                        ref_order += 1
                        target_file = target_file_for_page(page, page_map, source_root)
                        refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": "editorial_page",
                                "ref_raw": str(page),
                                "page_ref_raw": str(page),
                                "page_ref_int": page,
                                "page_ref_col": None,
                                "line_ref_raw": None,
                                "range_start_raw": None,
                                "range_end_raw": None,
                                "target_file": target_file,
                                "target_file_probability": 0.98 if target_file else None,
                                "section_start_file": str(files[0]),
                                "editorial_anchor_file": str(path),
                                "confidence": 0.94 if target_file else 0.6,
                                "raw_json": {
                                    "source_file": str(path),
                                    "locator_method": "header_page_map" if target_file else "unresolved",
                                },
                            }
                        )

                if len(helper_entries) < helper_sample_limit and page_hints:
                    helper_entries.append(
                        {
                            "entry_id": f"{VOLUME_ID.lower()}_{section_key.split(':')[2]}_{entry_order:04d}",
                            "lemma_raw": lemma_raw,
                            "query_names": [lemma_raw] if lemma_raw else [fragment],
                            "page_hints": [str(p) for p in page_hints[:3]],
                            "page_hint_ints": page_hints[:3],
                            "context_raw": fragment,
                        }
                    )

    return entries, refs, evidence_files, helper_entries


def build_sections(source_root: Path) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    classified: dict[str, dict[str, str]] = {}
    for path in iter_ocr_files(source_root):
        meta = classify_section(path)
        if meta:
            classified[str(path)] = meta

    for order, section in enumerate(SECTION_DEFS, start=1):
        files = [Path(p) for p, meta in classified.items() if meta["section_key"] == section["section_key"]]
        files.sort(key=file_seq)
        if not files:
            continue
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": order,
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": norm_sort(section["heading_raw"]),
                "heading_letter": None,
                "page_start": None,
                "page_end": None,
                "file_start": str(files[0]),
                "file_end": str(files[-1]),
                "confidence": 0.95,
                "raw_json": {
                    "files": [str(p) for p in files],
                    "section_kind_reason": "Detected from OCR heading text.",
                },
            }
        )
    return sections


def build_payload(source_root: Path) -> dict[str, Any]:
    page_map = header_page_map(source_root)
    sections = build_sections(source_root)

    all_entries: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    all_helper_entries: list[dict[str, Any]] = []
    evidence_files: list[str] = []

    for section in SECTION_DEFS:
        entries, refs, evidence, helper_entries = collect_entries(
            source_root,
            section["section_key"],
            section["section_kind"],
            section["start_match"],
            section["heading_match"],
            page_map,
        )
        if entries:
            all_entries.extend(entries)
            all_refs.extend(refs)
            evidence_files.extend(evidence)
            all_helper_entries.extend(helper_entries)

    # Deduplicate evidence files while preserving order.
    evidence_files = list(dict.fromkeys(evidence_files))

    helper_entries = all_helper_entries[:6]
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }

    write_json(HELPER_REQUEST_JSON, helper_request)
    TODO_JSON.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Build PL120 alphabetical payload and verify target locator output.",
            "completed": ["sections detected", "entries extracted from OCR tail", "helper request assembled"],
            "pending": ["run target locator helper", "write final payload", "spot-check target-file mapping"],
            "blocked": [],
            "notes": [
                "OCR tail contains two alphabetical indexes and one ordo rerum section.",
                "Target files for cited page numbers are resolved from header page numbers when possible.",
            ],
        },
    )

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(HELPER_REQUEST_JSON),
            "--output",
            str(HELPER_OUTPUT_JSON),
            "--pretty",
        ],
        check=True,
    )

    helper_output = json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8")) if HELPER_OUTPUT_JSON.exists() else {}

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": "Patrologia Latina 120",
        },
        "sections": sections,
        "nodes": [],
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the two alphabetical index sections and the closing ORDO RERUM section from the OCR tail; material references were resolved from page headers when available.",
            "evidence_files": evidence_files,
        },
        "notes": [
            "The first two sections are alphabetical indexes in Paschasius Radbertus' commentaries and opuscula dogmatica.",
            "The closing ORDO RERUM section is included as editorial closure.",
            f"Helper status: {helper_output.get('status', 'unknown')}.",
        ],
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(SOURCE_ROOT))
    parser.add_argument("--output-file", default=str(OUTPUT_FILE))
    args = parser.parse_args()

    source_root = Path(args.source_root)
    payload = build_payload(source_root)
    write_json(Path(args.output_file), payload)


if __name__ == "__main__":
    main()
