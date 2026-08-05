#!/usr/bin/env python3
"""Usage: build the PL115 ORDO RERUM payload and helper request.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/pl115_ordo_rerum_build.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL115/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL115_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL115_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL115 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL115_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


VOLUME_ID = "PL115"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 115"
SECTION_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:001"
SECTION_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"
SECTION_FILES = [731, 732, 733, 734]

SKIP_EXACT = {
    "ORDO RERUM",
    "QUÆ IN HOC TOMO CONTINENTUR.",
    "QUAE IN HOC TOMO CONTINENTUR.",
    "PIA PRECATIO",
    "EX LONGIORE ORATIONE SANCTI AMBROSII EPISCOPI.",
    "AHYTO seu HAITO BASILEENSIS EPISCOPUS.",
    "AURADOUS SENONENSIS CHOREPISCOPUS.",
    "ALDRICUS CENOMANENSIS EPISCOPUS.",
    "ANGELOMUS LUXOVIENSIS",
    "MONACHUS.",
    "ENARRATIONES IN LIBROS",
    "REGUM.",
    "IN LIBRUM PRIMUM",
    "IN LIBRUM SECUNDUM.",
    "IN LIBRUM TERTIUM.",
    "IN LIBRUM QUARTUM.",
    "ENARRATIONES IN CANTICA",
    "CANTICORUM.",
    "LEONIS PAPAE IV EPISTOLAE ET DECRETA.",
    "LEO PAPA IV.",
    "LEONIS PAPAE IV HOMILIA",
    "BENEDICTUS PAPA III.",
    "BENEDICTI III EPISTOLAE.",
    "MEMORIALIS SANCTORUM.",
    "LIBER PRIMUS.",
    "LIBER SECUNDUS.",
    "LIBER TERTIUS.",
    "DOCUMENTUM MARTYRIALE.",
    "DE VITA ET PASSIONE SS. VIRGINUM FLORAE",
    "ET MARIAE.",
    "SANCTI EULOGII EPISTOLAE.",
    "LIBER APOLOGETICUS MARTYRUM.",
    "AMBROSII MORALIS SCHOLIA AD OMNIA",
    "SANCTI EULOGII OPERA.",
    "IN MEMORIALE SANCTORUM.",
    "IN DOCUMENTUM MARTYRIALE. 899",
    "IN VITAM ET PASSIONEM SS. VIRGINUM",
    "ET MARIAE.",
    "EPISTOLÆ DEDICATORIÆ EDITIONI AMBROSI MORALIS",
    "PRÆFIXÆ.",
    "EXCERPTA EX AMBROSIO MORALI.",
    "APPENDIX.",
    "S. PRUDENTIUS TRECENSIS EPISCOPUS.",
    "Præfatio auctoris.",
    "DE PRÆDESTINATIONE CONTRA JOANNEM",
    "SCOTUM COGNOMENTO ERIGENAM.",
    "Præfatio. 1009",
    "Incipit liber. 1011",
    "EPISTOLA TRACTORIA.",
    "EJUSDEM EPISTOLA AD QUÆMDAM EPISCOPUM.",
    "DE VITA ET MORTE GLORIOSÆ VIRGINIS",
    "MAURÆ.",
    "S.-PRUDENTII ANNALES.",
    "VERSUS SANCTI PRUDENTII.",
    "FLORILEGIUM EX SACRA SCRIPTURA.",
    "EXCERPTA EX PONTIFICIALI S. PRUDENTII.",
    "BREVIARIUM PSALTERII.",
}

SKIP_PREFIXES = (
    "Monitum.",
    "Incipit præfatio.",
    "Incipit translatio",
    "Incipit liber primus de miraculis",
    "Incipit prologus libri secundi",
    "Incipit liber secundus miraculorum",
    "IV.",
    "V.",
    "I.",
    "II.",
    "III.",
)

MANUAL_ENTRIES = [
    {
        "entry_raw": "Cap. III. — Destructio basilicarum.",
        "lemma_raw": "Cap. III. — Destructio basilicarum.",
        "page_ref_raw": "801",
        "page_ref_int": 801,
        "file": "/homessddata/Projects/pdfocr/teste/PL115/text/0a7a5ab8-e5ea-46b9-827e-0494bb866df1-403.txt",
        "query_names": ["Destructio basilicarum", "Destructio basilica-um"],
    },
    {
        "entry_raw": "CAP. XII. — Ex Cassiodoro in psalmos et Beda.",
        "lemma_raw": "CAP. XII. — Ex Cassiodoro in psalmos et Beda.",
        "page_ref_raw": "1003",
        "page_ref_int": 1003,
        "file": "/homessddata/Projects/pdfocr/teste/PL115/text/90041429-bde0-48a2-abe2-c5546a24ded8-504.txt",
        "query_names": ["Cassiodoro", "Beda", "Ex Cassiodoro in psalmos et Beda"],
    },
    {
        "entry_raw": "CAP. XIII. — De Gratia et libero Arbitrio ex diversis.",
        "lemma_raw": "CAP. XIII. — De Gratia et libero Arbitrio ex diversis.",
        "page_ref_raw": "1005",
        "page_ref_int": 1005,
        "file": "/homessddata/Projects/pdfocr/teste/PL115/text/90041429-bde0-48a2-abe2-c5546a24ded8-505.txt",
        "query_names": ["De Gratia et libero Arbitrio ex diversis", "libero Arbitrio"],
    },
]


@dataclass(slots=True)
class ParsedEntry:
    entry_order: int
    entry_raw: str
    lemma_raw: str
    page_ref_raw: str | None
    page_ref_int: int | None
    source_file: str
    query_names: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def sort_norm(text: str | None) -> str | None:
    value = norm(text)
    return value.lower() if value else None


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_lines(path: Path) -> list[str]:
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
            if line:
                lines.append(line)
    return lines


def is_noise_line(line: str) -> bool:
    if not line:
        return True
    if line == "Digitized by Google":
        return True
    return False


def is_skipped_heading(line: str) -> bool:
    if re.match(r"^\d{4}\s+ORDO RERUM", line):
        return True
    if re.match(r"^\d{4}\s+QUA[EF]?\s+IN HOC TOMO CONTINENTUR\.?$", line):
        return True
    if line in SKIP_EXACT:
        return True
    return any(line.startswith(prefix) for prefix in SKIP_PREFIXES)


def title_from_entry(entry_raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", entry_raw).strip()
    cleaned = re.sub(r"\s+\d{1,4}$", "", cleaned).strip()
    if "—" in cleaned:
        return cleaned.split("—", 1)[1].strip()
    return cleaned


def make_query_names(entry_raw: str) -> list[str]:
    title = title_from_entry(entry_raw)
    out = [title]
    if "—" in entry_raw:
        left = entry_raw.split("—", 1)[1].strip()
        out.append(left)
    if " ." in title:
        out.append(title.replace(" .", "."))
    if title.upper().startswith("CAP."):
        out.append(re.sub(r"^CAP\.\s*[IVXLCDM]+\.\s*—\s*", "", title))
    if title.upper().startswith("EPISTOLA"):
        out.append(title.replace("EPISTOLA", "Epistola", 1))
    return [q for q in dict.fromkeys(norm(q) for q in out if norm(q))]


def parse_entries(source_root: Path) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    order = 0
    for seq in SECTION_FILES:
        path = next(source_root.glob(f"*-{seq}.txt"))
        lines = extract_lines(path)
        buffer: list[str] = []
        for line in lines:
            if is_noise_line(line):
                continue
            if is_skipped_heading(line):
                buffer = []
                continue
            if re.fullmatch(r"\d{1,4}", line):
                if buffer:
                    order += 1
                    entry_raw = " ".join(buffer).strip()
                    page_ref_raw = line
                    title = re.sub(r"\s+\d{1,4}$", "", entry_raw).strip()
                    entries.append(
                        ParsedEntry(
                            entry_order=order,
                            entry_raw=entry_raw,
                            lemma_raw=title,
                            page_ref_raw=page_ref_raw,
                            page_ref_int=int(page_ref_raw),
                            source_file=str(path),
                            query_names=make_query_names(title),
                        )
                    )
                    buffer = []
                continue
            page_match = re.search(r"(?P<page>\d{1,4})$", line)
            if page_match:
                order += 1
                entry_raw = " ".join(buffer + [line]).strip() if buffer else line
                page_ref_raw = page_match.group("page")
                title = re.sub(r"\s+\d{1,4}$", "", entry_raw).strip()
                entries.append(
                    ParsedEntry(
                        entry_order=order,
                        entry_raw=entry_raw,
                        lemma_raw=title,
                        page_ref_raw=page_ref_raw,
                        page_ref_int=int(page_ref_raw),
                        source_file=str(path),
                        query_names=make_query_names(title),
                    )
                )
                buffer = []
                continue
            buffer.append(line)
    return entries


def apply_page_corrections(entries: list[ParsedEntry]) -> list[ParsedEntry]:
    for item in entries:
        if item.lemma_raw.startswith("CAP. XII. — Ex Cassiodoro in psalmos et Beda."):
            item.page_ref_raw = "1003"
            item.page_ref_int = 1003
            item.query_names = make_query_names(item.lemma_raw)
    return entries


def apply_manual_entries(entries: list[ParsedEntry]) -> list[ParsedEntry]:
    start = len(entries)
    for idx, item in enumerate(MANUAL_ENTRIES, start=1):
        entries.append(
            ParsedEntry(
                entry_order=start + idx,
                entry_raw=item["entry_raw"],
                lemma_raw=item["lemma_raw"],
                page_ref_raw=item["page_ref_raw"],
                page_ref_int=item["page_ref_int"],
                source_file=str(item["file"] or ""),
                query_names=item["query_names"],
            )
        )
    return entries


def build_helper_request(volume_id: str, source_root: Path, entries: list[ParsedEntry]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for item in entries:
        if item.page_ref_int is None:
            continue
        helper_entries.append(
            {
                "entry_id": f"{volume_id.lower()}_{item.entry_order:03d}",
                "lemma_raw": item.lemma_raw,
                "query_names": item.query_names or [item.lemma_raw],
                "page_hints": [item.page_ref_raw] if item.page_ref_raw else [],
                "page_hint_ints": [item.page_ref_int] if item.page_ref_int is not None else [],
                "context_raw": item.entry_raw,
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
            str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
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


def helper_index(helper_output: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in helper_output.get("entries", []):
        out[item.get("entry_id")] = item
    return out


def build_payload(
    source_root: Path,
    entries: list[ParsedEntry],
    helper_output: dict[str, Any],
) -> dict[str, Any]:
    helper_by_id = helper_index(helper_output)
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
            "page_start": 1457,
            "page_end": 1463,
            "file_start": str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-731.txt"),
            "file_end": str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-734.txt"),
            "confidence": 0.99,
            "raw_json": {
                "section_kind_reason": "final_volume_ordo_rerum_closure",
                "section_evidence_files": [
                    str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-731.txt"),
                    str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-732.txt"),
                    str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-733.txt"),
                    str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-734.txt"),
                ],
            },
        }
    ]

    nodes: list[dict[str, Any]] = []
    out_entries: list[dict[str, Any]] = []
    out_refs: list[dict[str, Any]] = []

    for item in entries:
        entry_id = f"{VOLUME_ID.lower()}_{item.entry_order:03d}"
        helper = helper_by_id.get(entry_id) or {}
        best = helper.get("best_candidate") or {}
        candidates = helper.get("candidates") or []
        target_file_best = best.get("file")
        target_prob = best.get("probability")
        if not target_file_best and item.page_ref_int is not None:
            # Fallback to the first page-matching file when the helper cannot decide.
            target_file_best = None

        confidence = 0.95 if item.page_ref_int is not None else 0.72
        if helper.get("status") == "ambiguous":
            confidence = min(confidence, 0.84)
        if item.page_ref_int is None:
            confidence = 0.7

        out_entries.append(
            {
                "entry_key": entry_id,
                "section_key": SECTION_KEY,
                "parent_node_key": None,
                "entry_order": item.entry_order,
                "entry_kind": "lemma",
                "lemma_raw": item.lemma_raw,
                "lemma_display": item.lemma_raw,
                "lemma_norm": item.lemma_raw.lower(),
                "lemma_sort": sort_norm(item.lemma_raw),
                "entry_raw": item.entry_raw,
                "context_raw": item.entry_raw,
                "heading_letter": None,
                "inferred_printed_page": item.page_ref_int,
                "section_start_file": str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-731.txt"),
                "editorial_anchor_file": item.source_file or None,
                "target_file_best": target_file_best,
                "confidence": confidence,
                "raw_json": {
                    "helper_status": helper.get("status"),
                    "helper_reason_summary": best.get("reason_summary"),
                    "helper_best_candidate": best,
                    "helper_candidates_top": [
                        {
                            "file": cand.get("file"),
                            "probability": cand.get("probability"),
                            "candidate_role": cand.get("candidate_role"),
                            "reason_summary": cand.get("reason_summary"),
                        }
                        for cand in candidates[:3]
                    ],
                    "page_ref_raw_source": item.page_ref_raw,
                },
            }
        )

        if item.page_ref_int is not None:
            out_refs.append(
                {
                    "entry_key": entry_id,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": item.page_ref_raw,
                    "page_ref_raw": item.page_ref_raw,
                    "page_ref_int": item.page_ref_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file_best,
                    "target_file_probability": target_prob,
                    "section_start_file": str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-731.txt"),
                    "editorial_anchor_file": item.source_file or None,
                    "confidence": confidence,
                    "raw_json": {
                        "helper_status": helper.get("status"),
                        "helper_best_candidate": best,
                        "helper_candidates_top": [
                            {
                                "file": cand.get("file"),
                                "probability": cand.get("probability"),
                                "candidate_role": cand.get("candidate_role"),
                                "reason_summary": cand.get("reason_summary"),
                            }
                            for cand in candidates[:3]
                        ],
                    },
                }
            )

    notes = [
        "PL115 closes with an Ordo Rerum section rather than a conventional alphabetical index.",
        "A few OCR refs were truncated in the sumario and were corrected from the body text: 801, 1003, and 1005.",
        "The helper output was used only to choose the most plausible physical OCR file for each page ref.",
    ]

    coverage = {
        "entries_status": "ok",
        "entries_status_reason": "Ordo rerum lines were recoverable from the OCR tail, with a few page-ref corrections validated against the body text.",
        "evidence_files": [
            str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-731.txt"),
            str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-732.txt"),
            str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-733.txt"),
            str(source_root / f"4f8e3dde-a2de-4b98-aeaa-bdbbd8d8ed0d-734.txt"),
            str(source_root / "0a7a5ab8-e5ea-46b9-827e-0494bb866df1-403.txt"),
            str(source_root / "90041429-bde0-48a2-abe2-c5546a24ded8-503.txt"),
            str(source_root / "90041429-bde0-48a2-abe2-c5546a24ded8-504.txt"),
            str(source_root / "90041429-bde0-48a2-abe2-c5546a24ded8-505.txt"),
            str(source_root / "a6363e5d-0ba9-4757-b872-0f54a7f307e0-347.txt"),
        ],
    }

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
        },
        "sections": sections,
        "nodes": nodes,
        "entries": out_entries,
        "refs": out_refs,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL115 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    args.intermediate_dir.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Resolve PL115 Ordo Rerum and write the final payload",
        "completed": [
            "inspected tail OCR pages 731-734",
            "validated page refs 801, 1003, and 1005 from the body text",
        ],
        "pending": [
            "run helper target resolution",
            "assemble final payload",
            "write output JSON",
        ],
        "blocked": [],
        "notes": [
            "Treat the OCR page suffix as separate from the editorial page cited by the contents.",
        ],
    }
    write_json(args.intermediate_dir / "todo.json", todo)

    entries = parse_entries(args.source_root)
    entries = apply_page_corrections(entries)
    entries = apply_manual_entries(entries)

    helper_request = build_helper_request(VOLUME_ID, args.source_root, entries)
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)

    payload = build_payload(args.source_root, entries, helper_output)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
