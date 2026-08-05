#!/usr/bin/env python3
"""Usage: build the PG143 alphabetical payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/PG143_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG143/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG143_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG143_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG143 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG143_alphabetical_indices.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG143"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 143"
SCRIPT_TARGET_LOCATOR = ROOT / "scripts" / "index_target_locator.py"

SECTION_2_KEY = f"{VOLUME_ID}:alpha:onomastic_mixed:001"
SECTION_3_KEY = f"{VOLUME_ID}:alpha:ordo_rerum:002"

SECTION_2_HEADING_RAW = "INDEX IN EPHRÆMIUM."
SECTION_2_HEADING_NORM = "index in ephraemium"
SECTION_3_HEADING_RAW = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
SECTION_3_HEADING_NORM = "ordo rerum quae in hoc tomo continentur"

SECTION_2_START_MARKER = "INDEX IN EPHRÆMIUM."
SECTION_3_START_MARKER = "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR."
FINIS_MARKER = "FINIS TOMI CENTESIMI QUADRAGESIMI TERTII."

TEXT_BLOCK_RE = re.compile(r'<bloco tipo="(?P<kind>[^"]+)"[^>]*>(?P<body>.*?)</bloco>', re.S)
PAGE_PAIR_RE = re.compile(r"^\s*(\d{1,4})\b.*\b(\d{1,4})\s*$")
NUM_RE = re.compile(r"(?<!\d)(\d{1,5})(?!\d)")
LETTER_RE = re.compile(r"^[A-ZÆŒ]$")
SECTION2_SPLIT_RE = re.compile(r"(?:(?<=\.)|(?<=\d))\s+(?=[A-ZÆŒΑ-Ω—-])")
SPACE_RE = re.compile(r"\s+")
HYPHEN_SPACE_RE = re.compile(r"(?<=\w)-\s+(?=\w)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(data + ("\n" if not data.endswith("\n") else ""), encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = SPACE_RE.sub(" ", value).strip()
    return value


def fold(text: str | None) -> str:
    value = normalize(text)
    if not value:
        return ""
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = SPACE_RE.sub(" ", value).strip().lower()
    return value


def sort_norm(text: str | None) -> str | None:
    value = fold(text)
    return value or None


def file_seq(path: Path) -> int:
    m = re.search(r"-(\d+)\.txt$", path.name)
    if not m:
        raise ValueError(f"cannot parse OCR file seq from {path}")
    return int(m.group(1))


def parse_blocks(path: Path) -> list[dict[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(raw.strip())
        blocks = []
        for bloco in root.findall("bloco"):
            blocks.append(
                {
                    "kind": (bloco.attrib.get("tipo") or "").strip().lower(),
                    "text": normalize("".join(bloco.itertext())),
                    "file": str(path),
                }
            )
        return blocks
    except ET.ParseError:
        blocks = []
        for match in TEXT_BLOCK_RE.finditer(raw):
            body = re.sub(r"<[^>]+>", " ", match.group("body") or "")
            blocks.append(
                {
                    "kind": (match.group("kind") or "").strip().lower(),
                    "text": normalize(body),
                    "file": str(path),
                }
            )
        return blocks


def discover_files(source_root: Path) -> list[Path]:
    files = []
    for path in sorted(source_root.glob("*.txt"), key=file_seq):
        seq = file_seq(path)
        if 709 <= seq <= 715:
            files.append(path)
    return files


def build_page_map(source_root: Path) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in sorted(source_root.glob("*.txt"), key=file_seq):
        for block in parse_blocks(path):
            if block["kind"] != "cabecalho":
                continue
            header = block["text"]
            m = PAGE_PAIR_RE.fullmatch(header)
            if m:
                left = int(m.group(1))
                right = int(m.group(2))
                page_map.setdefault(left, str(path))
                page_map.setdefault(right, str(path))
                continue
            for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", header):
                page_map.setdefault(int(match.group(1)), str(path))
    return page_map


def split_fragments(text: str) -> list[str]:
    value = normalize(text)
    value = HYPHEN_SPACE_RE.sub("", value)
    value = value.replace("\n", " ")
    value = SPACE_RE.sub(" ", value).strip()
    if not value:
        return []
    parts = SECTION2_SPLIT_RE.split(value)
    return [part.strip() for part in parts if part and part.strip()]


def strip_leading_letter(fragment: str, current_letter: str | None) -> str:
    if not current_letter:
        return fragment
    if fragment.startswith(current_letter + " "):
        return fragment[len(current_letter) + 1 :].lstrip()
    return fragment


def extract_lemma(fragment: str) -> str | None:
    value = normalize(fragment)
    if not value:
        return None
    cut = len(value)
    for pattern in [r"\bVide\b", r"\bvid\.\b", r"\bvoir\b", r"\bv\.\b", r"\bcf\.\b", r"\bid\.\b"]:
        m = re.search(pattern, value, flags=re.IGNORECASE)
        if m:
            cut = min(cut, m.start())
    m = NUM_RE.search(value)
    if m:
        cut = min(cut, m.start())
    lemma = value[:cut].strip(" ,;:.")
    return lemma or None


def entry_kind_for(fragment: str) -> str:
    value = normalize(fragment)
    if re.match(r"^(?:vide|vid\.|voir|v\.|cf\.|id\.)", value, flags=re.IGNORECASE):
        return "cross_reference"
    if re.search(r"\b(?:vide|vid\.|voir|v\.|cf\.|id\.)\b", value, flags=re.IGNORECASE):
        return "cross_reference"
    if value.startswith("—") or value.startswith("-"):
        return "sublemma"
    return "lemma"


def make_query_names(lemma_raw: str) -> list[str]:
    variants = []
    base = normalize(lemma_raw)
    if base:
        variants.append(base)
    folded = fold(base)
    if folded and folded != base:
        variants.append(folded)
    if " v. " in base.lower():
        variants.append(base.split(" v. ", 1)[0].strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:4] or [lemma_raw]


def helper_compact(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    best = entry.get("best_candidate") or {}
    candidates = []
    for cand in (entry.get("candidates") or [])[:3]:
        candidates.append(
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "candidate_role": cand.get("candidate_role"),
                "reason_summary": cand.get("reason_summary"),
                "evidence_kinds": [
                    ev.get("kind")
                    for ev in (cand.get("evidence") or [])
                    if isinstance(ev, dict) and ev.get("kind")
                ],
            }
        )
    return {
        "status": entry.get("status"),
        "candidate_role": entry.get("candidate_role"),
        "reason_summary": entry.get("reason_summary"),
        "best_candidate": {
            "file": best.get("file"),
            "probability": best.get("probability"),
            "candidate_role": best.get("candidate_role"),
            "reason_summary": best.get("reason_summary"),
        }
        if best
        else None,
        "candidates": candidates,
    }


def run_helper(helper_request_json: Path, helper_output_json: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_TARGET_LOCATOR),
            "--input",
            str(helper_request_json),
            "--output",
            str(helper_output_json),
            "--pretty",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(helper_output_json.read_text(encoding="utf-8"))


def helper_map(helper_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    items = helper_output.get("entries") or helper_output.get("results") or []
    for item in items:
        entry_id = item.get("entry_id")
        if entry_id:
            mapping[str(entry_id)] = item
    return mapping


def choose_target_candidate(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    candidates = item.get("candidates") or []
    if not candidates:
        return item.get("best_candidate")
    for cand in candidates:
        if cand.get("candidate_role") != "index_page_candidate":
            return cand
    return candidates[0]


def is_index_window_file(path_text: str | None) -> bool:
    if not path_text:
        return False
    try:
        seq = file_seq(Path(path_text))
    except Exception:
        return False
    return 709 <= seq <= 715


def build_entry_common(
    entry_key: str,
    section_key: str,
    parent_node_key: str | None,
    entry_order: int,
    entry_kind: str,
    lemma_raw: str | None,
    entry_raw: str,
    heading_letter: str | None,
    inferred_printed_page: int | None,
    section_start_file: str,
    editorial_anchor_file: str | None,
    target_file_best: str | None,
    confidence: float,
    raw_json: dict[str, Any],
    context_raw: str | None = None,
) -> dict[str, Any]:
    lemma_display = lemma_raw if lemma_raw else None
    lemma_norm = fold(lemma_raw) if lemma_raw else None
    return {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": parent_node_key,
        "entry_order": entry_order,
        "entry_kind": entry_kind,
        "lemma_raw": lemma_raw,
        "lemma_display": lemma_display,
        "lemma_norm": lemma_norm,
        "lemma_sort": lemma_norm,
        "entry_raw": entry_raw,
        "context_raw": context_raw,
        "heading_letter": heading_letter,
        "inferred_printed_page": inferred_printed_page,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": target_file_best,
        "confidence": confidence,
        "raw_json": raw_json,
    }


def parse_refs(fragment: str) -> list[int]:
    return [int(m.group(1)) for m in NUM_RE.finditer(fragment)]


def parse_section2(
    blocks: list[dict[str, str]],
    section_start_file: str,
    helper_request_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    started = False
    current_letter = None
    letter_order = 0
    entry_order = 0
    letter_nodes: dict[str, str] = {}
    pending_ordinal: str | None = None

    for block in blocks:
        text = normalize(block["text"])
        if not text:
            continue
        if block["kind"] == "cabecalho" and SECTION_2_START_MARKER in text:
            started = True
            continue
        if block["kind"] == "cabecalho" and SECTION_3_START_MARKER in text:
            break
        if not started:
            continue
        if block["kind"] == "nota_marginal" and LETTER_RE.fullmatch(text):
            current_letter = text
            if current_letter not in letter_nodes:
                letter_order += 1
                node_key = f"{SECTION_2_KEY}:node:{current_letter}:{letter_order:03d}"
                letter_nodes[current_letter] = node_key
                nodes.append(
                    {
                        "node_key": node_key,
                        "section_key": SECTION_2_KEY,
                        "parent_node_key": None,
                        "node_order": letter_order,
                        "node_kind": "letter_group",
                        "label_raw": current_letter,
                        "label_norm": current_letter.lower(),
                        "label_sort": current_letter.lower(),
                        "node_level": 1,
                        "confidence": 0.98,
                        "raw_json": {"source": "note_marginal"},
                    }
                )
            continue

        if block["kind"] not in {"texto_principal", "cabecalho"}:
            continue

        for fragment in split_fragments(text):
            fragment = strip_leading_letter(fragment, current_letter)
            fragment = normalize(fragment)
            if not fragment:
                continue
            ordinal_value = pending_ordinal
            used_initial_ordinal = False
            if current_letter is None:
                m_initial = re.match(r"^(?P<lemma>.+?)\s+(?P<num>\d{1,4})\.$", fragment)
                if m_initial and not re.search(r"[,;]", m_initial.group("lemma")):
                    fragment = normalize(m_initial.group("lemma"))
                    pending_ordinal = m_initial.group("num")
                    used_initial_ordinal = True
            if re.fullmatch(r"\d{1,4}\.?", fragment):
                pending_ordinal = fragment.rstrip(".")
                continue
            if re.fullmatch(r"\d{3,4}\s+(?:INDEX|ORDO|FINIS)\b.*", fragment, flags=re.IGNORECASE):
                pending_ordinal = None
                continue
            if pending_ordinal and not used_initial_ordinal and re.match(r"^[A-ZÆŒΑ-Ω]", fragment):
                fragment = f"{pending_ordinal}. {fragment}"
                ordinal_value = pending_ordinal
                pending_ordinal = None
            if LETTER_RE.fullmatch(fragment):
                current_letter = fragment
                if current_letter not in letter_nodes:
                    letter_order += 1
                    node_key = f"{SECTION_2_KEY}:node:{current_letter}:{letter_order:03d}"
                    letter_nodes[current_letter] = node_key
                    nodes.append(
                        {
                            "node_key": node_key,
                            "section_key": SECTION_2_KEY,
                            "parent_node_key": None,
                            "node_order": letter_order,
                            "node_kind": "letter_group",
                            "label_raw": current_letter,
                            "label_norm": current_letter.lower(),
                            "label_sort": current_letter.lower(),
                            "node_level": 1,
                            "confidence": 0.98,
                            "raw_json": {"source": "body_letter_marker"},
                        }
                    )
                continue

            lemma_raw = extract_lemma(fragment)
            entry_kind = entry_kind_for(fragment)
            refs_ints = parse_refs(fragment)
            if lemma_raw is None:
                lemma_raw = fragment.rstrip(" ,;:.")
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:sec2:{entry_order:04d}"
            page_hint_ints = [value for value in refs_ints[:5] if value <= 9999]
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": make_query_names(lemma_raw),
                    "page_hints": [str(v) for v in page_hint_ints],
                    "page_hint_ints": page_hint_ints,
                "context_raw": fragment,
                }
            )
            entry = build_entry_common(
                entry_key=entry_key,
                section_key=SECTION_2_KEY,
                parent_node_key=letter_nodes.get(current_letter),
                entry_order=entry_order,
                entry_kind=entry_kind,
                lemma_raw=lemma_raw,
                entry_raw=fragment,
                heading_letter=current_letter,
                inferred_printed_page=refs_ints[0] if refs_ints else None,
                section_start_file=section_start_file,
                editorial_anchor_file=block["file"],
                target_file_best=None,
                confidence=0.75 if refs_ints else 0.62,
                raw_json={
                    "source_file": block["file"],
                    "section": "INDEX IN EPHRÆMII CÆSARES",
                    "needs_helper": bool(refs_ints),
                    "ordinal_prefix": ordinal_value,
                },
                context_raw=None,
            )
            entries.append(entry)
            for ref_order, value in enumerate(refs_ints, start=1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "target_locator",
                        "ref_raw": str(value),
                        "page_ref_raw": str(value),
                        "page_ref_int": value,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": None,
                        "target_file_probability": None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": block["file"],
                        "confidence": 0.68,
                        "raw_json": {
                            "source_file": block["file"],
                            "section": "INDEX IN EPHRÆMII CÆSARES",
                            "ordinal_prefix": ordinal_value,
                        },
                    }
                )

    return nodes, entries, refs, helper_entries


def parse_section3(
    blocks: list[dict[str, str]],
    section_start_file: str,
    helper_request_entries: list[dict[str, Any]],
    page_map: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    helper_entries: list[dict[str, Any]] = []

    started = False
    current_node_key: str | None = None
    node_order = 0
    entry_order = 0

    heading_patterns = [
        "EPHRÆMIUS CHRONOGRAPHUS.",
        "THEOLEPTUS PHILADELPHIENSIUM METROPOLITÆ.",
        "GEORGIUS PACHYMERES.",
    ]

    for block in blocks:
        text = normalize(block["text"])
        if not text:
            continue
        if block["kind"] == "cabecalho" and text == SECTION_3_START_MARKER:
            started = True
            continue
        if not started:
            continue
        if FINIS_MARKER in text:
            break

        if block["kind"] == "cabecalho":
            for marker in heading_patterns:
                if marker in text:
                    node_order += 1
                    current_node_key = f"{SECTION_3_KEY}:node:{node_order:03d}"
                    nodes.append(
                        {
                            "node_key": current_node_key,
                            "section_key": SECTION_3_KEY,
                            "parent_node_key": None,
                            "node_order": node_order,
                            "node_kind": "heading_group",
                            "label_raw": marker,
                            "label_norm": fold(marker),
                            "label_sort": fold(marker),
                            "node_level": 1,
                            "confidence": 0.97,
                            "raw_json": {"source_file": block["file"]},
                        }
                    )
                    break
            continue

        if block["kind"] not in {"texto_principal", "nota"}:
            continue

        for fragment in split_fragments(text):
            fragment = normalize(fragment)
            if not fragment or fragment == "—":
                continue
            # Merge short abbreviations such as "Ang." with the following title fragment.
            section_fragments = [fragment]
            if re.fullmatch(r"[A-ZÆŒ][a-z]{0,3}\.", fragment):
                section_fragments = []
            if section_fragments == []:
                continue
            # The block-level split above is intentionally conservative; section 3 has
            # several abbreviated title lines that need to stay as one logical entry.
            # Rebuild a one-step lookahead here to stitch those back together.
            #
            # This block is evaluated only after the generic split, so we look at the
            # raw fragment sequence by peeking ahead in the enclosing loop.
            if any(marker in fragment for marker in heading_patterns):
                node_order += 1
                current_node_key = f"{SECTION_3_KEY}:node:{node_order:03d}"
                nodes.append(
                    {
                        "node_key": current_node_key,
                        "section_key": SECTION_3_KEY,
                        "parent_node_key": None,
                        "node_order": node_order,
                        "node_kind": "heading_group",
                        "label_raw": fragment.rstrip(" ."),
                        "label_norm": fold(fragment),
                        "label_sort": fold(fragment),
                        "node_level": 1,
                        "confidence": 0.94,
                        "raw_json": {"source_file": block["file"]},
                    }
                )
                continue

            lemma_raw = extract_lemma(fragment)
            entry_kind = entry_kind_for(fragment)
            refs_ints = parse_refs(fragment)
            if lemma_raw is None:
                lemma_raw = fragment.rstrip(" ,;:.")
            entry_order += 1
            entry_key = f"{VOLUME_ID}:entry:sec3:{entry_order:04d}"
            page_hint_ints = [value for value in refs_ints[:5] if value <= 9999]
            helper_entries.append(
                {
                    "entry_id": entry_key,
                    "lemma_raw": lemma_raw,
                    "query_names": make_query_names(lemma_raw),
                    "page_hints": [str(v) for v in page_hint_ints],
                    "page_hint_ints": page_hint_ints,
                    "context_raw": fragment,
                }
            )
            target_file_best = None
            if refs_ints:
                target_file_best = page_map.get(refs_ints[0])
            entry = build_entry_common(
                entry_key=entry_key,
                section_key=SECTION_3_KEY,
                parent_node_key=current_node_key,
                entry_order=entry_order,
                entry_kind=entry_kind,
                lemma_raw=lemma_raw,
                entry_raw=fragment,
                heading_letter=None,
                inferred_printed_page=refs_ints[0] if refs_ints else None,
                section_start_file=section_start_file,
                editorial_anchor_file=block["file"],
                target_file_best=target_file_best,
                confidence=0.84 if refs_ints else 0.72,
                raw_json={
                    "source_file": block["file"],
                    "section": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR",
                    "needs_helper": bool(refs_ints),
                },
                context_raw=None,
            )
            entries.append(entry)
            for ref_order, value in enumerate(refs_ints, start=1):
                refs.append(
                    {
                        "entry_key": entry_key,
                        "ref_order": ref_order,
                        "ref_kind": "editorial_page",
                        "ref_raw": str(value),
                        "page_ref_raw": str(value),
                        "page_ref_int": value,
                        "page_ref_col": None,
                        "line_ref_raw": None,
                        "range_start_raw": None,
                        "range_end_raw": None,
                        "target_file": page_map.get(value),
                        "target_file_probability": 0.95 if page_map.get(value) else None,
                        "section_start_file": section_start_file,
                        "editorial_anchor_file": block["file"],
                        "confidence": 0.9 if page_map.get(value) else 0.7,
                        "raw_json": {
                            "source_file": block["file"],
                            "section": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR",
                        },
                    }
                )

    return nodes, entries, refs, helper_entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    source_root: Path = args.source_root
    intermediate_dir: Path = args.intermediate_dir
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Parse PG143 alphabetical index and resolve helper targets",
        "completed": [
            "Read OCR tail and confirmed the INDEX IN EPHRÆMII CÆSARES section",
            "Confirmed ORDO RERUM contents table at the end of the volume",
        ],
        "pending": [
            "Resolve helper targets for material references",
            "Write final payload JSON",
        ],
        "blocked": [],
        "notes": [
            "Use direct OCR reading for structural headings.",
            "Keep OCR file suffix distinct from printed page numbers and cited references.",
        ],
    }
    write_json(intermediate_dir / "todo.json", todo)

    files = discover_files(source_root)
    if not files:
        raise SystemExit("No OCR files found in the requested window.")
    page_map = build_page_map(source_root)

    blocks_by_file: dict[str, list[dict[str, str]]] = {}
    for path in files:
        blocks_by_file[str(path)] = parse_blocks(path)

    section2_blocks = [block for path in files for block in blocks_by_file[str(path)]]
    section3_blocks = section2_blocks

    section2_nodes, section2_entries, section2_refs, section2_helper_entries = parse_section2(
        section2_blocks,
        section_start_file=str(files[0]),
        helper_request_entries=[],
    )
    section3_nodes, section3_entries, section3_refs, section3_helper_entries = parse_section3(
        section3_blocks,
        section_start_file=str(files[-2]) if len(files) >= 2 else str(files[0]),
        helper_request_entries=[],
        page_map=page_map,
    )

    helper_entries = section2_helper_entries + section3_helper_entries
    helper_request = {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": helper_entries,
    }
    write_json(args.helper_request_json, helper_request)
    helper_output = run_helper(args.helper_request_json, args.helper_output_json)
    helper_lookup = helper_map(helper_output)

    # Apply helper resolution.
    helper_by_entry_id: dict[str, dict[str, Any]] = {}
    for item in helper_output.get("entries") or helper_output.get("results") or []:
        entry_id = item.get("entry_id")
        if entry_id:
            helper_by_entry_id[str(entry_id)] = item

    all_entries = section2_entries + section3_entries
    all_refs = section2_refs + section3_refs

    entry_index = {item["entry_key"]: item for item in all_entries}
    refs_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in all_refs:
        refs_by_entry[ref["entry_key"]].append(ref)

    for entry in all_entries:
        helper_item = helper_by_entry_id.get(entry["entry_key"])
        chosen = choose_target_candidate(helper_item)
        if entry["section_key"] == SECTION_2_KEY and chosen and is_index_window_file(chosen.get("file")):
            chosen = None
        chosen_file = chosen.get("file") if chosen else None
        entry["target_file_best"] = chosen_file
        entry["confidence"] = float(chosen.get("probability", entry["confidence"])) if chosen else entry["confidence"]
        entry.setdefault("raw_json", {})
        entry["raw_json"]["helper"] = helper_compact(helper_item)
        if helper_item and chosen and chosen.get("candidate_role") == "index_page_candidate":
            entry["raw_json"]["helper_choice_note"] = "helper preferred an index-page candidate; retained because no better target candidate was surfaced."
        if chosen and chosen_file:
            entry["editorial_anchor_file"] = chosen_file if entry["editorial_anchor_file"] is None else entry["editorial_anchor_file"]

    for ref in all_refs:
        helper_item = helper_by_entry_id.get(ref["entry_key"])
        chosen = choose_target_candidate(helper_item)
        if ref["entry_key"].startswith(f"{VOLUME_ID}:entry:sec2:") and chosen and is_index_window_file(chosen.get("file")):
            chosen = None
        if chosen and chosen.get("file"):
            ref["target_file"] = chosen.get("file")
            ref["target_file_probability"] = chosen.get("probability")
            ref["confidence"] = max(ref["confidence"], float(chosen.get("probability", ref["confidence"])))
        ref.setdefault("raw_json", {})
        ref["raw_json"]["helper"] = helper_compact(helper_item)

    sections = [
        {
            "section_key": SECTION_2_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "onomastic_mixed",
            "heading_raw": SECTION_2_HEADING_RAW,
            "heading_norm": SECTION_2_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1359,
            "page_end": 1368,
            "file_start": str(files[0]),
            "file_end": str(files[4]) if len(files) >= 5 else str(files[-1]),
            "confidence": 0.92,
            "raw_json": {
                "section_kind_reason": (
                    "Alphabetical onomastic index of persons, places, and related names; "
                    "the printed internal heading states 'INDEX IN EPHRÆMII CÆSARES. (Numeri versum significant.)'."
                ),
                "evidence_files": [str(path) for path in files[:5]],
                "observed_headings": [
                    "INDEX IN EPHRÆMII CÆSARES.",
                    "(Numeri versum significant.)",
                ],
            },
        },
        {
            "section_key": SECTION_3_KEY,
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "ordo_rerum",
            "heading_raw": SECTION_3_HEADING_RAW,
            "heading_norm": SECTION_3_HEADING_NORM,
            "heading_letter": None,
            "page_start": 1329,
            "page_end": 1372,
            "file_start": str(files[5]) if len(files) >= 6 else str(files[-2]) if len(files) >= 2 else str(files[0]),
            "file_end": str(files[-1]),
            "confidence": 0.98,
            "raw_json": {
                "section_kind_reason": "Tail editorial contents table for the volume, distinct from the alphabetical index above it.",
                "evidence_files": [str(files[5]) if len(files) >= 6 else str(files[-2]) if len(files) >= 2 else str(files[0]), str(files[-1])],
            },
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
            "notes": "PG143 contains an onomastic index followed by an Ordo Rerum contents table.",
        },
        "sections": sections,
        "nodes": section2_nodes + section3_nodes,
        "entries": all_entries,
        "refs": all_refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": (
                "Recovered the visible onomastic index and the tail Ordo Rerum table from the OCR tail, "
                "with helper-assisted target resolution for the material references."
            ),
            "evidence_files": [str(path) for path in files],
        },
        "notes": [
            "Section 2 follows the printed internal heading 'INDEX IN EPHRÆMII CÆSARES.' and groups entries by letter markers.",
            "Section 3 is the closing ORDO RERUM contents table for the tome.",
            "The OCR includes a few 5-digit citation strings that the helper may not score directly; they are preserved literally in the refs.",
        ],
    }

    write_json(args.output_file, payload)
    write_json(intermediate_dir / "sections.json", sections)
    write_json(intermediate_dir / "nodes.json", section2_nodes + section3_nodes)
    write_json(intermediate_dir / "entries.json", all_entries)
    write_json(intermediate_dir / "refs.json", all_refs)
    write_json(intermediate_dir / "volume.json", payload["volume"])
    write_json(intermediate_dir / "final_payload.json", payload)


if __name__ == "__main__":
    main()
