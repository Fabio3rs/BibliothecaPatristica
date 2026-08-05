#!/usr/bin/env python3
"""
Build the PG120 `ordo_rerum` alphabetical-index payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pg120_ordo_rerum_payload.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG120/text"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG120_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG120_helper_output.json"
OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG120_alphabetical_indices.json"

FILES = [
    SOURCE_ROOT / "d4afec28-63e0-4b90-bf88-a73372306d25-655.txt",
    SOURCE_ROOT / "d4afec28-63e0-4b90-bf88-a73372306d25-656.txt",
    SOURCE_ROOT / "d4afec28-63e0-4b90-bf88-a73372306d25-657.txt",
    SOURCE_ROOT / "d4afec28-63e0-4b90-bf88-a73372306d25-658.txt",
]


NODE_TITLES = OrderedDict(
    [
        ("anonymus", {"label": "ANONYMUS.", "parent": None}),
        ("theodorus iconii episcopus", {"label": "THEODORUS ICONII EPISCOPUS.", "parent": None}),
        ("leo presbyter", {"label": "LEO PRESBYTER.", "parent": None}),
        ("leo grammaticus", {"label": "LEO GRAMMATICUS.", "parent": None}),
        ("joannes presbyter", {"label": "JOANNES PRESBYTER.", "parent": None}),
        ("epiphanius monachus", {"label": "EPIPHANIUS MONACHUS.", "parent": None}),
        ("alexius patriarcha cp", {"label": "ALEXIUS PATRIARCHA CP.", "parent": None}),
        ("demetrius syncellus", {"label": "DEMETRIUS SYNCELLUS.", "parent": None}),
        ("symeon junior", {"label": "SYMEON JUNIOR.", "parent": None}),
        ("nicetas chartophylax nicenus", {"label": "NICETAS CHARTOPHYLAX NICÆNUS.", "parent": None}),
        ("michael cerularius patriarcha cp", {"label": "MICHAEL CERULARIUS PATRIARCHA CP.", "parent": None}),
        ("samonas gazensis episcopus", {"label": "SAMONAS GAZENSIS EPISCOPUS.", "parent": None}),
        ("leo achridanus", {"label": "LEO ACHRIDANUS.", "parent": None}),
        ("nicetas pectoratus", {"label": "NICETAS PECTORATUS.", "parent": None}),
        ("joannes euchaitarum metropolita", {"label": "JOANNES EUCHAITARUM METROPOLITA.", "parent": None}),
        ("joannes xiphilinus patriarcha cp", {"label": "JOANNES XIPHILINUS PATRIARCHA CP.", "parent": None}),
        ("joannes cp diaconus", {"label": "JOANNES CP. DIACONUS.", "parent": None}),
    ]
)

NODE_TITLE_SET = set(NODE_TITLES.keys())

TITLE_RE = re.compile(r"^(?P<body>.*?)(?:\s+(?P<dashes>-{4})\s*)?(?P<page>\d{1,4})\s*$")
ROOT_START_RE = re.compile(r"^ORDO RERUM\b", re.I)
FINIS_RE = re.compile(r"^FINIS TOMI CENTESIMI VICESIMI\.\s*$")


def strip_diacritics(text: str) -> str:
    text = text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_title(text: str) -> str:
    text = strip_diacritics(text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:\.\s*)?(?:-{4}\s*)?\d{1,4}\s*$", "", text)
    text = re.sub(r"\s*[\.;:,]+$", "", text)
    return re.sub(r"[^0-9A-Za-z ]+", "", text).lower().strip()


def normalize_sort(text: str) -> str:
    return normalize_title(text)


def extract_lines(path: Path) -> list[tuple[str, Path]]:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', text, re.S)
    lines: list[tuple[str, Path]] = []
    for block in blocks:
        for raw in block.splitlines():
            line = raw.strip()
            if line:
                lines.append((line, path))
    return lines


def is_continuation_line(line: str, prev_line: str | None) -> bool:
    if not line:
        return False
    if line[0].islower() or line.startswith(("(", ")", ",", ";", ":", "—")):
        return True
    if prev_line is None:
        return False
    prev = prev_line.strip().rstrip()
    if prev.endswith("-"):
        return True
    tail = prev.lower().rstrip(".;:,)")
    if tail.endswith((" ex", " ad", " in", " de", " et", " cum", " per", " ut", " quo", " quod", " seu", " sine", " ab", " a")):
        return True
    return False


def parse_title_and_page(line: str) -> tuple[str, str | None]:
    match = TITLE_RE.match(line)
    if not match:
        return line, None
    body = match.group("body").strip()
    if match.group("dashes"):
        body = f"{body} {match.group('dashes')}"
    return body, match.group("page")


def strip_page_from_last_line(last_line: str) -> tuple[str, str | None]:
    match = TITLE_RE.match(last_line.strip())
    if not match:
        return last_line, None
    body = match.group("body").strip()
    if match.group("dashes"):
        body = f"{body} {match.group('dashes')}"
    return body, match.group("page")


def main() -> None:
    lines: list[tuple[str, Path]] = []
    for file_path in FILES:
        lines.extend(extract_lines(file_path))

    section_file_start = str(FILES[0])
    section_file_end = str(FILES[-1])

    helper = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    helper_map = {entry["entry_id"]: entry for entry in helper.get("entries", [])}

    section_helper = helper_map.get("pg120_ordo_rerum_continentur", {})
    addenda_helper = helper_map.get("pg120_addenda_eusebia_fragmentum", {})

    section = {
        "section_key": "PG120:alpha:ordo_rerum:001",
        "volume_id": "PG120",
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 1298,
        "page_end": 1304,
        "file_start": section_file_start,
        "file_end": section_file_end,
        "confidence": 0.98,
        "raw_json": {
            "helper_status": section_helper.get("status"),
            "helper_entry_id": "pg120_ordo_rerum_continentur",
            "helper_candidate_role": section_helper.get("best_candidate", {}).get("candidate_role"),
            "helper_reason_summary": section_helper.get("best_candidate", {}).get("reason_summary"),
            "helper_best_candidate": section_helper.get("best_candidate"),
            "section_kind_reason": "Editorial `ORDO RERUM` closing the tome; the printed page sequence continues through 1304 and the OCR file suffixes are separate from editorial pagination.",
        },
    }

    nodes: list[dict] = []
    entries: list[dict] = []
    refs: list[dict] = []
    node_keys: dict[str, str] = {}
    current_parent: str | None = None
    current_node_order = 0
    current_entry_order = 0

    def make_node_key(title_norm: str) -> str:
        return f"PG120:node:{title_norm.replace(' ', '-')}"

    def make_entry_key(idx: int) -> str:
        return f"PG120:entry:{idx:04d}"

    pending_lines: list[tuple[str, Path]] = []

    def flush_pending() -> None:
        nonlocal pending_lines, current_parent, current_node_order, current_entry_order
        if not pending_lines:
            return
        raw_lines = [line for line, _ in pending_lines]
        source_file = str(pending_lines[0][1])
        target_file = str(pending_lines[0][1])
        page_raw = None
        if raw_lines:
            last_body, page_raw = strip_page_from_last_line(raw_lines[-1])
            if page_raw:
                raw_lines = raw_lines[:-1] + [last_body]
        raw_text = "\n".join(raw_lines).strip()
        body = re.sub(r"\s+", " ", raw_text.replace("\u2003", " ")).strip()
        title_norm = normalize_title(body)

        if title_norm in NODE_TITLE_SET:
            node_info = NODE_TITLES[title_norm]
            parent_norm = node_info["parent"]
            parent_key = node_keys.get(parent_norm) if parent_norm else None
            current_node_order += 1
            node_key = make_node_key(title_norm)
            node_keys[title_norm] = node_key
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": section["section_key"],
                    "parent_node_key": parent_key,
                    "node_order": current_node_order,
                    "node_kind": "heading_group",
                    "label_raw": node_info["label"],
                    "label_norm": normalize_title(node_info["label"]),
                    "label_sort": normalize_sort(node_info["label"]),
                    "node_level": 1 if parent_key is None else 2,
                    "confidence": 0.99,
                    "raw_json": {
                        "source_file": source_file,
                        "node_title_norm": title_norm,
                    },
                }
            )
            current_parent = node_key
            pending_lines = []
            return

        current_entry_order += 1
        entry_key = make_entry_key(current_entry_order)
        lemma_raw = body
        lemma_display = lemma_raw
        lemma_norm = normalize_title(lemma_raw)
        lemma_sort = normalize_sort(lemma_raw)

        inferred_printed_page = int(page_raw) if page_raw else None
        entry = {
            "entry_key": entry_key,
            "section_key": section["section_key"],
            "parent_node_key": current_parent,
            "entry_order": current_entry_order,
            "entry_kind": "lemma",
            "lemma_raw": lemma_raw,
            "lemma_display": lemma_display,
            "lemma_norm": lemma_norm,
            "lemma_sort": lemma_sort,
            "entry_raw": raw_text,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": inferred_printed_page,
            "section_start_file": section_file_start,
            "editorial_anchor_file": source_file,
            "target_file_best": target_file,
            "confidence": 0.9 if page_raw else 0.76,
            "raw_json": {
                "source_file": source_file,
                "line_buffered": True,
                "page_ref_tokens": [page_raw] if page_raw else [],
            },
        }

        if entry_key == "PG120:entry:0100":
            pass

        entries.append(entry)
        if page_raw:
            refs.append(
                {
                    "entry_key": entry_key,
                    "ref_order": 1,
                    "ref_kind": "editorial_page",
                    "ref_raw": page_raw if "----" not in raw_text else f"---- {page_raw}",
                    "page_ref_raw": page_raw,
                    "page_ref_int": int(page_raw),
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target_file,
                    "target_file_probability": 0.99 if source_file == target_file else 0.93,
                    "section_start_file": section_file_start,
                    "editorial_anchor_file": source_file,
                    "confidence": 0.95,
                    "raw_json": {
                        "source_file": source_file,
                        "page_ref_source": "trailing_OCR_number",
                    },
                }
            )
        pending_lines = []

    started = False
    saw_ordo = False
    for line, path in lines:
        if FINIS_RE.match(line):
            flush_pending()
            break
        if not started:
            if ROOT_START_RE.match(line):
                started = True
                saw_ordo = True
            continue
        if saw_ordo:
            if normalize_title(line) == "quae in hoc tomo continentur":
                continue
            if ROOT_START_RE.match(line):
                continue
            saw_ordo = False
        if not line:
            continue
        normalized_line = normalize_title(line)
        if normalized_line in NODE_TITLE_SET:
            flush_pending()
            pending_lines = [(line, path)]
            flush_pending()
            continue
        if pending_lines:
            pending_lines.append((line, path))
            _, maybe_page = strip_page_from_last_line(line)
            if maybe_page:
                flush_pending()
            continue
        pending_lines = [(line, path)]
        _, maybe_page = strip_page_from_last_line(line)
        if maybe_page:
            flush_pending()

    flush_pending()

    # Repair the trailing addenda item with helper metadata and a full literal name.
    for entry in entries:
        if entry["lemma_norm"] == "addenda ad joannem euchaitensem fragmentum ex vita sanctae eusebiae":
            entry["lemma_raw"] = "Addenda ad Joannem Euchaitensem. Fragmentum ex Vita sanctæ Eusebiæ."
            entry["lemma_display"] = entry["lemma_raw"]
            entry["lemma_norm"] = normalize_title(entry["lemma_raw"])
            entry["lemma_sort"] = normalize_sort(entry["lemma_raw"])
            entry["entry_raw"] = "Addenda ad Joannem Euchaitensem. Fragmentum ex\nVita sanctæ Eusebiæ. 1297"
            entry["context_raw"] = None
            entry["confidence"] = 0.93
            entry["editorial_anchor_file"] = str(FILES[-1])
            entry["target_file_best"] = str(FILES[-1])
            entry["raw_json"].update(
                {
                    "helper_status": addenda_helper.get("status"),
                    "helper_entry_id": "pg120_addenda_eusebia_fragmentum",
                    "helper_candidate_role": addenda_helper.get("best_candidate", {}).get("candidate_role"),
                    "helper_reason_summary": addenda_helper.get("best_candidate", {}).get("reason_summary"),
                    "helper_best_candidate": addenda_helper.get("best_candidate"),
                }
            )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PG120",
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PG120",
            "notes": [
                "PG120 closes with `ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR` followed by a small editorial addendum trail.",
                "OCR file suffixes, printed page numbers, and cited references were kept separate.",
            ],
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the `ORDO RERUM` closure as a hierarchical content list with explicit page references; the final addendum line was preserved as a separate entry.",
            "evidence_files": [str(path) for path in FILES],
        },
        "notes": [
            "Only the highest-level author and closing headings were promoted to `nodes`; the remaining title lines and chapter lists were serialized as `entries`.",
            "The helper resolved the section anchor to the page carrying the `ORDO RERUM` title and the final addendum to the terminal file in the volume tail.",
        ],
    }

    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
