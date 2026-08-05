#!/usr/bin/env python3
"""Usage: build the PL139 contents/ordo payload from the OCR tail.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/pl139_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL139/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL139_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL139_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL139 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL139_alphabetical_indices.json
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


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL139"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 139"

SECTION_SPECS = [
    {
        "section_order": 1,
        "section_key": f"{VOLUME_ID}:ordo:001",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "file_seqs": [823, 824],
        "section_kind_reason": "Closing contents/ordo block listing the earlier parts of the tomo; editorial order table rather than alphabetical index material.",
    },
    {
        "section_order": 2,
        "section_key": f"{VOLUME_ID}:ordo:002",
        "heading_raw": "QUÆ IN HOC TOMO CONTINENTUR.",
        "file_seqs": [825, 826],
        "section_kind_reason": "Contents block for the Gerbertus / epistolae material and its appendix; editorial order table rather than alphabetical index material.",
    },
    {
        "section_order": 3,
        "section_key": f"{VOLUME_ID}:ordo:003",
        "heading_raw": "QUÆ IN HOC TOMO CONTINENTUR.",
        "file_seqs": [827, 828, 829, 830, 831, 832],
        "section_kind_reason": "Large contents block spanning Silvester II, Gozpertus, Abbo, Aimoinus, and related works; editorial order table rather than alphabetical index material.",
    },
    {
        "section_order": 4,
        "section_key": f"{VOLUME_ID}:ordo:004",
        "heading_raw": "QUÆ IN HOC TOMO CONTINENTUR.",
        "file_seqs": [833, 834],
        "section_kind_reason": "Final contents block for the latter Patrologia Latina material and the end-of-volume closure; editorial order table rather than alphabetical index material.",
    },
]

HEADING_SKIP_PREFIXES = (
    "OPERUM ",
    "SECTIO ",
    "LIBER ",
    "PARS ",
    "SPURIA ",
    "BURCHARDUS ",
    "GOZPERTUS ",
    "THIETPALDUS ",
    "SANCTUS ",
    "SANCTI ",
    "AIMOINI ",
    "ALBERTUS ",
    "RORICO ",
    "NOTGERUS ",
    "THITMARUS ",
    "BRIDFERTUS ",
    "JOANNES ",
    "SERGIUS ",
    "ARNULFUS ",
    "CONSTANTINUS ",
    "BENEDICTUS ",
    "HENRICUS ",
    "BRUNO ",
)

NOISE_LINES = {"Digitized by Google"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("æ", "ae").replace("œ", "oe").replace("Æ", "AE").replace("Œ", "OE")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value else None


def load_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = (tipo_m.group(1).strip().lower() if tipo_m else "")
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = norm(raw_line)
            if line and line not in NOISE_LINES:
                lines.append(line)
    return lines


def is_heading_like(line: str) -> bool:
    if not line:
        return False
    if re.match(r"^\d{4}\s+(?:QU[AEÆ]|ORDO RERUM)\b", line):
        return True
    if re.match(r"^[IVXLCDM]+\.\s", line):
        return False
    if re.match(r"^(CAP|EPIST|Epist)\.", line):
        return False
    if any(line.startswith(prefix) for prefix in HEADING_SKIP_PREFIXES):
        return True
    if line.isupper() and not re.search(r"\d{1,4}\s*$", line):
        return True
    if re.match(r"^(QU[AEÆ]|ORDO RERUM)", line):
        return True
    if re.match(r"^(NOTITIA|PROLOGUS|PR[AÆ]FATIO|VITA |COMMENTARIUM |ACTA |JURAMENTUM |LIBELLUS |CARMEN |EPISTOLAE |EPISTOLÆ )", line):
        return True
    return False


def classify_entry_kind(text: str) -> str:
    if re.match(r"^(CAP|Epist|EPIST)\.", text):
        return "lemma"
    if re.match(r"^[IVXLCDM]+\.\s", text):
        return "lemma"
    if re.match(r"^(Notitia|Prologus|Pr[aæ]fatio|Dissertatio)", text):
        return "editorial_note"
    if text.isupper() or any(text.startswith(prefix) for prefix in HEADING_SKIP_PREFIXES):
        return "heading_group"
    if re.match(r"^(VITA |COMMENTARIUM |ACTA |EPISTOLAE |EPISTOLÆ |APPENDIX |SANCTUS |SANCTI |LIBER |SECTIO )", text):
        return "heading_group"
    return "lemma"


def extract_file_number(path: Path) -> int:
    m = re.search(r"-(\d{3})\.txt$", path.name)
    if not m:
        raise ValueError(f"Cannot parse file sequence from {path}")
    return int(m.group(1))


def file_by_seq(source_root: Path, seq: int) -> Path:
    matches = sorted(source_root.glob(f"*-{seq:03d}.txt"))
    if not matches:
        raise FileNotFoundError(f"No OCR file found for sequence {seq:03d}")
    return matches[0]


def parse_entry_lines(lines: list[str]) -> list[tuple[str, str, int | None]]:
    entries: list[tuple[str, str, int | None]] = []
    buffer: list[str] = []

    def flush(page_raw: str | None = None) -> None:
        nonlocal buffer
        if not buffer:
            return
        text = " ".join(buffer).strip()
        buffer = []
        if page_raw is None:
            return
        text = re.sub(r"\s+\d{1,4}$", "", text).strip()
        entries.append((text, page_raw, int(page_raw)))

    for line in lines:
        page_m = re.search(r"(?P<page>\d{1,4})$", line)
        if page_m:
            if re.match(r"^\d{4}\s+(?:ORDO RERUM|URDO RERUM|QU[AEÆ].*CONTINENTUR)\b", line):
                buffer = []
                continue
            page_raw = page_m.group("page")
            if buffer:
                buffer.append(re.sub(r"\s+\d{1,4}$", "", line).strip())
                flush(page_raw)
            else:
                text = re.sub(r"\s+\d{1,4}$", "", line).strip()
                entries.append((text, page_raw, int(page_raw)))
            continue
        if is_heading_like(line):
            buffer = []
            continue
        buffer.append(line)
    return entries


def build_sections(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    for spec in SECTION_SPECS:
        file_paths = [file_by_seq(source_root, seq) for seq in spec["file_seqs"]]
        section_entries: list[dict[str, Any]] = []
        entry_order = 0
        for file_path in file_paths:
            lines = load_lines(file_path)
            for text, page_raw, page_int in parse_entry_lines(lines):
                entry_order += 1
                entry_kind = classify_entry_kind(text)
                entry_key = f"{VOLUME_ID}:entry:{spec['section_order']:02d}:{entry_order:03d}"
                entry = {
                    "entry_key": entry_key,
                    "section_key": spec["section_key"],
                    "parent_node_key": None,
                    "entry_order": entry_order,
                    "entry_kind": entry_kind,
                    "lemma_raw": text,
                    "lemma_display": text,
                    "lemma_norm": norm(text),
                    "lemma_sort": sort_norm(text),
                    "entry_raw": text,
                    "context_raw": text,
                    "heading_letter": None,
                    "inferred_printed_page": page_int,
                    "section_start_file": str(file_paths[0]),
                    "editorial_anchor_file": str(file_path),
                    "target_file_best": str(file_path),
                    "confidence": 0.99 if entry_kind != "heading_group" else 0.96,
                    "raw_json": {
                        "source_file": str(file_path),
                        "parsed_from": "ocr_line_with_trailing_page_number",
                        "entry_kind_reason": (
                            "page-numbered title/heading line" if entry_kind == "heading_group"
                            else "page-numbered index line"
                        ),
                    },
                }
                ref = {
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
                    "target_file": str(file_path),
                    "target_file_probability": 1.0,
                    "section_start_file": str(file_paths[0]),
                    "editorial_anchor_file": str(file_path),
                    "confidence": 0.99,
                    "raw_json": {
                        "source_file": str(file_path),
                        "resolved_from": "local OCR contents page",
                    },
                }
                section_entries.append(entry)
                refs.append(ref)

        if section_entries:
            page_vals = [e["inferred_printed_page"] for e in section_entries if e["inferred_printed_page"] is not None]
            page_start = min(page_vals) if page_vals else None
            page_end = max(page_vals) if page_vals else None
        else:
            page_start = None
            page_end = None

        sections.append(
            {
                "section_key": spec["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": "ordo_rerum",
                "heading_raw": spec["heading_raw"],
                "heading_norm": norm(spec["heading_raw"]),
                "heading_letter": None,
                "page_start": page_start,
                "page_end": page_end,
                "file_start": str(file_paths[0]),
                "file_end": str(file_paths[-1]),
                "confidence": 0.98,
                "raw_json": {
                    "section_kind_reason": spec["section_kind_reason"],
                    "source_file_span": [str(file_paths[0]), str(file_paths[-1])],
                    "source_files": [str(p) for p in file_paths],
                    "observed_heading_lines": [spec["heading_raw"]],
                    "helper_status": None,
                },
            }
        )
        entries.extend(section_entries)

    return sections, entries, refs


def build_helper_request(source_root: Path) -> dict[str, Any]:
    entries = [
        {
            "entry_id": f"{VOLUME_ID.lower()}_sample_{idx:02d}",
            "lemma_raw": lemma,
            "query_names": query_names,
            "page_hints": [str(page)],
            "page_hint_ints": [page],
            "context_raw": lemma,
        }
        for idx, (lemma, page, query_names) in enumerate(
            [
                ("Versus in librum.", 158, ["Versus in librum", "Incipit liber"]),
                ("EPIST. I. — Ad Othonem Cæsarem.", 201, ["Ad Othonem Cæsarem", "EPIST. I"]),
                ("LIBER PRIMUS. — S. Landoaldi et sociorum gesta, translationes, miracula Winterslovii facta.", 1111, ["S. Landoaldi", "Winterslovii"]),
                ("NOTGERUS LEODIENSIS EPISCOPUS.", 1155, ["Notgerus Leodiensis", "Gesta episcoporum Leodiensium"]),
                ("BENEDICTUS VIII PAPA.", 1577, ["Benedictus VIII papa", "Epistolae et decreta"]),
            ],
            start=1,
        )
    ]
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": entries,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/index_target_locator.py"),
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
    return read_json(helper_output_json, {})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)

    helper_request = build_helper_request(args.source_root)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    sections, entries, refs = build_sections(args.source_root)
    helper_map = {item.get("entry_id"): item for item in helper_output.get("entries", [])}

    # Attach helper evidence to the first section raw_json for traceability only.
    if sections:
        sections[0]["raw_json"]["helper_status"] = helper_output.get("status")
        sections[0]["raw_json"]["helper_sample"] = list(helper_map)[:5]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(args.source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Contents / ordo material extracted from the tail OCR pages; this volume is not a pure alphabetical index.",
        },
        "sections": sections,
        "nodes": [],
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "PL139 tail OCR exposes contents/ordo blocks with recoverable page-numbered lines; extracted as ordo_rerum sections rather than an alphabetical lemma index.",
            "evidence_files": [sections[0]["file_start"], sections[-1]["file_end"]],
        },
        "notes": [
            "The OCR tail contains editorial contents tables and ordo blocks, not an alphabetical index proper.",
            "OCR file suffixes were kept separate from the printed page numbers cited in each entry.",
        ],
    }

    write_json(args.intermediate_dir / "sections.json", sections)
    write_json(args.intermediate_dir / "entries.json", entries)
    write_json(args.intermediate_dir / "refs.json", refs)
    write_json(args.intermediate_dir / "manifest.json", {
        "volume_id": VOLUME_ID,
        "generated_at": payload["generated_at"],
        "section_count": len(sections),
        "entry_count": len(entries),
        "ref_count": len(refs),
    })
    write_json(args.output_file, payload)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Final payload written and helper run completed.",
        "completed": [
            "Read OCR tail and confirmed contents/ordo material",
            "Built helper request and ran index_target_locator.py",
            "Assembled sections, entries, refs, and payload",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "Payload models editorial contents tables as ordo_rerum sections.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)


if __name__ == "__main__":
    main()
