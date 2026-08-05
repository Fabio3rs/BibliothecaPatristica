#!/usr/bin/env python3
"""Usage: build the PL180 closing ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl180_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL180/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL180_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL180_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL180 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL180_alphabetical_indices.json

The OCR tail contains the final contents table for the volume rather than a
lexical index proper. The script recovers the page-bearing lines, builds a
helper request for material target resolution, and writes the canonical JSON
payload plus intermediate checkpoints.
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
VOLUME_ID = "PL180"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 180"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_START_SEQ = 836
SECTION_END_SEQ = 850
SECTION_PAGE_START = 1659
SECTION_PAGE_END = 1688

TEXT_BLOCK_RE = re.compile(r"<bloco(?P<attrs>[^>]*)>(?P<body>.*?)</bloco>", re.S)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.I)
PAGE_ONLY_RE = re.compile(r"^\d{1,4}\.?$")
PAGE_TRAIL_RE = re.compile(r"^(?P<body>.*?)(?:\s+)(?P<page>\d{1,4})\.?$")

ROLE_MARKERS = (
    "ABBAS",
    "ABBATIS",
    "ABBATEM",
    "EPISCOPUS",
    "EPISCOPI",
    "MONACHUS",
    "PONTIFEX",
    "ARCHIEPISCOPUS",
    "CANONICVS",
    "SCHOLASTICVS",
    "DECANUS",
    "PAPA",
    "REGIS",
    "DUX",
    "COMES",
)

KNOWN_NODE_LINES = {
    "ORDO RERUM",
    "ORDU RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
    "Notitia.",
    "Monitum in tractatum sequentem.",
    "Præfatio.",
    "Praefatio.",
    "Prologus.",
    "LIBER PRIMUS.",
    "LIBER SECUNDUS.",
    "LIBER TERTIUS.",
    "PARS SECUNDA.",
    "PARS TERTIA.",
    "EPISTOLA.",
    "SERMONES.",
    "FINIS TOMI CENTESIMI OCTOGESIMI.",
}


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
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file sequence from {path}")
    return int(m.group(1))


def load_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for match in TEXT_BLOCK_RE.finditer(raw):
        attrs = match.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = (tipo_m.group(1).strip().lower() if tipo_m else "")
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
        for raw_line in body.splitlines():
            line = normalize(raw_line)
            if not line or FOOTER_RE.fullmatch(line) or line == "----------------------------------------------------------------":
                continue
            lines.append(line)
    return lines


def is_section_heading(line: str) -> bool:
    upper = line.upper()
    return "ORDO RERUM" in upper or "QUAE IN HOC TOMO CONTINENTUR" in upper or "QUÆ IN HOC TOMO CONTINENTUR" in upper


def is_node_like(line: str) -> bool:
    if line in KNOWN_NODE_LINES:
        return True
    lower = line.lower()
    if lower.startswith("relatio pro monasterio rotensi"):
        return True
    if lower.startswith("ulgerii testamentum"):
        return True
    if lower.startswith("marbodi redonensis epitaphium"):
        return True
    if lower.startswith("ocgarius lucidii abbas"):
        return True
    if lower.startswith("anulfus de bobbis"):
        return True
    if lower.startswith("speculum monachorum"):
        return True
    if lower.startswith("eugenius iii pontifex romanus"):
        return True
    if lower.startswith("notitia historica"):
        return True
    if lower.startswith("observatio praevia"):
        return True
    if lower.startswith("monitum in sequens opusculum"):
        return True
    words = line.split()
    if len(words) <= 4 and line.upper() == line and not any(ch.isdigit() for ch in line):
        if any(marker in line for marker in ROLE_MARKERS):
            return True
        if line.endswith("."):
            return True
    if len(words) <= 2 and line.endswith(".") and line[0].isupper():
        return True
    return False


def is_fragment_like(line: str) -> bool:
    if line.endswith(("-", ",", ";")):
        return True
    if line.endswith((" DE", " ET", " AD")):
        return True
    if line.startswith(("CAP.", "Epist.", "EPIST.", "LIBER", "TRACTATUS", "NARRATIO", "DISPUTATIO", "MEDITATIO", "DE ", "VISIO", "EXPOSITIO")):
        return True
    return len(line) > 25


def split_page_ref(line: str) -> tuple[str, str | None, int | None]:
    if PAGE_ONLY_RE.fullmatch(line):
        page = re.sub(r"\D", "", line)
        return "", page, int(page)
    m = PAGE_TRAIL_RE.match(line)
    if not m:
        return line, None, None
    body = normalize(m.group("body").rstrip(" ,;:."))
    page = m.group("page")
    return body, page, int(page)


def locate_section_files(source_root: Path) -> list[Path]:
    files = [p for p in sorted(source_root.glob("*.txt"), key=page_seq) if SECTION_START_SEQ <= page_seq(p) <= SECTION_END_SEQ]
    if not files:
        raise SystemExit(f"no OCR files found for PL180 in expected window {SECTION_START_SEQ}-{SECTION_END_SEQ}")
    return files


def extract_section_lines(files: list[Path]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in files:
        for line in load_lines(path):
            out.append((path.as_posix(), line))
    return out


def emit_node(nodes: list[dict[str, Any]], *, section_key: str, node_order: int, label_raw: str, source_file: str, parent_node_key: str | None) -> dict[str, Any]:
    node_key = f"{VOLUME_ID}:node:{node_order:03d}"
    upper = label_raw.upper()
    node_level = 1 if (
        label_raw in KNOWN_NODE_LINES
        or any(marker in upper for marker in ROLE_MARKERS)
        or label_raw.startswith(("RELATIO ", "ULGERII ", "OCGARIUS ", "ANULFUS ", "SPECULUM ", "FINIS "))
    ) else 2
    nodes.append(
        {
            "node_key": node_key,
            "section_key": section_key,
            "parent_node_key": parent_node_key,
            "node_order": node_order,
            "node_kind": "rubric_group",
            "label_raw": label_raw,
            "label_norm": sort_norm(label_raw),
            "label_sort": sort_norm(label_raw),
            "node_level": node_level,
            "confidence": 0.95 if node_level == 1 else 0.9,
            "raw_json": {
                "source_file": source_file,
                "role": "toc_heading",
            },
        }
    )
    return {"node_key": node_key, "node_level": node_level}


def parse_items(section_lines: list[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    node_order = 0
    entry_order = 0
    current_parent: str | None = None
    buffer_lines: list[str] = []
    buffer_file: str | None = None

    def flush_buffer_as_entry(page_raw: str | None, page_int: int | None, source_file: str) -> None:
        nonlocal buffer_lines, buffer_file, entry_order, current_parent
        if not buffer_lines and page_raw is None:
            return
        text = normalize(" ".join([part for part in buffer_lines if part]))
        if page_raw is not None and text:
            entry_raw = f"{text} {page_raw}".strip()
            lemma_raw = text
        elif page_raw is not None:
            entry_raw = page_raw
            lemma_raw = None
        else:
            entry_raw = text
            lemma_raw = text or None
        entry_order += 1
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        items.append(
            {
                "entry_key": entry_key,
                "section_key": SECTION_KEY,
                "parent_node_key": current_parent,
                "entry_order": entry_order,
                "entry_kind": "heading_group",
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": sort_norm(lemma_raw),
                "lemma_sort": sort_norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": page_int,
                "section_start_file": section_lines[0][0],
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": 0.72 if page_int is not None else 0.56,
                "raw_json": {
                    "source_file": source_file,
                    "page_ref_raw": page_raw,
                    "page_ref_int": page_int,
                    "note": "Contents-table line recovered from OCR tail.",
                },
            }
        )
        buffer_lines = []
        buffer_file = None

    for source_file, line in section_lines:
        if not line or re.fullmatch(r"\d{4}", line):
            continue

        if is_section_heading(line):
            # Preserve the section heading in evidence but do not emit it as an entry.
            continue

        page_body, page_raw, page_int = split_page_ref(line)
        if page_raw is not None and page_body == "":
            if buffer_lines:
                flush_buffer_as_entry(page_raw, page_int, source_file)
            continue

        if page_raw is not None:
            if buffer_lines:
                buffer_lines.append(page_body)
                flush_buffer_as_entry(page_raw, page_int, source_file)
            else:
                entry_order += 1
                entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
                items.append(
                    {
                        "entry_key": entry_key,
                        "section_key": SECTION_KEY,
                        "parent_node_key": current_parent,
                        "entry_order": entry_order,
                        "entry_kind": "heading_group",
                        "lemma_raw": page_body or None,
                        "lemma_display": page_body or None,
                        "lemma_norm": sort_norm(page_body),
                        "lemma_sort": sort_norm(page_body),
                        "entry_raw": line,
                        "context_raw": line,
                        "heading_letter": None,
                        "inferred_printed_page": page_int,
                        "section_start_file": section_lines[0][0],
                        "editorial_anchor_file": source_file,
                        "target_file_best": None,
                        "confidence": 0.72,
                        "raw_json": {
                            "source_file": source_file,
                            "page_ref_raw": page_raw,
                            "page_ref_int": page_int,
                            "note": "Page-bearing OCR line from contents table.",
                        },
                    }
                )
            current_parent = current_parent
            continue

        if is_node_like(line):
            if buffer_lines:
                # If the buffer looks like a header fragment, promote it to a node.
                if len(buffer_lines) == 1 and is_node_like(buffer_lines[0]):
                    pass
                else:
                    flush_buffer_as_entry(None, None, source_file)
            node_order += 1
            node_info = emit_node(
                nodes,
                section_key=SECTION_KEY,
                node_order=node_order,
                label_raw=line,
                source_file=source_file,
                parent_node_key=current_parent,
            )
            current_parent = node_info["node_key"]
            continue

        if buffer_file is None:
            buffer_file = source_file
        buffer_lines.append(line)

    if buffer_lines:
        flush_buffer_as_entry(None, None, buffer_file or section_lines[-1][0])

    return items, nodes


def helper_entry_for(item: dict[str, Any]) -> dict[str, Any] | None:
    page_int = item.get("inferred_printed_page")
    if page_int is None:
        return None
    lemma_raw = item.get("lemma_raw") or item.get("entry_raw")
    query_names = [normalize(lemma_raw)]
    stripped = normalize(lemma_raw).rstrip(" .;:,")
    if stripped and stripped not in query_names:
        query_names.append(stripped)
    if page_int is not None:
        return {
            "entry_id": item["entry_key"].replace(":", "_").lower(),
            "lemma_raw": lemma_raw,
            "query_names": [q for q in query_names if q],
            "page_hints": [str(page_int)],
            "page_hint_ints": [page_int],
            "context_raw": item.get("entry_raw") or item.get("context_raw") or "",
        }
    return None


def build_helper_request(source_root: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [entry for item in items if (entry := helper_entry_for(item)) is not None]
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
            str(ROOT / "scripts" / "index_target_locator.py"),
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
    source_root: Path,
    section_files: list[Path],
    items: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    helper_output: dict[str, Any],
) -> dict[str, Any]:
    helper_map = helper_index(helper_output)
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for item in items:
        entry_key = item["entry_key"]
        helper_entry = helper_map.get(entry_key.replace(":", "_").lower()) or {}
        best = helper_entry.get("best_candidate") or {}
        candidates = helper_entry.get("candidates") or []
        target_file = best.get("file") or item.get("editorial_anchor_file")
        target_prob = best.get("probability")
        status = helper_entry.get("status")
        confidence = 0.9 if target_file and status == "resolved" else 0.75 if target_file else 0.58
        item["target_file_best"] = target_file
        item["confidence"] = confidence
        item["raw_json"] = {
            **item.get("raw_json", {}),
            "helper": {
                "status": status,
                "candidate_role": best.get("candidate_role"),
                "reason_summary": best.get("reason_summary"),
                "best_candidate": best if best else None,
                "top_candidates": [
                    {
                        "file": cand.get("file"),
                        "probability": cand.get("probability"),
                        "candidate_role": cand.get("candidate_role"),
                        "reason_summary": cand.get("reason_summary"),
                        "evidence_kinds": [ev.get("kind") for ev in cand.get("evidence", []) if isinstance(ev, dict)],
                    }
                    for cand in candidates[:3]
                ],
            },
        }
        entries.append(item)
        if item.get("inferred_printed_page") is not None:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": item["raw_json"].get("page_ref_raw") or str(item["inferred_printed_page"]),
                    "page_ref_raw": item["raw_json"].get("page_ref_raw") or str(item["inferred_printed_page"]),
                    "page_ref_int": item["inferred_printed_page"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": section_files[0].as_posix(),
                    "editorial_anchor_file": item["editorial_anchor_file"],
                    "confidence": confidence - 0.03 if confidence > 0.03 else confidence,
                    "raw_json": {
                        "source_file": item["raw_json"].get("source_file"),
                        "helper": helper_entry,
                    },
                }
            )

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
        "file_start": section_files[0].as_posix(),
        "file_end": section_files[-1].as_posix(),
        "confidence": 0.96,
        "raw_json": {
            "section_kind_reason": "final_volume_ordo_rerum_closure",
            "evidence_files": [section_files[0].as_posix(), section_files[-1].as_posix()],
            "note": "The final contents table spans the closing OCR tail and cites pages throughout the volume.",
        },
    }

    coverage = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered the closing ORDO RERUM contents table and resolved page-bearing lines to target OCR files.",
        "evidence_files": [section_files[0].as_posix(), section_files[-1].as_posix()],
    }
    notes = [
        {
            "note_key": f"{VOLUME_ID}:note:001",
            "note_type": "extraction",
            "text": "PL180 ends with an ORDO RERUM contents table rather than a lexical alphabetical index proper.",
        },
        {
            "note_key": f"{VOLUME_ID}:note:002",
            "note_type": "extraction",
            "text": "Some OCR rows collapse adjacent contents lines; the payload keeps the OCR literal text and records helper evidence in raw_json.",
        },
    ]

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": source_root.as_posix(),
            "volume_label": VOLUME_LABEL,
            "notes": "The tail OCR is a closing contents table modeled as `ordo_rerum`.",
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def write_intermediates(intermediate_dir: Path, payload: dict[str, Any], helper_request: dict[str, Any], helper_output: dict[str, Any], section_files: list[Path]) -> None:
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(intermediate_dir / "helper_output.json", helper_output)
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", payload["sections"])
    write_json(intermediate_dir / "nodes.json", payload["nodes"])
    write_json(intermediate_dir / "entries.json", payload["entries"])
    write_json(intermediate_dir / "refs.json", payload["refs"])
    write_json(intermediate_dir / "scripture_refs.json", payload["scripture_refs"])
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PL180 ORDO RERUM payload assembled and validated",
            "completed": [
                "tail OCR parsed",
                "helper request built and resolved",
                "canonical payload assembled",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR literals intact.",
                f"Evidence files: {section_files[0].name} .. {section_files[-1].name}",
            ],
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL180 closing ORDO RERUM payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    section_files = locate_section_files(args.source_root)
    section_lines = extract_section_lines(section_files)
    items, nodes = parse_items(section_lines)
    helper_request = build_helper_request(args.source_root, items)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    payload = build_payload(args.source_root, section_files, items, nodes, helper_output)
    write_intermediates(args.intermediate_dir, payload, helper_request, helper_output, section_files)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output_file.write_text(encoded + ("\n" if not encoded.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    main()
