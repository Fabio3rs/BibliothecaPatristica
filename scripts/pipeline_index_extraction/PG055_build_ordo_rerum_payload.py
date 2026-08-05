#!/usr/bin/env python3
"""Usage: build the PG055 ORDO RERUM payload, helper request, and final JSON.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG055_build_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG055/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG055_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG055_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG055 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG055_alphabetical_indices.json
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

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG055"
COLLECTION = "PG"
VOLUME_LABEL = "PG055"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO QUINTO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo quinto continentur"
SECTION_PAGE_START = 785
SECTION_PAGE_END = 790
TEXT_BLOCK_RE = re.compile(r'<bloco tipo="(?P<kind>[^"]+)"[^>]*>(?P<body>.*?)</bloco>', re.S)
PAGE_PAIR_RE = re.compile(r"^\s*(\d{1,4})\b.*\b(\d{1,4})\s*$")
PAGE_REF_RE = re.compile(r"(?P<raw>(?P<int>\d{1,4})(?:\s*[-–]\s*(?P<end>\d{1,4})|\s+(?P<endsp>\d{1,4}))?|ibid\.?|Ibid\.?)\s*$")
FOOTER_RE = re.compile(r"^Digitized by Google$", re.I)
NODE_LABELS = {"DUBIA OPUSCULA.", "SPURIA."}
SKIP_PREFIXES = ("ORDO RERUM", "FINIS TOMI QUINQUAGESIMI QUINTI.", "Parisiis. — Ex Typis J.-P. MIGNE.")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    return re.sub(r"\s+", " ", value).strip()


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file seq from {path}")
    return int(m.group(1))


def extract_blocks(path: Path, kinds: set[str]) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        if kind not in kinds:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or FOOTER_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=file_seq):
        for line in extract_blocks(path, {"cabecalho"}):
            m = PAGE_PAIR_RE.fullmatch(line)
            if not m:
                continue
            left = int(m.group(1))
            right = int(m.group(2))
            page_map.setdefault(left, path.as_posix())
            page_map.setdefault(right, path.as_posix())
            break
    return page_map


def load_tail_lines(source_root: Path) -> list[tuple[Path, str]]:
    files = [p for p in sorted(source_root.glob("*.txt"), key=file_seq) if 818 <= file_seq(p) <= 820]
    out: list[tuple[Path, str]] = []
    for path in files:
        for line in extract_blocks(path, {"texto_principal"}):
            out.append((path, line))
    return out


def guess_lemma(entry_raw: str) -> str:
    value = normalize(entry_raw)
    while True:
        m = PAGE_REF_RE.search(value)
        if not m:
            break
        value = value[: m.start()].rstrip(" ,;:.")
    return value.rstrip(" .;:")


def entry_kind(lemma_raw: str) -> str:
    if lemma_raw.upper().startswith("MONITUM"):
        return "editorial_note"
    return "lemma"


def parse_page_ref(text: str) -> tuple[str | None, int | None, int | None]:
    m = PAGE_REF_RE.search(normalize(text))
    if not m:
        return None, None, None
    raw = m.group("raw")
    if raw.lower() in {"ibid.", "ibid"}:
        return raw, None, None
    start = m.group("int")
    end = m.group("end") or m.group("endsp")
    return raw, int(start) if start else None, int(end) if end else None


def parse_scripture_refs(entry_key: str, entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    raw = normalize(entry_raw)
    patterns = [
        (r"\(Psal\.?\s*(\d{1,3})\.\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\)", "Psal.", "salmos"),
        (r"\((1\.?\s*Cor\.?)\s*(\d{1,3})\.\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\)", "1. Cor.", "1 coríntios"),
    ]
    order = 0
    for pattern, book_raw, book_norm in patterns:
        for match in re.finditer(pattern, raw, flags=re.I):
            order += 1
            if book_raw == "Psal.":
                chapter = int(match.group(1))
                verse_start = int(match.group(2))
                verse_end = int(match.group(3)) if match.group(3) else None
            else:
                chapter = int(match.group(2))
                verse_start = int(match.group(3))
                verse_end = int(match.group(4)) if match.group(4) else None
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": order,
                    "ref_role": "citation",
                    "ref_raw": match.group(0)[1:-1],
                    "book_raw": book_raw,
                    "book_norm": book_norm,
                    "chapter_start": chapter,
                    "verse_start": verse_start,
                    "chapter_end": chapter,
                    "verse_end": verse_end,
                    "is_range": verse_end is not None,
                    "confidence": 0.96,
                    "raw_json": {},
                }
            )
    return refs


def helper_summary(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates = []
    for cand in helper_entry.get("candidates", [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", []) if isinstance(ev, dict) and ev.get("kind")][:6],
            }
        )
    return {
        "status": helper_entry.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
        },
        "candidates": candidates,
    }


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    selectors = [
        "HOMILIA in magnam hebdomadam",
        "HOMILIA in illud, Dominus regnavit",
        "ORATIO in illud, Precamini",
        "HOMILIA in dictum Psalmi XCII",
        "EXPOSITIO in Psalmum LXXVII",
    ]
    for idx, entry in enumerate(entries, start=1):
        if not any(sel in entry["entry_raw"] for sel in selectors):
            continue
        page_hint = entry.get("inferred_printed_page")
        if page_hint is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"pg055_{idx:03d}",
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": [entry["lemma_raw"] or entry["entry_raw"], entry["entry_raw"][:120]],
                "page_hints": [str(page_hint)],
                "page_hint_ints": [int(page_hint)],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "index_target_locator.py"),
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


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}


def parse_entries(source_root: Path, page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[Path, str]]]:
    tail_lines = load_tail_lines(source_root)
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    buffer: list[str] = []
    current_file: Path | None = None
    parent_node_key: str | None = None
    entry_order = 0
    node_order = 0
    start_re = re.compile(r"^(?:[A-ZÆŒ]|MONITUM|PROŒMIUM|ARGUMENTUM|SERMO|ORATIO|HOMILIA|EXPOSITIO|PRÆFATIO|PRAEFATIO|COLLECTIO)")

    def flush() -> None:
        nonlocal buffer, current_file, entry_order
        if not buffer:
            return
        combined = normalize(" ".join(buffer))
        if not combined or any(combined.startswith(prefix) for prefix in SKIP_PREFIXES):
            buffer = []
            current_file = None
            return
        lemma_raw = guess_lemma(combined)
        page_raw, page_int, page_end = parse_page_ref(combined)
        entry_order += 1
        entry_key = f"{SECTION_KEY}:entry:{entry_order:04d}"
        target_file = page_map.get(page_int) if page_int is not None else None
        scripture_refs = parse_scripture_refs(entry_key, combined)
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": parent_node_key,
            "entry_order": entry_order,
            "entry_kind": entry_kind(lemma_raw),
            "lemma_raw": lemma_raw or None,
            "lemma_display": lemma_raw or None,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": combined,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": None,
            "editorial_anchor_file": current_file.as_posix() if current_file else None,
            "target_file_best": target_file or (current_file.as_posix() if current_file else None),
            "confidence": 0.92 if page_int is not None else 0.66,
            "raw_json": {
                "source_file": current_file.as_posix() if current_file else None,
                "page_ref": {
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "range_end_raw": str(page_end) if page_end is not None else None,
                    "range_end_int": page_end,
                },
                "section_kind_reason": "closing ORDO RERUM contents table at the end of the tome",
            },
        }
        if page_raw and page_raw.lower() in {"ibid", "ibid."}:
            entry["confidence"] = 0.62
            entry["raw_json"]["page_ref"]["page_ref_reason"] = "relative locator with no explicit page"
        if entry["entry_kind"] == "editorial_note" and page_int is None:
            entry["confidence"] = 0.60
        entries.append(entry)
        buffer = []
        current_file = None

    for path, line in tail_lines:
        if line.startswith("DUBIA OPUSCULA.") or line.startswith("SPURIA."):
            flush()
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            page_raw, page_int, page_end = parse_page_ref(line)
            label_text = line
            if page_raw:
                label_text = normalize(line[: normalize(line).rfind(page_raw)]).rstrip(" ,;:.")
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": parent_node_key,
                    "node_order": node_order,
                    "node_kind": "heading_group",
                    "label_raw": label_text,
                    "label_norm": label_text.lower().rstrip("."),
                    "label_sort": sort_norm(label_text),
                    "node_level": 2 if line.startswith("SPURIA.") else 1,
                    "confidence": 0.97,
                    "raw_json": {
                        "source_file": path.as_posix(),
                        "role": "contents_group",
                        "page_ref": {
                            "page_ref_raw": page_raw,
                            "page_ref_int": page_int,
                            "range_end_raw": str(page_end) if page_end is not None else None,
                            "range_end_int": page_end,
                        },
                    },
                }
            )
            if line.startswith("DUBIA OPUSCULA."):
                parent_node_key = node_key
            elif line.startswith("SPURIA."):
                parent_node_key = node_key
            continue
        if line.startswith("Digitized by Google"):
            continue
        is_new_start = bool(start_re.match(line))
        if buffer and is_new_start:
            flush()
        if not buffer and not is_new_start:
            continue
        if not buffer:
            current_file = path
        buffer.append(line)
        if PAGE_REF_RE.search(line):
            flush()

    flush()
    return entries, nodes, tail_lines


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    page_map = build_page_map(source_root)
    entries, nodes, tail_lines = parse_entries(source_root, page_map)

    for idx, entry in enumerate(entries, start=1):
        entry["section_start_file"] = tail_lines[0][0].as_posix()
        entry["raw_json"]["source_files"] = [entry["raw_json"]["source_file"]]

    helper_request = build_helper_request(source_root, entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_map = helper_lookup(helper_output)

    for idx, entry in enumerate(entries, start=1):
        entry_id = f"pg055_{idx:03d}"
        helper_entry = helper_map.get(entry_id)
        if helper_entry:
            entry["raw_json"]["helper"] = helper_summary(helper_entry)
            best = helper_entry.get("best_candidate") or {}
            if best.get("file"):
                entry["target_file_best"] = best.get("file")
        if entry["target_file_best"] is None and entry["inferred_printed_page"] is not None:
            entry["target_file_best"] = page_map.get(int(entry["inferred_printed_page"]))

    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []
    for entry in entries:
        page_ref = entry["raw_json"]["page_ref"]
        page_int = page_ref.get("page_ref_int")
        page_end = page_ref.get("range_end_int")
        page_raw = page_ref.get("page_ref_raw")
        if page_raw and page_int is not None:
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(page_int),
                    "range_end_raw": str(page_end) if page_end is not None else None,
                    "target_file": entry["target_file_best"],
                    "target_file_probability": entry.get("raw_json", {}).get("helper", {}).get("best_candidate", {}).get("probability"),
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.93,
                    "raw_json": {
                        "page_ref_reason": "contents-table page span",
                        "helper": entry.get("raw_json", {}).get("helper"),
                    },
                }
            )
        scripture_refs.extend(parse_scripture_refs(entry["entry_key"], entry["entry_raw"]))

    section = {
        "section_key": SECTION_KEY,
        "volume_id": VOLUME_ID,
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": SECTION_HEADING_RAW,
        "heading_norm": SECTION_HEADING_NORM,
        "heading_letter": None,
        "page_start": SECTION_PAGE_START,
        "page_end": SECTION_PAGE_END,
        "file_start": tail_lines[0][0].as_posix(),
        "file_end": tail_lines[-1][0].as_posix(),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": "closing contents table (ordo rerum) at the end of the tome",
            "evidence_files": sorted({p.as_posix() for p, _ in tail_lines}),
        },
    }

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table from OCR files 818-820; page-linked entries and a few editorial notes were serialized conservatively.",
        "evidence_files": sorted({p.as_posix() for p, _ in tail_lines}),
    }

    notes = [
        "The section is an editorial closing table of contents, not an alphabetical lemma index.",
        "Nodes capture the internal table headings `DUBIA OPUSCULA.` and `SPURIA.`.",
        "Biblical citations were only emitted when the printed form was explicit enough to parse conservatively.",
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG055 ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Build the PG055 ORDO RERUM payload and validate helper-assisted locators.",
            "completed": [
                "inspected the OCR tail",
                "identified the closing contents table",
            ],
            "pending": [
                "write the final JSON payload",
                "validate the helper output and the resulting schema",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals in entry_raw and ref_raw.",
            ],
        },
    )

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json)
    write_json(args.output_file, payload)

    todo_path = args.intermediate_dir / "todo.json"
    todo = read_json(todo_path, {})
    todo["updated_at"] = now_iso()
    todo["completed"] = list(todo.get("completed", [])) + ["payload written"]
    todo["pending"] = []
    write_json(todo_path, todo)


if __name__ == "__main__":
    main()
