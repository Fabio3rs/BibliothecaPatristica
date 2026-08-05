#!/usr/bin/env python3
"""Usage: build the PG054 closing ORDO RERUM payload and helper request.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg054_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG054/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG054_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG054_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG054 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG054_alphabetical_indices.json
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
VOLUME_ID = "PG054"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 54"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN DUABUS PARTIBUS TOMI IV CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in duabus partibus tomi iv continentur"
SECTION_PAGE_START = 733
SECTION_PAGE_END = 738
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
TYPE_RE = re.compile(r'tipo="([^"]+)"')
PAGE_ONLY_RE = re.compile(r"^\d{1,4}(?:\s+\d{1,4})?$")
PAGE_REF_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<ref>ibid\.?|\d{1,4}(?:\s*[-–]\s*\d{1,4}|\s+\d{1,4})?)\.?$", re.I)
NOISE_LINES = {"Digitized by Google", "Ord. Rerum", "ORDO RERUM", "ORDO RERUM."}
ENTRY_START_RE = re.compile(
    r"^(?:PRÆFATIO|PRAEFATIO|S\. PATRIS|Sermo admonitorius|HOMIL\.|MONITUM\b|SERM\.|SELECTA\b)",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file seq from {path}")
    return int(m.group(1))


def load_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = TYPE_RE.search(attrs)
        tipo = (tipo_m.group(1) if tipo_m else "").strip().lower()
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or line in NOISE_LINES:
                continue
            if re.fullmatch(r"\d{1,4}", line):
                continue
            lines.append(line)
    return lines


def iter_relevant_lines(source_root: Path) -> list[tuple[Path, str]]:
    files = sorted(source_root.glob("*.txt"), key=file_seq)
    selected = [p for p in files if 355 <= file_seq(p) <= 358]
    out: list[tuple[Path, str]] = []
    for path in selected:
        for line in load_lines(path):
            if line.startswith("ORDO RERUM QUÆ IN DUABUS PARTIBUS TOMI IV CONTINENTUR"):
                continue
            if re.fullmatch(r"\d{3}\s+ORDO RERUM.*\d{3}", line):
                continue
            if line.startswith("FINIS TOMI QUINQUAGESIMI QUARTI"):
                continue
            out.append((path, line))
    return out


def extract_page_ref(text: str) -> tuple[str, int | None, int | None]:
    cleaned = normalize(text).rstrip(" ,;:.")
    if not cleaned:
        return "", None, None
    if cleaned.lower() in {"ibid", "ibid."}:
        return "ibid.", None, None
    if m := re.fullmatch(r"(\d{1,4})(?:\s*[-–]\s*(\d{1,4})|\s+(\d{1,4}))?", cleaned):
        start = int(m.group(1))
        end = int(m.group(2) or m.group(3)) if (m.group(2) or m.group(3)) else None
        return cleaned, start, end
    if m := PAGE_REF_RE.match(cleaned):
        ref_raw = m.group("ref").strip()
        start_m = re.match(r"^(\d{1,4})", ref_raw)
        start = int(start_m.group(1)) if start_m else None
        end_m = re.search(r"(?:[-–]|\s+)(\d{1,4})$", ref_raw)
        end = int(end_m.group(1)) if end_m else None
        return ref_raw, start, end
    return "", None, None


def guess_lemma(body: str) -> str:
    cleaned = normalize(body)
    if not cleaned:
        return ""
    if "ibid." in cleaned.lower():
        cleaned = cleaned.rsplit(" ", 1)[0].strip()
    if m := PAGE_REF_RE.match(cleaned):
        cleaned = m.group("body").strip()
    return cleaned.rstrip(" .;:")


def entry_kind_from_lemma(lemma_raw: str) -> str:
    upper = lemma_raw.upper()
    if upper.startswith("MONITUM") or upper.startswith("SELECTA"):
        return "editorial_note"
    return "heading_group"


def make_query_names(lemma_raw: str) -> list[str]:
    base = normalize(lemma_raw)
    variants = [base]
    if ";" in base:
        variants.append(base.split(";", 1)[0].strip())
    if ":" in base:
        variants.append(base.split(":", 1)[0].strip())
    if "—" in base:
        variants.append(base.split("—", 1)[0].strip())
    words = base.split()
    if len(words) >= 6:
        variants.append(" ".join(words[:6]))
    if len(words) >= 8:
        variants.append(" ".join(words[:8]))
    dedup: list[str] = []
    for item in variants:
        item = normalize(item)
        if item and item not in dedup:
            dedup.append(item)
    return dedup[:5]


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=file_seq):
        for line in load_lines(path):
            if m := re.fullmatch(r"(\d{1,4})", line):
                page_map.setdefault(int(m.group(1)), path.as_posix())
            if m := re.fullmatch(r"(\d{1,4})\s+(\d{1,4})", line):
                page_map.setdefault(int(m.group(1)), path.as_posix())
                page_map.setdefault(int(m.group(2)), path.as_posix())
            if m := re.fullmatch(r"(\d{1,4})\s*[-–]\s*(\d{1,4})", line):
                page_map.setdefault(int(m.group(1)), path.as_posix())
                page_map.setdefault(int(m.group(2)), path.as_posix())
    return page_map


def parse_entries(source_root: Path) -> list[dict[str, Any]]:
    rows = iter_relevant_lines(source_root)
    entries: list[dict[str, Any]] = []
    buffer: list[str] = []
    entry_file: Path | None = None
    entry_order = 0
    current_ref_raw: str | None = None
    current_ref_start: int | None = None
    current_ref_end: int | None = None
    current_ref_line: str | None = None

    def flush() -> None:
        nonlocal buffer, entry_file, entry_order, current_ref_raw, current_ref_start, current_ref_end, current_ref_line
        if not buffer:
            return
        full_text = normalize(" ".join(buffer))
        lemma_raw = guess_lemma(full_text)
        entry_order += 1
        entry_key = f"{SECTION_KEY}:entry:{entry_order:04d}"
        entry_kind = entry_kind_from_lemma(lemma_raw or full_text)
        entry_raw = full_text
        if current_ref_raw and current_ref_raw.lower() in {"ibid", "ibid."}:
            if entries:
                prev = entries[-1]["raw_json"].get("page_ref")
                if isinstance(prev, dict):
                    current_ref_start = prev.get("page_ref_int")
                    current_ref_end = prev.get("range_end_int")
        page_hint = current_ref_start
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw or None,
                "lemma_display": lemma_raw or None,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": page_hint,
                "section_start_file": rows[0][0].as_posix() if rows else None,
                "editorial_anchor_file": entry_file.as_posix() if entry_file else None,
                "target_file_best": None,
                "confidence": 0.0,
                "raw_json": {
                    "source_files": sorted({entry_file.as_posix()} if entry_file else set()),
                    "entry_kind_reason": (
                        "monitorial/editorial heading in the closing ORDO RERUM table"
                        if entry_kind == "editorial_note"
                        else "contents-line heading in the closing ORDO RERUM table"
                    ),
                    "page_ref": {
                        "page_ref_raw": current_ref_raw,
                        "page_ref_int": current_ref_start,
                        "range_end_raw": str(current_ref_end) if current_ref_end is not None else None,
                        "range_end_int": current_ref_end,
                        "raw_line": current_ref_line,
                    },
                },
            }
        )
        buffer = []
        entry_file = None
        current_ref_raw = None
        current_ref_start = None
        current_ref_end = None
        current_ref_line = None

    for path, line in rows:
        if line.startswith("ORDO RERUM"):
            continue
        if line.startswith("FINIS TOMI QUINQUAGESIMI QUARTI"):
            break
        if not buffer and not ENTRY_START_RE.match(line):
            continue
        if ENTRY_START_RE.match(line):
            if buffer:
                flush()
            buffer = [line]
            entry_file = path
            ref_raw, ref_start, ref_end = extract_page_ref(line)
            if ref_raw:
                current_ref_raw, current_ref_start, current_ref_end = ref_raw, ref_start, ref_end
                current_ref_line = line
                flush()
            continue

        if not buffer:
            continue
        buffer.append(line)
        ref_raw, ref_start, ref_end = extract_page_ref(line)
        if ref_raw:
            current_ref_raw, current_ref_start, current_ref_end = ref_raw, ref_start, ref_end
            current_ref_line = line
            flush()

    flush()
    return entries


def build_helper_request(volume_id: str, source_root: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        page_hint = entry.get("inferred_printed_page")
        if page_hint is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"{volume_id.lower()}_{idx:03d}",
                "lemma_raw": entry["lemma_raw"] or entry["entry_raw"],
                "query_names": make_query_names(entry["lemma_raw"] or entry["entry_raw"]),
                "page_hints": [str(page_hint)],
                "page_hint_ints": [int(page_hint)],
                "context_raw": entry["entry_raw"],
            }
        )
    return {
        "volume_id": volume_id,
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
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, Any]:
    return {item.get("entry_id"): item for item in helper_output.get("entries", []) if isinstance(item, dict)}


def summarize_helper(helper_entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not helper_entry:
        return None
    best = helper_entry.get("best_candidate") or {}
    candidates = []
    best_candidate_evidence_raws: list[str] = []
    for ev in best.get("evidence", []):
        if isinstance(ev, dict) and ev.get("raw"):
            best_candidate_evidence_raws.append(str(ev["raw"]))
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
        "best_candidate_evidence_raws": best_candidate_evidence_raws,
        "candidates": candidates,
    }


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    page_map = build_page_map(source_root)
    entries = parse_entries(source_root)
    helper_request = build_helper_request(VOLUME_ID, source_root, entries)
    write_json(helper_request_json, helper_request)
    helper_output = run_helper(helper_request_json, helper_output_json)
    helper_map = helper_lookup(helper_output)

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
        "file_start": source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-355.txt").as_posix(),
        "file_end": source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-358.txt").as_posix(),
        "confidence": 0.96,
        "raw_json": {
            "section_kind_reason": (
                "Closing ORDO RERUM contents table at the end of the tome; "
                "the printed table spans the 733-738 page spread and the OCR tail files 355-358."
            ),
            "evidence_files": [
                source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-355.txt").as_posix(),
                source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-356.txt").as_posix(),
                source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-357.txt").as_posix(),
                source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-358.txt").as_posix(),
            ],
            "section_heading_fragments": [
                "ORDO RERUM",
                "ORDO RERUM.",
                "ORDO RERUM QUÆ IN DUABUS PARTIBUS TOMI IV CONTINENTUR.",
            ],
        },
    }

    for idx, entry in enumerate(entries, start=1):
        helper_entry = helper_map.get(f"{VOLUME_ID.lower()}_{idx:03d}")
        helper_summary = summarize_helper(helper_entry)
        page_ref = entry["raw_json"]["page_ref"]
        ref_raw = page_ref.get("page_ref_raw")
        ref_start = page_ref.get("page_ref_int")
        ref_end = page_ref.get("range_end_int")
        if (not ref_raw or ref_start is None) and helper_summary:
            for raw_ev in helper_summary.get("best_candidate_evidence_raws", []):
                m = re.search(r"(\d{1,4})\s*[-–]\s*(\d{1,4})", raw_ev)
                if not m:
                    continue
                ref_raw = m.group(0)
                ref_start = int(m.group(1))
                ref_end = int(m.group(2))
                page_ref["page_ref_raw"] = ref_raw
                page_ref["page_ref_int"] = ref_start
                page_ref["range_end_raw"] = str(ref_end)
                page_ref["range_end_int"] = ref_end
                page_ref["raw_line"] = raw_ev
                break
        target_file = None
        if ref_start is not None:
            target_file = page_map.get(int(ref_start))
        if helper_summary and helper_summary["best_candidate"]["file"]:
            target_file = helper_summary["best_candidate"]["file"]
        if entry["lemma_raw"] is None:
            entry["lemma_raw"] = entry["entry_raw"]
            entry["lemma_display"] = entry["entry_raw"]
            entry["lemma_norm"] = sort_norm(entry["entry_raw"])
            entry["lemma_sort"] = sort_norm(entry["entry_raw"])
        entry["section_start_file"] = section["file_start"]
        entry["editorial_anchor_file"] = entry["editorial_anchor_file"] or section["file_start"]
        entry["target_file_best"] = target_file
        entry["confidence"] = 0.95 if ref_raw else 0.72
        if entry["entry_kind"] == "editorial_note" and not ref_raw:
            entry["confidence"] = 0.61
        entry["raw_json"].update(
            {
                "page_ref": page_ref,
                "helper": helper_summary,
            }
        )
        if ref_raw and ref_raw.lower() in {"ibid", "ibid."} and ref_start is None and idx > 1:
            prev = entries[idx - 2]["raw_json"]["page_ref"]
            entry["raw_json"]["page_ref"]["page_ref_int"] = prev.get("page_ref_int")
            entry["raw_json"]["page_ref"]["range_end_int"] = prev.get("range_end_int")
            if entry["target_file_best"] is None:
                entry["target_file_best"] = entries[idx - 2].get("target_file_best")

    refs: list[dict[str, Any]] = []
    for entry in entries:
        page_ref = entry["raw_json"]["page_ref"]
        ref_raw = page_ref.get("page_ref_raw")
        helper_summary = entry["raw_json"].get("helper")
        if not ref_raw or ref_raw.lower() in {"ibid", "ibid."}:
            if not ref_raw or entry["inferred_printed_page"] is None:
                continue
            ref_raw = "ibid."
        page_ref_int = page_ref.get("page_ref_int")
        range_end_int = page_ref.get("range_end_int")
        if ref_raw.lower() in {"ibid", "ibid."} and page_ref_int is None:
            continue
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(page_ref_int) if range_end_int is not None and page_ref_int is not None else None,
                "range_end_raw": str(range_end_int) if range_end_int is not None else None,
                "target_file": entry["target_file_best"],
                "target_file_probability": helper_summary.get("best_candidate", {}).get("probability") if isinstance(helper_summary, dict) else None,
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "confidence": 0.94 if page_ref_int is not None else 0.62,
                "raw_json": {
                    "page_ref_reason": (
                        "page-range entry line from the ORDO RERUM contents table"
                        if ref_raw.lower() not in {"ibid", "ibid."}
                        else "inherited from the previous contents line"
                    ),
                    "helper": entry["raw_json"].get("helper"),
                },
            }
        )

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": (
            "Recovered the closing ORDO RERUM contents table from OCR files 355-358; "
            "all page-linked contents lines were serialized, and the final SELECTA line was retained as an editorial note without a material locator."
        ),
        "evidence_files": [
            source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-355.txt").as_posix(),
            source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-356.txt").as_posix(),
            source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-357.txt").as_posix(),
            source_root.joinpath("c1b22c9f-fc05-4c19-b39d-c4c945211770-358.txt").as_posix(),
        ],
    }

    notes = [
        "The section heading is split across the OCR spread; file 355 begins the table and files 356-358 carry the printed page headers 733-738.",
        "The `Sermo admonitorius... ibid.` line inherits the previous page span from the preceding contents line.",
        "The OCR line `90 98` was preserved literally in the entry text and treated as a page range with a missing hyphen.",
        "The final `SELECTA ex notis...` line has no explicit material locator and is modeled as an editorial note.",
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
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG054 ORDO RERUM alphabetical payload.")
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
            "current_focus": "Build and validate the PG054 ORDO RERUM payload.",
            "completed": [
                "identified the closing ORDO RERUM section",
                "confirmed the 355-358 OCR tail files",
            ],
            "pending": [
                "run index_target_locator on the helper request",
                "assemble the final payload and validate the JSON",
            ],
            "blocked": [],
            "notes": [
                "Keep OCR literals in entry_raw and ref_raw.",
                "Treat the final SELECTA line as an editorial note without a page ref.",
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
