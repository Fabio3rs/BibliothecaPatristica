#!/usr/bin/env python3
"""Usage: build the PG006 closing index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg006_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG006/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG006_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG006_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG006 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG006_alphabetical_indices.json
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PG006"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, Tomus VI"
SCRIPT_TARGET_LOCATOR = Path("/homessddata/Projects/pdfocr/scripts/index_target_locator.py")

SECTION_DEFS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:alphabetical_general:001",
        "section_order": 1,
        "section_kind": "alphabetical_general",
        "heading_raw": "INDEX RERUM QUÆ ANTE APPENDICEM INVENIUNTUR.",
        "heading_norm": "index rerum quae ante appendicem inveniuntur",
        "page_start": 1611,
        "page_end": 1694,
        "file_start_seq": 812,
        "file_end_seq": 854,
        "section_kind_reason": "Main alphabetical index of matters and words before the appendix.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
        "section_order": 2,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "page_start": 1695,
        "page_end": 1702,
        "file_start_seq": 854,
        "file_end_seq": 857,
        "section_kind_reason": "Closing contents table for the volume.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:003",
        "section_order": 3,
        "section_kind": "analytic_subject",
        "heading_raw": "INDEX ANALYTICUS.",
        "heading_norm": "index analyticus",
        "page_start": 1703,
        "page_end": 1814,
        "file_start_seq": 858,
        "file_end_seq": 913,
        "section_kind_reason": "Analytical index with numbered thematic entries and page citations.",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:004",
        "section_order": 4,
        "section_kind": "author_index",
        "heading_raw": "INDEX SCRIPTORUM.",
        "heading_norm": "index scriptorum",
        "page_start": 1815,
        "page_end": 1818,
        "file_start_seq": 914,
        "file_end_seq": 915,
        "section_kind_reason": "Author index of the writers cited in the volume.",
    },
]

NOISE_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
PAGE_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LEADING_SECTION_RE = re.compile(
    r"^(?:INDEX RERUM(?: QUÆ ANTE APPENDICEM INVENIUNTUR\.)?|ORDO RERUM(?: QUÆ IN HOC TOMO CONTINENTUR\.)?|INDEX ANALYTICUS\.?|INDEX SCRIPTORUM\.?)$",
    re.IGNORECASE,
)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ORDINAL_HEADING_RE = re.compile(r"^(?:I|II|III|IV|V|VI|VII|VIII|IX|X)\.\s*[A-Z].*$")
REF_WORD_RE = re.compile(r"\b(?:ibid\.?|id\.?)\b", re.IGNORECASE)
SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=(?:[A-ZÆŒΑ-Ω]|[IVXLCDM]+\.) )")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if value is None:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_lines(path: Path) -> list[str]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    text = parsed.get("all_text") or ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = normalize(raw)
        if not line:
            continue
        if NOISE_RE.fullmatch(line):
            continue
        lines.append(line)
    return lines


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in PAGE_NUM_RE.finditer(header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def resolve_target(page: int | None, page_map: dict[int, str]) -> tuple[str | None, float | None]:
    if page is None:
        return None, None
    if page in page_map:
        return page_map[page], 0.99
    best: tuple[int, int, str] | None = None
    for candidate_page, candidate_path in page_map.items():
        distance = abs(candidate_page - page)
        if best is None or (distance, candidate_page) < (best[0], best[1]):
            best = (distance, candidate_page, candidate_path)
    if best is None or best[0] > 2:
        return None, None
    return best[2], 0.72


def split_line(line: str) -> list[str]:
    text = normalize(line) or ""
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.;])\s+(?=[A-ZÆŒΑ-Ω])", text) if part.strip()]
    return parts or [text]


def page_refs_from_text(text: str, last_page: int | None) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_page = last_page
    for token in PAGE_NUM_RE.findall(text):
        page = int(token)
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
        current_page = page
    if not refs and REF_WORD_RE.search(text) and current_page is not None:
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": current_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs, current_page


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    text = re.sub(r"^\d+\s*[.)]?\s*—?\s*", "", text)
    if "—" in text and re.match(r"^[IVXLCDM]+\.\s*—", text):
        text = text.split("—", 1)[1].strip()
    first_ref = PAGE_NUM_RE.search(text)
    if first_ref:
        text = text[: first_ref.start()].rstrip(" ,;:.")
    if "." in text and not text.upper().startswith(("INDEX ", "ORDO ", "LIBER ")):
        text = text.split(".", 1)[0].strip()
    return text.strip(" ,;:.") or None


def entry_kind(section_kind: str, entry_raw: str) -> str:
    text = normalize(entry_raw) or ""
    if section_kind == "ordo_rerum":
        return "heading_group"
    if SINGLE_LETTER_RE.fullmatch(text):
        return "heading_group"
    if ORDINAL_HEADING_RE.match(text):
        return "heading_group"
    if text.startswith("Vide ") or text.startswith("Vid. ") or text.startswith("Voir ") or text.startswith("V. "):
        return "cross_reference"
    return "lemma"


def build_helper_request(volume_id: str, source_root: Path, page_map: dict[int, str]) -> dict[str, Any]:
    entries = [
        {
            "entry_id": f"{volume_id.lower()}_sample_001",
            "lemma_raw": "Abdera ab Herculis amico nomen accepit",
            "query_names": ["Abdera", "Democritus", "Herculis amico"],
            "page_hints": ["238"],
            "page_hint_ints": [238],
            "context_raw": "Abdera ab Herculis amico nomen accepit, 238.",
        },
        {
            "entry_id": f"{volume_id.lower()}_sample_002",
            "lemma_raw": "Vinum ad auxilium corporis factum est",
            "query_names": ["Vinum ad auxilium corporis factum est", "Vita"],
            "page_hints": ["414"],
            "page_hint_ints": [414],
            "context_raw": "Vinum ad auxilium corporis factum est, aqua omnino necessaria, 414.",
        },
        {
            "entry_id": f"{volume_id.lower()}_sample_003",
            "lemma_raw": "Anni Romanorum usque ad obitum Marci Aurelii",
            "query_names": ["Anni Romanorum usque ad obitum Marci Aurelii", "Marci Aurelii"],
            "page_hints": ["1162"],
            "page_hint_ints": [1162],
            "context_raw": "27. — Anni Romanorum usque ad obitum Marci Aurelii. 1162",
        },
        {
            "entry_id": f"{volume_id.lower()}_sample_004",
            "lemma_raw": "Cadmus, litterarum auctor Græcis, e Phœnicia",
            "query_names": ["Cadmus", "litterarum auctor Græcis", "Phœnicia"],
            "page_hints": ["263"],
            "page_hint_ints": [263],
            "context_raw": "Cadmus, litterarum auctor Græcis, e Phœnicia, 263.",
        },
    ]
    return {"volume_id": volume_id, "source_root": str(source_root), "options": {"top_k": 5, "adjacency_window": 2}, "entries": entries}


def run_helper(request_json: Path, output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(request_json),
            "--output",
            str(output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(output_json.read_text(encoding="utf-8"))


def build_payload(
    *,
    source_root: Path,
    helper_output: dict[str, Any] | None,
    page_map: dict[int, str],
) -> dict[str, Any]:
    files = discover_files(source_root)
    relevant_files = [path for path in files if 812 <= file_seq(path) <= 915]
    file_by_seq = {file_seq(path): path for path in relevant_files}
    section_by_key = {section["section_key"]: section for section in SECTION_DEFS}
    section_files_by_key: dict[str, list[str]] = {}
    for section in SECTION_DEFS:
        section_files_by_key[section["section_key"]] = [
            str(file_by_seq[seq])
            for seq in range(section["file_start_seq"], section["file_end_seq"] + 1)
            if seq in file_by_seq
        ]

    sections = []
    for section in SECTION_DEFS:
        section_files = section_files_by_key.get(section["section_key"], [])
        sections.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": section["heading_norm"],
                "heading_letter": None,
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "file_start": section_files[0] if section_files else None,
                "file_end": section_files[-1] if section_files else None,
                "confidence": 0.94 if section["section_kind"] != "author_index" else 0.96,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "evidence_files": section_files,
                },
            }
        )

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    current_section_key = SECTION_DEFS[0]["section_key"]
    current_node_key: str | None = None
    current_node_order = 0
    entry_order_by_section: dict[str, int] = defaultdict(int)
    ref_order_by_entry: dict[str, int] = defaultdict(int)
    last_page: int | None = None

    for path in relevant_files:
        lines = extract_lines(path)
        for raw_line in lines:
            text = normalize(raw_line) or ""
            if not text:
                continue
            if re.fullmatch(r"\d+(?:\s+\d+)+", text):
                continue
            if LEADING_SECTION_RE.fullmatch(text) or text in {
                "INDEX RERUM",
                "INDEX RERUM.",
                "ORDO RERUM",
                "ORDO RERUM.",
                "INDEX ANALYTICUS",
                "INDEX ANALYTICUS.",
                "INDEX SCRIPTORUM",
                "INDEX SCRIPTORUM.",
            }:
                if "ORDO RERUM" in text:
                    current_section_key = SECTION_DEFS[1]["section_key"]
                elif "INDEX ANALYTICUS" in text:
                    current_section_key = SECTION_DEFS[2]["section_key"]
                elif "INDEX SCRIPTORUM" in text:
                    current_section_key = SECTION_DEFS[3]["section_key"]
                continue
            if "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR" in text or "ORDO RERUM QUE IN HOC TOMO CONTINENTUR" in text:
                current_section_key = SECTION_DEFS[1]["section_key"]
                continue
            if "INDEX ANALYTICUS" in text:
                current_section_key = SECTION_DEFS[2]["section_key"]
                continue
            if "INDEX SCRIPTORUM" in text:
                current_section_key = SECTION_DEFS[3]["section_key"]
                continue
            if "INDEX RERUM" in text and not text.startswith(("Ab", "Ac", "Ad", "Æ", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z")):
                continue
            if text in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                current_node_order += 1
                current_node_key = f"{VOLUME_ID}:node:{current_node_order:04d}"
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": current_section_key,
                        "parent_node_key": None,
                        "node_order": current_node_order,
                        "node_kind": "letter_group",
                        "label_raw": text,
                        "label_norm": text,
                        "label_sort": text.lower(),
                        "node_level": 1,
                        "confidence": 0.95,
                        "raw_json": {"source_file": str(path)},
                    }
                )
                continue
            if ORDINAL_HEADING_RE.match(text):
                current_node_order += 1
                current_node_key = f"{VOLUME_ID}:node:{current_node_order:04d}"
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": current_section_key,
                        "parent_node_key": None,
                        "node_order": current_node_order,
                        "node_kind": "ordinal_group",
                        "label_raw": text,
                        "label_norm": text,
                        "label_sort": sort_norm(text),
                        "node_level": 1,
                        "confidence": 0.92,
                        "raw_json": {"source_file": str(path)},
                    }
                )
                continue

            for chunk in split_line(text):
                chunk = normalize(chunk) or ""
                if not chunk or LEADING_SECTION_RE.fullmatch(chunk):
                    continue
                if len(chunk) > 900:
                    continue
                section = section_by_key[current_section_key]
                entry_order_by_section[current_section_key] += 1
                entry_order = entry_order_by_section[current_section_key]
                entry_key = f"{VOLUME_ID}:entry:{section['section_order']:02d}:{entry_order:04d}"
                lemma_raw = lemma_from_entry(chunk)
                inferred_page: int | None = None
                numeric_refs = PAGE_NUM_RE.findall(chunk)
                if numeric_refs:
                    inferred_page = int(numeric_refs[0])
                elif last_page is not None and REF_WORD_RE.search(chunk):
                    inferred_page = last_page
                target_file_best, target_prob = resolve_target(inferred_page, page_map)
                raw_refs, last_page = page_refs_from_text(chunk, last_page)
                if raw_refs:
                    page_ref = raw_refs[0]["page_ref_int"]
                else:
                    page_ref = inferred_page
                entries.append(
                    {
                        "entry_key": entry_key,
                        "section_key": current_section_key,
                        "parent_node_key": current_node_key,
                        "entry_order": entry_order,
                        "entry_kind": entry_kind(section["section_kind"], chunk),
                        "lemma_raw": lemma_raw,
                        "lemma_display": lemma_raw,
                        "lemma_norm": sort_norm(lemma_raw),
                        "lemma_sort": sort_norm(lemma_raw),
                        "entry_raw": chunk,
                        "context_raw": None,
                        "heading_letter": next((node["label_raw"] for node in reversed(nodes) if node["node_key"] == current_node_key), None),
                        "inferred_printed_page": page_ref,
                        "section_start_file": section_files_by_key[current_section_key][0] if section_files_by_key.get(current_section_key) else None,
                        "editorial_anchor_file": str(path),
                        "target_file_best": target_file_best,
                        "confidence": 0.88 if target_file_best else 0.72,
                        "raw_json": {
                            "source_file": str(path),
                            "section_kind": section["section_kind"],
                            "page_refs": raw_refs,
                            "helper_best_candidate": (helper_output or {}).get("entries", [{}])[0].get("best_candidate") if helper_output else None,
                        },
                    }
                )
                if raw_refs:
                    for ref in raw_refs:
                        ref_order_by_entry[entry_key] += 1
                        target_file, target_probability = resolve_target(ref["page_ref_int"], page_map)
                        refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order_by_entry[entry_key],
                                "ref_kind": ref["ref_kind"],
                                "ref_raw": ref["ref_raw"],
                                "page_ref_raw": ref["page_ref_raw"],
                                "page_ref_int": ref["page_ref_int"],
                                "page_ref_col": ref["page_ref_col"],
                                "line_ref_raw": ref["line_ref_raw"],
                                "range_start_raw": ref["range_start_raw"],
                                "range_end_raw": ref["range_end_raw"],
                                "target_file": target_file,
                                "target_file_probability": target_probability,
                                "section_start_file": section_files_by_key[current_section_key][0] if section_files_by_key.get(current_section_key) else None,
                                "editorial_anchor_file": str(path),
                                "confidence": 0.88 if target_file else 0.70,
                                "raw_json": {
                                    "source_file": str(path),
                                    "section_kind": section["section_kind"],
                                },
                            }
                        )

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "PG006 closing index pages were parsed into line-level entries with material page references resolved from the OCR page headers.",
        "evidence_files": [
            str(file_by_seq[812]),
            str(file_by_seq[854]),
            str(file_by_seq[858]),
            str(file_by_seq[914]),
        ],
    }

    notes: list[Any] = [
        "Section 1 covers the index rerum before the appendix; section 2 is the contents table (ordo rerum); section 3 is the analytical index; section 4 is the author index.",
        "Line-level extraction was used conservatively; long OCR lines were kept as grouped fragments rather than being reflowed into synthetic page-wide blocks.",
    ]
    if helper_output:
        notes.append({"note_type": "helper_output", "status": helper_output.get("entries", []) and "resolved" or "empty"})

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--helper-request-json", required=True)
    parser.add_argument("--helper-output-json", required=True)
    parser.add_argument("--intermediate-dir", required=True)
    parser.add_argument("--output-file", required=True)
    args = parser.parse_args()

    source_root = Path(args.source_root)
    helper_request_json = Path(args.helper_request_json)
    helper_output_json = Path(args.helper_output_json)
    intermediate_dir = Path(args.intermediate_dir)
    output_file = Path(args.output_file)
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    files = discover_files(source_root)
    page_map = build_page_map(files)
    helper_request = build_helper_request(VOLUME_ID, source_root, page_map)
    helper_request_json.write_text(json.dumps(helper_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    helper_output = run_helper(helper_request_json, helper_output_json)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Assemble PG006 closing index payload from OCR line fragments and page map.",
        "completed": [
            "identified index sections",
            "built helper request and ran locator",
        ],
        "pending": [
            "validate final JSON payload structure",
        ],
        "blocked": [],
        "notes": [
            "Section boundaries follow the printed headings in the OCR tail.",
            "The helper was used only as a sanity check; printed page headers remain the main locator signal.",
        ],
    }
    (intermediate_dir / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = build_payload(source_root=source_root, helper_output=helper_output, page_map=page_map)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
