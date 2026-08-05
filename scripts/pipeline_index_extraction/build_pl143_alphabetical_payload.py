#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/build_pl143_alphabetical_payload.py prepare \
    --source-root /homessddata/Projects/pdfocr/teste/PL143/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL143_helper_request.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL143

  python scripts/pipeline_index_extraction/build_pl143_alphabetical_payload.py finalize \
    --source-root /homessddata/Projects/pdfocr/teste/PL143/text \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL143_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL143 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL143_alphabetical_indices.json

Builds the PL143 alphabetical-index draft/helper request and then finalizes the
canonical payload after local target resolution.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PL143"
COLLECTION = "PL"
VOLUME_LABEL = "Patrologia Latina 143"
SOURCE_ROOT_DEFAULT = ROOT / "teste/PL143/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PL143_alphabetical_indices.json"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PL143_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PL143_helper_output.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PL143"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

INDEX_START_SEQ = 802
INDEX_END_SEQ = 805
LOCAL_INDEX_HEAD = "ELENCHUS ONOMASTICUS LOCALIS GERMANIÆ."
SECTION1_HEAD = "INDEX ONOMASTICUS\nPERSONARUM ET LOCORUM\nIN CHRONICON HERMANNI CONTRACTI ET CHRONICON PERTHUSIANUM."

SECTION1_TITLE = "INDEX ONOMASTICUS PERSONARUM ET LOCORUM IN CHRONICON HERMANNI CONTRACTI ET CHRONICON PERTHUSIANUM."
SECTION2_TITLE = "ELENCHUS ONOMASTICUS LOCALIS GERMANIÆ."

SECTION1_HEADING_SET = {
    "I.",
    "PERSONALE ECCLESIASTICUM.",
    "SUMMI PONTIFICES.",
    "ARCHIEPISCOPI.",
    "Moguntini.",
    "Treuirenses.",
    "Colonienses.",
    "Lugdunenses.",
    "Magdeburgenses.",
    "Salzburgensis.",
    "Vesontionenses.",
    "Aquileienses.",
    "Mediolanenses.",
    "Ravennatenses.",
    "EPISCOPI.",
    "Aichstettenses.",
    "Argentinenses.",
    "Augustenses.",
    "Bambergenses.",
    "Basileenses.",
    "Brixinensis.",
    "Constantienses.",
    "Curienses.",
    "Frisingenses.",
    "Leodienses.",
    "Metenses.",
    "Mindenses.",
    "ALEMANIAE ET SUEVIE DUCES.",
    "AUSTRIAE MARCHIONES.",
    "BAVARIAE REGES ET DUCES.",
    "BOREALIS DUCES ET REGES.",
    "BURGUNDIAE REGES, DUCES, ET COMITES.",
    "CARINTHIAE DUCES.",
    "FRANCONIAE DUCES ET COMITES.",
    "PRISSONIS REGUL.",
    "LOTHARINGIAE REGES ET DUCES.",
    "MABARENSIS SEU NEMANIE DUCES.",
    "OSTROFRITANI DUCES.",
    "SAXONIAE DUCES.",
    "THURINGIAE DUCES.",
    "MONASTERIA.",
    "Abbatiae, Monachi.",
    "SANCTI.",
    "CONCILIA.",
    "SECTARII.",
    "II.",
    "PERSONAE SAECULARES.",
    "GERMANIAE.",
}

SECTION2_HEADING_SET = {
    "I.",
    "PROVINCIAE POPULI.",
    "II.",
    "PAGI GERMANIAE.",
    "III.",
    "URBES, CASTRA, LOCA INSIGNIORA.",
}

SECTION1_SUBHEADING_PATTERNS = (
    "ALEMANNIÆ COMITES.",
    "ACHALM.",
    "ARGENGEWE.",
    "BRIGANTINI.",
    "PUOLLDORF.",
    "BUSSEN.",
    "CASTELBERG.",
    "DILLINGEN.",
    "GAMERTINGEN.",
    "GINGEN.",
    "GOLDINSHUNDERE.",
    "HAUSBERG.",
    "HEILIGENBERG.",
    "LINZGOW.",
    "MONTIS BELLIGARDI.",
    "MONISBERG.",
    "NELLENBURG.",
    "PARA",
    "PFULLENDORF.",
    "POTAMI.",
    "RAMMISPERG.",
    "RAVENSPURG SEU ALTORF.",
    "RHETIA CURIENSIS.",
    "VERINGEN.",
    "WINTERTHUR ET KILCHG.",
    "ALEMANNIÆ NOBILES.",
    "ALSACIÆ DUCES ET COMITES.",
    "HOLLANDIÆ COMITES.",
)

CONTINUATION_PREFIXES = (
    "Hunc supra",
    "Similem fere",
    "Ampliore igitur",
    "Locus sigilli.",
)

FOOTER_RE = re.compile(r"^Digitized by Google$", re.IGNORECASE)
PAGE_TOKEN_RE = re.compile(r"\b(\d{1,4})(?:-(\d{1,4}))?(?:\s*(seq\.?|seqq\.?|usque|passim))?", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKD", text.replace("\xa0", " "))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def sort_norm(text: str | None) -> str | None:
    value = normalize_text(text)
    return value.lower() if value else None


def load_lines(path: Path) -> list[str]:
    lines: list[str] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for block in re.finditer(r"<bloco(?P<attrs>[^>]*)>(?P<content>.*?)</bloco>", raw, flags=re.S):
        attrs = block.group("attrs") or ""
        tipo_m = re.search(r'tipo="([^"]+)"', attrs)
        tipo = (tipo_m.group(1).strip().lower() if tipo_m else "")
        if tipo not in {"cabecalho", "texto_principal"}:
            continue
        content = re.sub(r"<[^>]+>", " ", block.group("content") or "")
        for raw_line in content.splitlines():
            line = normalize_text(raw_line)
            if not line or FOOTER_RE.fullmatch(line):
                continue
            lines.append(line)
    return lines


def page_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(m.group(1))


def discover_index_files(source_root: Path) -> list[Path]:
    files = [p for p in sorted(source_root.glob("*.txt"), key=page_seq) if INDEX_START_SEQ <= page_seq(p) <= INDEX_END_SEQ]
    if not files:
        raise RuntimeError("no PL143 index files found in the expected OCR window")
    return files


def is_heading_line(line: str, section: int) -> bool:
    if line in (SECTION1_TITLE, SECTION2_TITLE):
        return True
    if line == "1.":
        return True
    if section == 1 and line in SECTION1_HEADING_SET:
        return True
    if section == 2 and line in SECTION2_HEADING_SET:
        return True
    if section == 1 and any(line == prefix for prefix in SECTION1_SUBHEADING_PATTERNS):
        return True
    if section == 1 and line.endswith(".") and line.replace(".", "").isupper() and not any(ch.isdigit() for ch in line):
        return True
    if section == 2 and line.endswith(".") and line.replace(".", "").isupper() and not any(ch.isdigit() for ch in line):
        return True
    return False


def is_continuation_note(line: str) -> bool:
    if line.startswith(CONTINUATION_PREFIXES):
        return True
    if line.startswith("Hunc supra"):
        return True
    if line.startswith("perperam tamen"):
        return True
    if line.startswith("Ampliore igitur"):
        return True
    return False


def heading_level(line: str) -> int:
    if line in {"I.", "II.", "III."}:
        return 1
    if line == "1.":
        return 2
    if line in {SECTION1_TITLE, SECTION2_TITLE}:
        return 1
    if line in {"PERSONAE SAECULARES.", "PERSONALE ECCLESIASTICUM.", "ELENCHUS ONOMASTICUS LOCALIS GERMANIÆ."}:
        return 1
    if line in {"SUMMI PONTIFICES.", "ARCHIEPISCOPI.", "EPISCOPI.", "MONASTERIA.", "SANCTI.", "CONCILIA.", "SECTARII.", "PROVINCIAE POPULI.", "PAGI GERMANIAE.", "URBES, CASTRA, LOCA INSIGNIORA."}:
        return 2
    if line in {
        "Abbatiae, Monachi.",
        "Moguntini.",
        "Treuirenses.",
        "Colonienses.",
        "Lugdunenses.",
        "Magdeburgenses.",
        "Salzburgensis.",
        "Vesontionenses.",
        "Aquileienses.",
        "Mediolanenses.",
        "Ravennatenses.",
        "Aichstettenses.",
        "Argentinenses.",
        "Augustenses.",
        "Bambergenses.",
        "Basileenses.",
        "Brixinensis.",
        "Constantienses.",
        "Curienses.",
        "Frisingenses.",
        "Leodienses.",
        "Metenses.",
        "Mindenses.",
        "ALEMANIAE ET SUEVIE DUCES.",
        "AUSTRIAE MARCHIONES.",
        "BAVARIAE REGES ET DUCES.",
        "BOREALIS DUCES ET REGES.",
        "BURGUNDIAE REGES, DUCES, ET COMITES.",
        "CARINTHIAE DUCES.",
        "FRANCONIAE DUCES ET COMITES.",
        "PRISSONIS REGUL.",
        "LOTHARINGIAE REGES ET DUCES.",
        "MABARENSIS SEU NEMANIE DUCES.",
        "OSTROFRITANI DUCES.",
        "SAXONIAE DUCES.",
        "THURINGIAE DUCES.",
        "ALEMANIAE COMITES.",
        "ACHALM.",
        "ARGENGEWE.",
        "BRIGANTINI.",
        "PUOLLDORF.",
        "BUSSEN.",
        "CASTELBERG.",
        "DILLINGEN.",
        "GAMERTINGEN.",
        "GINGEN.",
        "GOLDINSHUNDERE.",
        "HAUSBERG.",
        "HEILIGENBERG.",
        "LINZGOW.",
        "MONTIS BELLIGARDI.",
        "MONISBERG.",
        "NELLENBURG.",
        "PARA",
        "PFULLENDORF.",
        "POTAMI.",
        "RAMMISPERG.",
        "RAVENSPURG SEU ALTORF.",
        "RHETIA CURIENSIS.",
        "VERINGEN.",
        "WINTERTHUR ET KILCHG.",
        "ALEMANNIÆ NOBILES.",
        "ALSACIÆ DUCES ET COMITES.",
        "HOLLANDIÆ COMITES.",
    }:
        return 3
    return 2 if line.endswith(".") and line.isupper() else 3


def extract_page_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str | None]] = set()
    for match in PAGE_TOKEN_RE.finditer(text):
        page = int(match.group(1))
        page_end = match.group(2)
        seq_word = (match.group(3) or "").lower().strip()
        if page_end:
            ref_raw = match.group(0)
            key = (ref_raw, page, page_end)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": str(page),
                    "range_end_raw": page_end,
                    "ref_kind": "editorial_range",
                }
            )
            continue
        if seq_word in {"seq", "seqq", "usque", "passim"}:
            ref_raw = match.group(0)
            key = (ref_raw, page, None)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": page,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "ref_kind": "editorial_range",
                }
            )
            continue
        ref_raw = match.group(0)
        key = (ref_raw, page, None)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "ref_raw": ref_raw,
                "page_ref_raw": ref_raw,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "ref_kind": "editorial_page",
            }
        )
    return refs


def derive_lemma(text: str) -> str | None:
    cleaned = normalize_text(text) or ""
    if not cleaned:
        return None
    if cleaned.startswith("Vid.") or cleaned.startswith("Vide ") or cleaned.startswith("Vid ") or cleaned.startswith("voir"):
        return None
    m = re.search(r"\b\d{1,4}\b", cleaned)
    if m:
        return cleaned[: m.start()].strip(" ,.;:")
    return cleaned.strip(" ,.;:")


def derive_query_names(lemma_raw: str | None, entry_raw: str) -> list[str]:
    names: list[str] = []
    for candidate in [lemma_raw, normalize_text(entry_raw)]:
        if candidate and candidate not in names:
            names.append(candidate)
    if lemma_raw:
        stripped = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
        if stripped and stripped not in names:
            names.append(stripped)
    return names


def infer_section_and_nodes(lines_by_file: dict[str, list[str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []

    section1_key = f"{VOLUME_ID}:alpha:onomastic_mixed:001"
    section2_key = f"{VOLUME_ID}:alpha:onomastic_place:002"

    sections.append(
        {
            "section_key": section1_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "onomastic_mixed",
            "heading_raw": SECTION1_TITLE,
            "heading_norm": normalize_text(SECTION1_TITLE),
            "heading_letter": None,
            "page_start": 1587,
            "page_end": 1594,
            "file_start": str(SOURCE_ROOT_DEFAULT / "3f6bc174-597b-4055-810f-331a363722e9-802.txt"),
            "file_end": str(SOURCE_ROOT_DEFAULT / "3f6bc174-597b-4055-810f-331a363722e9-805.txt"),
            "confidence": 0.82,
            "raw_json": {
                "section_kind_reason": "Onomastic index of persons, saints, ecclesiastical offices, monasteries, secular dynasties, and local groupings before the local German place index.",
                "observed_headings": [
                    SECTION1_TITLE,
                    "I.",
                    "PERSONALE ECCLESIASTICUM.",
                    "II.",
                    "PERSONAE SAECULARES.",
                ],
            },
        }
    )
    sections.append(
        {
            "section_key": section2_key,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "onomastic_place",
            "heading_raw": SECTION2_TITLE,
            "heading_norm": normalize_text(SECTION2_TITLE),
            "heading_letter": None,
            "page_start": 1593,
            "page_end": 1594,
            "file_start": str(SOURCE_ROOT_DEFAULT / "3f6bc174-597b-4055-810f-331a363722e9-805.txt"),
            "file_end": str(SOURCE_ROOT_DEFAULT / "3f6bc174-597b-4055-810f-331a363722e9-805.txt"),
            "confidence": 0.93,
            "raw_json": {
                "section_kind_reason": "Local German place index with province, region, and urban locality groupings.",
                "observed_headings": [
                    SECTION2_TITLE,
                    "I. PROVINCIAE POPULI.",
                    "II. PAGI GERMANIAE.",
                    "III. URBES, CASTRA, LOCA INSIGNIORA.",
                ],
            },
        }
    )

    node_index: dict[tuple[str, str], str] = {}
    section_current = section1_key
    node_stack: list[tuple[int, str]] = []
    entry_counter = 0

    def add_node(label_raw: str, section_key: str, node_kind: str, level: int, parent: str | None, source_file: str, order: int) -> str:
        node_key = f"{VOLUME_ID}:node:{len(nodes) + 1:04d}"
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_key,
                "parent_node_key": parent,
                "node_order": order,
                "node_kind": node_kind,
                "label_raw": label_raw,
                "label_norm": normalize_text(label_raw),
                "label_sort": sort_norm(label_raw),
                "node_level": level,
                "confidence": 0.93 if node_kind == "heading_group" else 0.88,
                "raw_json": {"source_file": source_file},
            }
        )
        return node_key

    for file_path_str, lines in lines_by_file.items():
        source_file = file_path_str
        file_name = Path(file_path_str).name
        for line in lines:
            if file_name.endswith("-802.txt") and line in {
                "INDEX ONOMASTICUS",
                "PERSONARUM ET LOCORUM",
                "IN CHRONICON HERMANNI CONTRACTI ET CHRONICON PERTHUSIANUM.",
                "Revocatur Lector ad cifras crassiores textui intermistas.",
            }:
                continue
            if file_name.endswith("-803.txt") and line.startswith("1589 INDEX IN CHRONICON HERMANNI CONTRACTI. 1590"):
                continue
            if file_name.endswith("-804.txt") and line == "INDEX IN CHRONICON HERMANNI CONTRACTI.":
                continue
            if file_name.endswith("-805.txt") and line.startswith("1593 INDEX IN CHRONICON HERMANNI CONTRACTI. 1594"):
                continue
            if line == LOCAL_INDEX_HEAD:
                section_current = section2_key
                node_stack.clear()
                continue
            if is_heading_line(line, 1 if section_current == section1_key else 2):
                kind = "ordinal_group" if line in {"I.", "II.", "III."} else "heading_group"
                level = heading_level(line)
                while node_stack and node_stack[-1][0] >= level:
                    node_stack.pop()
                parent = node_stack[-1][1] if node_stack else None
                node_key = add_node(line, section_current, kind, level, parent, source_file, len([n for n in nodes if n["section_key"] == section_current]) + 1)
                node_stack.append((level, node_key))
                continue

            if is_continuation_note(line) and entries:
                entries[-1]["entry_raw"] = f"{entries[-1]['entry_raw']} {line}".strip()
                entries[-1]["context_raw"] = entries[-1]["entry_raw"]
                entries[-1]["raw_json"].setdefault("continuation_notes", []).append(line)
                continue

            if not any(ch.isdigit() for ch in line) and line[0].isupper() and len(line.split()) > 7 and entries:
                entries[-1]["entry_raw"] = f"{entries[-1]['entry_raw']} {line}".strip()
                entries[-1]["context_raw"] = entries[-1]["entry_raw"]
                entries[-1]["raw_json"].setdefault("continuation_notes", []).append(line)
                continue

            lemma_raw = derive_lemma(line)
            entry_kind = "cross_reference" if lemma_raw and re.fullmatch(r"(?:Vid\.?|Vide|voir|v\.|cf\.|id\.?)", lemma_raw, re.IGNORECASE) else "lemma"
            entry_counter += 1
            entry_key = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            page_refs = extract_page_refs(line)
            heading_letter = None
            if lemma_raw:
                for ch in lemma_raw:
                    if ch.isalpha():
                        heading_letter = ch.upper()
                        break
            parent_node_key = node_stack[-1][1] if node_stack else None
            entries.append(
                {
                    "entry_key": entry_key,
                    "section_key": section_current,
                    "parent_node_key": parent_node_key,
                    "entry_order": len([e for e in entries if e["section_key"] == section_current]) + 1,
                    "entry_kind": entry_kind,
                    "lemma_raw": None if entry_kind == "cross_reference" else lemma_raw,
                    "lemma_display": None if entry_kind == "cross_reference" else lemma_raw,
                    "lemma_norm": None if entry_kind == "cross_reference" else normalize_text(lemma_raw),
                    "lemma_sort": None if entry_kind == "cross_reference" else sort_norm(lemma_raw),
                    "entry_raw": line,
                    "context_raw": line,
                    "heading_letter": heading_letter,
                    "inferred_printed_page": page_refs[0]["page_ref_int"] if page_refs else None,
                    "section_start_file": source_file,
                    "editorial_anchor_file": source_file,
                    "target_file_best": None,
                    "confidence": 0.86 if page_refs else 0.72,
                    "raw_json": {
                        "source_file": source_file,
                        "page_refs": page_refs,
                        "section_kind": "onomastic_mixed" if section_current == section1_key else "onomastic_place",
                    },
                }
            )

    return sections, nodes, entries


def build_helper_request(entries: list[dict[str, Any]]) -> dict[str, Any]:
    helper_entries: list[dict[str, Any]] = []
    for entry in entries:
        page_refs = entry["raw_json"].get("page_refs") or []
        if not page_refs:
            continue
        for ref_idx, ref in enumerate(page_refs, start=1):
            entry_id = f"{entry['entry_key']}__r{ref_idx:02d}"
            lemma_raw = entry["lemma_raw"] or entry["entry_raw"]
            helper_entries.append(
                {
                    "entry_id": entry_id,
                    "lemma_raw": lemma_raw,
                    "query_names": derive_query_names(lemma_raw, entry["entry_raw"]),
                    "page_hints": [str(ref["page_ref_int"])],
                    "page_hint_ints": [ref["page_ref_int"]],
                    "context_raw": entry["entry_raw"],
                }
            )
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT_DEFAULT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
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
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return read_json(helper_output_json, {})


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries", []) or []:
        best = item.get("best_candidate") or {}
        out[str(item.get("entry_id"))] = {
            "status": item.get("status"),
            "best_candidate": best,
            "candidates": item.get("candidates", []),
        }
    return out


def finalize_entries(entries: list[dict[str, Any]], helper_lookup: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs: list[dict[str, Any]] = []
    for entry in entries:
        page_refs = entry["raw_json"].get("page_refs") or []
        if not page_refs:
            continue
        best_target: str | None = None
        best_prob: float | None = None
        best_summary: dict[str, Any] | None = None
        for ref_idx, ref in enumerate(page_refs, start=1):
            helper_entry = helper_lookup.get(f"{entry['entry_key']}__r{ref_idx:02d}") or {}
            best = helper_entry.get("best_candidate") or {}
            target_file = best.get("file")
            target_prob = best.get("probability")
            if target_file and best_target is None:
                best_target = target_file
                best_prob = target_prob
                best_summary = best
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_idx,
                    "ref_kind": ref["ref_kind"],
                    "ref_raw": ref["ref_raw"],
                    "page_ref_raw": ref["page_ref_raw"],
                    "page_ref_int": ref["page_ref_int"],
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": ref["range_start_raw"],
                    "range_end_raw": ref["range_end_raw"],
                    "target_file": target_file,
                    "target_file_probability": target_prob,
                    "section_start_file": entry["section_start_file"],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.89 if target_file else 0.62,
                    "raw_json": {
                        "helper_entry_id": f"{entry['entry_key']}__r{ref_idx:02d}",
                        "helper_result": helper_entry,
                    },
                }
            )
        entry["target_file_best"] = best_target
        entry["confidence"] = 0.92 if best_target else 0.74
        entry["raw_json"]["helper_best_candidate"] = best_summary
        entry["raw_json"]["helper_related_entry_ids"] = [
            f"{entry['entry_key']}__r{idx:02d}" for idx in range(1, len(page_refs) + 1)
        ]
    return entries, refs


def build_payload(sections: list[dict[str, Any]], nodes: list[dict[str, Any]], entries: list[dict[str, Any]], refs: list[dict[str, Any]], helper_output: dict[str, Any], coverage_status: str, coverage_reason: str, evidence_files: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "volume": {
            "volume_id": VOLUME_ID,
            "collection": COLLECTION,
            "source_root": str(SOURCE_ROOT_DEFAULT),
            "volume_label": VOLUME_LABEL,
            "notes": "PL143 onomastic index and local German place index.",
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
            }
        ],
    }


def prepare(args: argparse.Namespace) -> None:
    source_root = Path(args.source_root or SOURCE_ROOT_DEFAULT)
    intermediate_dir = Path(args.intermediate_dir or DEFAULT_INTERMEDIATE_DIR)
    helper_request_json = Path(args.helper_request_json or DEFAULT_HELPER_REQUEST)
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    lines_by_file: dict[str, list[str]] = {}
    for path in discover_index_files(source_root):
        lines_by_file[str(path)] = load_lines(path)

    sections, nodes, entries = infer_section_and_nodes(lines_by_file)
    helper_request = build_helper_request(entries)
    write_json(helper_request_json, helper_request)

    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", nodes)
    write_json(intermediate_dir / "entries_draft.json", entries)
    write_json(intermediate_dir / "helper_request.json", helper_request)
    write_json(
        TODO_JSON,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Resolve PL143 onomastic index targets and finalize payload.",
            "completed": [
                "section boundaries recovered",
                "draft entries and helper request prepared",
            ],
            "pending": [
                "run index_target_locator helper",
                "assemble refs with helper candidates",
                "write final payload and validate",
            ],
            "blocked": [],
            "notes": [
                "Section 1 is an onomastic mixed index with ecclesiastical, secular, and local groupings.",
                "Section 2 is the local German place index beginning at the ELENCHUS ONOMASTICUS LOCALIS GERMANIÆ heading.",
            ],
        },
    )


def finalize(args: argparse.Namespace) -> None:
    source_root = Path(args.source_root or SOURCE_ROOT_DEFAULT)
    intermediate_dir = Path(args.intermediate_dir or DEFAULT_INTERMEDIATE_DIR)
    output_file = Path(args.output_file or DEFAULT_OUTPUT_FILE)
    helper_output_json = Path(args.helper_output_json or DEFAULT_HELPER_OUTPUT)

    sections = read_json(intermediate_dir / "sections.json", [])
    nodes = read_json(intermediate_dir / "nodes.json", [])
    entries = read_json(intermediate_dir / "entries_draft.json", [])
    helper_output = read_json(helper_output_json, {})
    helper_lookup = helper_map(helper_output)

    entries, refs = finalize_entries(entries, helper_lookup)

    payload = build_payload(
        sections=sections,
        nodes=nodes,
        entries=entries,
        refs=refs,
        helper_output=helper_output,
        coverage_status="extracted",
        coverage_reason="Recovered the visible onomastic and local-place index entries from the OCR tail, with helper-assisted target resolution for each material reference.",
        evidence_files=[
            str(source_root / "3f6bc174-597b-4055-810f-331a363722e9-802.txt"),
            str(source_root / "3f6bc174-597b-4055-810f-331a363722e9-803.txt"),
            str(source_root / "3f6bc174-597b-4055-810f-331a363722e9-804.txt"),
            str(source_root / "3f6bc174-597b-4055-810f-331a363722e9-805.txt"),
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
            "current_focus": "Payload written and ready for validation/import.",
            "completed": [
                "section boundaries recovered",
                "draft entries and helper request prepared",
                "helper-assisted refs assembled",
                "final payload written",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "Target files are attached per ref from the helper output.",
                "Section 1 page_start is inferred from adjacent pagination continuity because file 802 does not expose a visible page header.",
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
