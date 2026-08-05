#!/usr/bin/env python3
"""Usage: build the PG037 closing ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg037_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG037/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG037_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG037_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG037 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG037_alphabetical_indices.json
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG037"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 37"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_KIND_REASON = (
    "Closing ORDO RERUM contents table at the end of the tome; editorial contents structure "
    "rather than alphabetical lemma list."
)
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
FILE_START_SEQ = 806
FILE_END_SEQ = 808


HEADER_RE = re.compile(
    r"^(?:\d{3,4}\s+)?(?:ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?|QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)"
    r"(?:\s+\d{3,4})?$",
    re.IGNORECASE,
)
SECTION_START_RE = re.compile(r"^ORDO RERUM(?:\s+QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.)?$", re.IGNORECASE)
SECTION_CONTINUE_RE = re.compile(r"^QU[AEÆ]\s+IN\s+HOC\s+TOMO\s+CONTINENTUR\.$", re.IGNORECASE)
PAGE_AT_END_RE = re.compile(r"^(?P<text>.*?)(?:\s+)(?P<page>\d{1,4})(?:[)\].,'’]*)?$")
WS_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    return WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = (
        value.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
        .replace("Ĳ", "IJ")
        .replace("ĳ", "ij")
    )
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def parse_page(page: str) -> int | None:
    return int(page) if page.isdigit() else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(data + ("\n" if not data.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def discover_files(source_root: Path) -> list[Path]:
    files = sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
    selected: list[Path] = []
    for path in files:
        seq = int(path.stem.rsplit("-", 1)[-1])
        if FILE_START_SEQ <= seq <= FILE_END_SEQ:
            selected.append(path)
    return selected


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", raw, flags=re.S | re.I):
        attrs = block.group("attrs") or ""
        kind_match = re.search(r'tipo="([^"]+)"', attrs, flags=re.I)
        kind = (kind_match.group(1).strip().lower() if kind_match else "")
        if kind not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", block.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line == "Digitized by Google":
                continue
            lines.append(line)
    return lines


def join_hyphenated(lines: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        source_file, line = lines[i]
        if line.endswith("-") and i + 1 < len(lines):
            next_source_file, next_line = lines[i + 1]
            if next_line and not HEADER_RE.fullmatch(next_line):
                merged.append((source_file, line[:-1].rstrip() + next_line.lstrip()))
                i += 2
                continue
        merged.append((source_file, line))
        i += 1
    return merged


def collect_content_lines(source_root: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    files = discover_files(source_root)
    if not files:
        raise SystemExit("No OCR files found in the requested tail window.")

    raw_lines: list[tuple[str, str]] = []
    started = False
    saw_intro_heading = False

    for path in files:
        for line in join_hyphenated([(path.as_posix(), item) for item in extract_lines(path)]):
            source_file, text = line
            if not started:
                if SECTION_START_RE.fullmatch(text):
                    saw_intro_heading = True
                    continue
                if saw_intro_heading and SECTION_CONTINUE_RE.fullmatch(text):
                    started = True
                    saw_intro_heading = False
                    continue
                saw_intro_heading = False
                continue
            if HEADER_RE.fullmatch(text):
                continue
            raw_lines.append({"source_file": source_file, "text": text})

    if not raw_lines:
        raise SystemExit("Could not locate the ORDO RERUM tail in the selected OCR files.")

    return raw_lines, {
        "files": [path.as_posix() for path in files],
        "section_start_file": files[0].as_posix(),
        "section_end_file": files[-1].as_posix(),
    }


def strip_page_suffix(text: str) -> tuple[str, str | None, int | None]:
    cleaned = normalize(text)
    match = PAGE_AT_END_RE.match(cleaned)
    if not match:
        return cleaned, None, None
    lemma = normalize(match.group("text")).rstrip(" ,;:.")
    page_raw = match.group("page")
    return lemma, page_raw, parse_page(page_raw)


def is_root_heading(text: str) -> bool:
    return bool(text == "S. GREGORIUS THEOLOGUS, ARCHIEPISCOPUS CONSTANTINOPOLITANUS.")


def is_pure_heading(text: str) -> bool:
    return bool(text == "EPISTOLÆ S. GREGORII.")


def is_structural_entry(lemma_raw: str) -> bool:
    return bool(
        re.match(r"^(?:Monitum\b|TESTAMENTUM\b|CARMINA\b|LIBER\b|SECTIO\b)", lemma_raw)
        or lemma_raw.startswith("S. GREGORIUS THEOLOGUS")
        or lemma_raw.startswith("EPISTOLÆ S. GREGORII")
    )


def build_query_names(lemma_raw: str) -> list[str]:
    candidates = [
        lemma_raw,
        lemma_raw.replace("Æ", "AE").replace("æ", "ae"),
        lemma_raw.replace("—", " "),
        lemma_raw.replace("–", " "),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = normalize(item)
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:4]


def helper_compact(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates = []
    for candidate in (helper_entry.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "candidate_role": candidate.get("candidate_role"),
                "reason_summary": candidate.get("reason_summary"),
                "evidence_kinds": [ev.get("kind") for ev in candidate.get("evidence", []) if isinstance(ev, dict) and ev.get("kind")],
            }
        )
    return {
        "status": helper_entry.get("status"),
        "candidate_role": helper_entry.get("candidate_role"),
        "reason_summary": helper_entry.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidate_count": len(helper_entry.get("candidates") or []),
        "candidates": candidates,
    }


def build_payload(
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    content_lines, evidence = collect_content_lines(source_root)

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    root_node_key = f"{VOLUME_ID}:node:root:001"
    ep_node_key = f"{VOLUME_ID}:node:epistolae:002"

    nodes.append(
        {
            "node_key": root_node_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "S. GREGORIUS THEOLOGUS, ARCHIEPISCOPUS CONSTANTINOPOLITANUS.",
            "label_norm": "s gregorius theologus archiepiscopus constantinopolitanus",
            "label_sort": "s gregorius theologus archiepiscopus constantinopolitanus",
            "node_level": 1,
            "confidence": 0.97,
            "raw_json": {
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "source_file": content_lines[0]["source_file"],
                "note": "Root heading recovered from the opening of the contents table.",
            },
        }
    )
    nodes.append(
        {
            "node_key": ep_node_key,
            "section_key": SECTION_KEY,
            "parent_node_key": root_node_key,
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "EPISTOLÆ S. GREGORII.",
            "label_norm": "epistolae s gregorii",
            "label_sort": "epistolae s gregorii",
            "node_level": 2,
            "confidence": 0.96,
            "raw_json": {
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "note": "Epistle subheading in the contents table.",
            },
        }
    )

    current_parent_node_key = root_node_key
    entry_order = 0
    for item in content_lines:
        source_file = item["source_file"]
        text = item["text"]

        if is_pure_heading(text):
            current_parent_node_key = ep_node_key
            continue
        if is_root_heading(text):
            current_parent_node_key = root_node_key
            continue

        entry_order += 1
        lemma_raw, page_raw, page_int = strip_page_suffix(text)
        entry_kind = "heading_group" if is_structural_entry(lemma_raw) else "lemma"

        if lemma_raw.startswith("EPISTOLÆ S. GREGORII"):
            current_parent_node_key = ep_node_key
        elif lemma_raw.startswith("Monitum") or lemma_raw.startswith("TESTAMENTUM") or lemma_raw.startswith("CARMINA") or lemma_raw.startswith("LIBER") or lemma_raw.startswith("SECTIO"):
            current_parent_node_key = root_node_key
        elif lemma_raw.startswith("I. - Basilio") or lemma_raw.startswith("II. - Eidem"):
            current_parent_node_key = ep_node_key
        elif lemma_raw.startswith("Monitum de S. Gregorii testamento"):
            current_parent_node_key = root_node_key

        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        lemma_clean = lemma_raw or text
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": current_parent_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_clean,
            "lemma_display": lemma_clean,
            "lemma_norm": normalize(lemma_clean),
            "lemma_sort": sort_norm(lemma_clean),
            "entry_raw": text,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": evidence["section_start_file"],
            "editorial_anchor_file": source_file,
            "target_file_best": None,
            "confidence": 0.78 if page_int is not None else 0.9,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "entry_kind_reason": (
                    "contents-table structural heading"
                    if entry_kind == "heading_group"
                    else "contents-table line item"
                ),
            },
        }
        entries.append(entry)

        if page_raw is not None and page_int is not None:
            helper_id = f"{VOLUME_ID.lower()}_{entry_order:04d}_01"
            helper_entries.append(
                {
                    "entry_id": helper_id,
                    "lemma_raw": lemma_clean,
                    "query_names": build_query_names(lemma_clean),
                    "page_hints": [page_raw],
                    "page_hint_ints": [page_int],
                    "context_raw": text,
                }
            )
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": evidence["section_start_file"],
                    "editorial_anchor_file": source_file,
                    "confidence": 0.74,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "page_ref_source": "ocr_contents_table",
                        "helper_entry_id": helper_id,
                    },
                }
            )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": source_root.as_posix(),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)

    if helper_entries:
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
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    else:
        write_json(helper_output_json, {"volume_id": VOLUME_ID, "status": "empty", "entries": []})

    helper_output = read_json(helper_output_json, {})
    helper_by_id = {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }

    for entry in entries:
        helper_id = f"{VOLUME_ID.lower()}_{entry['entry_order']:04d}_01"
        helper = helper_by_id.get(helper_id)
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        best_file = best.get("file")
        if best_file:
            entry["target_file_best"] = best_file
            entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))
            entry["raw_json"]["helper"] = helper_compact(helper)

    for ref in refs:
        helper = helper_by_id.get(ref["raw_json"]["helper_entry_id"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            ref["confidence"] = max(ref["confidence"], float(best.get("probability") or 0.0))
            ref["raw_json"]["helper"] = helper_compact(helper)

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
            "page_start": 1600,
            "page_end": 1603,
            "file_start": evidence["section_start_file"],
            "file_end": evidence["section_end_file"],
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "evidence_files": evidence["files"],
                "heading_variants": [
                    "ORDO RERUM QUE IN HOC TOMO CONTINENTUR.",
                    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                ],
            },
        }
    ]

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": (
            "Recovered the closing ORDO RERUM contents table from OCR files 806-808 and resolved "
            "the cited page anchors conservatively with the local helper."
        ),
        "evidence_files": evidence["files"],
    }

    notes = [
        "The tail material is a closing ORDO RERUM contents table, not an alphabetical lemma index.",
        "OCR file suffixes were kept distinct from printed page numbers in every ref.",
        "The page 1603 heading in file 808 is preserved as the continuation header, not a separate section.",
    ]

    payload = {
        "schema_version": 1,
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

    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(intermediate_dir / "helper_output.json", helper_output)
    write_json(intermediate_dir / "manifest.json", payload)

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG037 ORDO RERUM alphabetical payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo_path = args.intermediate_dir / "todo.json"
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve the closing ORDO RERUM table and preserve OCR page anchors literally.",
        "completed": [
            "identified the closing ORDO RERUM section",
            "bounded the OCR window to files 806-808",
        ],
        "pending": [
            "run index_target_locator on every page-linked line",
            "assemble and validate the final payload",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR suffixes distinct from printed page citations.",
            "Section evidence is limited to the closing table-of-contents spread.",
        ],
    }
    write_json(todo_path, todo)

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("payload written")
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()
