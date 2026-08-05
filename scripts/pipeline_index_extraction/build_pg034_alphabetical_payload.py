#!/usr/bin/env python3
"""Usage: build the PG034 alphabetical payload from OCR tail index pages.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg034_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG034/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG034_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG034_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG034 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG034_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.editorial_page_estimator import estimate_editorial_pages


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG034"
COLLECTION = "PG"
VOLUME_LABEL = "PG034"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

INDEX_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_FILES = list(range(653, 665))
ORDO_FILES = [665, 667]

SECTION_1_HEADING_RAW = (
    "INDICES ANALYTICI. IN SS. MACARIUM AEGYPTIUM ET MACARIUM ALEXANDRINUM. "
    "I. INDEX RERUM MEMORABILIUM QUÆ IN SS. MACARIORUM ÆGYPTII ET ALEXANDRINI "
    "VITIS, ACTIS, APOPHTHEGMATIBUS, EPISTOLIS, NEC NON ET IN PRECIBUS CONTINENTUR."
)
SECTION_2_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."

NOISE_LINES = {
    "INDICES ANALYTICI",
    "INDEX ANALYTICUS",
    "INDEX ANALYCTICUS",
    "INDEX ANALYLICUS",
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
    "Digitized by Google",
}

SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
PAGE_HEADER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*([a-d]))?((?:\s*,\s*[a-d])*)(?:\s+et\s+seqq\.?)?", re.IGNORECASE)
RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–]\s*(\d{1,4})")
HELPER_SPLIT_RE = re.compile(r"(?<=[.;])\s+(?=[A-ZÆŒ])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = normalize_ws(text)
    value = value.strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    if value is None:
        return None
    cleaned = strip_accents(value)
    cleaned = cleaned.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse sequence from {path}")
    return int(m.group(1))


def discover_files(source_root: Path, seqs: list[int]) -> list[Path]:
    wanted = set(seqs)
    files = [p for p in sorted(source_root.glob("*.txt"), key=file_seq) if file_seq(p) in wanted]
    return files


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_blocks(path: Path, block_type: str) -> list[str]:
    text = read_text(path)
    blocks = re.findall(rf'<bloco[^>]*tipo="{block_type}"[^>]*>(.*?)</bloco>', text, flags=re.S | re.I)
    out: list[str] = []
    for block in blocks:
        body = re.sub(r"<[^>]+>", "", block)
        body = body.replace("\r", "\n")
        for raw_line in body.splitlines():
            line = normalize_ws(raw_line)
            if line:
                out.append(line)
    return out


def header_numbers(path: Path) -> list[int]:
    nums: list[int] = []
    for line in extract_blocks(path, "cabecalho"):
        for match in PAGE_HEADER_RE.finditer(line):
            value = int(match.group(1))
            if value not in nums:
                nums.append(value)
    return nums


def build_header_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for number in header_numbers(path):
            mapping.setdefault(number, str(path))
    return mapping


def _best_guess_pages(best_guess: Any) -> list[int]:
    if isinstance(best_guess, int):
        return [best_guess]
    if isinstance(best_guess, list):
        return [int(item) for item in best_guess if isinstance(item, int) or str(item).isdigit()]
    return []


def build_estimator_page_map(source_root: Path) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        payload = estimate_editorial_pages(volume_id=VOLUME_ID, source_root=source_root, collection=COLLECTION)
    except Exception:
        return mapping
    for item in payload.get("files") or []:
        file_path = str(item.get("file") or "")
        if not file_path:
            continue
        candidate_pages: list[int] = []
        candidate_pages.extend(_best_guess_pages(item.get("best_guess")))
        candidate_pages.extend(_best_guess_pages(item.get("best_single_page")))
        candidate_pages.extend(_best_guess_pages(item.get("best_left_page")))
        candidate_pages.extend(_best_guess_pages(item.get("best_right_page")))
        for candidate in item.get("candidate_editorial_pages") or []:
            candidate_pages.extend(_best_guess_pages(candidate.get("pages")))
        for page in candidate_pages:
            mapping.setdefault(page, file_path)
    return mapping


def build_page_map(files: list[Path], source_root: Path) -> dict[int, str]:
    page_map = build_header_page_map(files)
    estimator = build_estimator_page_map(source_root)
    for page, target in estimator.items():
        page_map.setdefault(page, target)
    return page_map


def page_target(page: int, page_map: dict[int, str]) -> str | None:
    return page_map.get(page)


def extract_section_lines(files: list[Path]) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for path in files:
        for line in extract_blocks(path, "texto_principal"):
            if line in NOISE_LINES:
                continue
            if SINGLE_LETTER_RE.fullmatch(line):
                continue
            if line.startswith("INDEX ANALYTICUS") or line.startswith("ORDO RERUM"):
                continue
            if line.startswith("Digitized by Google"):
                continue
            if re.fullmatch(r"\d{3,4}", line):
                continue
            lines.append((str(path), line))
    return lines


def build_text_entries(lines: list[tuple[str, str]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current_text: str | None = None
    current_file: str | None = None

    def flush() -> None:
        nonlocal current_text, current_file
        if current_text is None:
            return
        text = normalize_ws(current_text)
        if text:
            entries.append({"file": current_file, "text": text})
        current_text = None
        current_file = None

    for file_path, line in lines:
        if current_text is None:
            current_text = line
            current_file = file_path
            continue
        if re.match(r"^[a-z0-9(\[]", line) or line.startswith("ibid") or line.startswith("Ib") or line.startswith("et "):
            current_text += " " + line
            continue
        if len(line) < 50 and not PAGE_REF_RE.search(line) and not line.startswith(("Vide", "Vid.", "voir", "cf.", "id.")):
            current_text += " " + line
            continue
        flush()
        current_text = line
        current_file = file_path

    flush()
    return entries


def split_compound_entries(entry: dict[str, Any]) -> list[dict[str, Any]]:
    text = entry["text"]
    if text.startswith("Amina, filia regis, scilicet Dei"):
        return [
            {"file": entry["file"], "text": "Amina, filia regis, scilicet Dei, 414 c ; 418 a."},
            {"file": entry["file"], "text": "Anima sponsa Dei, 415 b, c ; templum Spiritus sancti, ibid."},
            {"file": entry["file"], "text": "Anima, regina Deo copulata, 418 a."},
        ]
    return [entry]


def heading_letter(text: str) -> str | None:
    m = re.search(r"[A-ZÆŒ]", text)
    return m.group(0) if m else None


def lemma_from_entry(text: str) -> str | None:
    cleaned = norm(text)
    if not cleaned:
        return None
    if cleaned.startswith(("Vide ", "Vid. ", "voir ", "cf. ", "id. ")):
        return None
    stop_positions = [pos for pos in (cleaned.find("."), cleaned.find(","), cleaned.find(";")) if pos != -1]
    if stop_positions:
        cleaned = cleaned[: min(stop_positions)]
    cleaned = cleaned.strip(" .;:")
    return cleaned or None


def entry_kind(section_kind: str, text: str) -> str:
    cleaned = norm(text) or ""
    if cleaned.startswith(("Vide ", "Vid. ", "voir ", "cf. ", "id. ")):
        return "cross_reference"
    if section_kind == "ordo_rerum":
        return "heading_group"
    return "lemma"


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, str | None, str]] = set()
    for match in PAGE_REF_RE.finditer(text):
        page = int(match.group(1))
        col = match.group(2)
        extra = match.group(3) or ""
        raw = match.group(0).strip().rstrip(".,;:")
        if raw.lower().startswith("ibid"):
            continue
        key = (page, col, extra)
        if key in seen:
            continue
        seen.add(key)
        cols = [col] if col else []
        if extra:
            cols.extend(part.strip().strip(",") for part in extra.split(",") if part.strip())
        page_ref_col = ", ".join(cols) if cols else None
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": page_ref_col,
            }
        )
    for match in RANGE_RE.finditer(text):
        raw = match.group(0).strip().rstrip(".,;:")
        page = int(match.group(1))
        end = match.group(2)
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_raw": raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "range_start_raw": str(page),
                "range_end_raw": str(int(end)),
            }
        )
    return refs


def build_helper_query_name(text: str) -> list[str]:
    cleaned = norm(text) or ""
    if not cleaned:
        return []
    first = cleaned.split(".", 1)[0].strip()
    names = [first]
    if "," in first:
        names.append(first.split(",", 1)[0].strip())
    return [name for name in names if name]


def build_entries(
    section_key: str,
    section_kind: str,
    lines: list[tuple[str, str]],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    base_entries = build_text_entries(lines)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    current_letter: str | None = None
    node_map: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    entry_order = 0
    node_order = 0

    for base in base_entries:
        for item in split_compound_entries(base):
            text = item["text"]
            page_refs = extract_page_refs(text)
            if not page_refs and section_kind != "ordo_rerum":
                # Keep OCR literal even when the locator is absent, but lower confidence.
                pass
            lemma_raw = lemma_from_entry(text)
            if lemma_raw is None and section_kind == "ordo_rerum":
                lemma_raw = text.split("  ", 1)[0]
            entry_order += 1
            initial = heading_letter(lemma_raw or text)
            if initial and initial != current_letter and section_kind != "ordo_rerum":
                current_letter = initial
                if initial not in node_map:
                    node_order += 1
                    node_key = f"{section_key}:node:{node_order:04d}"
                    node_map[initial] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": section_key,
                            "parent_node_key": f"{section_key}:node:0002" if section_kind != "ordo_rerum" else None,
                            "node_order": node_order,
                            "node_kind": "letter_group",
                            "label_raw": initial,
                            "label_norm": initial.lower(),
                            "label_sort": initial.lower(),
                            "node_level": 2,
                            "confidence": 0.96,
                            "raw_json": {"source_file": item["file"], "kind": "alphabetic divider"},
                        }
                    )
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            source_file = item["file"]
            entry = {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": node_map.get(initial) if section_kind != "ordo_rerum" else None,
                "entry_order": entry_order,
                "entry_kind": entry_kind(section_kind, text),
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": text,
                "context_raw": text if len(text) > 220 else None,
                "heading_letter": initial,
                "inferred_printed_page": page_refs[0]["page_ref_int"] if page_refs else None,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "target_file_best": source_file,
                "confidence": 0.86 if page_refs else 0.63,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": section_kind,
                    "page_hints": [ref["page_ref_int"] for ref in page_refs[:3]],
                    "split_strategy": "line_merge",
                },
            }
            entries.append(entry)
            helper_entry = {
                "entry_id": entry_key,
                "lemma_raw": lemma_raw or text[:80],
                "query_names": build_helper_query_name(text),
                "page_hints": [str(ref["page_ref_int"]) for ref in page_refs[:3]],
                "page_hint_ints": [ref["page_ref_int"] for ref in page_refs[:3]],
                "context_raw": text,
            }
            if helper_entry["page_hint_ints"]:
                helper_entries.append(helper_entry)
            for ref_order, ref in enumerate(page_refs, start=1):
                page = ref["page_ref_int"]
                target_file = page_target(page, page_map)
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page_column" if ref.get("page_ref_col") else "editorial_page",
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": page,
                        "page_ref_col": ref.get("page_ref_col"),
                        "line_ref_raw": None,
                        "range_start_raw": ref.get("range_start_raw"),
                        "range_end_raw": ref.get("range_end_raw"),
                        "target_file": target_file,
                        "target_file_probability": 0.96 if target_file else None,
                        "section_start_file": source_file,
                        "editorial_anchor_file": source_file,
                        "confidence": 0.92 if target_file else 0.68,
                        "raw_json": {
                            "source_file": source_file,
                            "page_map_source": "header+estimator" if target_file else "unresolved",
                        },
                    }
                )
    return entries, refs, nodes, helper_entries


def build_ordo_section(files: list[Path], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_order = 0
    current_text: str | None = None
    current_file: str | None = None

    def flush() -> None:
        nonlocal current_text, current_file, entry_order
        if current_text is None:
            return
        text = normalize_ws(current_text)
        current_text = None
        if not text or not text.startswith("Cap."):
            return
        page_refs = extract_page_refs(text)
        if not page_refs:
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:ordo:{entry_order:04d}"
        first_page = page_refs[0]["ref_raw"]
        lemma_raw = text.split(first_page, 1)[0].strip(" .—-")
        source_file = current_file or str(files[0])
        entry = {
            "entry_key": entry_key,
            "section_key": ORDO_SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": "heading_group",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": text,
            "context_raw": text if len(text) > 220 else None,
            "heading_letter": None,
            "inferred_printed_page": page_refs[0]["page_ref_int"],
            "section_start_file": source_file,
            "editorial_anchor_file": source_file,
            "target_file_best": source_file,
            "confidence": 0.9,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "ordo_rerum",
                "split_strategy": "chapter_line_merge",
            },
        }
        entries.append(entry)
        for ref_order, ref in enumerate(page_refs, start=1):
            target_file = page_target(ref["page_ref_int"], page_map)
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": ref_order,
                    "ref_kind": "editorial_page_column" if ref.get("page_ref_col") else "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref.get("page_ref_col"),
                    "line_ref_raw": None,
                    "range_start_raw": ref.get("range_start_raw"),
                    "range_end_raw": ref.get("range_end_raw"),
                    "target_file": target_file,
                    "target_file_probability": 0.96 if target_file else None,
                    "section_start_file": source_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.92 if target_file else 0.68,
                    "raw_json": {"source_file": source_file, "page_map_source": "header+estimator" if target_file else "unresolved"},
                }
            )

    for path in files:
        for line in extract_blocks(path, "texto_principal"):
            if line.startswith("ORDO RERUM") or line.startswith("QUÆ IN HOC TOMO CONTINENTUR") or line.startswith("QUAE IN HOC TOMO CONTINENTUR"):
                continue
            if line.startswith("Digitized by Google"):
                continue
            if SINGLE_LETTER_RE.fullmatch(line):
                continue
            if current_text is None:
                current_text = line
                current_file = str(path)
                continue
            if re.match(r"^[a-z0-9(\[]", line) or line.startswith("ibid"):
                current_text += " " + line
                continue
            if len(line) < 60 and not PAGE_REF_RE.search(line):
                current_text += " " + line
                continue
            flush()
            current_text = line
            current_file = str(path)
    flush()
    return entries, refs


def build_sections(index_files: list[Path], ordo_files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "section_key": INDEX_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": SECTION_1_HEADING_RAW,
            "heading_norm": sort_norm(SECTION_1_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1273,
            "page_end": 1302,
            "file_start": str(index_files[0]),
            "file_end": str(index_files[-1]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Alphabetical subject index with dense analytical and onomastic entries; the letter divisions are represented with nodes.",
                "source_window": [str(index_files[0]), str(index_files[-1])],
            },
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_2_HEADING_RAW,
            "heading_norm": sort_norm(SECTION_2_HEADING_RAW),
            "heading_letter": None,
            "page_start": 1307,
            "page_end": 1308,
            "file_start": str(ordo_files[0]),
            "file_end": str(ordo_files[-1]),
            "confidence": 0.96,
            "raw_json": {
                "section_kind_reason": "Editorial table of contents / order of matters, not part of the alphabetical index proper.",
                "source_window": [str(ordo_files[0]), str(ordo_files[-1])],
            },
        },
    ]


def build_letter_nodes(section_key: str, entries: list[dict[str, Any]], parent_key: str, start_order: int) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    order = start_order - 1
    for entry in entries:
        letter = entry.get("heading_letter")
        if not letter or letter in seen:
            continue
        seen.add(letter)
        order += 1
        nodes.append(
            {
                "node_key": f"{section_key}:node:{order:04d}",
                "section_key": section_key,
                "parent_node_key": parent_key,
                "node_order": order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 2,
                "confidence": 0.96,
                "raw_json": {"kind": "alphabetic divider"},
            }
        )
    return nodes


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


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
        raise SystemExit(f"helper failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        page_hints = [str(x) for x in entry["raw_json"].get("page_hints") or []]
        page_hint_ints = [x for x in entry["raw_json"].get("page_hints") or [] if isinstance(x, int)]
        if not page_hint_ints:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:80],
                "query_names": [x for x in [entry["lemma_raw"], (entry["entry_raw"].split(",", 1)[0] if entry["entry_raw"] else None)] if x],
                "page_hints": page_hints[:3],
                "page_hint_ints": page_hint_ints[:3],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": None,
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG034 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    index_files = discover_files(args.source_root, INDEX_FILES)
    ordo_files = discover_files(args.source_root, ORDO_FILES)
    if not index_files:
        raise SystemExit("no index OCR files found")
    if not ordo_files:
        raise SystemExit("no ordo OCR files found")

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build and validate PG034 alphabetical payload",
        "completed": [
            "OCR tail files identified",
            "section boundaries located",
        ],
        "pending": [
            "run helper target locator on the selected page-hint entries",
            "assemble final payload and write JSON",
        ],
        "blocked": [],
        "notes": [
            "Use OCR header map plus editorial page estimator for cited page targets.",
            "Keep INDEX ANALYTICI and ORDO RERUM as separate sections.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    page_map = build_page_map(index_files + ordo_files, args.source_root)

    index_lines = extract_section_lines(index_files)
    index_entries, index_refs, index_nodes, helper_entries = build_entries(INDEX_SECTION_KEY, "analytic_subject", index_lines, page_map)
    ordo_entries, ordo_refs = build_ordo_section(ordo_files, page_map)

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(args.source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [],
    }
    for entry in index_entries + ordo_entries:
        page_hints = [x for x in entry["raw_json"].get("page_hints") or [] if isinstance(x, int)]
        if not page_hints:
            continue
        helper_request["entries"].append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"][:80],
                "query_names": [name for name in [entry["lemma_raw"], entry["entry_raw"].split(",", 1)[0]] if name],
                "page_hints": [str(page) for page in page_hints[:3]],
                "page_hint_ints": page_hints[:3],
                "context_raw": entry["entry_raw"],
            }
        )
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    sections = build_sections(index_files, ordo_files)
    nodes = [
        {
            "node_key": f"{INDEX_SECTION_KEY}:node:0001",
            "section_key": INDEX_SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "ordinal_group",
            "label_raw": "I.",
            "label_norm": "i",
            "label_sort": "i",
            "node_level": 1,
            "confidence": 0.97,
            "raw_json": {"kind": "section_intro"},
        },
        {
            "node_key": f"{INDEX_SECTION_KEY}:node:0002",
            "section_key": INDEX_SECTION_KEY,
            "parent_node_key": f"{INDEX_SECTION_KEY}:node:0001",
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "INDEX RERUM MEMORABILIUM",
            "label_norm": "index rerum memorabilium",
            "label_sort": "index rerum memorabilium",
            "node_level": 2,
            "confidence": 0.97,
            "raw_json": {
                "kind": "macroheading",
                "heading_raw": "INDEX RERUM MEMORABILIUM",
            },
        },
    ]
    nodes.extend(build_letter_nodes(INDEX_SECTION_KEY, index_entries, f"{INDEX_SECTION_KEY}:node:0002", start_order=3))

    # Attach a compact helper summary only where the helper actually helps.
    helper_by_id = {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}
    for entry in index_entries + ordo_entries:
        helper_item = helper_by_id.get(entry["entry_key"])
        if not helper_item:
            continue
        if not entry["raw_json"].get("page_hints"):
            continue
        entry["raw_json"]["helper_status"] = helper_item.get("status")
        entry["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
        entry["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
        best = helper_item.get("best_candidate") or {}
        if best:
            entry["raw_json"]["helper_best_candidate"] = {
                "file": best.get("file"),
                "probability": best.get("probability"),
                "candidate_role": best.get("candidate_role"),
                "evidence_kinds": best.get("evidence_kinds"),
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
                "PG034 ends with an analytical subject index followed by ORDO RERUM.",
                "OCR file suffixes, printed pages, and cited pages were kept separate.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": index_entries + ordo_entries,
        "refs": index_refs + ordo_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Index entries and editorial closure were recovered from the OCR tail; refs were mapped from OCR headers plus the editorial page estimator.",
            "evidence_files": [str(index_files[0]), str(index_files[-1]), str(ordo_files[0]), str(ordo_files[-1])],
        },
        "notes": [
            {
                "kind": "method",
                "text": "Line-oriented OCR segmentation with conservative merging of wrapped continuations.",
            },
            {
                "kind": "scope",
                "text": "No biblical scripture index was present in the extracted tail, so scripture_refs remains empty.",
            },
        ],
    }

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output_file, payload)

    write_json(args.intermediate_dir / "volume.json", payload["volume"])
    write_json(args.intermediate_dir / "sections.json", payload["sections"])
    write_json(args.intermediate_dir / "nodes.json", payload["nodes"])
    write_json(args.intermediate_dir / "entries.json", payload["entries"])
    write_json(args.intermediate_dir / "refs.json", payload["refs"])
    write_json(args.intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(args.intermediate_dir / "coverage.json", payload["coverage"])
    write_json(args.intermediate_dir / "notes.json", payload["notes"])
    write_json(args.intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})


if __name__ == "__main__":
    main()
