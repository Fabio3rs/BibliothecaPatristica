#!/usr/bin/env python3
"""Usage: build the PG097 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG097_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG097/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG097_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG097_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG097 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG097_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.indexing.editorial_page_estimator import estimate_editorial_pages


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG097"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 97"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG097/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG097_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PG097_helper_request.json"
DEFAULT_HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PG097_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG097"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION1_PAGES = list(range(980, 990))
SECTION2_PAGES = list(range(990, 995))
SECTION3_PAGES = [995]

SECTION1_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION2_KEY = f"{VOLUME_ID}:alpha:analytic_subject:002"
SECTION3_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:003"

SECTION1_HEADING = "INDEX RERUM(1) QUÆ IN CHRONOGRAPHIA J. MALALE CONTINENTUR."
SECTION2_HEADING = "INDEX ANALYTICUS AD S. ANDREÆ CRETENSIS OPERA."
SECTION3_HEADING = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*-\s*(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-Z]$")
ALL_CAPS_RE = re.compile(r"^[A-ZÆŒ0-9 ,.'()/-]{4,}$")
NOISE_RE = re.compile(r"^(?:Digitized by Google|\[ilegivel\])$", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-ZÆŒ])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^0-9A-Za-z]+", " ", value)
    return re.sub(r"\s+", " ", value).strip().lower() or None


def extract_page_num_from_header(header_text: str) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", header_text or ""):
        if token.startswith("0"):
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            pages.append(value)
    return pages


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    try:
        estimator = estimate_editorial_pages(volume_id=VOLUME_ID, source_root=source_root, collection=COLLECTION)
        for item in estimator.get("files", []):
            file_path = str(item.get("file") or "")
            if not file_path:
                continue
            best_guess = item.get("best_guess")
            candidates: list[int] = []
            if isinstance(best_guess, list):
                candidates = [int(v) for v in best_guess if isinstance(v, int) or str(v).isdigit()]
            elif isinstance(best_guess, int):
                candidates = [best_guess]
            for page in candidates:
                page_map.setdefault(page, file_path)
    except Exception:
        pass
    for path in sorted(source_root.glob("*.txt")):
        raw = read_text(path)
        header_match = re.search(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', raw, re.S)
        if not header_match:
            continue
        header_text = re.sub(r"<[^>]+>", " ", header_match.group(1))
        for page in extract_page_num_from_header(header_text):
            page_map.setdefault(page, str(path))
    return page_map


def file_for_page(source_root: Path, page: int) -> Path | None:
    for path in source_root.glob(f"*-{page:03d}.txt"):
        return path
    return None


def extract_blocks(raw_xml: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for block_type, inner in BLOCK_RE.findall(raw_xml):
        text = re.sub(r"<[^>]+>", " ", inner)
        text = text.replace("\r", "")
        blocks.append((block_type, text))
    return blocks


def split_text_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for raw_line in text.splitlines():
        line = normalize(raw_line)
        if not line or NOISE_RE.fullmatch(line):
            continue
        if LETTER_RE.fullmatch(line):
            fragments.append(line)
            continue
        if line in {"INDEX RERUM", "INDEX ANALYTICUS", "JOANNES MALALAS.", "S. ANDREAS HIEROSOLYMITANUS.", "ORATIONES.", "INDICES."}:
            fragments.append(line)
            continue
        fragments.extend(_split_sentence_chunks(line))
    return [frag for frag in (normalize(f) for f in fragments) if frag]


def _split_sentence_chunks(text: str) -> list[str]:
    chunks = [c.strip() for c in SENTENCE_SPLIT_RE.split(text) if c.strip()]
    return chunks or [text.strip()]


def extract_page_hints(text: str) -> list[int]:
    hints: list[int] = []
    seen: set[int] = set()
    for page in NUM_RE.findall(text or ""):
        value = int(page)
        if value not in seen:
            seen.add(value)
            hints.append(value)
    return hints


def first_number(text: str) -> int | None:
    m = NUM_RE.search(text)
    return int(m.group(1)) if m else None


def lemma_from_fragment(fragment: str) -> str | None:
    frag = normalize(fragment) or ""
    if not frag:
        return None
    m = NUM_RE.search(frag)
    if m:
        frag = frag[: m.start()].rstrip(" ,;:.")
    return frag or None


def extract_ref_items(fragment: str) -> list[dict[str, Any]]:
    ref_items: list[dict[str, Any]] = []
    for match in RANGE_RE.finditer(fragment):
        start, end = int(match.group(1)), int(match.group(2))
        ref_raw = match.group(0).strip(" ,.;")
        ref_items.append(
            {
                "ref_raw": ref_raw,
                "page_ref_raw": str(start),
                "page_ref_int": start,
                "page_ref_col": None,
                "range_start_raw": str(start),
                "range_end_raw": str(end),
            }
        )
    if ref_items:
        return ref_items
    for match in NUM_RE.finditer(fragment):
        num = int(match.group(1))
        tail = fragment[match.end() : match.end() + 12]
        col_match = re.match(r"^\s*,\s*([a-d](?:\s*,\s*[a-d])*)", tail, re.I)
        page_ref_col = col_match.group(1) if col_match else None
        ref_items.append(
            {
                "ref_raw": match.group(1) + (f", {page_ref_col}" if page_ref_col else ""),
                "page_ref_raw": str(num),
                "page_ref_int": num,
                "page_ref_col": page_ref_col,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return ref_items


def make_entry(
    section_key: str,
    section_kind: str,
    entry_order: int,
    fragment: str,
    current_node_key: str | None,
    page_map: dict[int, str],
    section_start_file: str,
    editorial_anchor_file: str,
    extra_note: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lemma_raw = lemma_from_fragment(fragment)
    refs = extract_ref_items(fragment)
    first_page = refs[0]["page_ref_int"] if refs else first_number(fragment)
    target_file_best = page_map.get(first_page) if first_page is not None else None
    entry_key = f"{VOLUME_ID}:entry:{section_kind}:{entry_order:04d}"
    entry_kind = "cross_reference" if lemma_raw and lemma_raw.lower().startswith(("vid", "vide", "voir", "cf", "id")) and not refs else "lemma"
    heading_letter = None
    if current_node_key:
        m = re.search(r":node:[^:]+:([a-z]):\d{3}$", current_node_key)
        if m:
            heading_letter = m.group(1).upper()
    confidence = 0.84 if refs else 0.72
    entry = {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": current_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": normalize(lemma_raw),
        "lemma_sort": sort_norm(lemma_raw),
        "entry_raw": fragment,
        "context_raw": None,
        "heading_letter": heading_letter,
        "inferred_printed_page": first_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": target_file_best,
        "confidence": confidence,
        "raw_json": {
            "source_file": editorial_anchor_file,
            "section_kind": section_kind,
            "fragment": fragment,
            "page_hints": extract_page_hints(fragment),
            "target_file_best_reason": "page_map_lookup" if target_file_best else "unresolved",
        },
    }
    if extra_note:
        entry["raw_json"]["note"] = extra_note
    ref_objs: list[dict[str, Any]] = []
    for ref_order, ref in enumerate(refs, start=1):
        ref_objs.append(
            {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": ref["ref_raw"],
                "page_ref_raw": ref["page_ref_raw"],
                "page_ref_int": ref["page_ref_int"],
                "page_ref_col": ref["page_ref_col"],
                "line_ref_raw": None,
                "range_start_raw": ref["range_start_raw"],
                "range_end_raw": ref["range_end_raw"],
                "target_file": target_file_best,
                "target_file_probability": 0.88 if target_file_best else None,
                "section_start_file": section_start_file,
                "editorial_anchor_file": editorial_anchor_file,
                "confidence": confidence,
                "raw_json": {
                    "source_file": editorial_anchor_file,
                    "section_kind": section_kind,
                },
            }
        )
    return entry, ref_objs


def build_sections() -> list[dict[str, Any]]:
    return [
        {
            "section_key": SECTION1_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION1_HEADING,
            "heading_norm": normalize(SECTION1_HEADING),
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": None,
            "file_end": None,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Alphabetical analytic index headed INDEX RERUM and continued through INDEX ANALYTICUS for Joannis Malalae Chronographiam.",
            },
        },
        {
            "section_key": SECTION2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION2_HEADING,
            "heading_norm": normalize(SECTION2_HEADING),
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": None,
            "file_end": None,
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Alphabetical analytic index for S. Andreas Cretensis opera, with letter dividers and page/column locators.",
            },
        },
        {
            "section_key": SECTION3_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 3,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION3_HEADING,
            "heading_norm": normalize(SECTION3_HEADING),
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": None,
            "file_end": None,
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Editorial contents table at the end of the tome; separate from the alphabetical indices.",
            },
        },
    ]


def iter_volume_fragments(source_root: Path, pages: Iterable[int]) -> list[tuple[int, str, str, str]]:
    fragments: list[tuple[int, str, str, str]] = []
    for page in pages:
        file_path = file_for_page(source_root, page)
        if file_path is None:
            continue
        raw_xml = read_text(file_path)
        for block_type, inner in extract_blocks(raw_xml):
            if block_type not in {"texto_principal", "nota_marginal", "cabecalho"}:
                continue
            block_text = normalize(re.sub(r"<[^>]+>", " ", inner) or "")
            if not block_text or NOISE_RE.fullmatch(block_text):
                continue
            if block_type == "nota_marginal" and LETTER_RE.fullmatch(block_text):
                fragments.append((page, str(file_path), block_type, block_text))
                continue
            if block_type == "cabecalho":
                if block_text in {SECTION1_HEADING, SECTION2_HEADING, SECTION3_HEADING}:
                    continue
                continue
            for frag in split_text_fragments(inner):
                if frag in {SECTION1_HEADING, SECTION2_HEADING, SECTION3_HEADING, "INDEX RERUM", "INDEX ANALYTICUS", "INDICES.", "ORDO RERUM"}:
                    continue
                if NOISE_RE.fullmatch(frag):
                    continue
                fragments.append((page, str(file_path), block_type, frag))
    return fragments


def build_helper_request(selected_entries: list[dict[str, Any]], source_root: Path, helper_request_json: Path) -> None:
    helper_entries = []
    for entry in selected_entries:
        page_hints = entry["raw_json"].get("page_hints") or []
        if not page_hints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:80],
                "query_names": [
                    entry["lemma_raw"] or entry["entry_raw"][:80],
                    (entry["entry_raw"].split(",", 1)[0] or entry["entry_raw"][:80]).strip(),
                ],
                "page_hints": [str(p) for p in page_hints[:3]],
                "page_hint_ints": page_hints[:3],
                "context_raw": entry["entry_raw"],
            }
        )
    write_json(
        helper_request_json,
        {
            "volume_id": VOLUME_ID,
            "source_root": str(source_root),
            "options": {"top_k": 5, "adjacency_window": 2},
            "entries": helper_entries[:24],
        },
    )


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def write_todo(intermediate_dir: Path, note: str) -> None:
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": note,
            "completed": [
                "OCR tail inspected",
                "page map estimated",
            ],
            "pending": [
                "validate the helper request",
                "review any low-confidence fragment splits",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                "Do not collapse editorial pages with OCR file suffixes.",
            ],
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST_JSON)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT_JSON)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    ap.add_argument("--write-helper-request", action="store_true", default=True)
    ap.add_argument("--skip-helper", action="store_true")
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_todo(args.intermediate_dir, "Resolve PG097 alphabetical indices and closing contents table")

    page_map = build_page_map(args.source_root)

    sections = build_sections()
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    section_specs = [
        (SECTION1_KEY, "analytic_subject", SECTION1_PAGES),
        (SECTION2_KEY, "analytic_subject", SECTION2_PAGES),
        (SECTION3_KEY, "ordo_rerum", SECTION3_PAGES),
    ]

    current_node_key_by_section: dict[str, str | None] = {SECTION1_KEY: None, SECTION2_KEY: None, SECTION3_KEY: None}
    node_counters: dict[str, int] = {SECTION1_KEY: 0, SECTION2_KEY: 0, SECTION3_KEY: 0}
    entry_counters: dict[str, int] = {SECTION1_KEY: 0, SECTION2_KEY: 0, SECTION3_KEY: 0}
    fragments_by_section: dict[str, list[tuple[int, str, str, str]]] = {
        SECTION1_KEY: iter_volume_fragments(args.source_root, SECTION1_PAGES),
        SECTION2_KEY: iter_volume_fragments(args.source_root, SECTION2_PAGES),
        SECTION3_KEY: iter_volume_fragments(args.source_root, SECTION3_PAGES),
    }

    for section_key, section_kind, pages in section_specs:
        section_start_path = file_for_page(args.source_root, pages[0])
        section_end_path = file_for_page(args.source_root, pages[-1])
        section_start_file = str(section_start_path) if section_start_path else None
        section_end_file = str(section_end_path) if section_end_path else None
        sections[[SECTION1_KEY, SECTION2_KEY, SECTION3_KEY].index(section_key)]["file_start"] = section_start_file
        sections[[SECTION1_KEY, SECTION2_KEY, SECTION3_KEY].index(section_key)]["file_end"] = section_end_file
        for page, source_file, block_type, frag in fragments_by_section[section_key]:
            if LETTER_RE.fullmatch(frag):
                node_counters[section_key] += 1
                node_key = f"{VOLUME_ID}:node:{section_key.split(':')[2]}:{frag.lower()}:{node_counters[section_key]:03d}"
                current_node_key_by_section[section_key] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_counters[section_key],
                        "node_kind": "heading_group",
                        "label_raw": frag,
                        "label_norm": frag,
                        "label_sort": frag.lower(),
                        "node_level": 1,
                        "confidence": 0.99,
                        "raw_json": {"source_file": source_file, "block_type": block_type},
                    }
                )
                continue
            if section_kind == "ordo_rerum" and ALL_CAPS_RE.fullmatch(frag) and not NUM_RE.search(frag):
                node_counters[section_key] += 1
                node_key = f"{VOLUME_ID}:node:{section_key.split(':')[2]}:heading:{node_counters[section_key]:03d}"
                current_node_key_by_section[section_key] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": section_key,
                        "parent_node_key": None,
                        "node_order": node_counters[section_key],
                        "node_kind": "heading_group",
                        "label_raw": frag,
                        "label_norm": normalize(frag),
                        "label_sort": sort_norm(frag),
                        "node_level": 1,
                        "confidence": 0.95,
                        "raw_json": {"source_file": source_file, "block_type": block_type},
                    }
                )
                continue
            entry_counters[section_key] += 1
            entry, entry_refs = make_entry(
                section_key=section_key,
                section_kind=section_kind,
                entry_order=entry_counters[section_key],
                fragment=frag,
                current_node_key=current_node_key_by_section[section_key],
                page_map=page_map,
                section_start_file=section_start_file,
                editorial_anchor_file=source_file,
                extra_note="heuristic line segmentation from OCR block text",
            )
            entries.append(entry)
            refs.extend(entry_refs)

    if args.write_helper_request:
        build_helper_request(entries, args.source_root, args.helper_request_json)
    helper_output: dict[str, Any] = {}
    if not args.skip_helper and args.helper_request_json.exists():
        helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    helper_by_id = {item.get("entry_id"): item for item in helper_output.get("entries", [])} if helper_output else {}
    for entry in entries:
        helper_item = helper_by_id.get(entry["entry_key"])
        if helper_item:
            entry["raw_json"]["helper_status"] = helper_output.get("status")
            entry["raw_json"]["helper_entry_id"] = helper_item.get("entry_id")
            entry["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")
            entry["raw_json"]["helper_candidates"] = helper_item.get("candidates", [])
            if helper_item.get("best_candidate", {}).get("file"):
                entry["target_file_best"] = helper_item["best_candidate"]["file"]
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the final index tail from OCR files 980-995 with conservative line segmentation and page-map resolution.",
        "evidence_files": [
            str(path)
            for p in (980, 981, 982, 983, 984, 985, 986, 987, 988, 989, 990, 991, 992, 993, 994, 995)
            if (path := file_for_page(args.source_root, p)) is not None
        ],
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "PG097 contains two alphabetical analytic indices and a closing Ordo Rerum contents table.",
                "OCR literals were preserved; brief heuristic segmentation was used where line wraps crossed columns.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": [
            "Section headings were kept separate from entries.",
            "Target files were resolved with the editorial page estimator when possible; ambiguous fragments retain OCR evidence in raw_json.",
        ],
    }
    write_json(args.output_file, payload)
    write_json(args.intermediate_dir / "payload.json", payload)


if __name__ == "__main__":
    main()
