#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg036_alphabetical_payload.py request \
    --source-root /homessddata/Projects/pdfocr/teste/PG036/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG036_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG036

  python scripts/pipeline_index_extraction/build_pg036_alphabetical_payload.py finalize \
    --source-root /homessddata/Projects/pdfocr/teste/PG036/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG036_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG036 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG036_alphabetical_indices.json

Builds the PG036 alphabetical-index payload from OCR line blocks, preserving
material page references and the final ORDO RERUM contents table as a separate
section.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG036"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 36"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG036/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG036_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG036_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG036_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG036"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION1 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS.",
    "heading_norm": "index analyticus",
    "heading_letter": None,
    "page_start": 1261,
    "page_end": 1366,
    "file_start_seq": 668,
    "file_end_seq": 720,
    "section_kind_reason": "Main analytical index in the volume; pages 720 carries the final Z entries before the contents table begins.",
}

SECTION2 = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:002",
    "section_order": 2,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": 1365,
    "page_end": 1366,
    "file_start_seq": 720,
    "file_end_seq": 720,
    "section_kind_reason": "Editorial contents table beginning after the analytical index closes; distinct from alphabetical entries.",
}

SECTION2_HEADINGS = {
    "S. GREGORIUS THEOLOGUS, ARCHIEPISCOPUS CONSTANTINOPOLITANUS.",
    "ORATIONES S. GREGORII.",
    "APPENDIX.",
    "NICETAS SERRONIUS.",
    "NONNUS ABBAS.",
    "BASILIUS MINIMUS.",
    "ANONYMI SCHOLIA IN ORATIONEM I CONTRA JULIANUM.",
}

SECTION2_NOTE_PREFIXES = (
    "Monitum",
    "Significatio",
    "Metaphrasis",
    "Tractatus",
    "Fragmentum",
    "Liturgia",
    "Precatio",
    "RUFINI",
    "ELIÆ METROPOLITÆ CÆSAREÆ",
    "Epistola",
    "Præfatio",
    "Conspectus",
    "Commentarii",
    "Additamenta",
    "Indices",
    "Notitia",
    "In orationem",
    "Ejusdem",
)

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
LEADING_PAGE_ONLY_RE = re.compile(r"^\d{1,4}(?:/\d{1,4})?$")
PAGE_REF_RE = re.compile(
    r"(?<!\d)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?",
)
FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
HEADER_TITLE_RE = re.compile(r"^(INDEX ANALYTICUS(?:\.|$)|ORDO RERUM(?: QUÆ IN HOC TOMO CONTINENTUR\.)?|ORDO RERUM\.?)$", re.IGNORECASE)

HELPER_REQUEST_ENTRIES = [
    {
        "entry_id": "pg036_athanasius_589",
        "lemma_raw": "Athanasius (S.)",
        "query_names": [
            "Athanasius",
            "S. Athanasius",
            "Athanasius (S.) in divinis moribus ac disciplinis educatus",
        ],
        "page_hints": ["589"],
        "page_hint_ints": [589],
        "context_raw": "Athanasius (S.) in divinis moribus ac disciplinis educatus, 589.",
    },
    {
        "entry_id": "pg036_baptismus_695",
        "lemma_raw": "Baptismus",
        "query_names": [
            "Baptismus omnium Dei beneficiorum",
            "Baptisma Christi mysterium excelsum",
            "Baptismus non differendus",
        ],
        "page_hints": ["695"],
        "page_hint_ints": [695],
        "context_raw": "Baptismus omnium Dei beneficiorum præclarissimum est et præstantissimum, 692. Quo fine institutus, 695.",
    },
    {
        "entry_id": "pg036_gregorius_pater_528",
        "lemma_raw": "Gregorius, Theologi pater",
        "query_names": [
            "Gregorius, Theologi pater",
            "Hypsistariarum",
            "Gregorius et Nonna",
        ],
        "page_hints": ["528"],
        "page_hint_ints": [528],
        "context_raw": "Gregorius, Theologi pater, in Hypsistariarum errore aliquando versatur. 528, 533.",
    },
    {
        "entry_id": "pg036_jeremias_901",
        "lemma_raw": "Jeremias",
        "query_names": [
            "Jeremias",
            "Quousque Jerosolymam defleat",
            "Jeremiæ Threnorum lectio lacrymas movet",
        ],
        "page_hints": ["901"],
        "page_hint_ints": [901],
        "context_raw": "Jeremias, receptissimus prophetarum, 901. Prophetarum omnium ad commiserationem propensissimus, 517.",
    },
    {
        "entry_id": "pg036_jesus_258",
        "lemma_raw": "Jesus",
        "query_names": [
            "Jesus, nostri causa homo factus",
            "Christus, Filius Dei, Incarnatio",
            "Jesus pura perfectio est",
        ],
        "page_hints": ["258"],
        "page_hint_ints": [258],
        "context_raw": "Jesus, nostri causa homo factus, 258. Vide Christus, Filius Dei, Incarnatio.",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def append_ocr_continuation(base: str, continuation: str) -> str:
    if base.rstrip().endswith("-"):
        return f"{base.rstrip()[:-1]}{continuation.lstrip()}"
    return f"{base} {continuation}"


def norm_sort(text: str | None) -> str | None:
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


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_bloco_lines(path: Path) -> list[dict[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[dict[str, str]] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        tipo = normalize(match.group(1) or "") or ""
        if tipo not in {"texto", "texto_principal", "titulo"}:
            continue
        content = re.sub(r"<[^>]+>", " ", match.group(2) or "")
        content = content.replace("\u00ad", "")
        for raw_line in content.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            out.append({"tipo": tipo, "line": line})
    return out


def header_page_numbers(path: Path) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for item in extract_bloco_lines(path):
        if item["tipo"] != "cabecalho":
            continue
        for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", item["line"]):
            value = int(match.group(1))
            if value not in seen:
                seen.add(value)
                numbers.append(value)
    return numbers


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for page in header_page_numbers(path):
            mapping.setdefault(page, str(path))
    return mapping


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"start": start, "end": end, "raw": match.group(0).strip()})
    return refs


def is_cross_reference(text: str) -> bool:
    return bool(re.search(r"\b(Vide|Vid\.|voir|cf\.|id\.)\b", text, flags=re.IGNORECASE))


def is_section1_heading(text: str) -> bool:
    return HEADER_TITLE_RE.fullmatch(text) is not None


def is_section2_heading(text: str) -> bool:
    normalized = normalize(text)
    if not normalized:
        return False
    return normalized.upper() in SECTION2_HEADINGS


def is_section2_note(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in SECTION2_NOTE_PREFIXES)


def guess_lemma(text: str) -> str | None:
    working = normalize(text)
    if not working:
        return None
    for token in extract_page_refs(working):
        pos = working.find(token["raw"])
        if pos > 0:
            working = working[:pos].strip()
            break
    if "," in working:
        comma_prefix = working.split(",", 1)[0].strip()
        if comma_prefix:
            working = comma_prefix
    working = working.rstrip(" .;:")
    if "." in working and not re.search(r"\b(?:S|S\.|S\.? )\b", working):
        parts = working.split(".")
        if parts and len(parts[0].strip()) > 1:
            working = parts[0].strip()
    return working or None


def section_for_seq(seq: int) -> dict[str, Any]:
    if SECTION1["file_start_seq"] <= seq <= SECTION1["file_end_seq"]:
        return SECTION1
    if SECTION2["file_start_seq"] <= seq <= SECTION2["file_end_seq"]:
        return SECTION2
    raise ValueError(f"file seq {seq} outside PG036 extraction window")


def section_key_for_section(section: dict[str, Any]) -> str:
    return section["section_key"]


def load_helper_summary(helper_request_path: Path, helper_output_path: Path) -> dict[str, dict[str, Any]]:
    req = read_json(helper_request_path, {})
    out = read_json(helper_output_path, {})
    request_entries = {item.get("entry_id"): item for item in req.get("entries", []) if item.get("entry_id")}
    summary: dict[str, dict[str, Any]] = {}
    for item in out.get("entries", []):
        entry_id = item.get("entry_id")
        if not entry_id or entry_id not in request_entries:
            continue
        candidate = (item.get("candidates") or [{}])[0] or {}
        req_item = request_entries[entry_id]
        summary[entry_id] = {
            "status": item.get("status"),
            "candidate_role": candidate.get("candidate_role"),
            "reason_summary": candidate.get("reason_summary") or item.get("reason_summary"),
            "best_candidate": {
                "file": candidate.get("file") or (item.get("best_candidate") or {}).get("file"),
                "probability": candidate.get("probability") or (item.get("best_candidate") or {}).get("probability"),
                "candidate_role": candidate.get("candidate_role") or (item.get("best_candidate") or {}).get("candidate_role"),
                "inferred_printed_page": candidate.get("inferred_printed_page") or (item.get("best_candidate") or {}).get("inferred_printed_page"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in candidate.get("evidence", [])
                    if isinstance(ev, dict) and ev.get("kind")
                ][:8],
            },
            "query_names": req_item.get("query_names"),
            "page_hints": req_item.get("page_hint_ints") or req_item.get("page_hints"),
        }
    return summary


def helper_request_payload(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": HELPER_REQUEST_ENTRIES,
    }


def update_todo(intermediate_dir: Path, current_focus: str, completed: list[str], pending: list[str], blocked: list[str], notes: list[str]) -> None:
    write_json(
        intermediate_dir / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": current_focus,
            "completed": completed,
            "pending": pending,
            "blocked": blocked,
            "notes": notes,
        },
    )


def make_entry(
    *,
    section: dict[str, Any],
    entry_order: int,
    text: str,
    source_file: str,
    letter: str | None,
    page_map: dict[int, str],
    helper_summary: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    entry_raw = normalize(text) or text
    lemma_raw = guess_lemma(entry_raw)
    page_refs = extract_page_refs(entry_raw)
    entry_kind = "lemma"
    if is_cross_reference(entry_raw) and not page_refs:
        entry_kind = "cross_reference"
    elif section["section_kind"] == "ordo_rerum" and (
        any(entry_raw.startswith(prefix) for prefix in SECTION2_NOTE_PREFIXES) or entry_raw.startswith("RUFINI") or entry_raw.startswith("ELIÆ")
    ):
        entry_kind = "editorial_note"

    first_ref = page_refs[0]["start"] if page_refs else None
    target_file_best = page_map.get(first_ref) if first_ref is not None else None
    if target_file_best is None:
        target_file_best = source_file
    inferred_printed_page = first_ref

    entry_key = f"{section['section_key']}:entry:{entry_order:04d}"
    context_raw = None
    if len(entry_raw) > 220:
        context_raw = entry_raw[:220]

    helper_id = None
    helper_note = None
    if lemma_raw:
        lemma_lower = lemma_raw.lower()
        for entry_id, summary in helper_summary.items():
            query_names = [normalize(q).lower() for q in (summary.get("query_names") or []) if normalize(q)]
            if not query_names:
                continue
            if any(q == lemma_lower or q in lemma_lower or lemma_lower in q for q in query_names):
                helper_id = entry_id
                helper_note = summary
                break

    entry = {
        "entry_key": entry_key,
        "section_key": section["section_key"],
        "parent_node_key": None,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_raw,
        "lemma_norm": norm_sort(lemma_raw),
        "lemma_sort": norm_sort(lemma_raw),
        "entry_raw": entry_raw,
        "context_raw": context_raw,
        "heading_letter": letter,
        "inferred_printed_page": inferred_printed_page,
        "section_start_file": source_file,
        "editorial_anchor_file": source_file,
        "target_file_best": target_file_best,
        "confidence": 0.91 if page_refs else (0.72 if entry_kind == "cross_reference" else 0.78),
        "raw_json": {
            "source_file": source_file,
            "section_kind": section["section_kind"],
            "entry_kind_reason": "Line-level OCR extraction with wrapped continuations preserved conservatively.",
            "page_refs": page_refs,
            "helper_entry": helper_id,
            "helper_summary": helper_note,
        },
    }

    refs: list[dict[str, Any]] = []
    for ref_order, ref in enumerate(page_refs, start=1):
        target_file = page_map.get(ref["start"]) or target_file_best
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": ref["raw"],
                "page_ref_raw": ref["raw"],
                "page_ref_int": ref["start"],
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": str(ref["start"]) if ref["end"] is not None else None,
                "range_end_raw": str(ref["end"]) if ref["end"] is not None else None,
                "target_file": target_file,
                "target_file_probability": 0.97 if target_file != source_file else 0.55,
                "section_start_file": source_file,
                "editorial_anchor_file": source_file,
                "confidence": 0.92 if target_file != source_file else 0.7,
                "raw_json": {
                    "page_map_hit": target_file != source_file,
                    "source_entry": entry_raw[:260],
                },
            }
        )

    scripture_refs: list[dict[str, Any]] = []
    return entry, refs, scripture_refs


def build_payload(source_root: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    all_files = discover_files(source_root)
    work_files = [
        path
        for path in all_files
        if SECTION1["file_start_seq"] <= file_seq(path) <= SECTION2["file_end_seq"]
    ]
    page_map = build_page_map(all_files)
    helper_summary = load_helper_summary(DEFAULT_HELPER_REQUEST, helper_output_json)

    sections = [
        {
            "section_key": SECTION1["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": SECTION1["section_order"],
            "section_kind": SECTION1["section_kind"],
            "heading_raw": SECTION1["heading_raw"],
            "heading_norm": SECTION1["heading_norm"],
            "heading_letter": SECTION1["heading_letter"],
            "page_start": SECTION1["page_start"],
            "page_end": SECTION1["page_end"],
            "file_start": str(work_files[0]),
            "file_end": str(work_files[-1]),
            "confidence": 0.84,
            "raw_json": {
                "section_kind_reason": SECTION1["section_kind_reason"],
                "source_window": [str(work_files[0]), str(work_files[-1])],
                "boundary_notes": [
                    "File 720 carries the final Z entries before the contents table starts on the same OCR page.",
                ],
            },
        },
        {
            "section_key": SECTION2["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": SECTION2["section_order"],
            "section_kind": SECTION2["section_kind"],
            "heading_raw": SECTION2["heading_raw"],
            "heading_norm": SECTION2["heading_norm"],
            "heading_letter": SECTION2["heading_letter"],
            "page_start": SECTION2["page_start"],
            "page_end": SECTION2["page_end"],
            "file_start": str(work_files[-1]),
            "file_end": str(work_files[-1]),
            "confidence": 0.89,
            "raw_json": {
                "section_kind_reason": SECTION2["section_kind_reason"],
                "source_window": [str(work_files[-1])],
            },
        },
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    current_section = SECTION1
    current_letter = None
    current_entry_text: str | None = None
    current_entry_source_file: str | None = None
    current_entry_section = SECTION1
    current_entry_letter: str | None = None
    entry_order_by_section = {SECTION1["section_key"]: 0, SECTION2["section_key"]: 0}
    node_order_by_section = {SECTION1["section_key"]: 0, SECTION2["section_key"]: 0}
    node_for_letter: dict[tuple[str, str], str] = {}
    current_macro_node: str | None = None
    current_macro_label: str | None = None

    def ensure_letter_node(section: dict[str, Any], letter: str) -> str:
        key = (section["section_key"], letter)
        if key in node_for_letter:
            return node_for_letter[key]
        node_order_by_section[section["section_key"]] += 1
        node_key = f"{section['section_key']}:node:{node_order_by_section[section['section_key']]:04d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "node_order": node_order_by_section[section["section_key"]],
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"source": "standalone letter heading"},
            }
        )
        node_for_letter[key] = node_key
        return node_key

    def ensure_macro_node(section: dict[str, Any], label: str) -> str:
        nonlocal current_macro_node, current_macro_label
        if current_macro_label == label and current_macro_node:
            return current_macro_node
        node_order_by_section[section["section_key"]] += 1
        node_key = f"{section['section_key']}:node:{node_order_by_section[section['section_key']]:04d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section["section_key"],
                "parent_node_key": None,
                "node_order": node_order_by_section[section["section_key"]],
                "node_kind": "heading_group",
                "label_raw": label,
                "label_norm": normalize(label),
                "label_sort": norm_sort(label),
                "node_level": 1,
                "confidence": 0.93,
                "raw_json": {"source": "ordo_rerum macro heading"},
            }
        )
        current_macro_node = node_key
        current_macro_label = label
        return node_key

    def commit_entry() -> None:
        nonlocal current_entry_text, current_entry_source_file, current_entry_letter, current_entry_section
        if not current_entry_text or not current_entry_source_file:
            current_entry_text = None
            current_entry_source_file = None
            current_entry_letter = None
            current_entry_section = current_section
            return
        entry_order_by_section[current_entry_section["section_key"]] += 1
        entry, entry_refs, entry_scripture_refs = make_entry(
            section=current_entry_section,
            entry_order=entry_order_by_section[current_entry_section["section_key"]],
            text=current_entry_text,
            source_file=current_entry_source_file,
            letter=current_entry_letter,
            page_map=page_map,
            helper_summary=helper_summary,
        )
        if current_entry_letter:
            entry["parent_node_key"] = ensure_letter_node(current_entry_section, current_entry_letter)
        elif current_entry_section["section_kind"] == "ordo_rerum" and current_macro_node:
            entry["parent_node_key"] = current_macro_node
        entries.append(entry)
        refs.extend(entry_refs)
        scripture_refs.extend(entry_scripture_refs)
        current_entry_text = None
        current_entry_source_file = None
        current_entry_letter = None
        current_entry_section = current_section

    for path in work_files:
        blocks = extract_bloco_lines(path)
        source_file = str(path)

        for item in blocks:
            tipo = item["tipo"]
            line = item["line"]
            if tipo == "cabecalho":
                continue
            if FOOTER_RE.fullmatch(line):
                continue
            if current_section["section_kind"] == "analytic_subject" and (line == "ORDO RERUM" or is_section2_heading(line)):
                commit_entry()
                current_section = SECTION2
                current_macro_node = None
                current_macro_label = None
                ensure_macro_node(current_section, line)
                continue
            if current_section["section_kind"] == "ordo_rerum" and is_section2_heading(line):
                commit_entry()
                ensure_macro_node(current_section, line)
                continue
            if is_section1_heading(line):
                continue

            if LEADING_PAGE_ONLY_RE.fullmatch(line):
                continue

            if LETTER_RE.fullmatch(line):
                commit_entry()
                current_letter = line
                ensure_letter_node(current_section, line)
                continue

            if current_section["section_kind"] == "ordo_rerum" and is_section2_note(line) and not extract_page_refs(line):
                commit_entry()
                current_entry_text = line
                current_entry_source_file = source_file
                current_entry_section = current_section
                current_entry_letter = None
                commit_entry()
                continue

            if line.startswith("- ") and current_entry_text:
                current_entry_text = append_ocr_continuation(current_entry_text, line[2:].strip())
                continue

            if re.match(r"^[a-zæœ]", line) and current_entry_text:
                current_entry_text = append_ocr_continuation(current_entry_text, line)
                continue

            if re.match(r"^[A-ZÆŒ]", line):
                commit_entry()
                current_entry_text = line
                current_entry_source_file = source_file
                current_entry_section = current_section
                current_entry_letter = current_letter
                continue

            if current_entry_text:
                current_entry_text = append_ocr_continuation(current_entry_text, line)

        if not (current_entry_text and current_entry_text.rstrip().endswith("-")):
            commit_entry()

    # remove accidental empty entries from structural headings that slipped through
    filtered_entries: list[dict[str, Any]] = []
    filtered_refs: list[dict[str, Any]] = []
    filtered_scripture_refs: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.get("lemma_raw") and entry.get("entry_kind") not in {"editorial_note", "cross_reference"}:
            continue
        filtered_entries.append(entry)
    filtered_refs.extend(refs)
    filtered_scripture_refs.extend(scripture_refs)

    coverage = {
        "entries_status": "partial",
        "entries_status_reason": "Conservative line-level extraction of the analytical index and the closing ORDO RERUM table; wrapped OCR continuations were merged where they were clearly part of the same line, but some cross-page segmentation remains heuristic.",
        "evidence_files": [
            str(work_files[0]),
            str(work_files[min(16, len(work_files) - 1)]),
            str(work_files[-1]),
        ],
    }

    notes = [
        {
            "kind": "section_boundary",
            "message": "Analytical index spans files 668-720; file 720 also contains the final Z entries immediately before the contents table.",
        },
        {
            "kind": "helper",
            "message": "Helper request built for a small set of page-target candidates; helper output is only used as supporting evidence.",
        },
        {
            "kind": "method",
            "message": "Entries were derived line-by-line from OCR blocks, with lowercase continuations appended to the previous entry when they clearly belonged to the same line.",
        },
    ]

    payload = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "PG036 analytical index plus final ORDO RERUM contents table.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": filtered_entries,
        "refs": filtered_refs,
        "scripture_refs": filtered_scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }

    update_todo(
        intermediate_dir,
        "Finalize PG036 alphabetical payload",
        [
            "analytical index lines parsed",
            "ordo rerum lines parsed",
            "helper request prepared",
        ],
        [
            "run helper on prepared request",
            "write final payload",
            "validate output structure",
        ],
        [],
        [
            "Keep OCR literal text in entry_raw.",
            "Use page-map lookup for target_file fields.",
        ],
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    req = sub.add_parser("request")
    req.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    req.add_argument("--helper-request-json", type=Path, default=DEFAULT_HELPER_REQUEST)
    req.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)

    fin = sub.add_parser("finalize")
    fin.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    fin.add_argument("--helper-output-json", type=Path, default=DEFAULT_HELPER_OUTPUT)
    fin.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    fin.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)

    args = ap.parse_args()

    if args.command == "request":
        payload = helper_request_payload(args.source_root)
        write_json(args.helper_request_json, payload)
        update_todo(
            args.intermediate_dir,
            "Prepare PG036 helper request",
            ["helper request written"],
            ["run helper", "finalize payload"],
            [],
            ["The helper set is intentionally small and focused on target-file resolution."],
        )
        return

    if args.command == "finalize":
        payload = build_payload(args.source_root, args.helper_output_json, args.intermediate_dir)
        write_json(args.output_file, payload)
        update_todo(
            args.intermediate_dir,
            "PG036 payload complete",
            ["helper request built", "final payload written"],
            [],
            [],
            ["Ready for import/validation."],
        )
        return


if __name__ == "__main__":
    main()
