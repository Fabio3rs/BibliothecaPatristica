#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pg098_alphabetical_payload.py request \
    --source-root /homessddata/Projects/pdfocr/teste/PG098/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG098_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG098

  python scripts/pipeline_index_extraction/build_pg098_alphabetical_payload.py finalize \
    --source-root /homessddata/Projects/pdfocr/teste/PG098/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG098_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG098 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG098_alphabetical_indices.json

Builds the PG098 alphabetical-index payload from OCR blocks. The script keeps
the analytical indexes and the final ORDO RERUM closure as separate sections.
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

from patristica_pipeline.index_target_locator import parse_ocr_page_xml


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG098"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca, volume 98"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG098/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG098_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG098_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG098_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG098"

SECTION1 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "section_order": 1,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS AD SANCTI GERMANI OPERA.",
    "heading_norm": "index analyticus ad sancti germani opera",
    "heading_letter": None,
    "page_start": 1499,
    "page_end": 1508,
    "file_start_seq": 754,
    "file_end_seq": 758,
    "section_kind_reason": "Alphabetical analytic index for the opera of S. Germanus, opening after the hymn text on page 1499/1500 and closing before the next indexed work begins.",
}

SECTION2 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
    "section_order": 2,
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX IN S. GREGORIUM AGRIGENTINUM.",
    "heading_norm": "index in s gregorium agrigentinum",
    "heading_letter": None,
    "page_start": 1509,
    "page_end": 1516,
    "file_start_seq": 758,
    "file_end_seq": 763,
    "section_kind_reason": "Second alphabetical analytic index in the tail, for S. Gregorius Agrigentinus; the sequence is alphabetic and citation-driven, not an ordo rerum table.",
}

SECTION3 = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
    "section_order": 3,
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": 1517,
    "page_end": 1520,
    "file_start_seq": 763,
    "file_end_seq": 764,
    "section_kind_reason": "Editorial contents table at the end of the tome; distinct from the alphabetical indexes and ending with a clear FINIS marker.",
}

LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
ROMAN_RE = re.compile(r"^(?:[IVXLCDM]+)\.\s*—\s*(.+)$")
PAGE_REF_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*(?:[-–—]\s*(\d{1,4})))?(?:\s*(?:et seqq\.|seqq\.|seq\.))?", re.IGNORECASE)
BLOCK_RE = re.compile(r'<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>', flags=re.DOTALL | re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sort_norm(text: str | None) -> str | None:
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


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse file seq from {path}")
    return int(m.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_blocks(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    for match in BLOCK_RE.finditer(raw):
        attrs = {m.group(1): m.group(2) for m in ATTR_RE.finditer(match.group("attrs") or "")}
        tipo = normalize(attrs.get("tipo") or "")
        if tipo not in {"cabecalho", "texto_principal", "nota_marginal", "outro", "nota"}:
            continue
        content = match.group("content") or ""
        content = re.sub(r"<[^>]+>", " ", content)
        lines = [normalize(line) for line in content.splitlines() if normalize(line)]
        for line in lines:
            out.append((tipo, line))
        out.append(("__break__", ""))
    return out


def header_pages(path: Path) -> list[int]:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header = normalize(parsed.get("header_text") or "")
    nums: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            nums.append(value)
    return nums


def build_page_map(files: list[Path]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in files:
        for page in header_pages(path):
            mapping.setdefault(page, str(path))
    return mapping


def split_paragraphs(lines: list[str]) -> list[str]:
    paras: list[str] = []
    buf: list[str] = []
    for line in lines:
        if not line:
            if buf:
                paras.append(" ".join(buf))
                buf = []
            continue
        if buf and buf[-1].endswith("-") and line[:1].islower():
            buf[-1] = buf[-1][:-1] + line
        else:
            buf.append(line)
    if buf:
        paras.append(" ".join(buf))
    return paras


def section_for_file_seq(seq: int) -> dict[str, Any] | None:
    if SECTION1["file_start_seq"] <= seq <= SECTION1["file_end_seq"]:
        return SECTION1
    if SECTION2["file_start_seq"] <= seq <= SECTION2["file_end_seq"]:
        return SECTION2
    if SECTION3["file_start_seq"] <= seq <= SECTION3["file_end_seq"]:
        return SECTION3
    return None


def helper_request_payload(source_root: Path) -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": [
            {
                "entry_id": "pg098_germanus_anna_314",
                "lemma_raw": "Anna, Virginis Mariæ mater",
                "query_names": [
                    "Anna Virginis Mariae mater",
                    "Anna diuturnam sterilitatem",
                    "Mariam ad templum Domini consecrandam"
                ],
                "page_hints": ["314", "315"],
                "page_hint_ints": [314, 315],
                "context_raw": "Anna, Virginis Mariæ mater, ex tribu Aaronitica, prophetica et regia stirpe orta est, 314, a. Deum precatur ut sterilitatis opprobrium auferat ab ea, ibid. b; exauditur, ibid. c, d.",
            },
            {
                "entry_id": "pg098_germanus_corpus_359",
                "lemma_raw": "Corpus Virginis Mariae",
                "query_names": [
                    "Corpus Virginis Mariae a corruptione liberum",
                    "Dormitio vel assumptio beatae Mariae Virginis"
                ],
                "page_hints": ["359", "547"],
                "page_hint_ints": [359, 547],
                "context_raw": "Corpus Virginis Mariae a corruptione liberum, in cœlos assumptum, 359 et seqq.; 547 et seqq.; 570, c, d.",
            },
            {
                "entry_id": "pg098_gregorii_58",
                "lemma_raw": "Gregorius",
                "query_names": [
                    "Gregorius",
                    "Gregorius papa",
                    "Germanus"
                ],
                "page_hints": ["147", "153", "162"],
                "page_hint_ints": [147, 153, 162],
                "context_raw": "Gregorius papa ad Germanum scribit, praesertim de imaginum cultu, 147 et seqq. Germanus ad Joannem episcopum Synadensem scribit, 153 et seqq.",
            },
            {
                "entry_id": "pg098_cosmas_459",
                "lemma_raw": "COSMAS HIEROSOLYMITANUS",
                "query_names": [
                    "Cosmas Hierosolymitanus",
                    "Hymni",
                    "In Natale Domini"
                ],
                "page_hints": ["455", "459"],
                "page_hint_ints": [455, 459],
                "context_raw": "COSMAS HIEROSOLYMITANUS. Notitiæ. 455. HYMNI. 459.",
            },
            {
                "entry_id": "pg098_tarasius_1385",
                "lemma_raw": "S. Tarasius Patriarcha Constantinopolitanus",
                "query_names": [
                    "S. Tarasius Patriarcha Constantinopolitanus",
                    "Caput primum",
                    "Vita S. Tarasii"
                ],
                "page_hints": ["1385", "1396"],
                "page_hint_ints": [1385, 1396],
                "context_raw": "S. TARASIUS PATRIARCHA CONSTANTINOPO-LITANUS. Notitia. 1371. Vita S. Tarasii auctore Ignatio episcopo. 1385. Cap. V. — Synodus VII oecumenica Nicæa habita. Imagines restitutæ. 1396.",
            },
        ],
    }


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


def page_refs_for_text(text: str, page_map: dict[int, str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[int, str | None]] = set()
    for match in PAGE_REF_RE.finditer(text):
        raw = normalize(match.group(0))
        start = int(match.group(1))
        col = None
        if start in seen:
            continue
        seen.add((start, col))
        refs.append(
            {
                "ref_raw": raw,
                "page_ref_int": start,
                "page_ref_col": col,
                "range_start_raw": str(start) if match.group(2) else None,
                "range_end_raw": match.group(2),
                "target_file": page_map.get(start),
                "target_file_probability": 0.99 if page_map.get(start) else None,
            }
        )
    return refs


def lemma_from_entry(entry_raw: str) -> str | None:
    text = normalize(entry_raw)
    if not text:
        return None
    m = PAGE_REF_RE.search(text)
    if m:
        text = text[: m.start()].strip()
    text = text.rstrip(" .;:")
    if not text:
        return None
    return text


def guess_entry_kind(section: dict[str, Any], entry_raw: str) -> str:
    if section["section_kind"] == "ordo_rerum":
        if re.match(r"^(?:S\.|[A-Z][A-Z]+)\b", entry_raw) and "." in entry_raw and not PAGE_REF_RE.search(entry_raw):
            return "heading_group"
        if ROMAN_RE.match(entry_raw):
            return "heading_group"
        return "lemma"
    if entry_raw in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "X", "Y", "Z"}:
        return "heading_group"
    return "lemma"


def build_payload(source_root: Path, helper_output_json: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    page_map = build_page_map(files)
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
            "file_start": str(next(p for p in files if file_seq(p) == SECTION1["file_start_seq"])),
            "file_end": str(next(p for p in files if file_seq(p) == SECTION1["file_end_seq"])),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": SECTION1["section_kind_reason"],
                "source_window": [
                    str(next(p for p in files if file_seq(p) == SECTION1["file_start_seq"])),
                    str(next(p for p in files if file_seq(p) == SECTION1["file_end_seq"])),
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
            "file_start": str(next(p for p in files if file_seq(p) == SECTION2["file_start_seq"])),
            "file_end": str(next(p for p in files if file_seq(p) == SECTION2["file_end_seq"])),
            "confidence": 0.92,
            "raw_json": {
                "section_kind_reason": SECTION2["section_kind_reason"],
                "source_window": [
                    str(next(p for p in files if file_seq(p) == SECTION2["file_start_seq"])),
                    str(next(p for p in files if file_seq(p) == SECTION2["file_end_seq"])),
                ],
            },
        },
        {
            "section_key": SECTION3["section_key"],
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": SECTION3["section_order"],
            "section_kind": SECTION3["section_kind"],
            "heading_raw": SECTION3["heading_raw"],
            "heading_norm": SECTION3["heading_norm"],
            "heading_letter": SECTION3["heading_letter"],
            "page_start": SECTION3["page_start"],
            "page_end": SECTION3["page_end"],
            "file_start": str(next(p for p in files if file_seq(p) == SECTION3["file_start_seq"])),
            "file_end": str(next(p for p in files if file_seq(p) == SECTION3["file_end_seq"])),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": SECTION3["section_kind_reason"],
                "source_window": [
                    str(next(p for p in files if file_seq(p) == SECTION3["file_start_seq"])),
                    str(next(p for p in files if file_seq(p) == SECTION3["file_end_seq"])),
                ],
            },
        },
    ]

    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    current_section = None
    current_letter = None
    current_major_node = None
    node_counter: dict[str, int] = {SECTION1["section_key"]: 0, SECTION2["section_key"]: 0, SECTION3["section_key"]: 0}
    entry_counter: dict[str, int] = {SECTION1["section_key"]: 0, SECTION2["section_key"]: 0, SECTION3["section_key"]: 0}
    letter_node_keys: dict[tuple[str, str], str] = {}

    def next_node_key(section_key: str) -> str:
        node_counter[section_key] += 1
        return f"{section_key}:node:{node_counter[section_key]:04d}"

    def next_entry_key(section_key: str) -> str:
        entry_counter[section_key] += 1
        return f"{section_key}:entry:{entry_counter[section_key]:04d}"

    def ensure_letter_node(section_key: str, letter: str) -> str:
        key = (section_key, letter)
        if key in letter_node_keys:
            return letter_node_keys[key]
        node_key = next_node_key(section_key)
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": None,
                "node_order": node_counter[section_key],
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.97,
                "raw_json": {"source": "standalone letter heading"},
            }
        )
        letter_node_keys[key] = node_key
        return node_key

    def ensure_major_node(section_key: str, label: str, level: int = 1, parent: str | None = None, kind: str = "heading_group") -> str:
        node_key = next_node_key(section_key)
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent,
                "node_order": node_counter[section_key],
                "node_kind": kind,
                "label_raw": label,
                "label_norm": normalize(label),
                "label_sort": sort_norm(label),
                "node_level": level,
                "confidence": 0.95,
                "raw_json": {"source": "structural heading"},
            }
        )
        return node_key

    def push_entry(section: dict[str, Any], raw_entry: str, source_file: str, parent_node_key: str | None = None) -> None:
        entry_raw = normalize(raw_entry)
        if not entry_raw or entry_raw in {"Digitized by Google"}:
            return
        if section["section_kind"] == "ordo_rerum" and entry_raw == "FINIS TOMI NONAGESIMI OCTAVI.":
            return
        if section["section_kind"] == "ordo_rerum" and entry_raw == "Parisitis. — Ex typis MIGNE.":
            return
        entry_key = next_entry_key(section["section_key"])
        page_refs = page_refs_for_text(entry_raw, page_map)
        first_ref = page_refs[0]["page_ref_int"] if page_refs else None
        target_file_best = page_map.get(first_ref) if first_ref is not None else source_file
        lemma_raw = lemma_from_entry(entry_raw)
        entry_kind = guess_entry_kind(section, entry_raw)
        confidence = 0.86 if page_refs else 0.74
        if section["section_kind"] == "ordo_rerum":
            confidence = 0.82 if page_refs else 0.76
        helper_note = None
        if lemma_raw:
            lemma_lower = lemma_raw.lower()
            for entry_id, summary in helper_summary.items():
                query_names = [normalize(q).lower() for q in (summary.get("query_names") or []) if normalize(q)]
                if any(q == lemma_lower or q in lemma_lower or lemma_lower in q for q in query_names):
                    helper_note = {"entry_id": entry_id, **summary}
                    break
        entry = {
            "entry_key": entry_key,
            "section_key": section["section_key"],
            "parent_node_key": parent_node_key,
            "entry_order": entry_counter[section["section_key"]],
            "entry_kind": entry_kind,
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_raw,
            "lemma_norm": sort_norm(lemma_raw) if lemma_raw else None,
            "lemma_sort": sort_norm(lemma_raw) if lemma_raw else None,
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": current_letter,
            "inferred_printed_page": first_ref,
            "section_start_file": str(next(p for p in files if file_seq(p) == section["file_start_seq"])),
            "editorial_anchor_file": source_file,
            "target_file_best": target_file_best,
            "confidence": confidence,
            "raw_json": {
                "section_kind": section["section_kind"],
                "source_file": source_file,
                "helper_evidence": helper_note,
                "page_refs": page_refs,
            },
        }
        if entry_kind == "heading_group" and not page_refs:
            entry["lemma_raw"] = entry_raw
            entry["lemma_display"] = entry_raw
            entry["lemma_norm"] = sort_norm(entry_raw)
            entry["lemma_sort"] = sort_norm(entry_raw)
        entries.append(entry)
        for idx, ref in enumerate(page_refs, start=1):
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": idx,
                    "ref_kind": "editorial_page",
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": ref["page_ref_col"],
                    "line_ref_raw": None,
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": ref["target_file"],
                    "target_file_probability": ref["target_file_probability"],
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": source_file,
                    "confidence": 0.9 if ref["target_file"] else 0.6,
                    "raw_json": {
                        "source_file": source_file,
                        "helper_evidence": helper_note,
                    },
                }
            )

    for path in files:
        seq = file_seq(path)
        section = section_for_file_seq(seq)
        if section is None:
            continue
        blocks = extract_blocks(path)
        text_lines = [line for tipo, line in blocks if tipo in {"texto_principal", "nota_marginal", "outro", "cabecalho", "nota", "__break__"}]
        paras = split_paragraphs(text_lines)
        source_file = str(path)

        for para in paras:
            if current_section is None and normalize(para).startswith("Aaron manus Moysis"):
                current_section = SECTION1
                current_letter = None
                current_major_node = ensure_major_node(SECTION1["section_key"], SECTION1["heading_raw"], level=1)
            elif current_section == SECTION1 and normalize(para).startswith("Abbates Graeci Monasterii"):
                current_section = SECTION2
                current_letter = None
                current_major_node = ensure_major_node(SECTION2["section_key"], SECTION2["heading_raw"], level=1)
            elif current_section is None:
                continue
            if "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR." in para:
                current_section = SECTION3
                current_letter = None
                current_major_node = ensure_major_node(SECTION3["section_key"], SECTION3["heading_raw"], level=1)
                continue
            if current_section is None:
                continue
            cleaned = normalize(para)
            if not cleaned:
                continue
            if cleaned == "Digitized by Google":
                continue
            if cleaned in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"}:
                current_letter = cleaned
                ensure_letter_node(current_section["section_key"], cleaned)
                continue
            m = ROMAN_RE.match(cleaned)
            parent = current_major_node
            if current_section["section_kind"] == "ordo_rerum" and m:
                ordinal = m.group(1)
                ord_node = ensure_major_node(current_section["section_key"], f"{ordinal}.", level=2, parent=current_major_node, kind="ordinal_group")
                push_entry(current_section, cleaned, source_file, parent_node_key=ord_node)
                continue
            if current_section["section_kind"] == "ordo_rerum" and re.match(r"^[A-Z][A-ZÆŒ .\-]+(?:\.|$)", cleaned) and not PAGE_REF_RE.search(cleaned):
                # Major headings in the contents table.
                if cleaned.endswith("."):
                    current_major_node = ensure_major_node(current_section["section_key"], cleaned, level=2, parent=current_major_node)
                    continue
            push_entry(current_section, cleaned, source_file, parent_node_key=parent if current_section["section_kind"] != "ordo_rerum" else parent)

    coverage = {
        "entries_status": "partial",
        "entries_status_reason": "Conservative OCR-driven extraction of the two analytical indexes and the final ORDO RERUM table; page-map resolution was used for most citation targets, while some line wrapping and OCR ligatures remain literal.",
        "evidence_files": [
            str(next(p for p in files if file_seq(p) == 754)),
            str(next(p for p in files if file_seq(p) == 758)),
            str(next(p for p in files if file_seq(p) == 764)),
        ],
    }

    notes = [
        {
            "kind": "boundary",
            "message": "Section 1 opens on file 754 after the hymn text and continues through file 758; section 2 begins at the in-file heading on 758 and runs to 763; section 3 begins at 763 and ends at the FINIS marker on 764.",
        },
        {
            "kind": "method",
            "message": "Entries were assembled from OCR paragraphs; standalone letter lines were stored as letter_group nodes rather than flattened into lemmata.",
        },
        {
            "kind": "helper",
            "message": "Helper request is intentionally small and only used to sanity-check target-file resolution for representative page hints.",
        },
    ]

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(source_root),
            "volume_label": VOLUME_LABEL,
            "notes": "PG098 tail indexes and final contents table.",
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": scripture_refs,
        "coverage": coverage,
        "notes": notes,
    }
    update_todo(
        intermediate_dir,
        "Finalize PG098 alphabetical payload",
        ["section boundaries identified", "helper request prepared", "OCR paragraph extraction implemented"],
        ["run helper", "write payload", "validate JSON"],
        [],
        ["Use page-map lookup for printed page targets.", "Keep OCR literals in entry_raw."],
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
        write_json(args.helper_request_json, helper_request_payload(args.source_root))
        update_todo(
            args.intermediate_dir,
            "Prepare PG098 helper request",
            ["helper request written"],
            ["run helper", "finalize payload"],
            [],
            ["The helper set is intentionally small and targeted at representative page hints."],
        )
        return

    payload = build_payload(args.source_root, args.helper_output_json, args.intermediate_dir)
    write_json(args.output_file, payload)
    update_todo(
        args.intermediate_dir,
        "PG098 payload complete",
        ["helper request written", "payload written"],
        [],
        [],
        ["Ready for validation/import."],
    )


if __name__ == "__main__":
    main()
