#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/pg025_build_ordo_rerum_payload.py

Build the PG025 closing `ordo_rerum` payload from the OCR files in teste/PG025/text
and write the canonical JSON payload to the runtime output path.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG025/text"
OUTPUT = ROOT / "data/alphabetical_index_payloads/PG025_alphabetical_indices.json"
TODO = ROOT / "data/intermediate_payloads/PG025/todo.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG025_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG025_helper_output.json"

OCR_FILES = {
    "683": SOURCE_ROOT / "46c07ac3-45ff-4954-8b5c-cbadd029d26f-683.txt",
    "684": SOURCE_ROOT / "46c07ac3-45ff-4954-8b5c-cbadd029d26f-684.txt",
    "685": SOURCE_ROOT / "46c07ac3-45ff-4954-8b5c-cbadd029d26f-685.txt",
    "686": SOURCE_ROOT / "46c07ac3-45ff-4954-8b5c-cbadd029d26f-686.txt",
}

SECTION_KEY = "PG025:alpha:ordo_rerum:001"


def roman_to_int(value: str) -> int | None:
    text = value.strip().upper().rstrip(".")
    if not text or not re.fullmatch(r"[IVXLCDM]+", text):
        return None
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(text):
        cur = table[ch]
        if cur < prev:
            total -= cur
        else:
            total += cur
            prev = cur
    return total


def extract_text_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(
        r'<bloco[^>]*tipo="(?:texto_principal|cabecalho)"[^>]*>(.*?)</bloco>',
        text,
        flags=re.S,
    )
    lines: list[str] = []
    for block in blocks:
        for raw in block.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("<") and line.endswith(">"):
                continue
            lines.append(re.sub(r"\s+", " ", line))
    return lines


REF_RE = re.compile(r"(?P<ref>(?:[ivxlcdmIVXLCDM]+|\d+))\.?$")


def parse_entries(lines: list[str], source_file: str, default_anchor: str) -> list[dict]:
    entries: list[dict] = []
    buffer: list[str] = []
    order = 1
    active = False

    def flush(entry_line: str) -> None:
        nonlocal order
        m = REF_RE.search(entry_line.strip())
        if not m:
            return
        ref_raw = m.group("ref")
        body = entry_line[: m.start("ref")].rstrip(" .—-")
        if not body:
            return
        page_int = int(ref_raw) if ref_raw.isdigit() else roman_to_int(ref_raw)
        entry = {
            "entry_key": f"PG025:{source_file}:entry:{order:03d}",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "entry_order": order,
            "entry_kind": "heading_group" if body in {
                "Proœmium.",
                "ANIMADVERSIONES NOVÆ IN VITAM ET SCRIPTA S. ATHANASII.",
            } else "lemma",
            "lemma_raw": body,
            "lemma_display": body,
            "lemma_norm": body,
            "lemma_sort": body.lower(),
            "entry_raw": entry_line.strip(),
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": page_int,
            "section_start_file": OCR_FILES["683"].as_posix(),
            "editorial_anchor_file": source_file,
            "target_file_best": source_file,
            "confidence": 0.90 if page_int is not None else 0.65,
            "raw_json": {
                "source_file": source_file,
                "ref_raw": ref_raw,
                "ref_kind": "editorial_page",
                "section_kind_reason": "Closing contents/ordo rerum block with printed locator at line end.",
            },
        }
        entries.append(entry)
        order += 1

    for line in lines:
        if not line:
            continue
        if re.fullmatch(r"\d+|Digitized by Google", line):
            continue
        if "FINIS TOMI VICESIMI QUINTI." in line:
            active = False
            buffer = []
            continue
        if "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR." in line:
            active = True
            continue
        if line.startswith("ORDO RERUM") or line.startswith("QUÆ IN HOC TOMO CONTINENTUR."):
            active = True
            continue
        if line.startswith("ANIMADVERSIONES NOVÆ IN VITAM ET SCRIPTA"):
            active = True
            continue
        if line.startswith("S. ATHANASIUS ALEXANDRINUS ARCHIEPISCOPUS."):
            active = True
            continue
        if not active:
            buffer = []
            continue
        has_ref = bool(REF_RE.search(line.strip()))
        if not buffer and not has_ref and not (
            line.startswith("ANNO")
            or line.startswith("Animadversio")
            or line.startswith("Proœmium.")
            or line.startswith("Romanorum et Alexandrinorum sacerdotum et anti-")
            or line.startswith("Arianorum querelæ")
        ):
            continue
        if line in {
            "ORDO RERUM",
            "QUÆ IN HOC TOMO CONTINENTUR.",
            "ANIMADVERSIONES NOVÆ IN VITAM ET SCRIPTA",
            "S. ATHANASII.",
        }:
            continue
        if line.startswith("VITÆ ATHANASII SCRIPTORES VARII.") or line.startswith("ELOGIA VETERUM.") or line.startswith("S. ATHANASII OPERA."):
            continue
        if line.startswith("Vita et conversatio S. Athanasii auctore incerto.") or line.startswith("Vita S. Athanasii ex Photio.") or line.startswith("Vita S. Athanasii ex Melaphraste.") or line.startswith("Vita S. Athanasii ex Arabico versa, interprete D. Renaudotio.") or line.startswith("DISSERTATIO J. FONTANINI de anno emortuali S. Athanasii.") or line.startswith("DE COLTU S. ATHANASII,") or line.startswith("Agnus et dies mortis atque cultus: Vitæ scriptores posteriores.") or line.startswith("De corpore S. Athanasii Constantinopoli Venetias translato et ejus ibidem cultu.") or line.startswith("Quomodo Constantinopoli deportatum sit corpus.") or line.startswith("Veritas sancti corporis a patriarchæ examinata et probata.") or line.startswith("Miracula S. Athanasii præcipue post translationem corporis.") or line.startswith("De cultu sacri capitis in Hispania et Gallia.") or line.startswith("Monitum in duos Contra gentes et De Incarnatione libros.") or line.startswith("ORATIO CONTRA GENTES.") or line.startswith("ORATIO DE INCARNATIONE VERBI.") or line.startswith("Admonitio in sequentem fidei Expositionem.") or line.startswith("EXPOSITIO FIDEI.") or line.startswith("Moritum in sequentem Tractatum.") or line.startswith("TRACTATUS in illud, Omnia nihil tradita sunt a Pareo meo, etc.") or line.startswith("Monitum in sequentem Encyclicam.") or line.startswith("EPISTOLA ENCYCLICA AD EPISCOPOS:") or line.startswith("Admonitio in Apologiam contra Arianos.") or line.startswith("APOLOGIA CONTRA ARIANOS.") or line.startswith("Admonitio in Epistulam sequentem.") or line.startswith("EPISTOLA DE DECRETIS SYNODI NICÆNÆ.") or line.startswith("Monitum in Epistolam sequentem.") or line.startswith("EPISTOLA DE SENTENTIA DIONYSII.") or line.startswith("Monitum in Epistolam ad Draconium.") or line.startswith("EPISTOLA AD DRACONIUM.") or line.startswith("Admonitio in Epistolam sequentem.") or line.startswith("EPISTOLA ENCYCLICA AD EPISCOPOS ÆGYPTI ET LIBYÆ.") or line.startswith("Monitum in sequentem Apologiam.") or line.startswith("APOLOGIA AD IMPERATORUM CONSTANTII M.") or line.startswith("Admonitio in opus sequentem.") or line.startswith("APOLOGIA DE FUGA SUA.") or line.startswith("Admonitio in duas sequentis epistolas.") or line.startswith("EPISTOLA AD SERAPIÓNEM DE MORTE ARI.") or line.startswith("EPISTOLA AD MONACHOS DE HISTORIA ARIANORUM.") or line.startswith("HISTORIA ARIANORUM.") or line.startswith("Admonitio in duo opuscula sequentia.") or line.startswith("OPUSCULA DUO ALEXANDRI archiep. Alexandriæ Arians"):
            continue
        if line.startswith("ANNO") or line.startswith("Animadversio") or line.startswith("Proœmium.") or line.startswith("Romanorum et Alexandrinorum sacerdotum et anti-") or line.startswith("Arianorum querelæ") or buffer:
            buffer.append(line)
            if has_ref:
                flush(" ".join(buffer))
                buffer = []
            continue
        # Drop stray fragments that do not have a stable start marker.
        if has_ref:
            buffer = [line]
            flush(" ".join(buffer))
            buffer = []

    return entries


def build_payload() -> dict:
    lines_683 = extract_text_blocks(OCR_FILES["683"])
    lines_684 = extract_text_blocks(OCR_FILES["684"])
    lines_685 = extract_text_blocks(OCR_FILES["685"])

    entries = []
    entries.extend(parse_entries(lines_683, "683", OCR_FILES["683"].as_posix()))
    entries.extend(parse_entries(lines_684, "684", OCR_FILES["684"].as_posix()))
    entries.extend(parse_entries(lines_685, "685", OCR_FILES["685"].as_posix()))

    # De-duplicate by entry_raw to avoid the overlapping OCR fragments in the tail.
    deduped: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        key = entry["entry_raw"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)

    skip_markers = (
        "Coelerius",
        "Arianoram",
        "Socralem",
        "coppulare",
        "Depositionem Arii",
        "Habetur hæc Epistola",
    )
    final_entries: list[dict] = []
    for entry in deduped:
        if any(marker in entry["entry_raw"] for marker in skip_markers):
            continue
        final_entries.append(entry)

    for idx, entry in enumerate(final_entries, start=1):
        entry["entry_order"] = idx
        entry["entry_key"] = f"PG025:ordo_rerum:entry:{idx:03d}"

    refs = []
    for entry in final_entries:
        page_int = entry["inferred_printed_page"]
        ref_raw = entry["raw_json"]["ref_raw"]
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": entry["target_file_best"],
                "target_file_probability": 0.83,
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "confidence": entry["confidence"],
                "raw_json": {
                    "source_file": entry["editorial_anchor_file"],
                    "locating_note": "Parsed from terminal page locator in the OCR line.",
                },
            }
        )

    nodes = [
        {
            "node_key": "PG025:node:ordo:001",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "S. ATHANASIUS ALEXANDRINUS ARCHIEPISCOPUS.",
            "label_norm": "S. Athanasius Alexandrinus archiepiscopus",
            "label_sort": "athanasius alexandrinus archiepiscopus",
            "node_level": 1,
            "confidence": 0.92,
            "raw_json": {"source_file": OCR_FILES["686"].as_posix(), "kind": "macro_heading"},
        },
        {
            "node_key": "PG025:node:ordo:002",
            "section_key": SECTION_KEY,
            "parent_node_key": "PG025:node:ordo:001",
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "PROLEGOMENA.",
            "label_norm": "Prolegomena",
            "label_sort": "prolegomena",
            "node_level": 2,
            "confidence": 0.92,
            "raw_json": {"source_file": OCR_FILES["683"].as_posix(), "kind": "subheading"},
        },
        {
            "node_key": "PG025:node:ordo:003",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 3,
            "node_kind": "heading_group",
            "label_raw": "VITÆ ATHANASII SCRIPTORES VARII.",
            "label_norm": "Vitæ Athanasii scriptores varii",
            "label_sort": "vitae athanasii scriptores varii",
            "node_level": 1,
            "confidence": 0.92,
            "raw_json": {"source_file": OCR_FILES["686"].as_posix(), "kind": "macro_heading"},
        },
        {
            "node_key": "PG025:node:ordo:004",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 4,
            "node_kind": "heading_group",
            "label_raw": "ELOGIA VETERUM.",
            "label_norm": "Elogia veterum",
            "label_sort": "elogia veterum",
            "node_level": 1,
            "confidence": 0.92,
            "raw_json": {"source_file": OCR_FILES["686"].as_posix(), "kind": "macro_heading"},
        },
        {
            "node_key": "PG025:node:ordo:005",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 5,
            "node_kind": "heading_group",
            "label_raw": "S. ATHANASII OPERA.",
            "label_norm": "S. Athanasii opera",
            "label_sort": "athanasii opera",
            "node_level": 1,
            "confidence": 0.92,
            "raw_json": {"source_file": OCR_FILES["686"].as_posix(), "kind": "macro_heading"},
        },
        {
            "node_key": "PG025:node:ordo:006",
            "section_key": SECTION_KEY,
            "parent_node_key": None,
            "node_order": 6,
            "node_kind": "heading_group",
            "label_raw": "ANIMADVERSIONES NOVÆ IN VITAM ET SCRIPTA S. ATHANASII.",
            "label_norm": "Animadversiones novæ in vitam et scripta S. Athanasii",
            "label_sort": "animadversiones nove in vitam et scripta s athanasii",
            "node_level": 1,
            "confidence": 0.92,
            "raw_json": {"source_file": OCR_FILES["685"].as_posix(), "kind": "macro_heading"},
        },
    ]

    section = {
        "section_key": SECTION_KEY,
        "volume_id": "PG025",
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "Ordo rerum quæ in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 789,
        "page_end": 801,
        "file_start": OCR_FILES["683"].as_posix(),
        "file_end": OCR_FILES["685"].as_posix(),
        "confidence": 0.89,
        "raw_json": {
            "section_kind_reason": "Editorial closing contents / ordo rerum block; not an alphabetical lemma index.",
            "source_files": [p.as_posix() for p in OCR_FILES.values()],
        },
    }

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PG025",
            "collection": "PG",
            "source_root": SOURCE_ROOT.as_posix(),
            "volume_label": "PG025",
            "notes": "Closing contents / ordo rerum material with overlapping OCR fragments in the tail.",
        },
        "sections": [section],
        "nodes": nodes,
        "entries": final_entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the closing contents block and its major headings from the tail OCR files; overlapping duplicate fragments were de-duplicated.",
            "evidence_files": [p.as_posix() for p in OCR_FILES.values()],
        },
        "notes": [
            "Broad work-list headings from the closing ordo were modeled as nodes, not lemma entries.",
            "The detailed year-based contents entries were parsed conservatively from OCR lines with terminal page locators.",
        ],
    }
    return payload


def main() -> None:
    TODO.parent.mkdir(parents=True, exist_ok=True)
    TODO.write_text(
        json.dumps(
            {
                "volume_id": "PG025",
                "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "current_focus": "Finalize PG025 ordo rerum payload",
                "completed": [
                    "Read tail OCR files",
                    "Identified closing contents / ordo rerum section",
                    "Built conservative parser for locator-bearing lines",
                ],
                "pending": [
                    "Write helper request/output",
                    "Generate final payload",
                    "Validate JSON structure",
                ],
                "blocked": [],
                "notes": [
                    "Keep the broad headings from page 686 as nodes only.",
                    "Do not collapse the OCR file suffix with the printed page locator.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_payload()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
