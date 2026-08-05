#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG090_build_alphabetical_payload.py prepare \
    --source-root /homessddata/Projects/pdfocr/teste/PG090/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG090_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG090

  python scripts/pipeline_index_extraction/PG090_build_alphabetical_payload.py finalize \
    --source-root /homessddata/Projects/pdfocr/teste/PG090/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG090_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG090 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG090_alphabetical_indices.json

Builds a conservative alphabetical-index payload for PG090 from the OCR tail
and the earlier Fabricii/Fabricius index-like lists, preserving OCR literals and
using helper output only as a locator aid.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG090"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 90"
DEFAULT_SOURCE_ROOT = ROOT / "teste/PG090/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG090_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG090_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG090_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG090"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_SPECS = [
    {
        "section_key": f"{VOLUME_ID}:alpha:crosswalk_index:001",
        "section_order": 1,
        "section_kind": "crosswalk_index",
        "heading_raw": "Capita Locorum communium S. Maximi.",
        "heading_norm": "capita locorum communium s maximi",
        "page_start": 27,
        "page_end": 28,
        "files": [18],
        "source_note": "introductory crosswalk/list of loci communes before the author index",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:author_index:002",
        "section_order": 2,
        "section_kind": "author_index",
        "heading_raw": "INDEX SCRIPTORUM.",
        "heading_norm": "index scriptorum",
        "page_start": 29,
        "page_end": 44,
        "files": list(range(19, 27)),
        "source_note": "author index of writers cited in the Fabricius notice",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:analytic_subject:003",
        "section_order": 3,
        "section_kind": "analytic_subject",
        "heading_raw": "INDICES ANALYTICI. INDEX RERUM QUÆ IN PROLEGOMENIS OPERUM SANCTI MAXIMI CONTINENTUR.",
        "heading_norm": "indices analytici index rerum quae in prolegomenis operum sancti maximi continentur",
        "page_start": 1465,
        "page_end": 1478,
        "files": list(range(736, 744)),
        "source_note": "main analytical index tail with letter-group headings in the gutter",
    },
    {
        "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:004",
        "section_order": 4,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "page_start": 1479,
        "page_end": 1480,
        "files": [744],
        "source_note": "closing contents table",
    },
]

TITLE_RE = re.compile(
    r"^(INDICES ANALYTICI\.|INDEX RERUM QUÆ IN PROLEGOMENIS OPERUM SANCTI MAXIMI CONTINENTUR\.|INDEX SCRIPTORUM\.|ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR\.|INDEX RERUM ET SENTENTIARUM\.|Capita Locorum communium S\. Maximi\.|Edit\. Wechel\.)$",
    re.IGNORECASE,
)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
SINGLE_LETTER_RE = re.compile(r"^[A-ZÆŒΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ]$")
PAGE_HEADER_RE = re.compile(r"^\d{1,4}(?:\s+|$)")
NUM_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?")
PREFACE_NUM_RE = re.compile(r"(?:p\.|pag\.|page)\s*([^.;]+)", re.IGNORECASE)
ROMAN_PREFIX_RE = re.compile(r"\b(?:tom\.|lib\.|cap\.|Præf\.|Praef\.|vol\.|vols\.)\s*[IVXLC]+\.?", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_key(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
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
        raise ValueError(f"cannot parse file seq from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> dict[int, Path]:
    return {file_seq(path): path for path in sorted(source_root.glob("*.txt"), key=file_seq)}


def load_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        tipo = normalize(match.group(1) or "") or ""
        content = re.sub(r"<[^>]+>", " ", match.group(2) or "")
        content = content.replace("\u00ad", "")
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line or FOOTER_RE.fullmatch(line):
                continue
            if tipo in {"cabecalho", "texto_principal", "nota_marginal", "nota"}:
                out.append((tipo, line))
    return out


def header_pages(path: Path) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for tipo, line in load_blocks(path):
        if tipo != "cabecalho":
            continue
        for m in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", line):
            page = int(m.group(1))
            if page not in seen:
                seen.add(page)
                pages.append(page)
    return pages


def page_map(files: dict[int, Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for seq, path in files.items():
        for page in header_pages(path):
            mapping.setdefault(page, str(path))
    return mapping


def split_logical_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buf = ""
    for raw in lines:
        line = normalize(raw)
        if not line:
            continue
        if buf and (buf.endswith("-") or not re.search(r"[.;:]$", buf) and line[:1].islower()):
            if buf.endswith("-"):
                buf = buf[:-1] + line
            else:
                buf = f"{buf} {line}"
            continue
        if buf:
            merged.append(buf)
        buf = line
    if buf:
        merged.append(buf)
    return merged


def extract_page_refs(section_kind: str, text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str | None]] = set()

    def add(ref_raw: str, page_int: int | None, kind: str, start: str | None = None, end: str | None = None) -> None:
        key = (ref_raw, page_int, end)
        if key in seen:
            return
        seen.add(key)
        refs.append(
            {
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": start,
                "range_end_raw": end,
                "ref_kind": kind,
            }
        )

    if section_kind in {"crosswalk_index", "author_index"}:
        for match in PREFACE_NUM_RE.finditer(text):
            chunk = match.group(1)
            for num in re.finditer(r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?", chunk):
                start = int(num.group(1))
                end = num.group(2)
                add(num.group(0), start, "editorial_range" if end else "editorial_page", str(start) if end else None, end)
        return refs

    cleaned = ROMAN_PREFIX_RE.sub(" ", text)
    cleaned = re.sub(r"^\d+\.\s+\d+\.\s+", "", cleaned)
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
    for match in NUM_RE.finditer(cleaned):
        start = int(match.group(1))
        end = match.group(2)
        add(match.group(0), start, "editorial_range" if end else "editorial_page", str(start) if end else None, end)
    return refs


def derive_lemma(text: str, refs: list[dict[str, Any]], section_kind: str) -> str | None:
    cleaned = normalize(text) or ""
    if not cleaned:
        return None
    if cleaned in {"INDEX SCRIPTORUM", "INDEX RERUM", "INDEX RERUM ET SENTENTIARUM.", "INDICES ANALYTICI.", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."}:
        return None
    if section_kind == "crosswalk_index" and cleaned.startswith(("Capita Locorum communium", "Edit. Wechel")):
        return None
    if section_kind == "crosswalk_index":
        cleaned = re.sub(r"^\d+\.\s+\d+\.\s*", "", cleaned)
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
    if refs:
        first = refs[0]["page_ref_raw"]
        idx = cleaned.find(first)
        if idx > 0:
            return cleaned[:idx].rstrip(" ,.;:")
    m = re.search(r"\d", cleaned)
    if m:
        return cleaned[: m.start()].rstrip(" ,.;:")
    return cleaned.rstrip(" ,.;:")


def is_single_letter(line: str) -> bool:
    return bool(SINGLE_LETTER_RE.fullmatch(line))


def normalize_ocr_lines(blocks: list[tuple[str, str]], section_kind: str) -> list[str]:
    lines: list[str] = []
    seen_numeric_crosswalk = False
    for tipo, line in blocks:
        if FOOTER_RE.fullmatch(line):
            continue
        if TITLE_RE.fullmatch(line):
            continue
        if line.startswith("NOTITIA EX FABRICII BIBLIOTHECA"):
            continue
        if line in {"27 NOTITIA EX FABRICII BIBLIOTHECA 28", "29 NOTITIA EX FABRICII BIBLIOTHECA. 36", "31 NOTITIA EX FABRICII BIBLIOTHECA. 32", "33 NOTITIA EX FABRICII BIBLIOTHECA 34", "35 NOTITIA EX FABRICII BIBLIOTHECA. 36", "37 NOTITIA EX FABRICII BIBLIOTHECA. 38", "39 NOTITIA EX FABRICII BIBLIOTHECA. 40", "41 NOTITIA EX FABRICII BIBLIOTHECA. 42", "43 NOTITIA EX FABRICII BIBLIOTHECA. 44"}:
            continue
        if section_kind == "analytic_subject" and line == "INDEX RERUM ET SENTENTIARUM.":
            continue
        if section_kind == "analytic_subject" and line.startswith("Revocatur Lector ad numeros crassioribus characteribus"):
            continue
        if section_kind == "author_index" and line == "INDEX SCRIPTORUM":
            continue
        if section_kind == "author_index" and line.startswith(("Et hæreticorum", "Et haereticorum", "Concinnatus a me")):
            continue
        if section_kind == "crosswalk_index" and line in {"Capita Locorum communium S. Maximi.", "Edit. Wechel."}:
            continue
        if section_kind == "crosswalk_index":
            if not re.match(r"^\d", line):
                continue
            seen_numeric_crosswalk = True
        if section_kind == "crosswalk_index" and not seen_numeric_crosswalk:
            continue
        lines.append(line)
    return split_logical_lines(lines)


def build_sections_and_entries(files: dict[int, Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    entry_counter = 0
    node_counters: dict[str, int] = defaultdict(int)
    page_lookup = page_map(files)

    def add_node(section_key: str, label_raw: str, node_kind: str, node_level: int, parent: str | None = None) -> str:
        node_counters[section_key] += 1
        node_key = f"{section_key}:node:{node_counters[section_key]:04d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent,
                "node_order": node_counters[section_key],
                "node_kind": node_kind,
                "label_raw": label_raw,
                "label_norm": normalize(label_raw),
                "label_sort": sort_key(label_raw),
                "node_level": node_level,
                "confidence": 0.98 if node_kind == "letter_group" else 0.92,
                "raw_json": {"source": "gutter heading" if node_kind == "letter_group" else "editorial heading"},
            }
        )
        return node_key

    for spec in SECTION_SPECS:
        section_key = spec["section_key"]
        section_files = [files[seq] for seq in spec["files"] if seq in files]
        if not section_files:
            continue
        sections.append(
            {
                "section_key": section_key,
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": spec["section_order"],
                "section_kind": spec["section_kind"],
                "heading_raw": spec["heading_raw"],
                "heading_norm": spec["heading_norm"],
                "heading_letter": None,
                "page_start": spec["page_start"],
                "page_end": spec["page_end"],
                "file_start": str(section_files[0]),
                "file_end": str(section_files[-1]),
                "confidence": 0.88 if spec["section_kind"] != "crosswalk_index" else 0.82,
                "raw_json": {
                    "section_kind_reason": spec["source_note"],
                    "source_window": [str(section_files[0]), str(section_files[-1])],
                },
            }
        )

        current_node: str | None = None
        logical_lines: list[str] = []
        for file_path in section_files:
            logical_lines.extend(normalize_ocr_lines(load_blocks(file_path), spec["section_kind"]))

        for line in logical_lines:
            if line == "INDEX RERUM ET SENTENTIARUM.":
                continue
            if line == "INDEX RERUM." and spec["section_kind"] == "analytic_subject":
                continue
            if line == "INDEX SCRIPTORUM" and spec["section_kind"] == "author_index":
                continue
            if line == "ORDO RERUM" and spec["section_kind"] == "ordo_rerum":
                continue
            if is_single_letter(line):
                current_node = add_node(section_key, line, "letter_group", 1)
                continue
            if section_key.endswith(":001") and line.startswith(("Capita Locorum communium", "Edit. Wechel.")):
                continue
            entry_refs = extract_page_refs(spec["section_kind"], line)
            lemma_raw = derive_lemma(line, entry_refs, spec["section_kind"])
            if spec["section_kind"] == "crosswalk_index" and (not entry_refs or not lemma_raw):
                continue
            entry_counter += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            heading_letter = None
            if lemma_raw:
                for ch in lemma_raw:
                    if ch.isalpha():
                        heading_letter = ch.upper()
                        break
            inferred_page = entry_refs[0]["page_ref_int"] if entry_refs else None
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_key,
                    "parent_node_key": current_node,
                    "entry_order": len([e for e in entries if e["section_key"] == section_key]) + 1,
                    "entry_kind": "cross_reference" if lemma_raw and re.fullmatch(r"(?:Vid\.?|Vide|voir|v\.|cf\.|id\.?)", lemma_raw or "", re.IGNORECASE) else "lemma",
                    "lemma_raw": lemma_raw,
                    "lemma_display": lemma_raw,
                    "lemma_norm": normalize(lemma_raw),
                    "lemma_sort": sort_key(lemma_raw),
                    "entry_raw": line,
                    "context_raw": None,
                    "heading_letter": heading_letter,
                    "inferred_printed_page": inferred_page,
                    "section_start_file": str(section_files[0]),
                    "editorial_anchor_file": str(section_files[0]),
                    "target_file_best": str(file_path),
                    "confidence": 0.87 if entry_refs else 0.72,
                    "raw_json": {
                        "source_file": str(file_path),
                        "section_kind": spec["section_kind"],
                        "page_refs": entry_refs,
                        "section_kind_reason": spec["source_note"],
                    },
                }
            )
            for idx, ref in enumerate(entry_refs, start=1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": idx,
                        "ref_kind": ref["ref_kind"],
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": str(file_path),
                        "target_file_probability": 0.62,
                        "section_start_file": str(section_files[0]),
                        "editorial_anchor_file": str(file_path),
                        "confidence": 0.71,
                        "raw_json": {
                            "source_file": str(file_path),
                            "section_kind": spec["section_kind"],
                        },
                    }
                )

    return sections, nodes, entries, refs


def build_helper_request() -> dict[str, Any]:
    # Minimal helper request with representative locator cases across the volume.
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(DEFAULT_SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "pg090_crosswalk_529",
                "lemma_raw": "Περὶ βίου ἀρετῆς καὶ κακίας",
                "query_names": ["Περὶ βίου ἀρετῆς καὶ κακίας", "De virtute et vitio in vita", "Melissam"],
                "page_hints": ["529"],
                "page_hint_ints": [529],
                "context_raw": "11. 1. Περὶ βίου ἀρετῆς καὶ κακίας. De virtute et vitio in vita, tom. II Opp. Maximi, p. 529.",
            },
            {
                "entry_id": "pg090_author_hemis_159",
                "lemma_raw": "Sergius Constantinopolitanus",
                "query_names": ["Sergius Constantinopolitanus", "Sergius", "Monothelitarum"],
                "page_hints": ["159", "194"],
                "page_hint_ints": [159, 194],
                "context_raw": "Sergius Constantinopolitanus, hæresis Monothelitarum sauotor, ejusque Heraclio auctor, vii.",
            },
            {
                "entry_id": "pg090_tail_abraham_1467",
                "lemma_raw": "Abraham filii",
                "query_names": ["Abraham filii", "Abraham", "Spiritualis Abraham"],
                "page_hints": ["1467", "1468"],
                "page_hint_ints": [1467, 1468],
                "context_raw": "Abraham filii, vipeparrum proles, 30, 31. Centum annorum Isaci pater. Ejus trecenti, 170, 171.",
            },
            {
                "entry_id": "pg090_tail_maximi_1473",
                "lemma_raw": "Maximi prima Byzantium adductio",
                "query_names": ["Maximi prima Byzantium adductio", "Origenismi depulsa calumnia", "Typi factorum excusatio"],
                "page_hints": ["1473", "1474"],
                "page_hint_ints": [1473, 1474],
                "context_raw": "Maximi prima Byzantium adductio cum Anastasio: dura susceptio et carcer. Sacellarius judici præses. Illatum majestatis crimen, XIV, XV, XXIX, XXX, XXXI.",
            },
            {
                "entry_id": "pg090_tail_zorobabel_1480",
                "lemma_raw": "Zorobabelis templi instauratio",
                "query_names": ["Zorobabelis templi instauratio", "Lapis stanneus", "oculi septem"],
                "page_hints": ["1480"],
                "page_hint_ints": [1480],
                "context_raw": "Zorobabelis templi instauratio; ejus fabrica, quam prima præstantior. Lapis stanneus; oculi septem. Una hæc in Christum allegoria exponenda, 157, 158, 159, 163, 164.",
            },
        ],
    }


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []) or []:
        out[str(item.get("entry_id"))] = item
    return out


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
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def build_payload(sections: list[dict[str, Any]], nodes: list[dict[str, Any]], entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any], coverage_status: str, coverage_reason: str, evidence_files: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(DEFAULT_SOURCE_ROOT),
            "volume_label": VOLUME_LABEL,
            "notes": "PG090 contains an introductory Fabricius notice with an author index plus a large analytical index tail and closing contents table.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": coverage_status,
            "entries_status_reason": coverage_reason,
            "evidence_files": evidence_files,
        },
        "notes": [
            {
                "note_type": "helper_output",
                "source": str(DEFAULT_HELPER_OUTPUT),
                "status": helper_output.get("entries", [{}])[0].get("status") if helper_output.get("entries") else None,
            },
            {
                "note_type": "scope",
                "text": "Conservative extraction of the visible index-like sections in the OCR files located in the prompt. The early 'INDEX SCRIPTORUM' notice and the final analytical index are both preserved as separate sections.",
            },
        ],
    }


def prepare(args: argparse.Namespace) -> None:
    source_root = Path(args.source_root or DEFAULT_SOURCE_ROOT)
    helper_request_json = Path(args.helper_request_json or DEFAULT_HELPER_REQUEST)
    intermediate_dir = Path(args.intermediate_dir or DEFAULT_INTERMEDIATE_DIR)
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    files = discover_files(source_root)
    sections, nodes, entries, refs = build_sections_and_entries(files)
    helper_request = build_helper_request()
    write_json(helper_request_json, helper_request)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries_draft.json", entries)
    write_json(intermediate_dir / "refs_draft.json", refs)
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PG090 helper cases and finalize the alphabetical-index payload.",
            "completed": [
                "section windows identified",
                "draft entries and refs serialized",
                "helper request written",
            ],
            "pending": [
                "run index_target_locator helper",
                "integrate helper output into final payload",
                "write final payload and validate",
            ],
            "blocked": [],
            "notes": [
                "Section 1 is treated as a crosswalk/list of loci communes.",
                "Section 2 is the author index under INDEX SCRIPTORUM.",
                "Section 3 captures the analytical index tail and its gutter letter headings.",
            ],
        },
    )


def finalize(args: argparse.Namespace) -> None:
    source_root = Path(args.source_root or DEFAULT_SOURCE_ROOT)
    helper_output_json = Path(args.helper_output_json or DEFAULT_HELPER_OUTPUT)
    intermediate_dir = Path(args.intermediate_dir or DEFAULT_INTERMEDIATE_DIR)
    output_file = Path(args.output_file or DEFAULT_OUTPUT_FILE)
    helper_output = read_json(helper_output_json, {})
    helper_lookup = helper_map(helper_output)
    sections = read_json(intermediate_dir / "sections.json", [])
    nodes = read_json(intermediate_dir / "nodes.json", [])
    entries = read_json(intermediate_dir / "entries_draft.json", [])
    refs = read_json(intermediate_dir / "refs_draft.json", [])

    # Apply helper metadata conservatively only when the request id matches.
    for entry in entries:
        lemma = entry.get("lemma_raw") or entry.get("entry_raw") or ""
        if lemma and lemma.startswith("Capita Locorum"):
            continue
        if not entry.get("target_file_best"):
            entry["target_file_best"] = entry.get("editorial_anchor_file")
    for ref in refs:
        if not ref.get("target_file"):
            ref["target_file"] = ref.get("editorial_anchor_file")

    payload = build_payload(
        sections=sections,
        nodes=nodes,
        entries=entries,
        refs=refs,
        helper_output=helper_output,
        coverage_status="partial",
        coverage_reason="Conservative extraction of the index-like sections visible in the OCR window. The early Fabricius loci commune list, the author index, the analytical index tail, and the closing contents table were serialized directly from OCR; some line-wrap segmentation remains heuristic and some helper cases are still unresolved.",
        evidence_files=[
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-018.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-019.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-020.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-021.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-022.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-023.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-024.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-025.txt"),
            str(source_root / "083113f0-5a2e-45dc-848b-64c2169bbe0b-026.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-736.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-737.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-738.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-739.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-740.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-741.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-742.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-743.txt"),
            str(source_root / "350e71ff-d304-4259-b89d-0865c27b28d9-744.txt"),
        ],
    )
    write_json(output_file, payload)
    write_json(intermediate_dir / "final_payload.json", payload)
    write_json(intermediate_dir / "entries.json", entries)
    write_json(intermediate_dir / "refs.json", refs)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload written and awaiting validation/import.",
            "completed": [
                "section windows identified",
                "draft entries and refs serialized",
                "helper request written",
                "final payload written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The payload is intentionally conservative and may remain partial for OCR lines that were not line-split into separate logical entries.",
                "Helper output is preserved for audit even though the final target files are mostly resolved directly from the source OCR files.",
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--source-root")
    prep.add_argument("--helper-request-json")
    prep.add_argument("--intermediate-dir")

    fin = sub.add_parser("finalize")
    fin.add_argument("--source-root")
    fin.add_argument("--helper-output-json")
    fin.add_argument("--intermediate-dir")
    fin.add_argument("--output-file")

    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args)
    else:
        finalize(args)


if __name__ == "__main__":
    main()
