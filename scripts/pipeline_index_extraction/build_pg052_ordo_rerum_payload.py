#!/usr/bin/env python3
"""Usage: build the PG052 ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg052_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG052/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG052_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG052_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG052 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG052_alphabetical_indices.json
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
VOLUME_ID = "PG052"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 52"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN DUABUS TOMI TERTII PARTIBUS CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in duabus tomi tertii partibus continentur"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
TYPE_RE = re.compile(r'tipo="([^"]+)"')
NOISE_RE = re.compile(r"^(?:Digitized by Google|\.|,|;|:|-+)$", re.IGNORECASE)
PAGE_PAIR_RE = re.compile(r"^(?P<p1>\d{1,4})\s+.*?\s+(?P<p2>\d{1,4})$")
PAGE_TRAIL_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<ref>(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?|ibid\.?))\.?$", re.I)
START_RE = re.compile(
    r"^(?:"
    r"PRÆFATIO|PRAEFATIO|"
    r"ADMONITIO|MONITUM|"
    r"HOMIL\.|HOMILIA|HOM\.|"
    r"CONCIO|SERMO|"
    r"EPISTOLA|EPIST\.|"
    r"LIBER|"
    r"SELECTA|"
    r"PROŒMIUM\.|"
    r"SPURIA\.|"
    r"§\s*\d+\.|"
    r"[IVXLCDM]+\s*,|"
    r"[IVXLCDM]+\."
    r")",
    re.I,
)


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
    value = re.sub(r"\s+", " ", value).strip()
    return value


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


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def extract_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = TYPE_RE.search(attrs)
        tipo = tipo_m.group(1).strip().lower() if tipo_m else ""
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or NOISE_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def is_section_heading(line: str) -> bool:
    upper = line.upper()
    return "ORDO RERUM" in upper or "QUÆ IN DUABUS TOMI TERTII PARTIBUS CONTINENTUR" in upper


def is_structural_only(line: str) -> bool:
    return line in {"PROŒMIUM.", "SPURIA."}


def split_trailing_ref(line: str) -> tuple[str, str | None, int | None, int | None]:
    m = PAGE_TRAIL_RE.match(line)
    if not m:
        return normalize(line), None, None, None
    body = normalize(m.group("body").rstrip(" ,;:."))
    ref = normalize(m.group("ref"))
    if ref.lower().startswith("ibid"):
        return body, "ibid.", None, None
    if "-" in ref or "–" in ref:
        start, end = re.split(r"\s*[-–]\s*", ref, maxsplit=1)
        return body, f"{start}-{end}", int(start), int(end)
    return body, ref, int(ref), None


def make_query_names(entry_raw: str) -> list[str]:
    base = normalize(entry_raw)
    variants = [base]
    if "—" in base:
        variants.append(normalize(base.split("—", 1)[0]))
        tail = normalize(base.split("—", 1)[1])
        if tail:
            variants.append(tail)
    if ":" in base:
        variants.append(normalize(base.split(":", 1)[0]))
    if ";" in base:
        variants.append(normalize(base.split(";", 1)[0]))
    words = base.split()
    if len(words) >= 6:
        variants.append(" ".join(words[:6]))
    if len(words) >= 8:
        variants.append(" ".join(words[:8]))
    cleaned = [v for v in (normalize(v) for v in variants) if v]
    dedup: list[str] = []
    for item in cleaned:
        if item not in dedup:
            dedup.append(item)
    return dedup[:5]


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        for line in extract_lines(path)[:10]:
            if is_section_heading(line):
                continue
            m = re.fullmatch(r"(\d{1,4})", line)
            if m:
                page_map.setdefault(int(m.group(1)), path.as_posix())
            m = PAGE_PAIR_RE.match(line)
            if m:
                page_map.setdefault(int(m.group("p1")), path.as_posix())
                page_map.setdefault(int(m.group("p2")), path.as_posix())
    return page_map


def build_content_corpus(source_root: Path) -> list[tuple[Path, str]]:
    corpus: list[tuple[Path, str]] = []
    for path in sorted(source_root.glob("*.txt"), key=page_seq):
        text = path.read_text(encoding="utf-8", errors="replace")
        body = re.sub(r"<[^>]+>", " ", text)
        body = unicodedata.normalize("NFKC", body.replace("\xa0", " "))
        body = re.sub(r"\s+", " ", body).strip().lower()
        corpus.append((path, body))
    return corpus


def resolve_target_file(entry_raw: str, corpus: list[tuple[Path, str]], excluded: set[Path]) -> str | None:
    variants = make_query_names(entry_raw)
    for variant in variants:
        needle = sort_norm(variant) or ""
        if not needle or len(needle) < 8:
            continue
        hits = [path for path, text in corpus if path not in excluded and needle in text]
        if hits:
            hits.sort(key=page_seq)
            return hits[0].as_posix()
    return None


def parse_items(files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    last_explicit_ref: dict[str, Any] | None = None
    current: dict[str, Any] | None = None
    entry_order = 0
    node_order = 0

    def flush_current() -> None:
        nonlocal current, entry_order, last_explicit_ref
        if current is None:
            return
        raw = normalize(" ".join(current["lines"]))
        body, ref_raw, ref_int, ref_end = split_trailing_ref(raw)
        if ref_raw is not None and ref_raw.lower().startswith("ibid") and last_explicit_ref:
            ref_int = last_explicit_ref["page_ref_int"]
            ref_end_raw = last_explicit_ref.get("range_end_raw")
            ref_end = int(ref_end_raw) if ref_end_raw else None
        if ref_raw is None and current.get("pending_ref") and last_explicit_ref:
            ref_raw = "ibid."
            ref_int = last_explicit_ref["page_ref_int"]
            ref_end_raw = last_explicit_ref.get("range_end_raw")
            ref_end = int(ref_end_raw) if ref_end_raw else None
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        kind = current.get("kind", "lemma")
        if current.get("structural"):
            kind = "heading_group"
        lemma_raw = body if kind not in {"cross_reference", "editorial_note"} else None
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": current.get("parent_node_key"),
            "entry_order": entry_order,
            "entry_kind": kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw),
            "lemma_sort": sort_norm(lemma_raw),
            "entry_raw": body,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": ref_int,
            "section_start_file": files[0].as_posix(),
            "editorial_anchor_file": current["source_file"],
            "target_file_best": None,
            "confidence": 0.9 if ref_int is not None else 0.68,
            "raw_json": {
                "source_files": sorted(current["source_files"]),
                "line_count": len(current["lines"]),
                "section_kind": "ordo_rerum",
                "page_ref_raw": ref_raw,
            },
        }
        if ref_raw is not None:
            ref = {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_range" if ref_end is not None else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(ref_int) if ref_int is not None else None,
                "range_end_raw": str(ref_end) if ref_end is not None else None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": files[0].as_posix(),
                "editorial_anchor_file": current["source_file"],
                "confidence": 0.9 if ref_end is None else 0.88,
                "raw_json": {
                    "source_files": sorted(current["source_files"]),
                    "resolved_from": current.get("resolved_from"),
                    "pending_ref": current.get("pending_ref", False),
                },
            }
            refs.append(ref)
            last_explicit_ref = ref
        entry["raw_json"]["has_page_ref"] = ref_raw is not None
        entries.append(entry)
        current = None

    for path in files:
        for line in extract_lines(path):
            if is_section_heading(line):
                continue
            if is_structural_only(line):
                if current is not None:
                    flush_current()
                node_order += 1
                nodes.append(
                    {
                        "node_key": f"{VOLUME_ID}:node:{node_order:03d}",
                        "section_key": SECTION_KEY,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "heading_group",
                        "label_raw": line,
                        "label_norm": sort_norm(line),
                        "label_sort": sort_norm(line),
                        "node_level": 1,
                        "confidence": 0.92,
                        "raw_json": {"source_file": path.as_posix()},
                    }
                )
                continue
            if current is None:
                current = {
                    "lines": [line],
                    "source_file": path.as_posix(),
                    "source_files": {path.as_posix()},
                    "kind": "heading_group" if line.startswith("§") else ("editorial_note" if line.upper().startswith(("MONITUM", "ADMONITIO")) else "lemma"),
                "pending_ref": False,
                "resolved_from": None,
            }
            if line.lower() == "ibid." and last_explicit_ref is not None:
                current["pending_ref"] = True
                current["resolved_from"] = last_explicit_ref["ref_raw"]
            if re.search(r"\s+(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?|ibid\.?)\.?$", line, re.I):
                current["pending_ref"] = False
                current["resolved_from"] = None
                continue
            if START_RE.match(line):
                flush_current()
                current = {
                    "lines": [line],
                    "source_file": path.as_posix(),
                    "source_files": {path.as_posix()},
                    "kind": "heading_group" if line.startswith("§") else ("editorial_note" if line.upper().startswith(("MONITUM", "ADMONITIO")) else "lemma"),
                    "pending_ref": False,
                    "resolved_from": None,
                }
                if re.search(r"\s+(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?|ibid\.?)\.?$", line, re.I):
                    current["pending_ref"] = False
                continue
            current["lines"].append(line)
            current["source_files"].add(path.as_posix())
            if line.lower() == "ibid." and last_explicit_ref is not None:
                current["pending_ref"] = True
                current["resolved_from"] = last_explicit_ref["ref_raw"]
            elif re.search(r"\s+(?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?|ibid\.?)\.?$", line, re.I):
                current["pending_ref"] = False
                current["resolved_from"] = None

    flush_current()
    return entries, refs, nodes


def build_helper_request(source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries = []
    for entry in entries:
        if entry["inferred_printed_page"] is None:
            continue
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": make_query_names(entry["entry_raw"]),
                "page_hints": [str(entry["inferred_printed_page"])],
                "page_hint_ints": [entry["inferred_printed_page"]],
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


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        entry_id = item.get("entry_id")
        if entry_id:
            out[entry_id] = item
    return out


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = [p for p in sorted(source_root.glob("*.txt"), key=page_seq) if 469 <= page_seq(p) <= 474]
    if not files:
        raise SystemExit("No OCR files found for PG052 ORDO RERUM window.")

    entries, refs, nodes = parse_items(files)
    corpus = build_content_corpus(source_root)
    excluded = set(files)
    helper_request = build_helper_request(source_root, entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    hmap = helper_map(helper_output)

    for entry in entries:
        helper_entry = hmap.get(entry["entry_key"])
        if helper_entry:
            entry["raw_json"]["helper"] = {
                "status": helper_entry.get("status"),
                "best_candidate": helper_entry.get("best_candidate"),
                "top_candidates": helper_entry.get("candidates", [])[:3],
                "debug": helper_entry.get("debug"),
            }
            best = helper_entry.get("best_candidate") or {}
            target_file = resolve_target_file(entry["entry_raw"], corpus, excluded) or best.get("file")
            entry["target_file_best"] = target_file
            entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))
            for ref in refs:
                if ref["entry_key"] == entry["entry_key"]:
                    ref["target_file"] = target_file
                    ref["target_file_probability"] = best.get("probability")
                    ref["raw_json"]["helper"] = {
                        "status": helper_entry.get("status"),
                        "best_candidate": best,
                        "top_candidates": helper_entry.get("candidates", [])[:3],
                    }
        else:
            entry["raw_json"]["helper"] = {"status": "missing"}
            target_file = resolve_target_file(entry["entry_raw"], corpus, excluded)
            entry["target_file_best"] = target_file
            for ref in refs:
                if ref["entry_key"] == entry["entry_key"]:
                    ref["target_file"] = target_file

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": [
                "PG052 ends with an ORDO RERUM contents table rather than a lexical index proper.",
            ],
        },
        "sections": [
            {
                "section_key": SECTION_KEY,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": 1,
                "section_kind": "ordo_rerum",
                "heading_raw": SECTION_HEADING_RAW,
                "heading_norm": SECTION_HEADING_NORM,
                "heading_letter": None,
                "page_start": 861,
                "page_end": 872,
                "file_start": files[0].as_posix(),
                "file_end": files[-1].as_posix(),
                "confidence": 0.97,
                "raw_json": {
                    "section_kind_reason": "Closing ORDO RERUM contents table spanning the tail pages of the volume.",
                    "file_window": [p.as_posix() for p in files],
                    "helper_entry_count": len(helper_request["entries"]),
                },
            }
        ],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and resolved page-bearing entries to target OCR files.",
            "evidence_files": [p.as_posix() for p in files],
        },
        "notes": [
            "The section is editorial contents material, not an alphabetical lemma index.",
            "Standalone Ibid. remissions were preserved at entry level and resolved conservatively to the previous explicit page reference.",
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "todo.json", {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PG052 ORDO RERUM payload assembled and validated",
        "completed": [
            "parsed OCR tail",
            "built helper request",
            "ran helper target resolution",
            "assembled canonical payload",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "No additional manual validation was provided for this volume.",
        ],
    })
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG052 ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
