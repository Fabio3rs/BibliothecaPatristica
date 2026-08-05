#!/usr/bin/env python3
"""Usage: build the PL070 alphabetical-index payload from OCR, helper output, and local checkpoints.

Run from the repository root, for example:
  python scripts/pipeline_index_extraction/build_pl070_alphabetical_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL070/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL070_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL070_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL070 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL070_alphabetical_indices.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


BLOCK_RE = re.compile(r'<bloco[^>]*tipo="(?P<kind>[^"]+)"[^>]*>(?P<content>.*?)</bloco>', re.IGNORECASE | re.DOTALL)
SECTION1_HEADING_RE = re.compile(r"INDEX RERUM ET VERBORUM,?\s*QU[AEÆ] IN HOC TOMO CONTINENTUR\.?", re.IGNORECASE)
SECTION2_HEADING_RE = re.compile(r"AUCTORES A CASSIODORO CITATI\.?", re.IGNORECASE)
SECTION2_SUBHEADING_RE = re.compile(r"IN TOMO (PRIMO|SECUNDO)\.?", re.IGNORECASE)
SECTION3_HEADING_RE = re.compile(r"ORDO RERUM(?:\s+QU[AEÆ] IN HOC TOMO CONTINENTUR\.?)?", re.IGNORECASE)
PAGE_RE = re.compile(r"(?<!\d)(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?(?!\d)")
IBID_RE = re.compile(r"\bibid\.?\b", re.IGNORECASE)
REMISSION_RE = re.compile(r"\b(?:vid\.?|vide|voir|v\.|cf\.|id\.)\b", re.IGNORECASE)
ANALYTIC_SPLIT_RE = re.compile(r"(?<=[0-9a-z\)])\.\s+(?=[A-ZÆŒ])")
NOISE_LINES = {"Digitized by Google", "||", "."}
SECTION_ORDER = [
    ("analytic_subject", "INDEX RERUM ET VERBORUM, QUÆ IN HOC TOMO CONTINENTUR.", 716, 734, 1425, 1458),
    ("author_index", "AUCTORES A CASSIODORO CITATI.", 734, 734, 1459, 1460),
    ("ordo_rerum", "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.", 735, 736, 1461, 1464),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(encoded + "\n", encoding="utf-8")


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = value.strip(" ,;:.")
    return value or None


def discover_text_files(source_root: Path) -> list[Path]:
    return sorted(source_root.glob("*.txt"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]))


def file_seq(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[-1])


def extract_blocks(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(raw):
        kind = (match.group("kind") or "").strip().lower()
        content = match.group("content") or ""
        lines = [normalize(line) for line in content.splitlines()]
        lines = [line for line in lines if line and line not in NOISE_LINES]
        if lines:
            blocks.append({"kind": kind, "lines": lines, "text": "\n".join(lines)})
    return blocks


def build_page_map(files: list[Path]) -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in files:
        blocks = extract_blocks(path)
        head_lines: list[str] = []
        for block in blocks[:3]:
            if block["kind"] in {"cabecalho", "outro"}:
                head_lines.extend(block["lines"][:3])
        if not head_lines:
            head_lines = [line for block in blocks[:2] for line in block["lines"][:3]]
        for line in head_lines:
            for match in PAGE_RE.finditer(line):
                page = int(match.group(1))
                if page not in page_map:
                    page_map[page] = str(path)
    return page_map


def lookup_target(page: int | None, page_map: dict[int, str]) -> tuple[str | None, float | None]:
    if page is None:
        return None, None
    if page in page_map:
        return page_map[page], 0.97
    # Conservative fuzzy fallback for OCR headers that dropped or changed a digit.
    candidates: list[tuple[int, int, str]] = []
    for candidate_page, candidate_path in page_map.items():
        score = abs(candidate_page - page)
        if score <= 3:
            candidates.append((score, candidate_page, candidate_path))
        elif str(candidate_page)[-3:] == str(page)[-3:]:
            candidates.append((100 + score, candidate_page, candidate_path))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2], 0.70


def split_analytic_segments(text: str) -> list[str]:
    parts = [part.strip() for part in ANALYTIC_SPLIT_RE.split(text) if part.strip()]
    collapsed: list[str] = []
    for part in parts:
        if (
            collapsed
            and REMISSION_RE.match(part)
            and not PAGE_RE.search(part)
            and not PAGE_RE.search(collapsed[-1])
        ):
            collapsed[-1] = f"{collapsed[-1]}. {part}".strip()
            continue
        collapsed.append(part)
    return collapsed


def page_tokens(text: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    last_page: int | None = None
    for match in PAGE_RE.finditer(text):
        raw = match.group(0).strip()
        page = int(match.group(1))
        end = match.group(2)
        token = {
            "ref_raw": raw,
            "page_ref_raw": raw,
            "page_ref_int": page,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "page": page,
            "is_range": bool(end),
        }
        if end is not None:
            token["range_start_raw"] = str(page)
            token["range_end_raw"] = str(int(end))
        tokens.append(token)
        last_page = page
    if IBID_RE.search(text):
        tokens.append(
            {
                "ref_raw": "ibid.",
                "page_ref_raw": "ibid.",
                "page_ref_int": last_page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "page": last_page,
                "is_range": False,
                "ibid": True,
            }
        )
    return tokens


def infer_lemma(text: str, section_kind: str) -> str | None:
    cleaned = normalize(text) or ""
    cleaned = re.sub(r"^\d{1,4}\.?\s+", "", cleaned)
    if section_kind == "analytic_subject":
        if " V. " in f" {cleaned} ":
            cleaned = cleaned.split(" V. ", 1)[0]
        first_page = PAGE_RE.search(cleaned)
        if first_page:
            cleaned = cleaned[: first_page.start()]
        return normalize(cleaned)
    if section_kind == "author_index":
        first_page = PAGE_RE.search(cleaned)
        if first_page:
            cleaned = cleaned[: first_page.start()]
        if "," in cleaned:
            cleaned = cleaned.split(",", 1)[0]
        return normalize(cleaned)
    if section_kind == "ordo_rerum":
        first_page = PAGE_RE.search(cleaned)
        if first_page:
            cleaned = cleaned[: first_page.start()]
        return normalize(cleaned)
    return normalize(cleaned)


def detect_entry_kind(text: str, section_kind: str) -> str:
    stripped = normalize(text) or ""
    has_pages = bool(PAGE_RE.search(stripped))
    has_remission = bool(REMISSION_RE.search(stripped))
    if section_kind == "ordo_rerum":
        if stripped.upper().startswith(("PRÆFATIO", "PRAEFATIO", "CAP.", "CAP ", "CAPUT", "EXPOSITIO", "DE ", "PARS ", "CASSIODORI OPERUM CONTINUATIO")):
            return "heading_group"
        return "heading_group" if has_pages else "heading_group"
    if not has_pages and has_remission:
        return "cross_reference"
    if not has_pages and stripped.isupper():
        return "heading_group"
    return "lemma"


def extract_refs(text: str, section_kind: str, page_map: dict[int, str], section_start_file: str, editorial_anchor_file: str) -> tuple[list[dict[str, Any]], int | None, str | None]:
    refs: list[dict[str, Any]] = []
    last_page: int | None = None
    first_target: str | None = None
    ref_order = 1
    for match in PAGE_RE.finditer(text):
        raw = match.group(0).strip()
        page = int(match.group(1))
        end = match.group(2)
        target_file, target_prob = lookup_target(page, page_map)
        if first_target is None:
            first_target = target_file
        ref = {
            "ref_order": ref_order,
            "ref_kind": "editorial_range" if end is not None else "editorial_page",
            "ref_raw": raw,
            "page_ref_raw": raw,
            "page_ref_int": page,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": str(page) if end is not None else None,
            "range_end_raw": str(int(end)) if end is not None else None,
            "target_file": target_file,
            "target_file_probability": target_prob,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "confidence": 0.94 if target_file else 0.63,
            "raw_json": {
                "source_token": raw,
                "section_kind": section_kind,
                "target_lookup": "exact" if target_prob and target_prob > 0.95 else ("fuzzy" if target_file else "unresolved"),
            },
        }
        refs.append(ref)
        ref_order += 1
        last_page = page
    if IBID_RE.search(text):
        target_file, target_prob = lookup_target(last_page, page_map)
        ref = {
            "ref_order": ref_order,
            "ref_kind": "editorial_page",
            "ref_raw": "ibid.",
            "page_ref_raw": "ibid.",
            "page_ref_int": last_page,
            "page_ref_col": None,
            "line_ref_raw": None,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": target_file,
            "target_file_probability": target_prob,
            "section_start_file": section_start_file,
            "editorial_anchor_file": editorial_anchor_file,
            "confidence": 0.72 if target_file else 0.55,
            "raw_json": {
                "source_token": "ibid.",
                "section_kind": section_kind,
                "inherited_page": last_page,
            },
        }
        refs.append(ref)
        last_page = last_page
    return refs, last_page, first_target


def maybe_letter(text: str) -> str | None:
    stripped = normalize(text) or ""
    return stripped if re.fullmatch(r"[A-Z]", stripped) else None


def maybe_heading(text: str, section_kind: str) -> bool:
    stripped = normalize(text) or ""
    if section_kind == "analytic_subject":
        return bool(SECTION1_HEADING_RE.search(stripped))
    if section_kind == "author_index":
        return bool(SECTION2_HEADING_RE.search(stripped) or SECTION2_SUBHEADING_RE.search(stripped))
    if section_kind == "ordo_rerum":
        return bool(SECTION3_HEADING_RE.search(stripped))
    return False


def make_section(section_kind: str, heading_raw: str, file_start: int, file_end: int, page_start: int, page_end: int, order: int, volume_id: str) -> dict[str, Any]:
    return {
        "section_key": f"{volume_id}:alpha:{section_kind}:{order:03d}",
        "volume_id": volume_id,
        "work_key": None,
        "section_order": order,
        "section_kind": section_kind,
        "heading_raw": heading_raw,
        "heading_norm": normalize(heading_raw).lower() if normalize(heading_raw) else None,
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": file_start,
        "file_end": file_end,
        "section_start_file": None,
        "confidence": 0.94 if section_kind != "ordo_rerum" else 0.97,
        "raw_json": {
            "section_kind_reason": {
                "analytic_subject": "Analytical subject index recovered from the OCR tail.",
                "author_index": "Alphabetical list of authors cited by Cassiodorus.",
                "ordo_rerum": "Editorial closing contents list.",
            }[section_kind],
        },
    }


def build_payload(
    *,
    volume_id: str,
    source_root: Path,
    helper_output_json: Path,
    intermediate_dir: Path,
) -> dict[str, Any]:
    files = discover_text_files(source_root)
    file_by_seq = {file_seq(path): str(path) for path in files}
    page_map = build_page_map(files)
    helper_output = read_json(helper_output_json, {})

    sections: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    scripture_refs: list[dict[str, Any]] = []

    section_spec_map = {
        "analytic_subject": make_section("analytic_subject", SECTION_ORDER[0][1], SECTION_ORDER[0][2], SECTION_ORDER[0][3], SECTION_ORDER[0][4], SECTION_ORDER[0][5], 1, volume_id),
        "author_index": make_section("author_index", SECTION_ORDER[1][1], SECTION_ORDER[1][2], SECTION_ORDER[1][3], SECTION_ORDER[1][4], SECTION_ORDER[1][5], 2, volume_id),
        "ordo_rerum": make_section("ordo_rerum", SECTION_ORDER[2][1], SECTION_ORDER[2][2], SECTION_ORDER[2][3], SECTION_ORDER[2][4], SECTION_ORDER[2][5], 3, volume_id),
    }
    section_spec_map["analytic_subject"]["file_start"] = file_by_seq.get(SECTION_ORDER[0][2])
    section_spec_map["analytic_subject"]["file_end"] = file_by_seq.get(SECTION_ORDER[0][3])
    section_spec_map["analytic_subject"]["section_start_file"] = file_by_seq.get(SECTION_ORDER[0][2])
    section_spec_map["author_index"]["file_start"] = file_by_seq.get(SECTION_ORDER[1][2])
    section_spec_map["author_index"]["file_end"] = file_by_seq.get(SECTION_ORDER[1][3])
    section_spec_map["author_index"]["section_start_file"] = file_by_seq.get(SECTION_ORDER[1][2])
    section_spec_map["ordo_rerum"]["file_start"] = file_by_seq.get(SECTION_ORDER[2][2])
    section_spec_map["ordo_rerum"]["file_end"] = file_by_seq.get(SECTION_ORDER[2][3])
    section_spec_map["ordo_rerum"]["section_start_file"] = file_by_seq.get(SECTION_ORDER[2][2])
    sections.extend([section_spec_map["analytic_subject"], section_spec_map["author_index"], section_spec_map["ordo_rerum"]])

    letter_nodes: dict[str, str] = {}
    tomo_nodes: dict[str, str] = {}
    current_section_kind: str | None = None
    current_letter: str | None = None
    current_tomo: str | None = None
    entry_counters = defaultdict(int)

    def add_letter_node(letter: str) -> str:
        if letter in letter_nodes:
            return letter_nodes[letter]
        node_key = f"{volume_id}:alpha:analytic_subject:001:node:{letter}"
        letter_nodes[letter] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_spec_map["analytic_subject"]["section_key"],
                "parent_node_key": None,
                "node_order": len(letter_nodes),
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"source": "standalone letter marker"},
            }
        )
        return node_key

    def add_tomo_node(label: str) -> str:
        if label in tomo_nodes:
            return tomo_nodes[label]
        node_key = f"{volume_id}:alpha:author_index:002:node:{len(tomo_nodes)+1}"
        tomo_nodes[label] = node_key
        nodes.append(
            {
                "node_key": node_key,
                "section_key": section_spec_map["author_index"]["section_key"],
                "parent_node_key": None,
                "node_order": len(tomo_nodes),
                "node_kind": "heading_group",
                "label_raw": label,
                "label_norm": normalize(label).lower() if normalize(label) else None,
                "label_sort": normalize(label).lower() if normalize(label) else None,
                "node_level": 1,
                "confidence": 0.98,
                "raw_json": {"source": "tomo heading"},
            }
        )
        return node_key

    def make_entry(section_kind: str, text: str, source_file: Path, parent_node_key: str | None) -> None:
        nonlocal entries, refs
        cleaned = normalize(text)
        if not cleaned:
            return
        if cleaned in NOISE_LINES:
            return
        if maybe_heading(cleaned, section_kind):
            return

        entry_counters[section_kind] += 1
        entry_order = entry_counters[section_kind]
        entry_key = f"{volume_id}:alpha:{section_kind}:{entry_order:05d}"
        section_start_file = section_spec_map[section_kind].get("section_start_file") or str(source_file)

        entry_kind = detect_entry_kind(cleaned, section_kind)
        lemma_raw = infer_lemma(cleaned, section_kind)
        page_refs, inferred_page, target_file_best = extract_refs(
            cleaned,
            section_kind,
            page_map,
            section_start_file,
            str(source_file),
        )
        if entry_kind == "cross_reference" and page_refs:
            entry_kind = "lemma"
        if section_kind == "ordo_rerum" and not page_refs:
            entry_kind = "heading_group"
        if lemma_raw is None and entry_kind != "cross_reference":
            lemma_raw = cleaned
        if entry_kind == "cross_reference" and lemma_raw and lemma_raw.startswith("V. "):
            lemma_raw = lemma_raw[3:].strip(" ,;:.")

        refs.extend(
            [
                {
                    **ref,
                    "entry_key": entry_key,
                }
                for ref in page_refs
            ]
        )

        if entry_kind == "cross_reference" and not page_refs and inferred_page is None and cleaned.startswith("V. "):
            inferred_page = None

        target_best = target_file_best or (page_refs[0]["target_file"] if page_refs else None) or str(source_file)
        confidence = 0.95 if page_refs else 0.76
        if not page_refs:
            confidence = 0.66 if entry_kind == "cross_reference" else 0.74
        if section_kind == "ordo_rerum":
            confidence = 0.92 if page_refs else 0.86

        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_spec_map[section_kind]["section_key"],
                "parent_node_key": parent_node_key,
                "entry_order": entry_order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": normalize(lemma_raw).lower() if lemma_raw else None,
                "lemma_sort": normalize(lemma_raw).lower() if lemma_raw else None,
                "entry_raw": cleaned,
                "context_raw": cleaned,
                "heading_letter": current_letter if section_kind == "analytic_subject" else None,
                "inferred_printed_page": inferred_page,
                "section_start_file": section_start_file,
                "editorial_anchor_file": str(source_file),
                "target_file_best": target_best,
                "confidence": confidence,
                "raw_json": {
                    "section_kind": section_kind,
                    "source_file": str(source_file),
                    "helper_used": bool(helper_output),
                    "split_strategy": "analytic_sentence_split" if section_kind == "analytic_subject" else "line_level",
                    "helper_status": helper_output.get("status"),
                    "helper_candidate_role": helper_output.get("candidate_role"),
                },
            }
        )

    for path in files:
        blocks = extract_blocks(path)
        for block in blocks:
            text_lines = block["lines"]
            joined = "\n".join(text_lines)
            if maybe_heading(joined, "analytic_subject") and current_section_kind != "author_index":
                current_section_kind = "analytic_subject"
                current_letter = None
                continue
            if maybe_heading(joined, "author_index"):
                current_section_kind = "author_index"
                current_tomo = None
                continue
            if maybe_heading(joined, "ordo_rerum"):
                current_section_kind = "ordo_rerum"
                continue
            if current_section_kind is None:
                continue

            if current_section_kind == "analytic_subject":
                for line in text_lines:
                    letter = maybe_letter(line)
                    if letter:
                        current_letter = letter
                        add_letter_node(letter)
                        continue
                    for segment in split_analytic_segments(line):
                        segment = re.sub(r"^\d{1,4}\.?\s+", "", segment).strip()
                        if not segment:
                            continue
                        parent_node = letter_nodes.get(current_letter) if current_letter else None
                        make_entry("analytic_subject", segment, path, parent_node)
            elif current_section_kind == "author_index":
                for line in text_lines:
                    letter = maybe_letter(line)
                    if letter:
                        current_letter = letter
                        continue
                    if SECTION2_SUBHEADING_RE.fullmatch(line):
                        current_tomo = line
                        add_tomo_node(line)
                        continue
                    if not line or line in NOISE_LINES:
                        continue
                    parent_node = tomo_nodes.get(current_tomo) if current_tomo else None
                    make_entry("author_index", line, path, parent_node)
            elif current_section_kind == "ordo_rerum":
                for line in text_lines:
                    if not line or line in NOISE_LINES:
                        continue
                    make_entry("ordo_rerum", line, path, None)

    volume = {
        "volume_id": volume_id,
        "collection": "PL",
        "source_root": str(source_root),
        "volume_label": "Patrologia Latina 70",
        "notes": "Analytical index, author index, and ordo rerum recovered from the OCR tail.",
    }
    coverage = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered the analytic index, the author index, and the closing ordo rerum block from the OCR tail with conservative line-level grouping. "
            "A few OCR lines remain noisy or partially inherited, so the payload preserves them without over-normalizing."
        ),
        "evidence_files": [
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-716.txt"),
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-717.txt"),
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-718.txt"),
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-719.txt"),
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-734.txt"),
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-735.txt"),
            str(source_root / "99afcb42-78ed-4465-9696-0b9ccccc64b9-736.txt"),
        ],
    }
    notes = [
        {
            "note_key": f"{volume_id}:note:1",
            "note_kind": "extraction_note",
            "note_raw": "The analytic index begins after the heading in file 716 and continues through the closing author list in file 734.",
            "confidence": 0.9,
            "raw_json": {
                "helper_output_path": str(helper_output_json),
            },
        }
    ]

    fragments = {
        "volume.json": volume,
        "sections.json": sections,
        "nodes.json": nodes,
        "entries.json": entries,
        "refs.json": refs,
        "scripture_refs.json": scripture_refs,
        "coverage.json": coverage,
        "notes.json": notes,
        "manifest.json": {
            "volume_id": volume_id,
            "updated_at": now_iso(),
            "generated_at": now_iso(),
        },
    }

    intermediate_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in fragments.items():
        write_json(intermediate_dir / name, payload)
    return fragments


def assemble_final(intermediate_dir: Path, output_file: Path) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "generated_at": read_json(intermediate_dir / "manifest.json").get("generated_at"),
        "volume": read_json(intermediate_dir / "volume.json"),
        "sections": read_json(intermediate_dir / "sections.json", []),
        "nodes": read_json(intermediate_dir / "nodes.json", []),
        "entries": read_json(intermediate_dir / "entries.json", []),
        "refs": read_json(intermediate_dir / "refs.json", []),
        "scripture_refs": read_json(intermediate_dir / "scripture_refs.json", []),
        "coverage": read_json(intermediate_dir / "coverage.json", {}),
        "notes": read_json(intermediate_dir / "notes.json", []),
    }
    write_json(output_file, payload)
    return payload


def run_helper(helper_request_json: Path, helper_output_json: Path) -> None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "index_target_locator.py"),
        "--input",
        str(helper_request_json),
        "--output",
        str(helper_output_json),
        "--pretty",
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"index_target_locator.py failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the PL070 alphabetical-index payload.")
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--helper-request-json", type=Path, required=True)
    ap.add_argument("--helper-output-json", type=Path, required=True)
    ap.add_argument("--intermediate-dir", type=Path, required=True)
    ap.add_argument("--output-file", type=Path, required=True)
    args = ap.parse_args()

    build_payload(
        volume_id="PL070",
        source_root=args.source_root,
        helper_output_json=args.helper_output_json,
        intermediate_dir=args.intermediate_dir,
    )
    assemble_final(args.intermediate_dir, args.output_file)


if __name__ == "__main__":
    main()
