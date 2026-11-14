#!/usr/bin/env python3
"""Build the reviewed PL100 final-ORDO fragment (files 585--589)."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "teste/PL100/text"
OUT = ROOT / "data/index_intermediate_payloads/PL100/chunks/section_002_part_001.json"
PREFIX = "30d1ba31-d951-494a-92bc-aba94acc3b27-"
OWNED_NUMBERS = range(584, 590)
CONTEXT_NUMBERS = range(580, 594)
SECTION_KEY = "PL100:candidate-section:002"
CHUNK_ID = "PL100:chunk:002:001"


def source_name(number: int) -> str:
    return str(SOURCE_ROOT / f"{PREFIX}{number}.txt")


OWNED = [source_name(number) for number in OWNED_NUMBERS]
CONTEXT = [source_name(number) for number in CONTEXT_NUMBERS]


def clean(line: str) -> str:
    return re.sub(r"[ \t]+", " ", html.unescape(line).strip())


def blocks(path: Path) -> list[str]:
    """Return main-text blocks in printed two-column reading order."""
    raw = path.read_text(encoding="utf-8")
    found: list[tuple[int, int, str]] = []
    pattern = re.compile(r'<bloco tipo="texto_principal"[^>]*bbox="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
    for match in pattern.finditer(raw):
        x1, y1, _, _ = (int(value) for value in match.group(1).split(","))
        text = "\n".join(clean(line) for line in match.group(2).splitlines() if clean(line))
        if text:
            found.append((x1, y1, text))
    return [text for _, _, text in sorted(found)]


START = re.compile(
    r"^(?:"
    r"EPIST\."
    r"|EPISTOLA\b"
    r"|Epist\."
    r"|CAP\."
    r"|CAPUT\b"
    r"|CAPP\."
    r"|LIBER\b"
    r"|OPUSCULUM\b"
    r"|Monitum\b"
    r"|Praefatio\b"
    r"|Prefatio\b"
    r"|Incip(?:it|int)\b"
    r"|Dicta\b"
    r"|De Benedictionibus\b"
    r"|De decem\b"
    r"|De beato\b"
    r"|Hymnus\b"
    r"|Carmen\b"
    r"|Versus\b"
    r"|Oratio\b"
    r"|Interpretatio\b"
    r"|Fragmentum\b"
    r"|Ordo alphabeticalis\b"
    r"|B\. FLACCUS\b"
    r"|B\. F\. ALBINI\b"
    r"|BEATI \w+ ALC\w+NI OPERUM PARS\b"
    r"|BEATI FLACCI ALCUINI VITA\b"
    r"|COMMENTATIO\b"
    r"|Prooemium\b"
    r"|Prologus\b"
    r"|INDEX ANALYTICUS\b"
    r")"
)


def combine(left: str, right: str) -> str:
    if left.endswith("-") and right[:1].isalpha():
        return left[:-1] + right
    return f"{left} {right}"


def entry_kind(target: str) -> str:
    letters = [char for char in target if char.isalpha()]
    if letters and all(char == char.upper() for char in letters):
        return "contents_heading"
    if target.startswith(("LIBER ", "OPUSCULUM ", "BEATI ALCUINI OPERUM PARS")):
        return "contents_heading"
    return "contents_entry"


def ref_fields(raw: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"\s+(Ibid\.|ibid\.|Ib\.|\d{1,4}\.?)$", raw)
    if not match:
        return raw, None, None
    locator = match.group(1)
    return raw[:match.start()].rstrip(), locator, int(locator.rstrip(".")) if locator.rstrip(".").isdigit() else None


def build_items() -> list[tuple[str, list[str], list[str]]]:
    """Segment contents rows, carrying only proven page-boundary continuations."""
    items: list[tuple[str, list[str], list[str]]] = []
    pending = ""
    pending_sources: list[str] = []
    pending_lines: list[str] = []

    def flush() -> None:
        nonlocal pending, pending_sources, pending_lines
        if pending:
            items.append((pending, pending_sources, pending_lines))
        pending, pending_sources, pending_lines = "", [], []

    started = False
    for number in OWNED_NUMBERS:
        path = Path(source_name(number))
        for block in blocks(path):
            for line in block.splitlines():
                if not started:
                    if line == "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.":
                        started = True
                    continue
                if START.match(line) and pending:
                    flush()
                pending = line if not pending else combine(pending, line)
                pending_lines.append(line)
                if str(path) not in pending_sources:
                    pending_sources.append(str(path))
                if re.search(r"(?:\bIbid\.|\bibid\.|\bIb\.|\b\d{1,4}\.?$)", line):
                    flush()
    flush()
    return items


def main() -> None:
    entries = []
    for order, (raw, source_files, source_lines) in enumerate(build_items(), 1):
        target, page_raw, page_int = ref_fields(raw)
        kind = entry_kind(target)
        initial_continuation = raw.startswith("aliosque complares in Gallia")
        entries.append(
            {
                "entry_key": f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}",
                "entry_order": order,
                "entry_raw": raw,
                "target_raw": target,
                "target_file": None,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": None,
                "note_raw": (
                    "heading" if kind == "contents_heading"
                    else "Initial continuation: the opening portion is not readable in the owned OCR evidence."
                    if initial_continuation else None
                ),
                "normalized_target": target,
                "confidence": "low" if initial_continuation else "medium" if page_raw is None else "high",
                "raw_json": {
                    "source_files": source_files,
                    "source_lines": source_lines,
                    "entry_kind": "continuation_fragment" if initial_continuation else kind,
                    "locator_system": "editorial_page",
                },
            }
        )

    payload = {
        "schema_version": 2,
        "volume_id": "PL100",
        "section_id": SECTION_KEY,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": "4bed225f520152a1a175de63323c96ae7435cf49fe7a324c7780fa36bd5dfa37",
        "status": "complete",
        "physical_files": OWNED,
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [
            {
                "physical_left_file": source_name(585),
                "physical_right_file": source_name(586),
                "decision": "joined_entry",
                "reason": "EPIST. L ends on file 585 as ‘Exhor-’ and file 586 begins ‘tatur ad virtutem. 214’; the hyphen and syntax prove the single entry ‘Exhortatur’. ",
            },
            {
                "physical_left_file": source_name(586),
                "physical_right_file": source_name(587),
                "decision": "joined_entry",
                "reason": "EPIST. CVIII ends on file 586 as ‘a sub-’ and file 587 begins ‘jectione defecisse’; this is the proven lexical continuation ‘subjectione’ in one entry ending 521.",
            },
            {
                "physical_left_file": source_name(587),
                "physical_right_file": source_name(588),
                "decision": "joined_entry",
                "reason": "EPIST. CLVIII ends on file 587 as ‘Congregationem re-’ and file 588 begins ‘prehendit’; the joined word ‘reprehendit’ and continuation to 415 prove one entry.",
            },
            {
                "physical_left_file": source_name(588),
                "physical_right_file": source_name(589),
                "decision": "joined_entry",
                "reason": "Epist. CCXXII ends on file 588 as ‘Commencu-’ and file 589 begins ‘rat sanam’; the continuation forms ‘Commencurat’ and remains one entry with locator 495.",
            },
        ],
        "works": [],
        "sections": [
            {
                "section_key": SECTION_KEY,
                "work_key": None,
                "scope_kind": "volume_end",
                "index_kind": "ORDO_RERUM",
                "heading_raw": "ORDO RERUM / QUÆ IN HOC TOMO CONTINENTUR.",
                "heading_norm": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR.",
                "page_start": 1166,
                "page_end": 1174,
                "file_start": OWNED[0],
                "file_end": OWNED[-1],
                "confidence": "high",
                "raw_json": {
                    "source_files": OWNED,
                    "adjacent_context_files_checked": CONTEXT,
                    "section_kind": "volume_contents_table",
                    "header_evidence": [
                        "1166 (file 584, context)",
                        "1167 ORDO RERUM 1168 (file 586)",
                        "1169 QUAE IN HOC TOMO CONTINENTUR. 1170 (file 587)",
                        "1171 ORDO RERUM 1172 (file 588)",
                        "1173 QUÆ IN HOC TOMO CONTINENTUR. 1174 (file 589)",
                    ],
                    "numbering_note": "Terminal numbers and Ibid./ibid./Ib. in rows are preserved as editorial locators. They are never equated with physical OCR filename suffixes.",
                    "extra_file_evidence": {
                        "preceding_files_checked": [source_name(number) for number in range(580, 585)],
                        "following_files_checked": [source_name(number) for number in range(590, 594)],
                        "preceding_result": "File 584 contains the table heading and initial rows, which are serialized. Its earlier analytical-index tail is outside this general-contents section. File 585 begins with the readable tail of a prior Vita row; that tail is retained as a low-confidence continuation fragment because its opening words are absent from the OCR evidence.",
                        "following_result": "File 590 continues the contents table after the owned CAP. XVIII row; files 591-593 are end matter. No following item was emitted.",
                        "target_locator": "not_run_by_chunk_contract",
                    },
                    "entries_status": "complete",
                    "entries_status_reason": "Every readable contents-table row whose ascending first physical source is owned (585-589) is represented. The four proven cross-file continuations retain both files; the 584-origin continuation is not re-emitted.",
                },
                "entries": entries,
            }
        ],
        "notes": [
            "Bounded fragment only; this does not assemble the volume-level ORDO RERUM.",
            "OCR literals are retained, including uncertain names and corrupted words.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(entries)} entries")


if __name__ == "__main__":
    main()
