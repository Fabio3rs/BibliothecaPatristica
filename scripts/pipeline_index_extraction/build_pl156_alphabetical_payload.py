#!/usr/bin/env python3
"""Usage: build the PL156 alphabetical payload and helper request.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl156_alphabetical_payload.py \
    --write-helper-request \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL156_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL156

  python scripts/index_target_locator.py \
    --input /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL156_helper_request.json \
    --output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL156_helper_output.json \
    --pretty

  python scripts/pipeline_index_extraction/build_pl156_alphabetical_payload.py \
    --build-payload \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL156_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL156 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL156_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL156"
COLLECTION = "PL"
VOLUME_LABEL = "PL156"
SOURCE_ROOT = ROOT / "teste/PL156/text"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
DEFAULT_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL156_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PL156_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL156_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL156"

ALPHA_SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
ORDO_SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

INDEX_FILES = [SOURCE_ROOT / f"c047a680-f5ed-415e-a6e9-0a67ecb5ca41-{n}.txt" for n in range(623, 638)]
SEQ_TO_PATH = {
    int(match.group(1)): str(path)
    for path in SOURCE_ROOT.glob("*.txt")
    if (match := re.search(r"-(\d+)\.txt$", path.name))
}

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
BLOCK_RE = re.compile(r'<bloco tipo="texto_principal" script="latino" bbox="[^"]+">\n(.*?)\n  </bloco>', re.S)
HEADER_RE = re.compile(r'<bloco tipo="cabecalho" script="latino" bbox="[^"]+">\n(.*?)\n  </bloco>', re.S)
NUMBER_RE = re.compile(r"\b\d{1,4}\b")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def strip_accents(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return value


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = strip_accents(text)
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def clean_line(line: str) -> str:
    value = line.strip()
    value = value.replace("ﬁ", "fi")
    value = re.sub(r"\s+", " ", value)
    return value


def extract_blocks(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8")
    blocks = []
    for match in BLOCK_RE.findall(text):
        lines = [clean_line(ln) for ln in match.splitlines() if clean_line(ln)]
        if lines:
            blocks.append(lines)
    return blocks


def extract_headers(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    headers: list[str] = []
    for match in HEADER_RE.findall(text):
        header = re.sub(r"\s+", " ", " ".join(ln.strip() for ln in match.splitlines())).strip()
        if header:
            headers.append(header)
    return headers


def parse_alpha_segments() -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    letter_nodes: list[str] = []
    started = False
    current_text = ""
    current_file: Path | None = None
    current_letter = None
    current_line_start = None
    current_line_end = None
    current_page = None

    def flush() -> None:
        nonlocal current_text, current_file, current_line_start, current_line_end, current_page
        text = current_text.strip()
        if text and started:
            segments.append(
                {
                    "text": text,
                    "file": str(current_file) if current_file else None,
                    "letter": current_letter,
                    "line_start": current_line_start,
                    "line_end": current_line_end,
                    "page": current_page,
                }
            )
        current_text = ""
        current_file = None
        current_line_start = None
        current_line_end = None
        current_page = None

    for path in INDEX_FILES:
        blocks = extract_blocks(path)
        for block_index, lines in enumerate(blocks, start=1):
            for line_index, line in enumerate(lines, start=1):
                if not started:
                    if path.name.endswith("-623.txt") and line == "A":
                        started = True
                        current_letter = "A"
                        letter_nodes.append("A")
                    continue

                if LETTER_RE.fullmatch(line):
                    flush()
                    current_letter = line
                    letter_nodes.append(line)
                    continue

                if line in {"INDEX RERUM ET VERBORUM.", "ORDO RERUM", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."}:
                    continue

                if not current_text:
                    current_text = line
                    current_file = path
                    current_line_start = line_index
                    current_line_end = line_index
                    current_page = path.name
                    continue

                if current_text.endswith("-"):
                    current_text = current_text[:-1] + line.lstrip()
                    current_line_end = line_index
                    continue

                if re.search(r"(?:ibid\.|et seq\.|et seqq\.|seqq\.|seq\.|sqq\.|V\.|Vide\.|cf\.|id\.)\s*$", current_text, re.I):
                    current_text += " " + line
                    current_line_end = line_index
                    continue

                if not re.search(r"[.!?]$", current_text):
                    current_text += " " + line
                    current_line_end = line_index
                    continue

                if line and line[0].islower():
                    current_text += " " + line
                    current_line_end = line_index
                    continue

                flush()
                current_text = line
                current_file = path
                current_line_start = line_index
                current_line_end = line_index
                current_page = path.name

    flush()
    return {"segments": segments, "letters": letter_nodes}


def parse_ordo_segments() -> list[dict[str, Any]]:
    path = SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-638.txt"
    blocks = extract_blocks(path)
    if not blocks:
        return []
    lines = [line for block in blocks for line in block]
    out: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        if line in {"ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR.", "VENERABILIS GUIBERTUS, ABBAS S. MARIÆ DE NOVIGENTO."}:
            out.append(
                {
                    "text": line,
                    "file": str(path),
                    "line": idx,
                }
            )
            continue
        if line.startswith("INDEX RERUM ET VERBORUM."):
            continue
        out.append({"text": line, "file": str(path), "line": idx})
    return out


def classify_entry(text: str) -> str:
    stripped = text.strip()
    if re.match(r"^(Vide\b|V\.\b|V\. |V\. etiam\b|Saeculares\. V\.|Superior\. V\.)", stripped):
        return "cross_reference"
    if " Vide " in f" {stripped} " and not NUMBER_RE.search(stripped):
        return "cross_reference"
    return "lemma"


def infer_lemma(text: str) -> str | None:
    stripped = text.strip(" ,;")
    if not stripped:
        return None
    if stripped in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
        return None
    first_num = NUMBER_RE.search(stripped)
    if first_num:
        head = stripped[: first_num.start()].strip(" ,;:.—-")
        if head:
            return head
    if "." in stripped:
        head = stripped.split(".", 1)[0].strip(" ,;:.—-")
        if head:
            return head
    return stripped


def extract_page_numbers(text: str) -> list[int]:
    cleaned = re.sub(r"(?<=\d)\.\d+(?=\s+[A-ZÆŒ])", ". ", text)
    cleaned = re.sub(r"\b(?:ibid|ibid\.|idem|id\.)\b", "ibid.", cleaned, flags=re.I)
    pages: list[int] = []
    seen = set()
    for match in NUMBER_RE.finditer(cleaned):
        value = int(match.group())
        if 1 <= value <= 1268 and value not in seen:
            seen.add(value)
            pages.append(value)
    return pages


def build_helper_request_from_segments(alpha_data: dict[str, Any]) -> dict[str, Any]:
    representative: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for idx, segment in enumerate(alpha_data["segments"], start=1):
        page_numbers = extract_page_numbers(segment["text"])
        if not page_numbers:
            continue
        lemma = infer_lemma(segment["text"]) or segment["text"][:80]
        for page in page_numbers:
            representative.setdefault(
                page,
                {
                    "entry_id": f"pl156_page_{page:04d}_{len(representative)+1:04d}",
                    "lemma_raw": lemma,
                    "query_names": [lemma, segment["text"].split(",")[0][:120]],
                    "page_hints": [str(page)],
                    "page_hint_ints": [page],
                    "context_raw": segment["text"],
                },
            )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": list(representative.values()),
    }


def helper_index(helper_output: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        page_match = re.search(r"page_(\d{4})_", str(entry_id or ""))
        if not page_match:
            continue
        page = int(page_match.group(1))
        result[page] = item
    return result


def fallback_target_file(page: int) -> str | None:
    seq = 623 + math.floor((page - 1237) / 2)
    return SEQ_TO_PATH.get(seq)


def helper_summary(item: dict[str, Any]) -> dict[str, Any]:
    best = item.get("best_candidate") or {}
    candidates = item.get("candidates") or []
    top_candidates = []
    for cand in candidates[:3]:
        top_candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "inferred_printed_page": cand.get("inferred_printed_page"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", [])[:4]],
            }
        )
    return {
        "helper_status": item.get("status"),
        "helper_best_file": best.get("file"),
        "helper_best_probability": best.get("probability"),
        "helper_candidate_role": best.get("candidate_role"),
        "helper_reason_summary": best.get("reason_summary"),
        "helper_top_candidates": top_candidates,
    }


def section_spans() -> list[dict[str, Any]]:
    alpha_start = SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-623.txt"
    alpha_end = SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-637.txt"
    ordo_file = SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-638.txt"
    return [
        {
            "section_key": ALPHA_SECTION_KEY,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM ET VERBORUM.",
            "heading_norm": "index rerum et verborum",
            "page_start": 1238,
            "page_end": 1266,
            "file_start": str(alpha_start),
            "file_end": str(alpha_end),
            "section_kind_reason": "Alphabetical analytical subject index of things and words at the end of the volume.",
            "evidence_files": [str(alpha_start), str(alpha_end)],
        },
        {
            "section_key": ORDO_SECTION_KEY,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "page_start": 1267,
            "page_end": 1268,
            "file_start": str(ordo_file),
            "file_end": str(ordo_file),
            "section_kind_reason": "Closing table of contents / order of contents for the same volume.",
            "evidence_files": [str(ordo_file)],
        },
    ]


def build_payload(helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    alpha_data = read_json(intermediate_dir / "parsed_alpha.json", {}) or {}
    ordo_data = read_json(intermediate_dir / "parsed_ordo.json", []) or []
    helper_output = read_json(helper_output_json, {})
    helper_map = helper_index(helper_output)

    sections = []
    for section in section_spans():
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
                "file_start": section["file_start"],
                "file_end": section["file_end"],
                "confidence": 0.96 if section["section_kind"] == "ordo_rerum" else 0.92,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "evidence_files": section["evidence_files"],
                },
            }
        )

    nodes: list[dict[str, Any]] = []
    letter_to_node: dict[str, str] = {}
    for idx, letter in enumerate(alpha_data.get("letters", []), start=1):
        node_key = f"{VOLUME_ID}:node:{idx:04d}"
        letter_to_node[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": ALPHA_SECTION_KEY,
                "parent_node_key": None,
                "node_order": idx,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter,
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {
                    "source": "OCR letter heading",
                },
            }
        )

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    def add_entry(
        *,
        section_key: str,
        entry_order: int,
        text: str,
        file: str,
        letter: str | None,
        page_numbers: list[int],
    ) -> None:
        entry_key = f"{VOLUME_ID}:entry:{len(entries)+1:05d}"
        entry_kind = classify_entry(text)
        lemma_raw = infer_lemma(text)
        helper_item = helper_map.get(page_numbers[0]) if page_numbers else None
        helper_best = (helper_item or {}).get("best_candidate") or {}
        target_file_best = helper_best.get("file") if helper_best else None
        target_resolution_method = "helper" if target_file_best else None
        if not target_file_best and page_numbers:
            target_file_best = fallback_target_file(page_numbers[0])
            if target_file_best:
                target_resolution_method = "ocr_page_formula"
        raw_json = {
            "source_line": text,
            "helper_used": bool(helper_item),
            "page_numbers": page_numbers,
            "section_key": section_key,
            "target_resolution_method": target_resolution_method,
        }
        if helper_item:
            raw_json.update(helper_summary(helper_item))
            raw_json["helper_page_hint"] = page_numbers[0] if page_numbers else None
            raw_json["helper_entry_id"] = helper_item.get("entry_id")
        confidence = 0.82 if page_numbers else 0.68
        if entry_kind == "cross_reference":
            confidence = min(confidence, 0.72)
        if helper_best:
            confidence = max(confidence, 0.74)
        entry_payload = {
            "entry_key": entry_key,
            "section_key": section_key,
            "parent_node_key": letter_to_node.get(letter) if letter else None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize(lemma_raw).lower() if normalize(lemma_raw) else None,
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": text,
            "context_raw": text,
            "heading_letter": letter,
            "inferred_printed_page": page_numbers[0] if page_numbers else None,
            "section_start_file": section_spans()[0]["file_start"] if section_key == ALPHA_SECTION_KEY else section_spans()[1]["file_start"],
            "editorial_anchor_file": file,
            "target_file_best": target_file_best,
            "confidence": confidence,
            "raw_json": raw_json,
        }
        entries.append(entry_payload)
        for ref_order, page in enumerate(page_numbers, start=1):
            helper_item_for_page = helper_map.get(page)
            helper_best_for_page = (helper_item_for_page or {}).get("best_candidate") or {}
            target_file = helper_best_for_page.get("file") if helper_best_for_page else None
            if not target_file:
                target_file = fallback_target_file(page)
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
                    "target_file_probability": helper_best_for_page.get("probability") if helper_best_for_page else None,
                    "section_start_file": section_spans()[0]["file_start"] if section_key == ALPHA_SECTION_KEY else section_spans()[1]["file_start"],
                    "editorial_anchor_file": file,
                    "confidence": confidence,
                    "raw_json": {
                        "source_line": text,
                        "helper_used": bool(helper_item_for_page),
                        "helper_page_hint": page,
                        "helper_entry_id": helper_item_for_page.get("entry_id") if helper_item_for_page else None,
                    },
                }
            )

    alpha_order = 0
    for segment in alpha_data.get("segments", []):
        text = segment["text"]
        if text == "A" and segment["file"] and segment["file"].endswith("-623.txt"):
            continue
        page_numbers = extract_page_numbers(text)
        alpha_order += 1
        add_entry(
            section_key=ALPHA_SECTION_KEY,
            entry_order=alpha_order,
            text=text,
            file=segment["file"] or str(SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-623.txt"),
            letter=segment.get("letter"),
            page_numbers=page_numbers,
        )

    ordo_order = 0
    ordo_file = str(SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-638.txt")
    for item in ordo_data:
        text = item["text"]
        if text in {"ORDO RERUM", "QUÆ IN HOC TOMO CONTINENTUR."}:
            continue
        page_numbers = extract_page_numbers(text)
        ordo_order += 1
        add_entry(
            section_key=ORDO_SECTION_KEY,
            entry_order=ordo_order,
            text=text,
            file=ordo_file,
            letter=None,
            page_numbers=page_numbers,
        )

    coverage = {
        "entries_status": "recovered_with_residual_ambiguity",
        "entries_status_reason": (
            "Recovered the PL156 closing subject index and the volume-end order of contents from OCR. "
            "Some long OCR lines preserve multiple semantically related clauses in one grouped fragment, "
            "so residual entry-boundary ambiguity is documented rather than normalized away."
        ),
        "evidence_files": [
            str(SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-623.txt"),
            str(SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-624.txt"),
            str(SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-637.txt"),
            str(SOURCE_ROOT / "c047a680-f5ed-415e-a6e9-0a67ecb5ca41-638.txt"),
        ],
    }

    notes = [
        "Section 1 is the final INDEX RERUM ET VERBORUM and is modeled as analytic_subject.",
        "Section 2 is the closing ORDO RERUM contents table.",
        "Letter-group nodes mirror the OCR alpha rubrics in the subject index.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def write_todo(intermediate_dir: Path, current_focus: str, completed: list[str], pending: list[str], blocked: list[str], notes: list[str]) -> None:
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": current_focus,
            "completed": completed,
            "pending": pending,
            "blocked": blocked,
            "notes": notes,
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL156 alphabetical payload and helper request.")
    ap.add_argument("--write-helper-request", action="store_true", help="Write the helper request JSON and exit.")
    ap.add_argument("--build-payload", action="store_true", help="Build the final payload JSON.")
    ap.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    ap.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)

    if args.write_helper_request:
        alpha_data = parse_alpha_segments()
        ordo_data = parse_ordo_segments()
        write_json(args.helper_request_json, build_helper_request_from_segments(alpha_data))
        write_json(args.intermediate_dir / "parsed_alpha.json", alpha_data)
        write_json(args.intermediate_dir / "parsed_ordo.json", ordo_data)
        write_json(args.intermediate_dir / "volume.json", {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
        })
        write_todo(
            args.intermediate_dir,
            "Generate helper output for PL156 and assemble the final alphabetical payload.",
            [
                "identified the subject index and closing order-of-contents section",
                "parsed OCR segments for the index and order-of-contents blocks",
            ],
            [
                "run index_target_locator on the helper request",
                "build the final payload from OCR fragments and helper evidence",
            ],
            [],
            [
                f"Helper request written to {args.helper_request_json}",
                "Keep the alphabetical subject index separate from the closing ORDO RERUM section.",
            ],
        )
        return

    if args.build_payload:
        payload = build_payload(args.helper_output_json, args.intermediate_dir)
        write_json(args.output_file, payload)
        write_todo(
            args.intermediate_dir,
            "Final payload assembled for PL156.",
            [
                "helper output consumed",
                "final payload written",
            ],
            [],
            [],
            [
                f"Final payload written to {args.output_file}",
                "Residual ambiguity remains only at the level of grouped OCR fragments.",
            ],
        )
        return

    raise SystemExit("Specify either --write-helper-request or --build-payload.")


if __name__ == "__main__":
    main()
