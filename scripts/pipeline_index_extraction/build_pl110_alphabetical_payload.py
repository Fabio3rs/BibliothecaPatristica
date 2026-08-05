#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/build_pl110_alphabetical_payload.py
# Builds the PL110 closing ORDO RERUM payload, runs the target locator helper, and writes the final JSON.

from __future__ import annotations

import json
import re
import subprocess
import sys
import html
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL110"
COLLECTION = "PL"
SOURCE_ROOT = ROOT / "teste/PL110/text"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL110_alphabetical_indices.json"
HELPER_REQUEST_JSON = ROOT / "data/alphabetical_index_payloads/PL110_helper_request.json"
HELPER_OUTPUT_JSON = ROOT / "data/alphabetical_index_payloads/PL110_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL110"
TODO_JSON = INTERMEDIATE_DIR / "todo.json"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts/index_target_locator.py"

SECTION_KEY = "PL110:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "Ordo rerum quae in hoc tomo continentur."
SECTION_START_FILE = str(SOURCE_ROOT / "1a6c4a5b-8907-4b05-9e22-7a3afb9c9911-607.txt")
SECTION_END_FILE = str(SOURCE_ROOT / "1a6c4a5b-8907-4b05-9e22-7a3afb9c9911-610.txt")
CONTENT_FILES = [
    SOURCE_ROOT / "1a6c4a5b-8907-4b05-9e22-7a3afb9c9911-607.txt",
    SOURCE_ROOT / "1a6c4a5b-8907-4b05-9e22-7a3afb9c9911-608.txt",
    SOURCE_ROOT / "1a6c4a5b-8907-4b05-9e22-7a3afb9c9911-609.txt",
    SOURCE_ROOT / "1a6c4a5b-8907-4b05-9e22-7a3afb9c9911-610.txt",
]

PAGE_RE = re.compile(r"^(?P<lemma>.*?)(?:\s+)(?P<page>\d{1,4})$")
ROMAN_HEAD_RE = re.compile(r"^(?:[IVXLCDM]+|\d+)\.?$")
NOISE_RE = re.compile(r"^(?:Digitized by Google|THIS VOLUME DOES NOT CIRCULATE OUTSIDE THE LIBRARY)$", re.IGNORECASE)

SPECIAL_ENTRIES: list[dict[str, Any]] = [
    {
        "title": "COMMENTARIA IN EZECHIELEM.",
        "page": 494,
        "entry_kind": "heading_group",
        "query_names": ["Commentaria in Ezechielem", "Ezechielem", "Ezechiel"],
        "note": "Major section heading recovered from OCR and anchored to the commentary opener.",
    },
    {
        "title": "QUOTA GENERATIONE LICITUM SIT CONNUBIUM EPISTOLA.",
        "page": 1085,
        "entry_kind": "heading_group",
        "query_names": ["Quota generatione licitum sit connubium", "connubium", "generatione licitum"],
        "note": "OCR omits the page on the contents line; body opener at file 547 confirms the printed-page anchor.",
    },
    {
        "title": "DE CONSANGUINEORUM NUPTIIS ET DE MAGORUM PRÆSTIGIIS FALSISQUE DIVINATIONIBUS TRACTATUS.",
        "page": 1088,
        "entry_kind": "heading_group",
        "query_names": [
            "De consanguineorum nuptiis",
            "De magorum præstigiis falsisque divinationibus",
            "Tractatus",
        ],
        "note": "Contents OCR omits the page; body title page 1088 carries the work heading.",
    },
    {
        "title": "TRACTATUS DE ANIMA.",
        "page": 1109,
        "entry_kind": "heading_group",
        "query_names": ["Tractatus de anima", "De anima", "Anima"],
        "note": "Umbrella title recovered from the body opener and preserved as a heading_group.",
    },
    {
        "title": "MARTYROLOGIUM.",
        "page": 1121,
        "entry_kind": "heading_group",
        "query_names": ["Martyrologium", "Rabanus Martyrologium"],
        "note": "Umbrella title for the Martyrologium sequence; body opener at page 1121 confirms the anchor.",
    },
    {
        "title": "OPUSCULA DUO.",
        "page": 1187,
        "entry_kind": "heading_group",
        "query_names": ["Opuscula duo", "Opuscula", "Duo"],
        "note": "Section heading for the two short opuscula; the first item starts on the same printed page.",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def normalize_sort(text: str | None) -> str | None:
    if not text:
        return None
    text = strip_accents(text)
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    return re.sub(r"\s+", " ", text).lower().strip(" .,:;")


def parse_lines(path: Path) -> list[str]:
    lines: list[str] = []
    in_text = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if stripped.startswith('<bloco tipo="texto_principal"'):
            in_text = True
            continue
        if stripped.startswith("</bloco"):
            in_text = False
            continue
        if stripped.startswith("<") and not in_text:
            continue
        if not in_text:
            continue
        text = normalize(html.unescape(stripped))
        if not text:
            continue
        if NOISE_RE.fullmatch(text):
            continue
        lines.append(text)
    return lines


def build_ocr_lines() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for path in CONTENT_FILES:
        for line in parse_lines(path):
            pairs.append((str(path), line))
    return pairs


def build_entries_from_ocr() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []
    evidence_files = [str(p) for p in CONTENT_FILES]

    current_parts: list[str] = []
    current_source_file: str | None = None
    started = False

    manual_iter = iter(SPECIAL_ENTRIES)
    pending_special = next(manual_iter, None)

    def emit_entry(entry_raw: str, source_file: str, page: int, kind: str = "lemma", note: str | None = None) -> None:
        entry_order = len(entries) + 1
        lemma_raw = entry_raw.rsplit(".", 1)[0].strip() if kind == "lemma" and "." in entry_raw else entry_raw.strip()
        entry_key = f"{VOLUME_ID}:entry:{entry_order:04d}"
        helper_id = f"pl110_ordo_{entry_order:04d}"
        helper_entry = {
            "entry_id": helper_id,
            "lemma_raw": lemma_raw,
            "query_names": [lemma_raw],
            "page_hints": [str(page)],
            "page_hint_ints": [page],
            "context_raw": entry_raw,
        }
        helper_entries.append(helper_entry)
        entries.append(
            {
                "entry_key": entry_key,
                "helper_entry_id": helper_id,
                "section_key": SECTION_KEY,
                "entry_order": entry_order,
                "entry_kind": kind,
                "lemma_raw": lemma_raw if kind != "cross_reference" else None,
                "lemma_display": lemma_raw if kind != "cross_reference" else None,
                "lemma_norm": normalize_sort(lemma_raw),
                "lemma_sort": normalize_sort(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": entry_raw,
                "heading_letter": None,
                "inferred_printed_page": page,
                "section_start_file": SECTION_START_FILE,
                "editorial_anchor_file": source_file,
                "target_file_best": None,
                "confidence": 0.0,
                "raw_json": {
                    "source_file": source_file,
                    "section_kind": "ordo_rerum",
                    "manual_note": note,
                },
            }
        )

    lines = build_ocr_lines()
    for source_file, line in lines:
        if line == SECTION_HEADING_RAW or line == SECTION_HEADING_RAW.replace("Æ", "AE"):
            continue
        if not started:
            if line.startswith("Praefatio ad Hasulfum ante episcopum."):
                started = True
            else:
                continue

        if pending_special and line == pending_special["title"]:
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            current_parts = []
            current_source_file = None
            continue

        match = PAGE_RE.fullmatch(line)
        if match:
            current_parts.append(match.group("lemma"))
            current_source_file = source_file
            entry_raw = normalize(" ".join(current_parts))
            page = int(match.group("page"))
            emit_entry(entry_raw, current_source_file or source_file, page)
            current_parts = []
            current_source_file = None
            continue

        if ROMAN_HEAD_RE.fullmatch(line) or line in {
            "HOMILIAE",
            "HOMILIAE DE FESTIS PRAECIPUIS, ITEM DE VIRTUTIBUS.",
            "HOMILIAE IN EVANGELIA ET EPISTOLAS",
            "MARTYROLOGIUM.",
            "OPUSCULA DUO.",
            "TRACTATUS DE ANIMA.",
            "COMMENTARIA IN EZECHIELEM.",
        }:
            current_parts = []
            current_source_file = source_file
            continue

        if line.startswith("QUOTA GENERATIONE LICITUM SIT CONNUBIUM EPISTOLA.") and pending_special and pending_special["title"].startswith("QUOTA GENERATIONE"):
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            continue

        if line.startswith("DE CONSANGUINEORUM NUPTIIS ET DE MAGORUM PRÆSTIGIIS FALSISQUE DIVINATIONIBUS TRACTATUS.") and pending_special and pending_special["title"].startswith("DE CONSANGUINEORUM"):
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            continue

        if line.startswith("TRACTATUS DE ANIMA.") and pending_special and pending_special["title"].startswith("TRACTATUS DE ANIMA"):
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            continue

        if line.startswith("MARTYROLOGIUM.") and pending_special and pending_special["title"].startswith("MARTYROLOGIUM"):
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            continue

        if line.startswith("OPUSCULA DUO.") and pending_special and pending_special["title"].startswith("OPUSCULA DUO"):
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            continue

        if line.startswith("COMMENTARIA IN EZECHIELEM.") and pending_special and pending_special["title"].startswith("COMMENTARIA IN EZECHIELEM"):
            emit_entry(
                pending_special["title"],
                source_file,
                int(pending_special["page"]),
                pending_special["entry_kind"],
                pending_special["note"],
            )
            pending_special = next(manual_iter, None)
            continue

        if line.startswith("BEATI RABANI MAURI") or line.startswith("FULDENSIS ABBATIS"):
            continue

        if current_parts:
            current_parts.append(line)
        else:
            current_parts = [line]
            current_source_file = source_file

    return entries, helper_entries, evidence_files


def build_helper_request(helper_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": helper_entries,
    }


def run_helper() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_TARGET_LOCATOR), "--input", str(HELPER_REQUEST_JSON), "--output", str(HELPER_OUTPUT_JSON), "--pretty"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(HELPER_OUTPUT_JSON.read_text(encoding="utf-8"))


def helper_map(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item["entry_id"]] = item
    return out


def target_file_from_entry(helper_item: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    best = helper_item.get("best_candidate") or {}
    return best.get("file"), best.get("probability"), best.get("reason_summary")


def build_payload(entries: list[dict[str, Any]], helper_output: dict[str, Any], evidence_files: list[str]) -> dict[str, Any]:
    hmap = helper_map(helper_output)
    payload_entries: list[dict[str, Any]] = []
    payload_refs: list[dict[str, Any]] = []

    for entry in entries:
        helper_id = entry.pop("helper_entry_id")
        helper_item = hmap.get(helper_id, {})
        target_file, target_prob, reason = target_file_from_entry(helper_item)
        entry["target_file_best"] = target_file
        entry["confidence"] = target_prob if target_prob is not None else 0.5
        entry["raw_json"]["helper"] = helper_item
        if reason:
            entry["raw_json"]["helper_reason_summary"] = reason
        payload_entries.append(entry)

        if target_file is not None and entry.get("inferred_printed_page") is not None:
            payload_refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": 1,
                    "ref_kind": "target_locator",
                    "ref_raw": str(entry["inferred_printed_page"]),
                    "page_ref_raw": str(entry["inferred_printed_page"]),
                    "page_ref_int": entry["inferred_printed_page"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": SECTION_START_FILE,
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": entry["confidence"],
                    "raw_json": {
                        "helper_entry_id": helper_id,
                        "helper": helper_item,
                    },
                }
            )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT),
            "volume_label": "Patrologia Latina 110",
            "notes": [
                "The tail is a closing ORDO RERUM / contents table, not a true alphabetical index.",
                "A few OCR headings omit visible page numbers on the contents page; those are anchored to their body openers in raw_json.",
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
                "page_start": 1205,
                "page_end": 1212,
                "file_start": SECTION_START_FILE,
                "file_end": SECTION_END_FILE,
                "confidence": 0.99,
                "raw_json": {
                    "section_kind_reason": "Closing contents table printed as ORDO RERUM / QUÆ IN HOC TOMO CONTINENTUR.",
                    "evidence_files": evidence_files,
                },
            }
        ],
        "nodes": [],
        "entries": payload_entries,
        "refs": payload_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and resolved the visible page anchors conservatively against body openers when the contents page omitted a number.",
            "evidence_files": evidence_files
            + [
                str(SOURCE_ROOT / "547100cb-928b-4a2d-b0c5-7d163b7a1c57-547.txt"),
                str(SOURCE_ROOT / "547100cb-928b-4a2d-b0c5-7d163b7a1c57-548.txt"),
                str(SOURCE_ROOT / "547100cb-928b-4a2d-b0c5-7d163b7a1c57-549.txt"),
                str(SOURCE_ROOT / "547100cb-928b-4a2d-b0c5-7d163b7a1c57-559.txt"),
                str(SOURCE_ROOT / "547100cb-928b-4a2d-b0c5-7d163b7a1c57-565.txt"),
                str(SOURCE_ROOT / "547100cb-928b-4a2d-b0c5-7d163b7a1c57-598.txt"),
            ],
        },
        "notes": [
            "The section is editorial contents material. Standalone headings without page numbers were preserved only when they materially identify a work title.",
            "The OCR page '75' for CAP. XXII is preserved in entry_raw; the helper and neighboring body pages show the material anchor around 735.",
        ],
    }


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Build PL110 closing ORDO RERUM payload and resolve special OCR headings.",
        "completed": [
            "confirmed the final volume section is ORDO RERUM",
            "inspected the OCR tail and neighboring body openers for ambiguous titles",
        ],
        "pending": [
            "run index_target_locator.py for the helper request",
            "assemble the final payload and validate the JSON",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR literals in entry_raw.",
            "Use neighboring body openers for page-less contents headings.",
        ],
    }
    write_json(TODO_JSON, todo)

    entries, helper_entries, evidence_files = build_entries_from_ocr()
    write_json(HELPER_REQUEST_JSON, build_helper_request(helper_entries))
    helper_output = run_helper()
    write_json(HELPER_OUTPUT_JSON, helper_output)
    payload = build_payload(entries, helper_output, evidence_files)
    write_json(OUTPUT_FILE, payload)

    todo["updated_at"] = now_iso()
    todo["completed"].append("helper resolved and final payload written")
    todo["pending"] = []
    write_json(TODO_JSON, todo)


if __name__ == "__main__":
    main()
