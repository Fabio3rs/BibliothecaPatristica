#!/usr/bin/env python3
"""Usage: build the PG155 alphabetical-index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG155_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG155/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG155_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG155_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG155 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG155_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG155"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 155"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"
SCRIPT_TARGET_LOCATOR_CHUNKED = ROOT / "scripts" / "pipeline_index_extraction" / "run_index_target_locator_in_chunks.py"

SECTION_ANALYTIC = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS.",
    "section_kind_reason": (
        "Greek analytical alphabetical index headed INDEX ANALYTICUS, with letter-group dividers A, B, Γ, Δ, E."
    ),
    "file_start_seq": 509,
    "file_end_seq": 522,
    "page_start": 977,
    "page_end": 1002,
}

SECTION_ORDO = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "section_kind_reason": (
        "Closing contents table headed ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR, kept separate from the analytical index."
    ),
    "file_start_seq": 523,
    "file_end_seq": 530,
    "page_start": 1011,
    "page_end": 1021,
}

SECTION_DEFS = [SECTION_ANALYTIC, SECTION_ORDO]

BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", flags=re.DOTALL | re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
LETTER_RE = re.compile(r"^[A-ZΑ-ΩÆŒ]$")
PAGE_CLUSTER_RE = re.compile(r"(?P<cluster>(?:,\s*(?:\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)(?:\s*καὶ\s+\d{1,4})?)+)\s*$", re.IGNORECASE)
PAGE_RANGE_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})(?!\d)")
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
ENTRY_SPLIT_RE = re.compile(r"(?<=\d\.)\s+(?=\S)")
ORDO_SPLIT_RE = re.compile(
    r"(?<=\d)\s+(?=(?:\d+\.\s|Questio\s|QUÆSTIO\s|CAP\.\s|DE\s|Proœmium\.|EJUSDEM\s|SUCCINCTA\s|RESPENSIONES\s|SYMEONIS\s|ADVERSUS\s|Notitia\s|EXPOSITIO\s))"
)
NOISE_LINES = {"Digitized by Google"}
SECTION_TITLE_MARKERS = {"INDEX ANALYTICUS", "ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR", "QUAE IN HOC TOMO CONTINENTUR"}
CONTINUATION_STARTERS = {
    "Καὶ",
    "καὶ",
    "Διατὶ",
    "διατὶ",
    "Διά",
    "δια",
    "Ὅτι",
    "ὅτι",
    "Τί",
    "τί",
    "Πῶς",
    "πῶς",
    "Εἰ",
    "εἰ",
    "Ἐὰν",
    "ἐὰν",
    "Ἀλλ",
    "ἀλλ",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_space(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize(text: str | None) -> str:
    value = normalize_space(text)
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return re.sub(r"\s+", " ", value).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def lemma_norm(text: str | None) -> str | None:
    value = sort_norm(text)
    return value or None


def discover_files(source_root: Path) -> list[Path]:
    out: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            seq = int(match.group(1))
            out.append((seq, path))
    return [path for _, path in sorted(out)]


def discover_tail_files(source_root: Path) -> list[Path]:
    out: list[tuple[int, Path]] = []
    for path in source_root.glob("*.txt"):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match:
            seq = int(match.group(1))
            if 509 <= seq <= 530:
                out.append((seq, path))
    return [path for _, path in sorted(out)]


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize_space(parsed.get("header_text") or "")
        for match in PAGE_RE.finditer(header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def iter_ordered_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks_out: list[tuple[str, str]] = []
    try:
        root = ET.fromstring(raw.strip())
        blocks = list(root.findall("bloco"))
    except ET.ParseError:
        blocks = []
        for match in BLOCK_RE.finditer(raw):
            attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
            block = ET.Element("bloco", attrs)
            block.text = match.group("content") or ""
            blocks.append(block)

    for bloco in blocks:
        block_type = (bloco.attrib.get("tipo") or "").strip().lower()
        content = "".join(bloco.itertext())
        if not content:
            continue
        parts = [normalize_space(raw_line) for raw_line in content.splitlines()]
        parts = [part for part in parts if part and part not in NOISE_LINES]
        if not parts:
            continue
        if block_type in {"texto_principal", "nota_marginal", "cabecalho"}:
            text = " ".join(parts)
        else:
            text = " ".join(parts)
        text = normalize_space(text)
        if text and text not in NOISE_LINES:
            blocks_out.append((block_type, text))
    return blocks_out


def split_fragments(text: str, section_kind: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    split_re = ORDO_SPLIT_RE if section_kind == "ordo_rerum" else ENTRY_SPLIT_RE
    parts = [part.strip() for part in split_re.split(text) if part.strip()]
    return parts or [text]


def extract_tail_refs(text: str) -> tuple[str, list[dict[str, Any]]]:
    clean = normalize_space(text)
    match = PAGE_CLUSTER_RE.search(clean)
    refs: list[dict[str, Any]] = []
    body = clean
    if match:
        body = clean[: match.start("cluster")].strip(" ,;:.")
        cluster = match.group("cluster")
        for token in re.findall(r"\d{1,4}(?:\s*[-–—]\s*\d{1,4})?", cluster):
            token = normalize_space(token).rstrip(" ,;:.")
            if not token:
                continue
            range_match = re.fullmatch(r"(?P<start>\d{1,4})(?:\s*[-–—]\s*(?P<end>\d{1,4}))?", token)
            if not range_match:
                continue
            start = int(range_match.group("start"))
            end = range_match.group("end")
            refs.append(
                {
                    "ref_kind": "editorial_range" if end else "editorial_page",
                    "ref_raw": token,
                    "page_ref_raw": token,
                    "page_ref_int": start,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": range_match.group("start") if end else None,
                    "range_end_raw": end,
                }
            )
        return body, refs

    for match in PAGE_RANGE_RE.finditer(clean):
        start = int(match.group(1))
        end = int(match.group(2))
        token = match.group(0).strip(" ,;:.")
        refs.append(
            {
                "ref_kind": "editorial_range",
                "ref_raw": token,
                "page_ref_raw": token,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(start),
                "range_end_raw": str(end),
            }
        )
        body = body.replace(match.group(0), " ", 1)

    for match in PAGE_RE.finditer(body):
        page = int(match.group(1))
        if any(ref["page_ref_int"] == page for ref in refs):
            continue
        refs.append(
            {
                "ref_kind": "editorial_page",
                "ref_raw": match.group(1),
                "page_ref_raw": match.group(1),
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )

    return body.strip(" ,;:.") or clean, refs


def infer_entry_kind(entry_raw: str, refs: list[dict[str, Any]], section_kind: str) -> str:
    stripped = normalize_space(entry_raw)
    if not stripped:
        return "editorial_note"
    if LETTER_RE.fullmatch(stripped):
        return "heading_group"
    if not refs and any(marker in stripped for marker in ("vid.", "vide", "voir", "cf.", "id.")):
        return "cross_reference"
    if section_kind == "ordo_rerum":
        if stripped.startswith("CAP.") or stripped.startswith("DE ") or stripped.isupper():
            return "heading_group"
    if stripped[:1].islower() or stripped.startswith(tuple(CONTINUATION_STARTERS)):
        return "sublemma"
    return "lemma"


def infer_lemma(entry_raw: str, refs: list[dict[str, Any]]) -> str | None:
    text = normalize_space(entry_raw)
    if not text:
        return None
    if not refs:
        return text.strip(" ,;:.") or None
    ref_raw = refs[0]["ref_raw"]
    idx = text.rfind(ref_raw)
    if idx > 0:
        return text[:idx].strip(" ,;:.") or None
    return text.strip(" ,;:.") or None


def make_query_names(entry_raw: str, lemma_raw: str | None) -> list[str]:
    values = [lemma_raw, entry_raw]
    if lemma_raw:
        values.append(lemma_raw.replace(".", ""))
    if entry_raw:
        values.append(entry_raw.replace(".", ""))
    out: list[str] = []
    for value in values:
        value = normalize_space(value)
        if value and value not in out:
            out.append(value)
    return out[:5]


def merge_orphan_entries(
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def file_seq(path_str: str | None) -> int | None:
        if not path_str:
            return None
        match = re.search(r"-(\d+)\.txt$", path_str)
        return int(match.group(1)) if match else None

    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)

    merged_entries: list[dict[str, Any]] = []
    skip_keys: set[str] = set()

    for idx, entry in enumerate(entries):
        if entry["entry_key"] in skip_keys:
            continue
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        next_entry = entries[idx + 1] if idx + 1 < len(entries) else None
        next_refs = refs_by_entry.get(next_entry["entry_key"], []) if next_entry else []

        entry_source = entry["raw_json"].get("source_file")
        next_source = next_entry["raw_json"].get("source_file") if next_entry else None
        entry_seq = file_seq(entry_source)
        next_seq = file_seq(next_source)
        should_merge = (
            not entry_refs
            and next_entry is not None
            and next_entry["entry_kind"] == "sublemma"
            and next_refs
            and entry["section_key"] == next_entry["section_key"]
            and (
                entry_source == next_source
                or (entry_seq is not None and next_seq is not None and next_seq - entry_seq == 1)
            )
        )
        if should_merge:
            combined_raw = normalize_space(f"{entry['entry_raw']} {next_entry['entry_raw']}")
            entry["entry_raw"] = combined_raw
            entry["lemma_raw"] = infer_lemma(combined_raw, next_refs)
            entry["lemma_display"] = entry["lemma_raw"]
            entry["lemma_norm"] = lemma_norm(entry["lemma_raw"])
            entry["lemma_sort"] = sort_norm(entry["lemma_raw"])
            entry["inferred_printed_page"] = next_refs[0]["page_ref_int"]
            entry["confidence"] = max(entry.get("confidence", 0.58), next_entry.get("confidence", 0.58))
            entry["raw_json"]["query_names"] = make_query_names(combined_raw, entry["lemma_raw"])
            entry["raw_json"]["page_hints"] = [ref["page_ref_int"] for ref in next_refs]
            entry["raw_json"]["ref_count"] = len(next_refs)
            for ref_order, ref in enumerate(next_refs, start=1):
                ref["entry_key"] = entry["entry_key"]
                ref["ref_order"] = ref_order
            skip_keys.add(next_entry["entry_key"])
            refs_by_entry[entry["entry_key"]] = next_refs
        merged_entries.append(entry)

    merged_entry_keys = {entry["entry_key"] for entry in merged_entries}
    merged_refs = [ref for ref in refs if ref["entry_key"] in merged_entry_keys and ref["entry_key"] not in skip_keys]
    return merged_entries, merged_refs


def extract_sections_and_entries(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    files = discover_tail_files(source_root)
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    entry_index = 0
    node_index = 0

    for section_def in SECTION_DEFS:
        section_files = [p for p in files if section_def["file_start_seq"] <= int(p.stem.rsplit("-", 1)[-1]) <= section_def["file_end_seq"]]
        if not section_files:
            continue
        section_start_file = str(section_files[0])
        current_letter: str | None = None
        started = False
        first_entry_file = section_start_file

        for path in section_files:
            for block_type, block_text in iter_ordered_blocks(path):
                upper = block_text.upper()
                if any(marker in upper for marker in SECTION_TITLE_MARKERS):
                    if section_def["section_kind"] == "analytic_subject":
                        if "INDEX ANALYTICUS" in upper:
                            started = True
                        continue
                    if section_def["section_kind"] == "ordo_rerum":
                        if "ORDO RERUM" in upper:
                            started = True
                        continue
                if not started:
                    continue
                if block_text in NOISE_LINES or block_text.isdigit():
                    continue
                if upper.startswith("FINIS TOMI") or upper.startswith("PARISIIS"):
                    continue
                if LETTER_RE.fullmatch(block_text):
                    node_index += 1
                    current_letter = block_text
                    nodes.append(
                        {
                            "node_key": f"{VOLUME_ID}:node:{section_def['section_order']:02d}:{node_index:03d}",
                            "section_key": section_def["section_key"],
                            "parent_node_key": None,
                            "node_order": node_index,
                            "node_kind": "letter_group" if section_def["section_kind"] == "analytic_subject" else "heading_group",
                            "label_raw": block_text,
                            "label_norm": sort_norm(block_text),
                            "label_sort": sort_norm(block_text),
                            "node_level": 1,
                            "confidence": 0.99,
                            "raw_json": {"source_file": str(path), "block_type": block_type, "role": "alphabetic_letter"},
                        }
                    )
                    continue

                for fragment in split_fragments(block_text, section_def["section_kind"]):
                    frag = normalize_space(fragment)
                    if not frag:
                        continue
                    if any(marker in frag.upper() for marker in SECTION_TITLE_MARKERS):
                        continue

                    body, tail_refs = extract_tail_refs(frag)
                    if section_def["section_kind"] == "analytic_subject" and current_letter is None and not tail_refs:
                        continue
                    if not tail_refs and section_def["section_kind"] != "ordo_rerum":
                        continue

                    if not first_entry_file:
                        first_entry_file = str(path)

                    entry_kind = infer_entry_kind(body, tail_refs, section_def["section_kind"])
                    lemma_raw = infer_lemma(body, tail_refs)
                    entry_index += 1
                    entry_key = f"{VOLUME_ID}:entry:{section_def['section_order']:02d}:{entry_index:05d}"
                    inferred_printed_page = tail_refs[0]["page_ref_int"] if tail_refs else None
                    entries.append(
                        {
                            "entry_key": entry_key,
                            "section_key": section_def["section_key"],
                            "parent_node_key": current_letter if section_def["section_kind"] == "analytic_subject" else None,
                            "entry_order": entry_index,
                            "entry_kind": entry_kind,
                            "lemma_raw": lemma_raw,
                            "lemma_display": lemma_raw,
                            "lemma_norm": lemma_norm(lemma_raw),
                            "lemma_sort": sort_norm(lemma_raw),
                            "entry_raw": body,
                            "context_raw": None,
                            "heading_letter": current_letter if section_def["section_kind"] == "analytic_subject" else None,
                            "inferred_printed_page": inferred_printed_page,
                            "section_start_file": section_start_file,
                            "editorial_anchor_file": str(path),
                            "target_file_best": None,
                            "confidence": 0.74 if tail_refs else 0.58,
                            "raw_json": {
                                "source_file": str(path),
                                "section_kind": section_def["section_kind"],
                                "section_kind_reason": section_def["section_kind_reason"],
                                "query_names": make_query_names(body, lemma_raw),
                                "page_hints": [ref["page_ref_int"] for ref in tail_refs],
                                "ref_count": len(tail_refs),
                            },
                        }
                    )
                    for ref_order, ref in enumerate(tail_refs, start=1):
                        refs.append(
                            {
                                "entry_key": entry_key,
                                "ref_order": ref_order,
                                "ref_kind": ref["ref_kind"],
                                "ref_raw": ref["ref_raw"],
                                "page_ref_raw": ref["page_ref_raw"],
                                "page_ref_int": ref["page_ref_int"],
                                "page_ref_col": ref["page_ref_col"],
                                "line_ref_raw": ref["line_ref_raw"],
                                "range_start_raw": ref["range_start_raw"],
                                "range_end_raw": ref["range_end_raw"],
                                "target_file": None,
                                "target_file_probability": None,
                                "section_start_file": section_start_file,
                                "editorial_anchor_file": str(path),
                                "confidence": 0.62,
                                "raw_json": {
                                    "source_file": str(path),
                                    "section_kind": section_def["section_kind"],
                                    "page_hint": ref["page_ref_int"],
                                },
                            }
                        )

                    if tail_refs:
                        first_entry_file = first_entry_file or str(path)

        sections.append(
            {
                "section_key": section_def["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section_def["section_order"],
                "section_kind": section_def["section_kind"],
                "heading_raw": section_def["heading_raw"],
                "heading_norm": sort_norm(section_def["heading_raw"]),
                "heading_letter": None,
                "page_start": section_def["page_start"],
                "page_end": section_def["page_end"],
                "file_start": section_start_file,
                "file_end": str(section_files[-1]),
                "confidence": 0.96,
                "raw_json": {
                    "section_kind_reason": section_def["section_kind_reason"],
                    "file_start_seq": section_def["file_start_seq"],
                    "file_end_seq": section_def["file_end_seq"],
                    "source_files": [str(p) for p in section_files],
                },
            }
        )

    return sections, nodes, entries, refs, files


def build_helper_request(
    entries: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    source_root: Path,
    page_map: dict[int, str],
) -> dict[str, Any]:
    refs_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    entry_by_key: dict[str, dict[str, Any]] = {entry["entry_key"]: entry for entry in entries}
    for ref in refs:
        refs_by_page[ref["page_ref_int"]].append(ref)

    helper_entries: list[dict[str, Any]] = []
    for page_hint in sorted(refs_by_page):
        if page_hint in page_map:
            continue
        sample_ref = refs_by_page[page_hint][0]
        entry = entry_by_key[sample_ref["entry_key"]]
        helper_entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_page_{page_hint}",
                "lemma_raw": entry.get("lemma_raw") or entry["entry_raw"][:80],
                "query_names": make_query_names(entry["entry_raw"][:240], entry.get("lemma_raw")),
                "page_hints": [str(page_hint)],
                "page_hint_ints": [page_hint],
                "context_raw": entry["entry_raw"][:240],
            }
        )

    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    request = read_json(helper_request_json, {})
    entries = request.get("entries") or []
    if len(entries) > 120:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_TARGET_LOCATOR_CHUNKED),
                "--input",
                str(helper_request_json),
                "--output",
                str(helper_output_json),
                "--chunk-size",
                "80",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"chunked index_target_locator failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return read_json(helper_output_json, {})

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
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_best_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id:
            continue
        mapping[str(entry_id)] = item
    return mapping


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    sections, nodes, entries, refs, files = extract_sections_and_entries(source_root)
    entries, refs = merge_orphan_entries(entries, refs)
    page_map = build_page_map(discover_files(source_root))
    node_key_by_section_letter: dict[tuple[str, str], str] = {}
    for node in nodes:
        label_raw = node.get("label_raw")
        section_key = node.get("section_key")
        if not label_raw or not section_key:
            continue
        node_key_by_section_letter.setdefault((section_key, label_raw), node["node_key"])

    for entry in entries:
        parent_label = entry.get("parent_node_key")
        if not parent_label:
            continue
        entry["parent_node_key"] = node_key_by_section_letter.get(
            (entry["section_key"], parent_label),
            parent_label,
        )

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Rebuild PG155 with block-level segmentation and full-page helper coverage for missing locators.",
        "completed": [
            "Confirmed INDEX ANALYTICUS begins at OCR file 509",
            "Confirmed ORDO RERUM begins at OCR file 523",
            "Verified the tail OCR directly against the previous payload checkpoint",
            "Identified systemic fragmentation from line-level segmentation in the previous builder",
        ],
        "pending": [
            "Run the rebuilt builder and inspect entry/ref counts",
            "Validate the rebuilt payload with import_alphabetical_index_json.py",
        ],
        "blocked": [],
        "notes": [
            "Preserve OCR literals, especially the noisy printed-page numbers in the section headers.",
            "Use helper evidence for all cited pages, not only the first 24 unique hints.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)

    helper_request = build_helper_request(entries, refs, source_root, page_map)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json) if helper_request["entries"] else {}
    helper_lookup = helper_best_map(helper_output)

    # Map page hints to OCR files using the helper output.
    page_target_map: dict[int, tuple[str | None, float | None, dict[str, Any] | None]] = {}
    for helper_entry in helper_request["entries"]:
        page_hint = helper_entry["page_hint_ints"][0]
        helper_item = helper_lookup.get(helper_entry["entry_id"])
        best = (helper_item or {}).get("best_candidate") or {}
        page_target_map[page_hint] = (
            best.get("file"),
            best.get("probability"),
            helper_item,
        )

    # Fill refs and entry target anchors conservatively.
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        refs_by_entry[ref["entry_key"]].append(ref)
        file_path, probability, helper_item = page_target_map.get(ref["page_ref_int"], (None, None, None))
        if not file_path:
            file_path = page_map.get(ref["page_ref_int"])
        ref["target_file"] = file_path
        ref["target_file_probability"] = probability if probability is not None else (1.0 if file_path else None)
        ref["confidence"] = 0.72 if file_path else 0.5
        ref["raw_json"]["helper_entry_id"] = f"{VOLUME_ID.lower()}_page_{ref['page_ref_int']}"
        if helper_item:
            ref["raw_json"]["helper_status"] = helper_item.get("status")
            ref["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
            ref["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
            ref["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")

    for entry in entries:
        entry_refs = refs_by_entry.get(entry["entry_key"], [])
        if entry_refs:
            first_ref = entry_refs[0]
            entry["target_file_best"] = first_ref.get("target_file") or page_map.get(entry["inferred_printed_page"] or -1)
            entry["confidence"] = 0.82 if first_ref.get("target_file") else 0.66
            entry["raw_json"]["helper_page_targets"] = [
                {
                    "page_ref_int": ref["page_ref_int"],
                    "target_file": ref.get("target_file"),
                    "target_file_probability": ref.get("target_file_probability"),
                }
                for ref in entry_refs[:5]
            ]
        else:
            entry["target_file_best"] = None

        helper_item = helper_lookup.get(f"{VOLUME_ID.lower()}_page_{entry['inferred_printed_page']}") if entry["inferred_printed_page"] else None
        if helper_item:
            entry["raw_json"]["helper_status"] = helper_item.get("status")
            entry["raw_json"]["helper_candidate_role"] = helper_item.get("candidate_role")
            entry["raw_json"]["helper_reason_summary"] = helper_item.get("reason_summary")
            entry["raw_json"]["helper_best_candidate"] = helper_item.get("best_candidate")

    # Helper-derived metadata for sections.
    for section in sections:
        section["raw_json"]["helper_entry_count"] = len(helper_request["entries"])
        section["raw_json"]["helper_status"] = helper_output.get("status")
        section["raw_json"]["helper_options_used"] = helper_output.get("options_used")

    evidence_files = []
    for seq in (509, 512, 523, 530):
        match = next((p for p in files if int(p.stem.rsplit("-", 1)[-1]) == seq), None)
        if match:
            evidence_files.append(str(match))
    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the PG155 analytical index and closing Ordo Rerum table from the OCR tail, "
            "with page targets resolved through the helper on unique cited page hints."
        ),
        "evidence_files": evidence_files,
    }

    notes = [
        "The first section is an analytical Greek index with letter-group nodes A, B, Γ, Δ, and E.",
        "The second section is the closing Ordo Rerum contents table and is serialized separately from the index proper.",
        "OCR page numbers, cited references, and physical OCR file suffixes are preserved as distinct numbering systems.",
    ]

    manifest = {"volume_id": VOLUME_ID, "updated_at": now_iso(), "generated_at": now_iso()}
    fragments = {
        "manifest.json": manifest,
        "volume.json": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
        },
        "sections.json": sections,
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": [],
        "coverage.json": coverage,
        "notes.json": notes,
        "todo.json": todo,
    }
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)

    return {
        "schema_version": "1.0",
        "generated_at": manifest["generated_at"],
        "volume": fragments["volume.json"],
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG155 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
