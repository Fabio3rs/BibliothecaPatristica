#!/usr/bin/env python3
"""Usage:
  python scripts/pipeline_index_extraction/PG026_build_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PG026/text \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG026_alphabetical_indices.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PG026 \
    --helper-request /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG026_helper_request.json \
    --helper-output /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PG026_helper_output.json

Builds the PG026 alphabetical payload from the OCR tail by splitting the
analytic index, the festal epistle index, and the closing contents table into
conservative entries and material references.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG026"
COLLECTION = "PG"
VOLUME_LABEL = "Patrologia Graeca 26"

DEFAULT_SOURCE_ROOT = ROOT / "teste/PG026/text"
DEFAULT_OUTPUT_FILE = ROOT / "data/alphabetical_index_payloads/PG026_alphabetical_indices.json"
DEFAULT_INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG026"
DEFAULT_HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG026_helper_request.json"
DEFAULT_HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG026_helper_output.json"
TODO_JSON = DEFAULT_INTERMEDIATE_DIR / "todo.json"

SECTION_1 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:001",
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX ANALYTICUS.",
    "heading_norm": "index analyticus",
    "heading_letter": None,
    "page_start": 1461,
    "page_end": 1518,
    "file_start_seq": 737,
    "file_end_seq": 765,
    "section_order": 1,
    "section_kind_reason": "Main analytic index of subjects and names. The OCR shows repeated header drift and occasional page-number corruption, so the section is anchored primarily by the section heading and file order.",
}

SECTION_2 = {
    "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
    "section_kind": "analytic_subject",
    "heading_raw": "INDEX AD EPISTOLAS FESTALES ET AD CHRONICA DUO.",
    "heading_norm": "index ad epistolas festales et ad chronica duo",
    "heading_letter": None,
    "page_start": 1519,
    "page_end": 1520,
    "file_start_seq": 766,
    "file_end_seq": 767,
    "section_order": 2,
    "section_kind_reason": "Alphabetical index for festal epistles and chronicle material. The title is repeated in the OCR, with letter-group headings preserved as nodes.",
}

SECTION_3 = {
    "section_key": f"{VOLUME_ID}:alpha:ordo_rerum:003",
    "section_kind": "ordo_rerum",
    "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
    "heading_norm": "ordo rerum quae in hoc tomo continentur",
    "heading_letter": None,
    "page_start": None,
    "page_end": None,
    "file_start_seq": 768,
    "file_end_seq": 768,
    "section_order": 3,
    "section_kind_reason": "Closing contents table for the tome. This is editorial closure rather than an ordinary alphabetical index, but it is kept in the same payload for completeness.",
}

SECTIONS = [SECTION_1, SECTION_2, SECTION_3]

NOISE_LINES = {
    "Digitized by Google",
}
TITLE_PREFIXES = (
    "INDEX ANALYTICUS",
    "IN DUOS. PRIORES TOMOS.",
    "Rerecatur lector ad numerales notas typis grandioribus in textu expressas.",
    "INDEX AD EPISTOLAS FESTALES ET AD CHRONICA DUO.",
    "INDEX AD EPISTOLAS FESTALES S. ATHANASII ET AD CHRONICA DUO.",
    "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
    "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def sort_norm(text: str | None) -> str | None:
    value = normalize(text)
    if not value:
        return None
    value = strip_accents(value)
    value = value.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE").replace("œ", "oe")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value or None


def file_seq(path: Path) -> int:
    match = re.search(r"-(\d+)\.txt$", path.name)
    if not match:
        raise ValueError(f"cannot parse file sequence from {path}")
    return int(match.group(1))


def discover_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=file_seq)


def extract_blocks(path: Path) -> tuple[list[str], list[str], list[str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    headers: list[str] = []
    main_lines: list[str] = []
    notes: list[str] = []
    for match in re.finditer(r'<bloco[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', raw, flags=re.S):
        tipo = (match.group(1) or "").strip().lower()
        content = re.sub(r"<[^>]+>", " ", match.group(2) or "")
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line]
        if tipo == "cabecalho":
            headers.extend(lines)
            continue
        if tipo == "notas":
            notes.extend(lines)
            continue
        if tipo != "texto_principal":
            continue
        main_lines.extend(lines)
    return headers, main_lines, notes


def header_page_numbers(headers: list[str]) -> list[int]:
    pages: list[int] = []
    for header in headers:
        for token in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", header):
            if token.startswith("0"):
                continue
            value = int(token)
            if value not in pages:
                pages.append(value)
    return pages


def is_single_letter(line: str) -> bool:
    return bool(re.fullmatch(r"[A-ZÆŒUVXYZ]", line))


def is_title_line(line: str) -> bool:
    normalized = normalize(line) or ""
    return any(normalized.startswith(prefix) for prefix in TITLE_PREFIXES)


def is_noise(line: str) -> bool:
    return line in NOISE_LINES


def starts_lowercase(line: str) -> bool:
    for ch in line:
        if ch.isalpha():
            return ch.islower()
    return False


def first_alpha_letter(text: str | None) -> str | None:
    if not text:
        return None
    for ch in text:
        if ch.isalpha():
            return ch.upper()
    return None


def same_letter_family(actual: str | None, expected: str | None) -> bool:
    if not actual or not expected:
        return False
    actual = actual.upper()
    expected = expected.upper()
    families = {
        "A": {"A", "Æ"},
        "Æ": {"A", "Æ"},
        "V": {"V", "U"},
        "U": {"U", "V"},
    }
    return actual in families.get(expected, {expected})


def extract_lemma(entry_raw: str) -> str | None:
    text = normalize(entry_raw) or ""
    # Prefer the leading clause before the first citation-like anchor.
    patterns = [
        r",\s*(?:p\.\s*)?\d",
        r"\s+p\.\s*\d",
        r"\s+\d{1,4}\s*(?:et\s+seqq\.?|seqq\.?|seq\.?|ibid\.?|id\.?)",
        r"\s+Vide\b",
        r"\s+vide\b",
    ]
    cut = None
    for pat in patterns:
        m = re.search(pat, text)
        if m and (cut is None or m.start() < cut):
            cut = m.start()
    if cut is not None and cut > 0:
        return normalize(text[:cut].rstrip(" .;:-"))
    if "," in text:
        left = normalize(text.split(",", 1)[0].rstrip(" .;:-"))
        if left:
            return left
    if ". " in text:
        left = normalize(text.split(". ", 1)[0].rstrip(" .;:-"))
        if left:
            return left
    return normalize(text.rstrip(" .;:-"))


def estimate_target_file(file_map: dict[int, Path], page_ref_int: int | None) -> tuple[str | None, float | None, dict[str, Any] | None]:
    if page_ref_int is None:
        return None, None, None
    estimated_seq = (page_ref_int + 13) // 2
    estimated_path = file_map.get(estimated_seq)
    if estimated_path:
        return (
            str(estimated_path),
            0.94,
            {
                "locator_method": "physical_page_estimate",
                "estimated_file_seq": estimated_seq,
                "estimator_formula": "(page_ref_int + 13) // 2",
            },
        )
    return None, None, {
        "locator_method": "physical_page_estimate",
        "estimated_file_seq": estimated_seq,
        "estimator_formula": "(page_ref_int + 13) // 2",
        "locator_status": "estimated_seq_missing",
    }


PAGE_REF_RE = re.compile(
    r"(?<!\w)"
    r"(?:(?:p\.\s*)?)"
    r"(?P<num>\d{1,4})"
    r"(?P<range>(?:\s*[-–]\s*\d{1,4})?)"
    r"(?P<tail>\s*(?:et\s+seqq\.?|seqq\.?|seq\.?|ibid\.?|id\.?)?)",
    re.IGNORECASE,
)


def extract_refs(entry_raw: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    text = normalize(entry_raw) or ""
    for match in PAGE_REF_RE.finditer(text):
        num = match.group("num")
        left = text[max(0, match.start() - 18): match.start()].lower()
        if any(tag in left for tag in ("anno", "ann.", "an. ", "annos", "christi")):
            continue
        ref_raw = normalize(match.group(0))
        key = (ref_raw or num, num)
        if key in seen:
            continue
        seen.add(key)
        page_ref_int = int(num)
        refs.append(
            {
                "ref_raw": ref_raw or num,
                "page_ref_raw": ref_raw or num,
                "page_ref_int": page_ref_int,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
            }
        )
    return refs


def segment_lines(
    lines: list[tuple[str, Path]],
    section_kind: str,
    *,
    entry_start: int = 0,
    node_start: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    entries: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    node_order = node_start
    entry_order = entry_start
    current_node_key: str | None = None

    def flush_current() -> None:
        nonlocal current, entry_order
        if not current:
            return
        entry_order += 1
        current["entry_order"] = entry_order
        current["entry_key"] = f"{VOLUME_ID}:entry:{entry_order:04d}"
        current["lemma_raw"] = extract_lemma(current["entry_raw"])
        current["lemma_display"] = current["lemma_raw"]
        current["lemma_norm"] = sort_norm(current["lemma_raw"])
        current["lemma_sort"] = sort_norm(current["lemma_raw"])
        current["heading_letter"] = first_alpha_letter(current["lemma_raw"])
        current["confidence"] = 0.86 if current["entry_kind"] == "lemma" else 0.8
        current["raw_json"] = {
            "source_file": str(current["_source_file"]),
            "section_kind": section_kind,
            "line_count": current.pop("_line_count"),
        }
        entries.append(current)
        current = None

    for line, source_file in lines:
        if is_noise(line) or is_title_line(line):
            continue
        if is_single_letter(line):
            flush_current()
            node_order += 1
            node_key = f"{VOLUME_ID}:node:{node_order:03d}"
            nodes.append(
                {
                    "node_key": node_key,
                    "section_key": None,
                    "parent_node_key": None,
                    "node_order": node_order,
                    "node_kind": "letter_group",
                    "label_raw": line,
                    "label_norm": line.lower(),
                    "label_sort": line.lower(),
                    "node_level": 1,
                    "confidence": 0.99,
                    "raw_json": {
                        "source_file": str(source_file),
                        "section_kind": section_kind,
                    },
                }
            )
            current_node_key = node_key
            continue
        if current and starts_lowercase(line):
            current["entry_raw"] = normalize_hyphen_break(current["entry_raw"] + " " + line)
            current["_line_count"] += 1
            continue
        flush_current()
        entry_kind = "cross_reference" if re.match(r"^(vide|vid\.|cf\.|voir)\b", line, re.IGNORECASE) else "lemma"
        current = {
            "entry_key": None,
            "section_key": None,
            "parent_node_key": current_node_key,
            "entry_order": None,
            "entry_kind": entry_kind,
            "lemma_raw": None,
            "lemma_display": None,
            "lemma_norm": None,
            "lemma_sort": None,
            "entry_raw": line,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": None,
            "section_start_file": None,
            "editorial_anchor_file": None,
            "target_file_best": None,
            "confidence": None,
            "_source_file": source_file,
            "_line_count": 1,
            "raw_json": {},
        }
    flush_current()
    return entries, nodes, entry_order, node_order


def merge_fragment_entries(
    entries: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    section_kind: str,
) -> list[dict[str, Any]]:
    node_letter_map = {node["node_key"]: node["label_raw"] for node in nodes if node.get("node_kind") == "letter_group"}
    merged: list[dict[str, Any]] = []
    for entry in entries:
        lemma = entry.get("lemma_raw") or extract_lemma(entry.get("entry_raw") or "")
        actual_letter = first_alpha_letter(lemma)
        expected_letter = node_letter_map.get(entry.get("parent_node_key"))
        starts_with_digit = bool(re.match(r"^\d", normalize(entry.get("entry_raw")) or ""))
        wrong_letter = expected_letter is not None and not same_letter_family(actual_letter, expected_letter)
        fragment_like = starts_with_digit or wrong_letter or actual_letter is None
        if fragment_like and merged:
            previous = merged[-1]
            previous["entry_raw"] = normalize_hyphen_break(f'{previous["entry_raw"]} {entry["entry_raw"]}') or previous["entry_raw"]
            prev_lines = previous.setdefault("_line_count", previous.get("raw_json", {}).get("line_count", 1))
            entry_lines = entry.get("_line_count", entry.get("raw_json", {}).get("line_count", 1))
            previous["_line_count"] = prev_lines + entry_lines
            previous.setdefault("_merged_fragments", []).append(
                {
                    "entry_raw": entry["entry_raw"],
                    "lemma_raw": lemma,
                    "source_file": str(entry.get("_source_file") or entry.get("raw_json", {}).get("source_file")),
                    "fragment_reason": {
                        "starts_with_digit": starts_with_digit,
                        "wrong_letter_for_node": wrong_letter,
                        "expected_letter": expected_letter,
                        "actual_letter": actual_letter,
                    },
                }
            )
            continue
        merged.append(entry)
    return merged


def normalize_hyphen_break(text: str) -> str:
    value = normalize(text) or ""
    value = re.sub(r"\bPATROL\.\s*GB\.\s*XXVI,?\s*", "", value)
    value = re.sub(r"(?<=\w)-\s+\[(?=\w)", "[", value)
    value = re.sub(r"(?<=\w)-\.\s+(?=\w)", "", value)
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = re.sub(r"(?<=\w)-$", "", value)
    return normalize(value) or ""


def deep_normalize_hyphen_break(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_hyphen_break(value)
    if isinstance(value, list):
        return [deep_normalize_hyphen_break(item) for item in value]
    if isinstance(value, dict):
        return {key: deep_normalize_hyphen_break(item) for key, item in value.items()}
    return value


def postprocess_ordo_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fixed: list[dict[str, Any]] = []
    for entry in entries:
        raw = normalize(entry.get("entry_raw")) or ""
        if fixed:
            prev = fixed[-1]
            prev_raw = normalize(prev.get("entry_raw")) or ""
            if prev_raw.endswith("-") and raw.isupper():
                prev["entry_raw"] = normalize_hyphen_break(f"{prev_raw} {raw}")
                prev["_line_count"] = prev.get("_line_count", 1) + entry.get("_line_count", 1)
                prev.setdefault("_merged_fragments", []).append(
                    {
                        "entry_raw": entry["entry_raw"],
                        "lemma_raw": extract_lemma(entry["entry_raw"]),
                        "source_file": str(entry.get("_source_file") or entry.get("raw_json", {}).get("source_file")),
                        "fragment_reason": {
                            "ordo_heading_continuation": True,
                        },
                    }
                )
                continue
        fixed.append(entry)
    for entry in fixed:
        raw = normalize(entry.get("entry_raw")) or ""
        if raw.isupper() or raw.startswith("S. ATHANASIUS"):
            entry["entry_kind"] = "heading_group"
    return fixed


def build_helper_request(source_root: Path) -> dict[str, Any]:
    entries = [
        {
            "entry_id": "pg026_a_athanasii_1519",
            "lemma_raw": "Athanasii admodum juvenis assumitur ad episcopatum an. Christi 328.",
            "query_names": [
                "Athanasii admodum juvenis assumitur ad episcopatum",
                "Athanasius",
                "episcopatum an. Christi 328",
            ],
            "page_hints": ["1519", "1520"],
            "page_hint_ints": [1519, 1520],
            "context_raw": "Athanasii admodum juvenis assumitur ad episcopatum an. Christi 328. Post pascha p. 2 et 3.",
        },
        {
            "entry_id": "pg026_a_arius_manes_1519",
            "lemma_raw": "Arius et Manes arguuntur",
            "query_names": ["Arius et Manes arguuntur", "Arius", "Manes"],
            "page_hints": ["1519"],
            "page_hint_ints": [1519],
            "context_raw": "Arius et Manes arguuntur, p. 93, 107, 110.",
        },
        {
            "entry_id": "pg026_a_judaeorum_1519",
            "lemma_raw": "Judaeorum recentiorum paschalis ritus vituperatur",
            "query_names": [
                "Judaeorum recentiorum paschalis ritus vituperatur",
                "paschalis ritus",
                "Judaeorum recentiorum",
            ],
            "page_hints": ["1519"],
            "page_hint_ints": [1519],
            "context_raw": "Judaeorum recentiorum paschalis ritus vituperatur, p. 26, 63, 134, 156, 144.",
        },
        {
            "entry_id": "pg026_a_verbum_simplex_1519",
            "lemma_raw": "Verbum simplex sanctis juramenti loco est",
            "query_names": ["Verbum simplex sanctis juramenti loco est", "juramenti loco est"],
            "page_hints": ["1519"],
            "page_hint_ints": [1519],
            "context_raw": "Verbum simplex sanctis juramenti loco est, 209.",
        },
    ]
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(source_root),
        "options": {
            "top_k": 5,
            "adjacency_window": 2,
        },
        "entries": entries,
    }


def build_payload(source_root: Path, helper_request_path: Path, helper_output_path: Path, intermediate_dir: Path) -> dict[str, Any]:
    files = discover_files(source_root)
    file_map = {file_seq(path): path for path in files}

    helper_request = build_helper_request(source_root)
    write_json(helper_request_path, helper_request)

    volume_note = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "PG026 alphabetical payload",
        "completed": [
            "Section boundaries identified",
            "OCR tail parsed into conservative entries",
        ],
        "pending": [
            "Validate helper output for representative festal-index anchors",
            "Review any entries with year-like numbers before import",
        ],
        "blocked": [],
        "notes": [
            "Section 1 is the main INDEX ANALYTICUS block across files 737-765.",
            "Section 2 is the festal/chronicle index across files 766-767.",
            "Section 3 is the closing ORDO RERUM table.",
        ],
    }
    write_json(TODO_JSON, volume_note)

    section_payloads: list[dict[str, Any]] = []
    node_payloads: list[dict[str, Any]] = []
    entry_payloads: list[dict[str, Any]] = []
    ref_payloads: list[dict[str, Any]] = []
    notes: list[str] = [
        "OCR literals preserved. Page drift and header corruption are recorded conservatively in section metadata.",
    ]

    entry_counter = 0
    node_counter = 0
    for section in SECTIONS:
        section_lines: list[tuple[str, Path]] = []
        section_pages: list[int] = []
        start_path = file_map.get(section["file_start_seq"])
        end_path = file_map.get(section["file_end_seq"])
        for seq in range(section["file_start_seq"], section["file_end_seq"] + 1):
            path = file_map.get(seq)
            if not path:
                continue
            headers, main_lines, _ = extract_blocks(path)
            page_numbers = header_page_numbers(headers)
            if page_numbers:
                section_pages.extend(page_numbers)
            for line in main_lines:
                section_lines.append((line, path))

        entries, nodes, entry_counter, node_counter = segment_lines(
            section_lines,
            section["section_kind"],
            entry_start=entry_counter,
            node_start=node_counter,
        )
        entries = merge_fragment_entries(entries, nodes, section["section_kind"])
        if section["section_kind"] == "ordo_rerum":
            entries = postprocess_ordo_entries(entries)

        for node in nodes:
            node["section_key"] = section["section_key"]
            node_payloads.append(node)

        entry_counter = len(entry_payloads)
        for entry in entries:
            entry_counter += 1
            entry["section_key"] = section["section_key"]
            entry["section_start_file"] = str(start_path) if start_path else None
            entry["editorial_anchor_file"] = str(entry.pop("_source_file"))
            entry["target_file_best"] = None
            if section["section_kind"] in {"analytic_subject", "ordo_rerum"}:
                entry["inferred_printed_page"] = section["page_start"]
            else:
                entry["inferred_printed_page"] = section["page_start"]
            if entry["entry_kind"] == "cross_reference" and "Vide" not in (entry["entry_raw"] or ""):
                entry["entry_kind"] = "cross_reference"
            entry["entry_order"] = entry_counter
            entry["entry_key"] = f"{VOLUME_ID}:entry:{entry_counter:04d}"
            entry["lemma_raw"] = normalize(entry["entry_raw"]) if entry["entry_kind"] == "heading_group" else extract_lemma(entry["entry_raw"])
            entry["lemma_display"] = entry["lemma_raw"]
            entry["lemma_norm"] = sort_norm(entry["lemma_raw"])
            entry["lemma_sort"] = sort_norm(entry["lemma_raw"])
            entry["heading_letter"] = first_alpha_letter(entry["lemma_raw"])
            line_count = entry.pop("_line_count", entry.get("raw_json", {}).get("line_count", 1))
            raw_json = {
                "source_file": entry["editorial_anchor_file"],
                "section_kind": section["section_kind"],
                "line_count": line_count,
            }
            if entry.get("_merged_fragments"):
                raw_json["merged_fragments"] = entry.pop("_merged_fragments")
            entry["raw_json"] = raw_json
            refs = extract_refs(entry["entry_raw"])
            best_target_file = None
            best_target_probability = None
            entry_payloads.append(entry)
            for idx, ref in enumerate(refs, start=1):
                target_file, target_probability, locator_raw = estimate_target_file(file_map, ref["page_ref_int"])
                if best_target_file is None and target_file is not None:
                    best_target_file = target_file
                    best_target_probability = target_probability
                ref_payloads.append(
                    {
                        "entry_key": entry["entry_key"],
                        "ref_order": idx,
                        "ref_kind": "editorial_page",
                        "ref_raw": ref["ref_raw"],
                        "page_ref_raw": ref["page_ref_raw"],
                        "page_ref_int": ref["page_ref_int"],
                        "page_ref_col": None,
                        "line_ref_raw": ref["line_ref_raw"],
                        "range_start_raw": ref["range_start_raw"],
                        "range_end_raw": ref["range_end_raw"],
                        "target_file": target_file,
                        "target_file_probability": target_probability,
                        "section_start_file": str(start_path) if start_path else None,
                        "editorial_anchor_file": entry["editorial_anchor_file"],
                        "confidence": 0.84 if target_file else 0.6,
                        "raw_json": {
                            "source_entry": entry["entry_raw"][:240],
                            "section_kind": section["section_kind"],
                            **({"locator_evidence": locator_raw} if locator_raw else {}),
                        },
                    }
                )
            entry["target_file_best"] = best_target_file
            if best_target_file:
                entry["raw_json"]["target_file_best_reason"] = {
                    "locator_method": "first_resolved_ref",
                    "target_file_probability": best_target_probability,
                }

        section_payloads.append(
            {
                "section_key": section["section_key"],
                "volume_id": VOLUME_ID,
                "work_key": None,
                "section_order": section["section_order"],
                "section_kind": section["section_kind"],
                "heading_raw": section["heading_raw"],
                "heading_norm": section["heading_norm"],
                "heading_letter": section["heading_letter"],
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "file_start": str(start_path) if start_path else None,
                "file_end": str(end_path) if end_path else None,
                "confidence": 0.92 if section["section_order"] < 3 else 0.88,
                "raw_json": {
                    "section_kind_reason": section["section_kind_reason"],
                    "observed_header_pages": section_pages[:12],
                    "file_span": [section["file_start_seq"], section["file_end_seq"]],
                },
            }
        )

    coverage = {
        "entries_status": "extracted",
        "entries_status_reason": "Conservative extraction completed for all three detected closing sections.",
        "evidence_files": [
            str(file_map[737]),
            str(file_map[766]),
            str(file_map[768]),
        ],
    }

    volume = {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(source_root),
        "volume_label": VOLUME_LABEL,
        "notes": [
            "Main analytic index spans files 737-765 with OCR header drift and page-number corruption.",
            "Festal/chronicle index spans files 766-767.",
            "The contents table in file 768 is kept as editorial closure.",
        ],
    }

    helper_output = read_json(helper_output_path, {})
    if helper_output:
        notes.append("Helper output was present and preserved in the runtime workspace.")

    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume,
        "sections": section_payloads,
        "nodes": node_payloads,
        "entries": entry_payloads,
        "refs": ref_payloads,
        "scripture_refs": [],
        "coverage": coverage,
        "notes": notes,
    }
    payload = deep_normalize_hyphen_break(payload)

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    write_json(intermediate_dir / "manifest.json", {"volume_id": VOLUME_ID, "updated_at": payload["generated_at"]})
    write_json(intermediate_dir / "volume.json", volume)
    write_json(intermediate_dir / "sections.json", section_payloads)
    write_json(intermediate_dir / "nodes.json", node_payloads)
    write_json(intermediate_dir / "entries.json", entry_payloads)
    write_json(intermediate_dir / "refs.json", ref_payloads)
    write_json(intermediate_dir / "coverage.json", coverage)
    write_json(intermediate_dir / "notes.json", notes)

    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PG026 alphabetical payload.")
    ap.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    ap.add_argument("--intermediate-dir", type=Path, default=DEFAULT_INTERMEDIATE_DIR)
    ap.add_argument("--helper-request", type=Path, default=DEFAULT_HELPER_REQUEST)
    ap.add_argument("--helper-output", type=Path, default=DEFAULT_HELPER_OUTPUT)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    payload = build_payload(args.source_root, args.helper_request, args.helper_output, args.intermediate_dir)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
