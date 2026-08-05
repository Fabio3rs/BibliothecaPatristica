#!/usr/bin/env python3
"""Build the PL094 alphabetical-index payload.

Usage:
  python scripts/pipeline_index_extraction/build_pl094_alphabetical_payload.py \
    --source-root teste/PL094/text \
    --helper-request-json data/alphabetical_index_payloads/PL094_helper_request.json \
    --helper-output-json data/alphabetical_index_payloads/PL094_helper_output.json \
    --intermediate-dir data/intermediate_payloads/PL094 \
    --output-file data/alphabetical_index_payloads/PL094_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PL094"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina, Tomus XCIV"


SECTION_FILES = [
    "24abe1d9-a39a-49cc-8caa-9c89cc1a9a20-600.txt",
    "24abe1d9-a39a-49cc-8caa-9c89cc1a9a20-601.txt",
    "24abe1d9-a39a-49cc-8caa-9c89cc1a9a20-602.txt",
]

SECTION_START_FILE = SECTION_FILES[0]
SECTION_END_FILE = SECTION_FILES[-1]

PAGE_TOKEN_RE = re.compile(r"\s+(?P<raw>(?:[Ii]bid\.?|1bid\.?|[0-9]{1,4}[a-z]?\.?))\s*$")
BLOCK_RE = re.compile(r'<bloco tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)

SKIP_LINES = {
    "PATROL. XCIV.",
}

SKIP_PREFIXES = (
    "FINIS TOMI",
    "Parisiis. — Ex Typis J.-P. MIGNE.",
)

ENTRY_START_RE = re.compile(
    r"^(?:"
    r"BEDA[AEÆ] OPERA PAR[ÆAE]NTHETICA\.?"
    r"|HOMILIARIUM GENUINARUM\. LIBER PRIMUS\.?"
    r"|HOMILIARIUM GENUINARUM LIBER SECUNDUS\.?"
    r"|HOMILI[ÆAE] SUBDITITI[ÆAE]\.?"
    r"|BREDE EPISTOL[ÆAE]\.?"
    r"|BREDE OPERA HAGIOGRAPHICA\.?"
    r"|BDE OPERA ASCETICA\.?"
    r"|EXCERPTA ex Patribus\.?"
    r"|SIX FORMULAE ORATIONIS\.?"
    r"|BDE CARMINA\.?"
    r"|VITA sanctorum abbatum monasterii Wiramuthensis\.?"
    r"|VITA PROSAICA S\. CUTHBERTI vel CUDDBERTI\.?"
    r"|Observationes Mabillonii\.?"
    r"|Pr[æa]fatio ad B[æae]dium Lindisfarnensem episcopum\.?"
    r"|CAPUT PRIMUM\.?"
    r"|CAP\. ?[IVXLCDM]+\.?"
    r"|EPISTOLA"
    r"|EPIST\. ?[IVXLCDM]+"
    r"|Homilia"
    r"|Hymnus"
    r"|Hymni"
    r"|MARTYROLOGIUM"
    r"|CARMINA SUPPOSTITIA VEL DUBIA\.?"
    r"|DE MEDITATIONE PASSIONIS per septem diei horas\.?"
    r"|DE REMEDIIS PECCATORUM\.?"
    r"|DE LOCIS SANCTIS\.?"
    r"|VITA S\. FELICIS CONFESSORIS\.?"
    r"|Libellus Precum\.?"
    r"|Admonitio previa\.?"
    r"|Incipit libellus\.?"
    r"|DE OFFICIIS libellus\.?"
    r"|De "
    r"|Ratio de Pascha\.?"
    r"|Praefatio\.?"
    r"|Pr[æa]fatio\.?"
    r"|Meditatio"
    r"|Flores ex diversis, quaestiones et parabolae\.?"
    r"|De duodecim lapidibus\.?"
    r"|De septem donis Spiritus sancti\.?"
    r"|De septem ordinibus\.?"
    r"|De quindecim signis\.?"
    r"|De cruce Domini\.?"
    r"|De septem peccatis\.?"
    r"|De quatuor ordinibus\.?"
    r"|De eucharistia sumenda\.?"
    r"|De septem verbis Christi in cruce\.?"
    r"|De diversorum dierum ac temporum jejuniis\.?"
    r"|De calendis Januarii\.?"
    r"|De triduanis jejuniis\.?"
    r"|De litania majore\.?"
    r"|De Pentecoste\.?"
    r"|De jejunio Pentecostes\.?"
    r"|De jejunio septimi mensis\.?"
    r"|De jejunio kalendarium Novembrium\.?"
    r"|De jejunio quatuor temporum tertia quarta\.?"
    r")"
)

MAJOR_NODE_TITLES = [
    "BEDAE OPERA PARÆNTHETICA.",
    "BREDE EPISTOLÆ.",
    "BREDE OPERA HAGIOGRAPHICA.",
    "BDE OPERA ASCETICA.",
    "BDE CARMINA.",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_sort(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def discover_files(source_root: Path) -> list[Path]:
    files = []
    for name in SECTION_FILES:
        path = source_root / name
        if not path.exists():
            raise FileNotFoundError(f"missing OCR file: {path}")
        files.append(path)
    return files


def extract_lines(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    lines: list[tuple[str, str]] = []
    for block_type, block_text in BLOCK_RE.findall(text):
        if block_type not in {"cabecalho", "texto_principal"}:
            continue
        for raw_line in block_text.splitlines():
            line = raw_line.strip()
            if line:
                lines.append((block_type, line))
    return lines


def strip_page_token(line: str) -> tuple[str, str | None, int | None]:
    m = PAGE_TOKEN_RE.search(line)
    if not m:
        return line.rstrip(), None, None
    token = m.group("raw")
    body = line[: m.start()].rstrip()
    if token.lower().startswith("ibid") or token.lower().startswith("1bid"):
        return body, token, None
    digits = re.search(r"\d{1,4}", token)
    page = int(digits.group(0)) if digits else None
    if token == "564":
        page = 364
    elif token == "151d.":
        page = 551
    return body, token, page


@dataclass
class LogicalEntry:
    source_file: Path
    raw_lines: list[str]
    page_token_raw: str | None
    page_ref_int: int | None

    @property
    def entry_raw(self) -> str:
        return "\n".join(self.raw_lines).rstrip()

    @property
    def lemma_raw(self) -> str:
        pieces = []
        for line in self.raw_lines:
            stripped, _, _ = strip_page_token(line)
            pieces.append(stripped)
        return " ".join(piece.strip() for piece in pieces if piece.strip())


def parse_logical_entries(files: list[Path]) -> list[LogicalEntry]:
    entries: list[LogicalEntry] = []
    current: list[str] = []
    current_file: Path | None = None
    current_page_token: str | None = None
    current_page_ref: int | None = None
    last_explicit_page: int | None = None

    def flush() -> None:
        nonlocal current, current_file, current_page_token, current_page_ref, last_explicit_page
        if not current or current_file is None:
            current = []
            current_file = None
            current_page_token = None
            current_page_ref = None
            return
        entries.append(LogicalEntry(current_file, current[:], current_page_token, current_page_ref))
        if current_page_ref is not None:
            last_explicit_page = current_page_ref
        current = []
        current_file = None
        current_page_token = None
        current_page_ref = None

    for file_path in files:
        for block_type, line in extract_lines(file_path):
            if line in SKIP_LINES or any(line.startswith(prefix) for prefix in SKIP_PREFIXES):
                continue
            if current and not ENTRY_START_RE.match(line):
                current.append(line)
                stripped, token, page_ref = strip_page_token(line)
                if token is not None:
                    current_page_token = token
                    current_page_ref = page_ref if page_ref is not None else last_explicit_page
                continue
            flush()
            current_file = file_path
            current = [line]
            stripped, token, page_ref = strip_page_token(line)
            if token is not None:
                current_page_token = token
                current_page_ref = page_ref if page_ref is not None else last_explicit_page
            else:
                current_page_token = None
                current_page_ref = None
        flush()
    return entries


def classify_entry(entry: LogicalEntry) -> str:
    lemma = entry.lemma_raw
    if lemma.startswith(("BEDAE OPERA PAR", "BREDE EPISTOL", "BREDE OPERA HAGIOGRAPHICA", "BDE OPERA ASCETICA", "BDE CARMINA")):
        return "heading_group"
    if lemma.startswith(("PATROL.", "FINIS TOMI", "Parisiis. — Ex Typis")):
        return "editorial_note"
    return "heading_group"


def build_nodes() -> list[dict[str, Any]]:
    return [
        {
            "node_key": f"{VOLUME_ID}:node:001",
            "section_key": f"{VOLUME_ID}:section:001",
            "parent_node_key": None,
            "node_order": 1,
            "node_kind": "heading_group",
            "label_raw": "BEDAE OPERA PARÆNTHETICA.",
            "label_norm": "bedae opera parenthetica",
            "label_sort": "bede opera parenthetica",
            "node_level": 1,
            "confidence": 0.97,
            "raw_json": {
                "source": "ocr_tail",
                "note": "Major contents block for Beda's homilies.",
            },
        },
        {
            "node_key": f"{VOLUME_ID}:node:002",
            "section_key": f"{VOLUME_ID}:section:001",
            "parent_node_key": None,
            "node_order": 2,
            "node_kind": "heading_group",
            "label_raw": "BREDE EPISTOLÆ.",
            "label_norm": "brede epistolae",
            "label_sort": "brede epistolae",
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {"source": "ocr_tail"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:003",
            "section_key": f"{VOLUME_ID}:section:001",
            "parent_node_key": None,
            "node_order": 3,
            "node_kind": "heading_group",
            "label_raw": "BREDE OPERA HAGIOGRAPHICA.",
            "label_norm": "brede opera hagiographica",
            "label_sort": "brede opera hagiographica",
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {"source": "ocr_tail"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:004",
            "section_key": f"{VOLUME_ID}:section:001",
            "parent_node_key": None,
            "node_order": 4,
            "node_kind": "heading_group",
            "label_raw": "BDE OPERA ASCETICA.",
            "label_norm": "bde opera ascetica",
            "label_sort": "bde opera ascetica",
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {"source": "ocr_tail"},
        },
        {
            "node_key": f"{VOLUME_ID}:node:005",
            "section_key": f"{VOLUME_ID}:section:001",
            "parent_node_key": None,
            "node_order": 5,
            "node_kind": "heading_group",
            "label_raw": "BDE CARMINA.",
            "label_norm": "bde carmina",
            "label_sort": "bde carmina",
            "node_level": 1,
            "confidence": 0.95,
            "raw_json": {"source": "ocr_tail"},
        },
    ]


def major_parent_for_entry(lemma_raw: str) -> str | None:
    if lemma_raw.startswith(("BREDE EPISTOLÆ", "EPIST.", "EPISTOLA")):
        return f"{VOLUME_ID}:node:002"
    if lemma_raw.startswith(("BREDE OPERA HAGIOGRAPHICA", "VITA ", "Observationes", "Præfatio", "Praefatio", "CAP", "MARTYROLOGIUM", "DE LOCIS SANCTIS")):
        return f"{VOLUME_ID}:node:003"
    if lemma_raw.startswith(("BDE OPERA ASCETICA", "Libellus", "Admonitio", "Incipit", "DE OFFICIIS", "De ", "Ratio", "EXCERPTA", "SIX FORMULAE ORATIONIS", "DE MEDITATIONE PASSIONIS", "DE REMEDIIS PECCATORUM", "Flores", "De duodecim lapidibus", "De septem", "De quindecim signis", "De cruce Domini", "De eucharistia sumenda", "De septem verbis Christi in cruce")):
        return f"{VOLUME_ID}:node:004"
    if lemma_raw.startswith(("BDE CARMINA", "VITA METRICA", "Hymni", "Hymnus", "CARMINA SUPPOSTITIA")):
        return f"{VOLUME_ID}:node:005"
    if lemma_raw.startswith(("BEDAE OPERA PAR", "HOMILIARIUM", "HOMILIÆ SUBDITITIÆ", "Homilia")):
        return f"{VOLUME_ID}:node:001"
    return None


def build_sections(files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "section_key": f"{VOLUME_ID}:section:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "ordo_rerum",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "ordo rerum quae in hoc tomo continentur",
            "heading_letter": None,
            "page_start": None,
            "page_end": None,
            "file_start": str(files[0]),
            "file_end": str(files[-1]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Final contents table printed as ORDO RERUM; not alphabetical, but a valid editorial-closure contents section for this volume.",
                "source_files": [str(path) for path in files],
                "heading_variants": [
                    "ORDO RERUM",
                    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                    "URDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                ],
            },
        }
    ]


def helper_entry_payload(entry: LogicalEntry, entry_id: str, page_hint_int: int | None) -> dict[str, Any]:
    lemma = entry.lemma_raw
    query_names = [lemma]
    if lemma != entry.entry_raw:
        query_names.append(entry.entry_raw)
    return {
        "entry_id": entry_id,
        "lemma_raw": lemma,
        "query_names": query_names,
        "page_hints": [str(page_hint_int)] if page_hint_int is not None else [],
        "page_hint_ints": [page_hint_int] if page_hint_int is not None else [],
        "context_raw": entry.entry_raw,
    }


def helper_lookup(helper_output: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in helper_output.get("entries", []) or []:
        lookup[str(item.get("entry_id"))] = item
    return lookup


def candidate_evidence_kinds(candidate: dict[str, Any]) -> list[str]:
    evidence = candidate.get("evidence") or []
    kinds = []
    for item in evidence:
        if isinstance(item, dict) and item.get("kind"):
            kinds.append(str(item["kind"]))
    return kinds


def build_payload(source_root: Path, helper_request_json: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    logical_entries = parse_logical_entries(files)

    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    last_explicit_page: int | None = None
    section_start_file = str(files[0])

    for idx, entry in enumerate(logical_entries, start=1):
        lemma_raw = entry.lemma_raw
        entry_kind = classify_entry(entry)
        parent_node_key = major_parent_for_entry(lemma_raw)
        entry_key = f"{VOLUME_ID}:entry:{idx:04d}"

        page_hint_int = entry.page_ref_int
        if page_hint_int is not None:
            last_explicit_page = page_hint_int

        if entry.page_token_raw and page_hint_int is None and last_explicit_page is not None:
            page_hint_int = last_explicit_page

        section_anchor_file = str(entry.source_file)

        confidence = 0.92 if page_hint_int is not None else 0.81
        if entry.page_token_raw in {"564", "151d."}:
            confidence = 0.74

        entry_obj = {
            "entry_key": entry_key,
            "section_key": f"{VOLUME_ID}:section:001",
            "parent_node_key": parent_node_key,
            "entry_order": idx,
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": normalize_sort(lemma_raw),
            "lemma_sort": normalize_sort(lemma_raw),
            "entry_raw": entry.entry_raw,
            "context_raw": entry.entry_raw,
            "heading_letter": None,
            "inferred_printed_page": page_hint_int,
            "section_start_file": section_start_file,
            "editorial_anchor_file": section_anchor_file,
            "target_file_best": None,
            "confidence": confidence,
            "raw_json": {
                "source_file": str(entry.source_file),
                "section_kind": "ordo_rerum",
                "page_token_raw": entry.page_token_raw,
                "page_ref_source": (
                    "explicit"
                    if entry.page_token_raw and entry.page_token_raw.lower() not in {"ibid.", "ibid", "1bid."}
                    else ("ibid" if entry.page_token_raw else None)
                ),
            },
        }
        if entry.page_token_raw in {"564", "151d."}:
            entry_obj["raw_json"]["ocr_page_correction"] = {
                "raw": entry.page_token_raw,
                "corrected_page_ref_int": page_hint_int,
                "reason": "OCR corruption obvious from the local sequence and surrounding OCR evidence.",
            }

        entries.append(entry_obj)

        if page_hint_int is not None:
            helper_entries.append(helper_entry_payload(entry, entry_key, page_hint_int))
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": entry.page_token_raw or str(page_hint_int),
                    "page_ref_raw": entry.page_token_raw or str(page_hint_int),
                    "page_ref_int": page_hint_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": None,
                    "target_file_probability": None,
                    "section_start_file": section_start_file,
                    "editorial_anchor_file": section_anchor_file,
                    "confidence": confidence,
                    "raw_json": {
                        "source_file": str(entry.source_file),
                        "section_kind": "ordo_rerum",
                        "page_token_raw": entry.page_token_raw,
                        "page_ref_source": entry_obj["raw_json"].get("page_ref_source"),
                    },
                }
            )

    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(helper_request_json, helper_request)

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    proc = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    helper_output = read_json(helper_output_json, {"entries": []})
    helper_map = helper_lookup(helper_output)

    for entry in entries:
        helper_item = helper_map.get(entry["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or {}
        top_candidates = helper_item.get("candidates") or []
        entry["raw_json"]["helper_locator"] = {
            "status": helper_item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "top_candidates": [
                {
                    "rank": cand.get("rank"),
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                    "evidence_kinds": candidate_evidence_kinds(cand),
                }
                for cand in top_candidates[:3]
            ],
        }
        if best.get("file"):
            entry["target_file_best"] = best["file"]
            entry["raw_json"]["helper_best_file"] = best["file"]
            entry["raw_json"]["helper_best_probability"] = best.get("probability")

    for ref in refs:
        helper_item = helper_map.get(ref["entry_key"])
        if not helper_item:
            continue
        best = helper_item.get("best_candidate") or {}
        top_candidates = helper_item.get("candidates") or []
        ref["raw_json"]["helper_locator"] = {
            "status": helper_item.get("status"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
            "top_candidates": [
                {
                    "rank": cand.get("rank"),
                    "file": cand.get("file"),
                    "probability": cand.get("probability"),
                    "candidate_role": cand.get("candidate_role"),
                    "reason_summary": cand.get("reason_summary"),
                    "evidence_kinds": candidate_evidence_kinds(cand),
                }
                for cand in top_candidates[:3]
            ],
        }
        if best.get("file"):
            ref["target_file"] = best["file"]
            ref["target_file_probability"] = best.get("probability")
            ref["raw_json"]["helper_best_file"] = best["file"]
            ref["raw_json"]["helper_best_probability"] = best.get("probability")

    manual_overrides = {
        "De septem ordinibus.": {
            "target_file": str(source_root / "69650867-ba22-4718-9be3-dc078319ec74-281.txt"),
            "probability": 0.86,
            "reason": "Direct OCR search found the title on the ascetic-dubia body page with printed 553 and the same section heading.",
            "search": "rg -n -S 'De septem ordinibus|^\\s*553\\b' ...-281.txt",
        },
        "Meditatio ad Tertiam.": {
            "target_file": str(source_root / "69650867-ba22-4718-9be3-dc078319ec74-287.txt"),
            "probability": 0.84,
            "reason": "Direct OCR search found the heading in the ascetic meditation sequence; the helper stayed unresolved because the printed-page hint drifts away from the OCR file metadata.",
            "search": "rg -n -S 'MEDITATIO AD TERTIAM' ...-287.txt",
        },
    }

    for entry in entries:
        override = manual_overrides.get(entry["lemma_raw"])
        if not override:
            continue
        entry["target_file_best"] = override["target_file"]
        entry["confidence"] = max(entry["confidence"], override["probability"])
        entry["raw_json"]["manual_locator"] = {
            "target_file": override["target_file"],
            "probability": override["probability"],
            "reason": override["reason"],
            "search_attempt": override["search"],
        }
        if entry["raw_json"].get("helper_locator", {}).get("status") == "unresolved":
            entry["raw_json"]["helper_locator"]["status"] = "resolved_by_manual_search"

    for ref in refs:
        override = manual_overrides.get(next((e["lemma_raw"] for e in entries if e["entry_key"] == ref["entry_key"]), ""))
        if not override:
            continue
        ref["target_file"] = override["target_file"]
        ref["target_file_probability"] = override["probability"]
        ref["confidence"] = max(ref["confidence"], override["probability"])
        ref["raw_json"]["manual_locator"] = {
            "target_file": override["target_file"],
            "probability": override["probability"],
            "reason": override["reason"],
            "search_attempt": override["search"],
        }
        if ref["raw_json"].get("helper_locator", {}).get("status") == "unresolved":
            ref["raw_json"]["helper_locator"]["status"] = "resolved_by_manual_search"

    sections = build_sections(files)
    nodes = build_nodes()

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "Final volume tail is an ORDO RERUM contents table, not a leaf alphabetical index.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the closing ORDO RERUM contents table from the OCR tail and resolved the material page locators conservatively, preserving OCR literals and known OCR corruptions.",
            "evidence_files": [str(path) for path in files],
        },
        "notes": [
            {
                "note_key": "pl094_ordo_rerum",
                "note_raw": "PL094 ends with a contents table (ORDO RERUM) spanning OCR files 600-602; file suffixes are not editorial pagination.",
                "confidence": 0.98,
            },
            {
                "note_key": "pl094_ocr_corruption",
                "note_raw": "Two page tokens are visibly corrupted in the OCR tail: 564 was normalized to 364 and 151d. was normalized to 551, both with explicit raw values preserved in ref_raw and raw_json.",
                "confidence": 0.9,
            },
        ],
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "todo.json", {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Finalize PL094 ORDO RERUM payload and preserve OCR corrections explicitly",
        "completed": [
            "inspected OCR tail files 600-602",
            "parsed contents entries and page locators",
            "built helper request and ran target locator",
        ],
        "pending": [
            "validate final payload structure",
        ],
        "blocked": [],
        "notes": [
            "Keep OCR file suffix, printed page, and cited reference separate.",
            "Do not collapse Ibid. into a numeric page token in ref_raw.",
        ],
    })
    write_json(intermediate_dir / "manifest.json", {
        "volume_id": VOLUME_ID,
        "generated_at": payload["generated_at"],
        "entries_count": len(entries),
        "refs_count": len(refs),
        "nodes_count": len(nodes),
        "source_files": [str(p) for p in files],
    })
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(intermediate_dir / "coverage.json", payload["coverage"])
    write_json(intermediate_dir / "notes.json", payload["notes"])

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL094 alphabetical index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request_json, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)


if __name__ == "__main__":
    main()
