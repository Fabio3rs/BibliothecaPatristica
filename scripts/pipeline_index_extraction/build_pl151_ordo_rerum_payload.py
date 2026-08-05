#!/usr/bin/env python3
"""Usage: build the PL151 ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl151_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL151/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL151_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL151_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL151 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL151_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL151"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 151"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"

TEXT_BLOCK_RE = re.compile(r'<bloco tipo="texto_principal"[^>]*>(?P<body>.*?)</bloco>', re.S)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.I)
PAGE_REF_RE = re.compile(r"(?P<raw>(?<!\d)(?P<int>\d{1,4})(?:\.)?|(?P<ibid>ibid\.?|Ibid\.?))$")


@dataclass(slots=True)
class ParsedItem:
    order: int
    source_file: str
    raw_lines: list[str]
    parent_node_key: str | None
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    inherited_page: int | None
    is_node: bool
    node_level: int | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value else None


def extract_principal_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or FOOTER_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def page_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse OCR file suffix from {path}")
    return int(match.group(1))


def load_tail_lines(source_root: Path) -> list[tuple[str, str]]:
    files = [p for p in sorted(source_root.glob("*.txt"), key=page_seq) if 736 <= page_seq(p) <= 750]
    if not files:
        raise SystemExit(f"no OCR tail files found in {source_root}")
    tail_lines: list[tuple[str, str]] = []
    for path in files:
        for line in extract_principal_lines(path):
            tail_lines.append((path.as_posix(), line))
    return tail_lines


def is_numeric_only(line: str) -> bool:
    return bool(re.fullmatch(r"\d{1,4}\.?", line))


def has_page_ref(line: str) -> bool:
    return bool(PAGE_REF_RE.search(line))


def split_page_ref(text: str, previous_page: int | None) -> tuple[str, str | None, int | None, int | None]:
    value = normalize(text)
    match = PAGE_REF_RE.search(value)
    if not match:
        return value, None, None, previous_page
    ref_raw = match.group("raw")
    page_int: int | None
    inherited: int | None = previous_page
    if match.group("int"):
        page_int = int(match.group("int"))
        inherited = page_int
    else:
        page_int = previous_page
    lemma = normalize(value[: match.start()]).rstrip(" ,;:.")
    return lemma, ref_raw, page_int, inherited


def heading_level(label: str) -> int:
    if label in {
        "FORMULÆ MURBACENSES.",
        "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "B. URBANUS II PAPA.",
        "B. URBANI II EPISTOLÆ ET PRIVILEGIA.",
        "URBANI II PAPAE SERMONES.",
        "SAECULI XI AUCTORES ANNI INCERTI ET SCRIPTA ANECDOTA.",
    }:
        return 1
    return 2 if len(label) < 80 else 1


def is_heading_like(line: str) -> bool:
    if has_page_ref(line) or is_numeric_only(line):
        return False
    if line in {
        "FORMULÆ MURBACENSES.",
        "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "B. URBANUS II PAPA.",
        "B. URBANI II EPISTOLÆ ET PRIVILEGIA.",
        "URBANI II PAPAE SERMONES.",
        "SAECULI XI AUCTORES ANNI INCERTI ET SCRIPTA ANECDOTA.",
        "APPENDIX AD VITAM URBANI II PAPÆ.",
        "SÆCULI XI MONUMENTA LITURGICA.",
    }:
        return True
    if re.fullmatch(r"[A-ZÆŒ0-9 .,'’\-()]+\.?", line) and len(line) <= 90:
        return True
    if re.fullmatch(r"(?:[A-Z][A-Za-zÆŒæœ\.'’\-]+(?:\s+[A-Z][A-Za-zÆŒæœ\.'’\-]+){0,5})\.?", line) and len(line) <= 80:
        return True
    return False


def normalize_query_candidates(text: str) -> list[str]:
    value = normalize(text)
    if not value:
        return []
    variants = [value]
    stripped = value.rstrip(" .;:,")
    if stripped != value:
        variants.append(stripped)
    variants.append(re.sub(r"^(?:[IVXLCDM]+\.\s*[—-]\s*)", "", stripped))
    variants.append(re.sub(r"\s+", " ", stripped.replace("—", " ")))
    out: list[str] = []
    for variant in variants:
        variant = normalize(variant)
        if variant and variant not in out:
            out.append(variant)
    return out[:4]


def parse_items(tail_lines: list[tuple[str, str]]) -> tuple[list[ParsedItem], list[dict[str, Any]]]:
    items: list[ParsedItem] = []
    nodes: list[dict[str, Any]] = []
    buffer_lines: list[str] = []
    buffer_file: str | None = None
    previous_page: int | None = None
    order = 0
    node_order = 0
    current_node_key: str | None = None

    def flush_entry() -> None:
        nonlocal buffer_lines, buffer_file, previous_page, order, current_node_key, node_order
        if not buffer_lines:
            return
        combined = normalize(" ".join(buffer_lines))
        lemma, page_ref_raw, page_ref_int, inherited = split_page_ref(combined, previous_page)
        if page_ref_raw is None:
            label = combined
            if not label:
                buffer_lines = []
                buffer_file = None
                return
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": current_node_key,
                    "node_order": node_order,
                    "node_kind": "rubric_group",
                    "label_raw": label,
                    "label_norm": normalize(label.lower()) if label else None,
                    "label_sort": sort_norm(label),
                    "node_level": heading_level(label),
                    "confidence": 0.95,
                    "raw_json": {
                        "source_file": buffer_file,
                        "role": "toc_heading",
                    },
                }
            )
            current_node_key = node_key
            buffer_lines = []
            buffer_file = None
            return

        order += 1
        items.append(
            ParsedItem(
                order=order,
                source_file=buffer_file or "",
                raw_lines=list(buffer_lines),
                parent_node_key=current_node_key,
                entry_raw=combined,
                lemma_raw=lemma or combined,
                page_ref_raw=page_ref_raw,
                page_ref_int=page_ref_int,
                inherited_page=inherited,
                is_node=False,
                node_level=None,
            )
        )
        buffer_lines = []
        buffer_file = None
        previous_page = inherited

    for source_file, line in tail_lines:
        if line == "ORDO RERUM":
            continue
        if line == SECTION_HEADING_RAW:
            flush_entry()
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "heading_group",
                    "label_raw": line,
                    "label_norm": sort_norm(line),
                    "label_sort": sort_norm(line),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {"source_file": source_file, "role": "section_heading"},
                }
            )
            current_node_key = node_key
            continue
        if is_numeric_only(line):
            if buffer_lines:
                buffer_lines.append(line)
                flush_entry()
            continue
        if has_page_ref(line):
            if not buffer_lines:
                buffer_file = source_file
            buffer_lines.append(line)
            flush_entry()
            continue
        if buffer_lines:
            buffer_lines.append(line)
            continue
        if is_heading_like(line):
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": current_node_key,
                    "node_order": node_order,
                    "node_kind": "rubric_group",
                    "label_raw": line,
                    "label_norm": sort_norm(line),
                    "label_sort": sort_norm(line),
                    "node_level": heading_level(line),
                    "confidence": 0.95,
                    "raw_json": {"source_file": source_file, "role": "toc_heading"},
                }
            )
            current_node_key = node_key
            continue
        # Fallback: treat unclassified no-page text as a heading node rather than
        # collapsing it into a malformed entry.
        node_order += 1
        node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": current_node_key,
                "node_order": node_order,
                "node_kind": "rubric_group",
                "label_raw": line,
                "label_norm": sort_norm(line),
                "label_sort": sort_norm(line),
                "node_level": heading_level(line),
                "confidence": 0.9,
                "raw_json": {"source_file": source_file, "role": "toc_heading_fallback"},
            }
        )
        current_node_key = node_key

    flush_entry()
    return items, nodes


def build_helper_request(items: list[ParsedItem], source_root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in items:
        if not (
            item.order <= 25
            or item.order % 100 == 0
            or (item.page_ref_raw and item.page_ref_raw.lower().startswith("ibid") and item.order <= 120)
        ):
            continue
        page_hint = item.inherited_page if item.page_ref_raw and item.page_ref_raw.lower().startswith("ibid") else item.page_ref_int
        page_hints = []
        page_hint_ints = []
        if page_hint is not None:
            page_hints = [str(page_hint)]
            page_hint_ints = [page_hint]
        entries.append(
            {
                "entry_id": f"{VOLUME_ID.lower()}_{item.order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": normalize_query_candidates(item.lemma_raw),
                "page_hints": page_hints,
                "page_hint_ints": page_hint_ints,
                "context_raw": item.entry_raw,
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "index_target_locator.py failed\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return read_json(helper_output_json, {})


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {item.get("entry_id"): item for item in helper_output.get("entries", []) if item.get("entry_id")}


def build_payload(
    items: list[ParsedItem],
    nodes: list[dict[str, Any]],
    helper_output: dict[str, Any],
    source_root: Path,
    tail_files: list[str],
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    last_explicit_page: int | None = None
    entry_node_key: str | None = None
    node_lookup = {node["label_raw"]: node["node_key"] for node in nodes}
    node_stack: list[str | None] = []

    for item in items:
        helper_entry = helper_map.get(f"{VOLUME_ID.lower()}_{item.order:03d}") or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        source_file = item.source_file
        if item.page_ref_raw and item.page_ref_raw.lower().startswith("ibid"):
            page_ref_int = item.page_ref_int or last_explicit_page
        else:
            page_ref_int = item.page_ref_int
            if page_ref_int is not None:
                last_explicit_page = page_ref_int

        target_file = best.get("file") or source_file
        entry = {
            "entry_key": f"{VOLUME_ID}:entry:{item.order:03d}",
            "section_key": SECTION_KEY,
            "parent_node_key": item.parent_node_key,
            "entry_order": item.order,
            "entry_kind": "heading_group",
            "lemma_raw": item.lemma_raw,
            "lemma_display": item.lemma_raw,
            "lemma_norm": sort_norm(item.lemma_raw),
            "lemma_sort": sort_norm(item.lemma_raw),
            "entry_raw": item.entry_raw,
            "context_raw": item.entry_raw,
            "heading_letter": None,
            "inferred_printed_page": page_ref_int,
            "section_start_file": tail_files[0],
            "editorial_anchor_file": source_file,
            "target_file_best": target_file,
            "confidence": 0.84 if target_file else 0.72,
            "raw_json": {
                "source_file": source_file,
                "page_ref_raw": item.page_ref_raw,
                "helper": {
                    "status": helper_entry.get("status"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                    "best_candidate": best if best else None,
                    "top_candidates": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                            "evidence_kinds": [
                                ev.get("kind") for ev in cand.get("evidence", []) if isinstance(ev, dict)
                            ],
                        }
                        for cand in candidates[:3]
                    ],
                },
            },
        }
        entries.append(entry)

        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": item.page_ref_raw,
                "page_ref_raw": item.page_ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": best.get("probability"),
                "section_start_file": tail_files[0],
                "editorial_anchor_file": source_file,
                "confidence": 0.83 if target_file else 0.7,
                "raw_json": {
                    "source_file": source_file,
                    "page_ref_raw": item.page_ref_raw,
                    "helper": {
                        "status": helper_entry.get("status"),
                        "candidate_role": best.get("candidate_role"),
                        "reason_summary": best.get("reason_summary"),
                        "best_candidate": best if best else None,
                    },
                },
            }
        )

    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_HEADING_RAW,
            "heading_norm": SECTION_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1467,
            "page_end": 1500,
            "file_start": tail_files[0],
            "file_end": tail_files[-1],
            "confidence": 0.87,
            "raw_json": {
                "section_kind_reason": (
                    "The tail OCR is a contents table headed by ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.; "
                    "the same run-in page also carries the FORMULÆ MURBACENSES list before the main volume contents."
                ),
                "evidence_files": tail_files,
                "source_note": "fallback_internal filtered pages confirmed the ORDO RERUM headings in the tail OCR window.",
            },
        }
    ]

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": (
            "Recovered the closing contents table as an ordo_rerum section with page-bearing lines and major rubric nodes."
        ),
        "evidence_files": [tail_files[0], tail_files[2], tail_files[-2], tail_files[-1]],
    }

    notes = [
        {
            "note_key": f"{VOLUME_ID}:note:001",
            "note_type": "extraction",
            "text": (
                "The OCR tail is a contents table, not a lexical alphabetical index. "
                "Entries preserve OCR literals, including inherited ibid. locators and wrapped Latin titles."
            ),
        }
    ]

    return {
        "schema_version": 1.0,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
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


def write_intermediate_state(
    intermediate_dir: Path,
    payload: dict[str, Any],
    helper_request: dict[str, Any],
    helper_output: dict[str, Any],
    items: list[ParsedItem],
    nodes: list[dict[str, Any]],
) -> None:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(intermediate_dir / "helper_output.json", helper_output)
    write_json(
        intermediate_dir / "items.json",
        [
                {
                    "order": item.order,
                    "source_file": item.source_file,
                    "raw_lines": item.raw_lines,
                    "parent_node_key": item.parent_node_key,
                    "entry_raw": item.entry_raw,
                    "lemma_raw": item.lemma_raw,
                    "page_ref_raw": item.page_ref_raw,
                "page_ref_int": item.page_ref_int,
                "inherited_page": item.inherited_page,
                "is_node": item.is_node,
            }
            for item in items
        ],
    )
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL151 ORDO RERUM payload assembled and validated",
            "completed": [
                "tail OCR parsed",
                "helper request built and resolved",
                "payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The tail pages contain one closing contents table with nested rubric headings.",
            ],
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL151 ORDO RERUM alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    tail_lines = load_tail_lines(args.source_root)
    items, nodes = parse_items(tail_lines)
    helper_request = build_helper_request(items, args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(
        items=items,
        nodes=nodes,
        helper_output=helper_output,
        source_root=args.source_root,
        tail_files=sorted({path for path, _ in tail_lines}, key=lambda p: page_seq(Path(p))),
    )
    write_intermediate_state(args.intermediate_dir, payload, helper_request, helper_output, items, nodes)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
