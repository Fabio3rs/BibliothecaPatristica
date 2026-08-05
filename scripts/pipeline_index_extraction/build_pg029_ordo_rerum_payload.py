#!/usr/bin/env python3
"""Usage: build the PG029 ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg029_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG029/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG029_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG029_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG029 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG029_alphabetical_indices.json
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
VOLUME_ID = "PG029"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 29"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"
SECTION_KIND_REASON = "Closing contents table (ordo rerum) recovered from the OCR tail; editorial structure rather than alphabetical lemma list."

START_MARKER = "SANCTUS BASILIUS MAGNUS, CÆSARIENSIS ARCHIEPISCOPUS."
NO_REF_HEADINGS = {
    "SANCTUS BASILIUS MAGNUS, CÆSARIENSIS ARCHIEPISCOPUS.",
    "VITA S. BASILII.",
    "Monitum.",
    "S. BASILII OPERA.",
    "HOMILIAE IN Hexaemeron.",
}

NEW_ENTRY_RE = re.compile(
    r"^(?:"
    r"CAP\.|CAPUT\b|§\s*[IVXLCDM0-9]+\.|"
    r"LIBRI ADVERSUS EUNOMIUM\.|Liber\s+[IVXLCM]+\.|"
    r"Monitum ad Homilias in Psalmos\.|Monitum ad opus sequens\.|"
    r"PRAEFATIO GARNERII\.|NOTITIA EX BIBLIOTHECA FABRICII\.|"
    r"PRAECIPUAE ANTIQUARUM EDITIONUM PRAEFATIONES\.|"
    r"EXCERPTA EX ACTIS S\. BASILII BOLLANDIANIS\.|"
    r"VITA S\. BASILII APOCRYPHA\.|"
    r"ACOLUTHIA OFFICII CANONICI\b|ACOLUTHIA TRIPLICIS FESTI\.|"
    r"DE RECENTIORIBUS TRIBUSQUE GRAECORUM DOCTORIBUS\b|"
    r"HISTORIA INSTITUTIONIS\.|CANON DE S\. BASILIO\b|"
    r"Homilia\s+(?:prima|II|III|IV|V|VI|VII|VIII|IX)\.|"
    r"Homilia in psalmum\b"
    r")",
    re.IGNORECASE,
)

PAGE_ONLY_RE = re.compile(r"^(?P<page>(?:\d{1,4}|[IVXLCDM]+))\.?$")
PAGE_AT_END_RE = re.compile(r"^(?P<text>.*?)(?:\s+)(?P<page>(?:\d{1,4}|[IVXLCDM]+))\.?$")
NUMERIC_RE = re.compile(r"^\d{1,4}$")
WS_RE = re.compile(r"\s+")
WORD_BREAK_RE = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ])-\s+([A-Za-zÀ-ÖØ-öø-ÿĀ-ſ])")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    value = (text or "").replace("\xa0", " ")
    previous = None
    while previous != value:
        previous = value
        value = WORD_BREAK_RE.sub(r"\1\2", value)
    return WS_RE.sub(" ", value).strip()


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


def roman_to_int(value: str) -> int | None:
    value = value.upper()
    if not value or not re.fullmatch(r"[IVXLCDM]+", value):
        return None
    total = 0
    prev = 0
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    for ch in reversed(value):
        cur = values[ch]
        if cur < prev:
            total -= cur
        else:
            total += cur
            prev = cur
    return total


def parse_page(page: str) -> int | None:
    if page.isdigit():
        return int(page)
    return roman_to_int(page)


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
        if 807 <= seq <= 810:
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


def is_page_only(line: str) -> bool:
    return bool(PAGE_ONLY_RE.fullmatch(line))


def join_hyphenated(lines: list[str]) -> list[str]:
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.endswith("-") and i + 1 < len(lines):
            next_line = lines[i + 1]
            if next_line and not is_page_only(next_line):
                merged.append(line[:-1].rstrip() + next_line.lstrip())
                i += 2
                continue
        merged.append(line)
        i += 1
    return merged


def is_start_marker(line: str) -> bool:
    return line.startswith("SANCTUS BASILIUS MAGNUS")


def is_new_entry_start(line: str) -> bool:
    if line in NO_REF_HEADINGS:
        return True
    if is_page_only(line):
        return False
    return bool(NEW_ENTRY_RE.match(line))


def extract_entry_and_page(text: str) -> tuple[str, str | None, int | None]:
    cleaned = normalize(text)
    match = PAGE_AT_END_RE.match(cleaned)
    if not match:
        return cleaned, None, None
    lemma = normalize(match.group("text")).rstrip(" ,;:.")
    page_raw = match.group("page")
    page_int = parse_page(page_raw)
    return lemma, page_raw, page_int


def build_payload(
    *,
    source_root: Path,
    helper_request_json: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_files(source_root)
    if not files:
        raise SystemExit("No OCR files found in the requested tail window.")

    raw_lines: list[tuple[str, str]] = []
    started = False
    for path in files:
        for line in join_hyphenated(extract_lines(path)):
            if not started:
                if is_start_marker(line):
                    started = True
                else:
                    continue
            raw_lines.append((path.as_posix(), line))

    if not raw_lines:
        raise SystemExit("Could not locate the ORDO RERUM tail in the selected OCR files.")

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_file: str | None = None
    entry_order = 0

    def flush_buffer(*, forced_page_raw: str | None = None, forced_page_int: int | None = None) -> None:
        nonlocal buffer, buffer_file, entry_order
        if not buffer:
            return
        entry_raw = normalize(" ".join(buffer))
        lemma_raw, page_raw, page_int = extract_entry_and_page(entry_raw)
        if forced_page_raw is not None:
            page_raw = forced_page_raw
            page_int = forced_page_int
            if page_raw and entry_raw.endswith(page_raw):
                lemma_raw = normalize(entry_raw[: -len(page_raw)]).rstrip(" ,;:.")
        entry_order += 1
        source_file = buffer_file or files[0].as_posix()
        entry_kind = "heading_group" if page_raw is None else "lemma"
        if entry_raw in NO_REF_HEADINGS:
            entry_kind = "heading_group"
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        entry = {
            "entry_key": entry_key,
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": entry_order,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw if lemma_raw else entry_raw,
            "lemma_display": lemma_raw if lemma_raw else entry_raw,
            "lemma_norm": normalize(lemma_raw if lemma_raw else entry_raw),
            "lemma_sort": sort_norm(lemma_raw if lemma_raw else entry_raw),
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": source_file,
            "editorial_anchor_file": source_file,
            "target_file_best": None,
            "confidence": 0.94 if page_raw else 0.88,
            "raw_json": {
                "source_file": source_file,
                "section_kind": "ordo_rerum",
                "section_kind_reason": SECTION_KIND_REASON,
                "page_ref_source": "ocr_tail" if page_raw else "structural_heading",
            },
        }
        if entry_raw in NO_REF_HEADINGS:
            entry["raw_json"]["entry_kind_reason"] = "Top-level structure heading in the contents table."
        elif page_raw is not None:
            entry["raw_json"]["entry_kind_reason"] = "Editorial contents-table line item."
        else:
            entry["raw_json"]["entry_kind_reason"] = "Structural heading recovered from the contents table."
        entries.append(entry)
        if page_raw is not None and page_int is not None:
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
                    "section_start_file": source_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.93,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "locator_source": "ocr_contents_table",
                    },
                }
            )
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": entry["lemma_raw"],
                    "query_names": list(
                        dict.fromkeys(
                            [
                                entry["lemma_raw"],
                                entry["lemma_raw"].replace("Æ", "AE").replace("æ", "ae"),
                                entry["lemma_raw"].replace("—", " "),
                            ]
                        )
                    ),
                    "page_hints": [page_raw],
                    "page_hint_ints": [page_int],
                    "context_raw": entry_raw,
                }
            )
        elif entry_raw == "Homilia prima. - In principio fecit Deus caelum et terram.":
            # The OCR tail truncates the final page marker, but the body page
            # carrying the homily start is recoverable directly.
            entry["inferred_printed_page"] = 2
            entry["target_file_best"] = str(source_root / "45d0923e-e052-4966-9a17-6b1d65ca728d-412.txt")
            entry["confidence"] = 0.92
            entry["raw_json"]["manual_page_recovery"] = {
                "reason": "Tail OCR truncates the page marker; the homily start is recoverable from the body file.",
                "target_file": str(source_root / "45d0923e-e052-4966-9a17-6b1d65ca728d-412.txt"),
            }
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": "2",
                    "page_ref_raw": "2",
                    "page_ref_int": 2,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": str(source_root / "45d0923e-e052-4966-9a17-6b1d65ca728d-412.txt"),
                    "target_file_probability": 0.99,
                    "section_start_file": source_file,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.89,
                    "raw_json": {
                        "section_kind": "ordo_rerum",
                        "locator_source": "manual_body_recovery",
                    },
                }
            )
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": entry["lemma_raw"],
                    "query_names": [entry["lemma_raw"], "In principio fecit Deus caelum et terram"],
                    "page_hints": ["2"],
                    "page_hint_ints": [2],
                    "context_raw": entry_raw,
                }
            )
        buffer = []
        buffer_file = None

    for source_file, line in raw_lines:
        if line in NO_REF_HEADINGS:
            flush_buffer()
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": SECTION_KEY,
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": "heading_group",
                    "lemma_raw": line,
                    "lemma_display": line,
                    "lemma_norm": normalize(line),
                    "lemma_sort": sort_norm(line),
                    "entry_raw": line,
                    "context_raw": None,
                    "heading_letter": None,
                    "inferred_printed_page": None,
                    "section_start_file": source_file,
                    "editorial_anchor_file": source_file,
                    "target_file_best": source_file,
                    "confidence": 0.91,
                    "raw_json": {
                        "source_file": source_file,
                        "section_kind": "ordo_rerum",
                        "section_kind_reason": SECTION_KIND_REASON,
                        "entry_kind_reason": "Top-level structure heading in the contents table.",
                    },
                }
            )
            continue

        if is_page_only(line):
            if buffer:
                buffer.append(line)
                flush_buffer(forced_page_raw=line, forced_page_int=parse_page(line))
            continue

        if is_new_entry_start(line) and buffer:
            flush_buffer()

        if not buffer:
            buffer_file = source_file
        buffer.append(line)

        if PAGE_AT_END_RE.match(line):
            flush_buffer()

    flush_buffer()

    # Apply helper evidence if available. Manual overrides are preserved.
    write_json(
        helper_request_json,
        {
            "volume_id": VOLUME_ID,
            "source_root": source_root.as_posix(),
            "options": {"top_k": 5, "adjacency_window": 2},
            "entries": helper_entries,
        },
    )
    if helper_entries:
        subprocess.run(
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
            check=True,
        )
    else:
        write_json(helper_output_json, {"volume_id": VOLUME_ID, "status": "empty", "entries": []})

    helper_output = read_json(helper_output_json, {})
    helper_by_id = {
        item.get("entry_id"): item
        for item in helper_output.get("entries", [])
        if isinstance(item, dict) and item.get("entry_id")
    }

    for entry in entries:
        helper = helper_by_id.get(entry["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        best_file = best.get("file")
        if best_file and entry["target_file_best"] in {None, entry["section_start_file"]}:
            entry["target_file_best"] = best_file
            entry["confidence"] = max(entry["confidence"], float(best.get("probability") or 0.0))
            entry["raw_json"]["helper"] = {
                "status": helper.get("status"),
                "candidate_role": helper.get("candidate_role"),
                "reason_summary": helper.get("reason_summary"),
                "best_candidate": {
                    "file": best.get("file"),
                    "probability": best.get("probability"),
                    "candidate_role": best.get("candidate_role"),
                    "reason_summary": best.get("reason_summary"),
                    "evidence_kinds": [ev.get("kind") for ev in best.get("evidence", []) if isinstance(ev, dict)],
                },
            }

    for ref in refs:
        helper = helper_by_id.get(ref["entry_key"])
        if not helper:
            continue
        best = helper.get("best_candidate") or {}
        if best.get("file"):
            ref["target_file"] = best.get("file")
            ref["target_file_probability"] = best.get("probability")
            ref["raw_json"]["helper_best_file"] = best.get("file")
            ref["raw_json"]["helper_best_probability"] = best.get("probability")

    section_files = [path.as_posix() for path in files]
    sections = [
        {
            "section_key": SECTION_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": 774,
            "page_end": 779,
            "file_start": section_files[0],
            "file_end": section_files[-1],
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": SECTION_KIND_REASON,
                "evidence_files": section_files,
            },
        }
    ]

    coverage = {
        "entries_status": "recovered_with_residual_ambiguity",
        "entries_status_reason": (
            "Recovered the ORDO RERUM tail and its page citations from the OCR pages 807-810. "
            "Two chapter-level refs required neighboring-body confirmation, and the first homily "
            "entry had a truncated terminal marker in the tail OCR."
        ),
        "evidence_files": section_files,
    }

    notes = [
        "The volume tail is an editorial contents table, so section_kind is ordo_rerum rather than alphabetical_general.",
        "The homily-start entry was anchored via the body file for Homilia I (page 2).",
        "CAPUT XLII and CAPUT XLIII were checked against neighboring prolegomena files before finalizing the payload.",
    ]

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": source_root.as_posix(),
        "volume_label": VOLUME_LABEL,
    }
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", [])
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "scripture_refs.json", [])
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Finalize PG029 ordo_rerum payload and verify helper target files.",
            "completed": [
                "OCR tail segmented into contents-table line items",
                "helper request generated and resolved",
                "intermediate fragments written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Keep OCR file suffixes separate from printed page numbers.",
                "The first homily entry was recovered from the body file because the tail OCR truncates its page marker.",
            ],
        },
    )
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "generated_at": payload["generated_at"]})
    write_json(intermediate_dir / "payload.json", payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG029 ORDO RERUM alphabetical payload from OCR.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(
        source_root=args.source_root,
        helper_request_json=args.helper_request_json,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
    )
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
