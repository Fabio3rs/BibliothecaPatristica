#!/usr/bin/env python3
"""Usage: build the PL100 alphabetical/analytical index payload from OCR and write the final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl100_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL100/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL100_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL100_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL100 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL100_alphabetical_indices.json
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


VOLUME_ID = "PL100"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, volume 100"
SECTION_KEY = f"{VOLUME_ID}:alpha:analytic_subject:001"
SECTION_FILE_START_SEQ = 580
SECTION_FILE_END_SEQ = 584

BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.IGNORECASE | re.DOTALL)
HEADER_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LINE_REF_RE = re.compile(r"^(?P<page>\d{1,4})(?:,\s*(?P<line>\d{1,3}(?:\s*sqq\.)?))?$", re.IGNORECASE)
PAGE_TOKEN_RE = re.compile(r"\b(?P<ibid>ibid\.?)\b|\b(?P<page>\d{1,4})(?:,\s*(?P<line>\d{1,3}(?:\s*sqq\.)?))?", re.IGNORECASE)
NOISE_LINES = {"Digitized by Google"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    return value.lower() if value is not None else None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line and line not in NOISE_LINES]
        if lines:
            blocks.append({"kind": kind, "lines": lines})
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        header = normalize(parsed.get("header_text") or "") or ""
        for match in HEADER_NUM_RE.finditer(header):
            page = int(match.group(1))
            page_map.setdefault(page, str(path))
    return page_map


def is_heading_line(line: str) -> bool:
    cleaned = normalize(line) or ""
    return bool(LETTER_RE.fullmatch(cleaned))


def is_section_break(line: str) -> bool:
    cleaned = normalize(line) or ""
    upper = cleaned.upper()
    return upper.startswith("ORDO RERUM")


def looks_like_continuation(line: str) -> bool:
    cleaned = normalize(line) or ""
    if not cleaned:
        return False
    if cleaned.startswith(("—", "-", "·")):
        return True
    if cleaned[:1].islower():
        return True
    if cleaned.startswith(("ibid.", "ibid,", "ibid;")):
        return True
    if re.match(r"^\d", cleaned):
        return True
    return False


def entry_kind_for(entry_raw: str) -> str:
    raw = normalize(entry_raw) or ""
    if not raw:
        return "editorial_note"
    if re.search(r"\bVide\b", raw, re.IGNORECASE) and not re.search(r"\d{1,4}", raw):
        return "cross_reference"
    return "lemma"


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    if not text:
        return None
    if re.search(r"\bVide\b", text, re.IGNORECASE):
        text = text.split("Vide", 1)[0].strip()
    first_match = PAGE_TOKEN_RE.search(text)
    if first_match:
        text = text[: first_match.start()].strip()
    text = text.strip(" .;:")
    text = re.sub(r"\s+", " ", text)
    return text or None


def parse_refs(entry_raw: str, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], int | None]:
    refs: list[dict[str, Any]] = []
    current_last: int | None = None
    inferred_page: int | None = None
    for match in PAGE_TOKEN_RE.finditer(entry_raw):
        raw = match.group(0).strip()
        if match.group("ibid"):
            if current_last is None:
                continue
            page_int = current_last
            ref_kind = "editorial_page"
            line_ref_raw = None
            page_ref_raw = raw
        else:
            page_int = int(match.group("page"))
            current_last = page_int
            inferred_page = inferred_page if inferred_page is not None else page_int
            line_ref_raw = match.group("line")
            ref_kind = "editorial_page_line" if line_ref_raw else "editorial_page"
            page_ref_raw = match.group("page")
        target_file = page_map.get(page_int)
        refs.append(
            {
                "entry_key": None,
                "ref_order": len(refs) + 1,
                "ref_kind": ref_kind,
                "ref_raw": raw,
                "page_ref_raw": page_ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": line_ref_raw,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": target_file,
                "target_file_probability": 0.99 if target_file and ref_kind != "editorial_page" else (0.78 if target_file else None),
                "section_start_file": None,
                "editorial_anchor_file": None,
                "confidence": 0.91 if page_int is not None else 0.62,
                "raw_json": {
                    "page_token_kind": "ibid" if match.group("ibid") else "page",
                },
            }
        )
    return refs, inferred_page


def collect_index_lines(section_files: list[Path]) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    for path in section_files:
        parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
        blocks = parsed["blocks"] if isinstance(parsed.get("blocks"), list) else extract_blocks(path)
        for block in blocks:
            if block["kind"] not in {"cabecalho", "texto_principal", "outro"}:
                continue
            for line in block["lines"]:
                collected.append((str(path), line))
    return collected


def build_sections(section_files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "analytic_subject",
            "heading_raw": "INDEX RERUM / INDEX ANALYTICUS RERUM QUÆ IN COMMENTARIIS ALCUINI IN APOCALYPSIN CONTINENTUR.",
            "heading_norm": "index rerum index analyticus rerum quae in commentariis alcuini in apocalypsin continentur",
            "heading_letter": None,
            "page_start": 135,
            "page_end": 1162,
            "file_start": str(section_files[0]),
            "file_end": str(section_files[-1]),
            "confidence": 0.97,
            "raw_json": {
                "section_kind_reason": "Analytical subject index for the Apocalypse commentary, with letter-group headings and dense page/line locators; the trailing ORDO RERUM block is editorial closure and not part of this section.",
                "evidence_files": [str(path) for path in section_files],
            },
        }
    ]


def build_todo(volume_id: str, intermediate_dir: Path, note: str) -> None:
    todo = {
        "volume_id": volume_id,
        "updated_at": now_iso(),
        "current_focus": note,
        "completed": [],
        "pending": [],
        "blocked": [],
        "notes": [],
    }
    (intermediate_dir / "todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_helper_request(volume_id: str, source_root: Path, entries: list[dict[str, Any]], helper_request_json: Path) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        hints = [int(x) for x in (entry["raw_json"].get("page_hints") or []) if isinstance(x, int)]
        if not hints:
            continue
        lemma_raw = entry["lemma_raw"] or entry["entry_raw"]
        entry_id = f"{volume_id.lower()}_{entry['entry_order']:05d}"
        helper_entries.append(
            {
                "entry_id": entry_id,
                "lemma_raw": lemma_raw,
                "query_names": [q for q in [lemma_raw, entry["entry_raw"]] if q],
                "page_hints": [str(i) for i in hints],
                "page_hint_ints": hints,
                "context_raw": entry["entry_raw"],
            }
        )
    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    helper_request_json.parent.mkdir(parents=True, exist_ok=True)
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


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
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL100 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    build_todo(VOLUME_ID, args.intermediate_dir, "Extract PL100 analytical index and resolve target files")

    files = discover_text_files(args.source_root)
    section_files = [path for path in files if SECTION_FILE_START_SEQ <= file_seq(path) <= SECTION_FILE_END_SEQ]
    if not section_files:
        raise SystemExit("No PL100 section files found in the expected tail window.")
    page_map = build_page_map(files)

    lines = collect_index_lines(section_files)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current_buffer: list[str] = []
    current_source_file: str | None = None
    current_node_key: str | None = None
    node_order = 0
    entry_order = 0
    started = False

    def ensure_letter_node(letter: str, source_file: str | None, synthetic: bool = False) -> None:
        nonlocal current_node_key, node_order
        if current_node_key and current_node_key.endswith(f":{letter}"):
            return
        node_order += 1
        current_node_key = f"{VOLUME_ID}:node:{node_order:03d}"
        nodes.append(
            {
                "node_key": current_node_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "node_order": node_order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.95 if not synthetic else 0.86,
                "raw_json": {
                    "source_file": source_file,
                    "synthetic": synthetic,
                },
            }
        )

    def flush_buffer() -> None:
        nonlocal entry_order, current_buffer
        if not current_buffer:
            return
        entry_raw = normalize(" ".join(current_buffer)) or ""
        current_buffer = []
        if not entry_raw:
            return
        if not re.search(r"\d{1,4}|\bibid\.?\b|Vide\b", entry_raw, re.IGNORECASE):
            return
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:06d}"
        entry_refs, inferred_page = parse_refs(entry_raw, page_map)
        lemma_raw = lemma_from_entry(entry_raw)
        entry_kind = entry_kind_for(entry_raw)
        target_file_best = current_source_file
        if inferred_page is not None:
            target_file_best = page_map.get(inferred_page) or current_source_file
        entry_payload = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": current_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": entry_raw,
            "context_raw": entry_raw,
            "heading_letter": current_node_key.split(":")[-1] if current_node_key else None,
            "inferred_printed_page": inferred_page,
            "section_start_file": str(section_files[0]),
            "editorial_anchor_file": current_source_file,
            "target_file_best": target_file_best,
            "confidence": 0.92 if entry_refs else 0.76,
            "raw_json": {
                "source_file": current_source_file,
                "page_hints": [ref["page_ref_int"] for ref in entry_refs if ref.get("page_ref_int") is not None],
                "fragmentary_start": False,
            },
        }
        if lemma_raw is None:
            entry_payload["raw_json"]["lemma_recovery"] = "not_recovered"
        entries.append(entry_payload)
        for ref in entry_refs:
            ref = dict(ref)
            ref["entry_key"] = entry_key
            ref["section_start_file"] = str(section_files[0])
            ref["editorial_anchor_file"] = current_source_file
            refs.append(ref)

    for source_file, line in lines:
        current_source_file = source_file
        cleaned = normalize(line) or ""
        upper = cleaned.upper()
        if (
            upper.startswith("INDEX RERUM")
            or upper.startswith("INDEX ANALYTICUS")
            or upper.startswith("VERS.")
            or upper.startswith("QUE IN COMMENT")
            or upper.startswith("QUAE IN COMMENT")
            or upper.startswith("QUÆ IN COMMENT")
            or upper.startswith("RERUM QUÆ IN COMMENTARIIS")
            or re.match(r"^\d+\s+(INDEX RERUM|INDEX ANALYTICUS|QUE IN COMMENT|QUAE IN COMMENT|QUÆ IN COMMENT)", upper)
        ):
            continue
        if is_section_break(cleaned):
            flush_buffer()
            break
        if cleaned in {"INDEX RERUM", "INDEX ANALYTICUS", "RERUM QUÆ IN COMMENTARIIS ALCUINI IN APOCALYPSIN CONTINENTUR.", "QUE IN COMMENT. IN APOC. CONTINENTUR.", "QUAE IN COMMENT. IN APOC. CONTINENTUR.", "QUÆ IN COMMENT. IN APOC. CONTINENTUR."}:
            continue
        if is_heading_line(cleaned):
            flush_buffer()
            ensure_letter_node(cleaned, source_file, synthetic=False)
            started = True
            continue
        if not started:
            if re.match(r"^[A-ZÆŒ]", cleaned):
                started = True
                if not current_node_key:
                    ensure_letter_node(cleaned[:1], source_file, synthetic=True)
            else:
                continue
        if current_buffer and looks_like_continuation(cleaned):
            current_buffer.append(cleaned)
            continue
        if current_buffer:
            flush_buffer()
        if re.match(r"^[A-ZÆŒ]", cleaned) or re.match(r"^\d", cleaned) or re.search(r"\bVide\b", cleaned, re.IGNORECASE):
            if not current_node_key and re.match(r"^[A-ZÆŒ]", cleaned):
                ensure_letter_node(cleaned[:1], source_file, synthetic=True)
            current_buffer = [cleaned]
        else:
            # Preserve only obvious continuations when there is already a buffer.
            continue
    flush_buffer()

    helper_request = build_helper_request(VOLUME_ID, args.source_root, entries, args.helper_request_json)
    helper_output: dict[str, Any] = run_helper(args.helper_request_json, args.helper_output_json)
    helper_lookup = {str(item.get("entry_id")): item for item in (helper_output.get("entries") or [])}

    for entry in entries:
        entry_id = f"{VOLUME_ID.lower()}_{entry['entry_order']:05d}"
        entry["raw_json"]["helper_entry_id"] = entry_id
        entry["raw_json"]["helper_request_entry"] = {
            "entry_id": entry_id,
            "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
            "query_names": [q for q in [entry["lemma_raw"], entry["entry_raw"]] if q],
            "page_hints": [str(p) for p in entry["raw_json"].get("page_hints") or []],
            "page_hint_ints": [p for p in entry["raw_json"].get("page_hints") or [] if isinstance(p, int)],
            "context_raw": entry["entry_raw"],
        }
        helper_item = helper_lookup.get(entry_id)
        if helper_item:
            entry["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": (helper_item.get("top_candidates") or [])[:3],
            }
            best = (helper_item.get("top_candidates") or [{}])[0]
            if best.get("file"):
                entry["target_file_best"] = best["file"]
                entry["raw_json"]["helper_best_file"] = best["file"]
                entry["raw_json"]["helper_best_probability"] = best.get("probability")

    for ref in refs:
        helper_item = helper_lookup.get(f"{VOLUME_ID.lower()}_{int(ref['entry_key'].rsplit(':', 1)[-1]):05d}") if ref.get("entry_key") else None
        if helper_item:
            ref["raw_json"]["helper_locator"] = {
                "status": helper_item.get("status"),
                "candidate_role": helper_item.get("candidate_role"),
                "reason_summary": helper_item.get("reason_summary"),
                "top_candidates": (helper_item.get("top_candidates") or [])[:3],
            }

    sections = build_sections(section_files)
    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Recovered the PL100 analytical index entries from the tail window spanning the index opening and the transition into the editorial ORDO RERUM block.",
        "evidence_files": [str(path) for path in section_files],
    }
    notes = [
        {
            "note_key": "pl100_index_transition",
            "note_raw": "The first index page begins mid-entry and the last page transitions into ORDO RERUM. The payload stops at the first ORDO RERUM heading and treats the editorial contents block as non-index material.",
            "confidence": 0.95,
        },
        {
            "note_key": "pl100_helper",
            "note_raw": "Helper request and output were generated for the material index entries; direct OCR reading remained the primary source of truth for segmentation.",
            "confidence": 0.9,
        },
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(args.source_root),
        "volume_label": VOLUME_LABEL,
        "notes": "Analytical subject index for Beati Alcuini commentarius in Apocalypsin; OCR tail also includes a separate ORDO RERUM block that is not part of the index section.",
    }

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": payload["generated_at"],
        "sections_count": len(sections),
        "entries_count": len(entries),
        "refs_count": len(refs),
    }
    (args.intermediate_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "volume.json").write_text(json.dumps(volume, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "sections.json").write_text(json.dumps(sections, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "nodes.json").write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "scripture_refs.json").write_text("[]\n", encoding="utf-8")
    (args.intermediate_dir / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.intermediate_dir / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_todo(VOLUME_ID, args.intermediate_dir, "Completed PL100 analytical index extraction")


if __name__ == "__main__":
    main()
