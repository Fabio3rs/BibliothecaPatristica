#!/usr/bin/env python3
"""Build the bounded PL089 closing-ORDO fragment from reviewed OCR blocks."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "teste/PL089/text"
OUT = ROOT / "data/index_intermediate_payloads/PL089/chunks/section_002_part_001.json"
PREFIX = "c0ce4443-ca22-48ae-bccc-44370cb15761-"
OWNED = [str(SOURCE_ROOT / f"{PREFIX}{n}.txt") for n in range(670, 676)]
CONTEXT = [str(SOURCE_ROOT / f"{PREFIX}{n}.txt") for n in range(666, 680)]
SECTION_KEY = "PL089:section:volume_end:ordo_rerum:002"
CHUNK_ID = "PL089:chunk:002:001"


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.strip())


def blocks(path: Path, *, index_y: int = 0) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    found: list[tuple[int, int, str]] = []
    pattern = re.compile(r'<bloco tipo="texto_principal"[^>]*bbox="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
    for match in pattern.finditer(raw):
        x1, y1, _, _ = (int(value) for value in match.group(1).split(","))
        if y1 < index_y:
            continue
        text = re.sub(r"<[^>]+>", "", match.group(2))
        text = "\n".join(clean(line) for line in text.splitlines() if clean(line))
        if text:
            found.append((x1, y1, text))
    # The scan is a two-column page: left column precedes right column editorially.
    return [text for _, _, text in sorted(found)]


def is_upper_heading(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all(char == char.upper() for char in letters) and not re.search(r"(?:\b\d{1,4}|\bIbid\.)$", text)


def combine(left: str, right: str) -> str:
    if left.endswith("-") and right[:1].islower():
        return left[:-1] + right
    return f"{left} {right}"


def ref_fields(entry: str) -> tuple[str, str | None, int | None, str | None]:
    ibid = re.search(r"\s+(Ibid\.)$", entry)
    if ibid:
        return entry[:ibid.start()].rstrip(), ibid.group(1), None, "col."
    number = re.search(r"\s+(\d{1,4})$", entry)
    if number:
        return entry[:number.start()].rstrip(), number.group(1), int(number.group(1)), "col."
    return entry, None, None, None


def entry_kind(target: str) -> str:
    return "contents_heading" if (
        is_upper_heading(target) or target == "CYPRIANUS Cassinensis monachus."
    ) else "contents_entry"


def source_name(number: int) -> str:
    return str(SOURCE_ROOT / f"{PREFIX}{number}.txt")


def build_logical_items() -> list[tuple[str, list[str], list[str]]]:
    items: list[tuple[str, list[str], list[str]]] = []
    pending = ""
    pending_lines: list[str] = []
    pending_sources: list[str] = []

    def flush() -> None:
        nonlocal pending, pending_lines, pending_sources
        if pending:
            items.append((pending, pending_sources, pending_lines))
        pending, pending_lines, pending_sources = "", [], []

    for number in range(670, 676):
        file_path = SOURCE_ROOT / f"{PREFIX}{number}.txt"
        y_min = 510 if number == 670 else 0
        lines = [line for block in blocks(file_path, index_y=y_min) for line in block.splitlines()]
        if number == 674:
            # OCR column order isolates the locator before the final sentence of Epist. XI.
            joined = "\n".join(lines)
            joined = joined.replace(
                "de sede metropolitana non Co'oniæ, sed Moguntiæ defixa.\n943\nPermittit donique ut sibi deligat successorem.",
                "de sede metropolitana non Co'oniæ, sed Moguntiæ defixa. Permittit donique ut sibi deligat successorem. 943",
            )
            lines = joined.splitlines()
        for line in lines:
            if is_upper_heading(line):
                flush()
                items.append((line, [str(file_path)], [line]))
                continue
            pending = line if not pending else combine(pending, line)
            pending_lines.append(line)
            if str(file_path) not in pending_sources:
                pending_sources.append(str(file_path))
            if re.search(r"(?:\bIbid\.|\b\d{1,4})$", line):
                flush()

    # The owned final item is explicitly continued in the first body line of 676.
    continuation_path = SOURCE_ROOT / f"{PREFIX}676.txt"
    continuation = blocks(continuation_path)[0].splitlines()[0]
    pending = continuation if not pending else combine(pending, continuation)
    pending_lines.append(continuation)
    pending_sources.append(str(continuation_path))
    if re.search(r"(?:\bIbid\.|\b\d{1,4})$", continuation):
        flush()
    if pending:
        raise RuntimeError(f"unclosed item: {pending!r}")
    # The two-line author label is printed before, not as part of, its historical notice.
    for index, (raw, source_files, source_lines) in enumerate(items):
        if raw == "CYPRIANUS":
            following_raw, following_files, following_lines = items[index + 1]
            if following_raw == "Cassinensis monachus. Adnotatiuncula historica. 1049":
                items[index:index + 2] = [
                    ("CYPRIANUS Cassinensis monachus.", source_files, ["CYPRIANUS", "Cassinensis monachus."]),
                    ("Adnotatiuncula historica. 1049", following_files, ["Adnotatiuncula historica. 1049"]),
                ]
            break
    return items


def main() -> None:
    logical_items = build_logical_items()
    entries = []
    for order, (raw, source_files, source_lines) in enumerate(logical_items, 1):
        target, page_raw, page_int, page_col = ref_fields(raw)
        key = f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}"
        entries.append(
            {
                "entry_key": key,
                "entry_order": order,
                "entry_raw": raw,
                "target_raw": target,
                "target_file": None,
                "page_ref_raw": page_raw,
                "page_ref_int": page_int,
                "page_ref_col": page_col,
                "note_raw": "heading" if entry_kind(target) == "contents_heading" else None,
                "normalized_target": target.rstrip(".").replace("Æ", "Ae").replace("æ", "ae").replace("Œ", "Oe").replace("œ", "oe"),
                "confidence": "medium" if "INC. RTUM" in raw or "Co'oniæ" in raw else "high",
                "raw_json": {
                    "source_files": source_files,
                    "source_lines": source_lines,
                    "entry_kind": entry_kind(target),
                },
            }
        )

    payload = {
        "schema_version": 2,
        "volume_id": "PL089",
        "section_id": "PL089:candidate-section:002",
        "chunk_id": CHUNK_ID,
        "input_fingerprint": "22db6d81d248ae2d7ffc95b74f87d4f9d20d9aa85e340c9cd5260d5ab9b43744",
        "status": "complete",
        "physical_files": OWNED,
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [
            {
                "physical_left_file": source_name(673),
                "physical_right_file": source_name(674),
                "decision": "joined_entry",
                "reason": "The left column ends 'sub Carlo-' and physical file 674 begins 'manuo, majore domus regiæ'; together they form the single CONCILIUM LIPTINENSE item, ending at the editorial locator 809.",
            },
            {
                "physical_left_file": source_name(675),
                "physical_right_file": source_name(676),
                "decision": "joined_entry",
                "reason": "The owned final EPIST. VIII item ends 'qualiter' and physical file 676 begins its grammatical continuation 'justitias B. Petri ... receperit. 1151'; it is one entry owned by file 675.",
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
                "heading_norm": "Ordo rerum quae in hoc tomo continentur",
                "page_start": None,
                "page_end": None,
                "file_start": OWNED[0],
                "file_end": OWNED[-1],
                "confidence": "high",
                "raw_json": {
                    "source_files": OWNED,
                    "adjacent_context_files_checked": CONTEXT,
                    "section_kind": "volume_contents_table",
                    "numbering_note": "The headers display inconsistent OCR editorial-number readings across this scan sequence; all terminal table numbers and Ibid. values are retained as editorial column locators, never physical-file identifiers.",
                    "extra_file_evidence": "Files 666-669 were read to confirm prose immediately before the closing ORDO. File 676 was read to prove the continuation of EPIST. VIII and then continues the ORDO through FINIS TOMI OCTOGESIMI NONI; 677-679 are blank/end matter. No target locator was run.",
                    "entries_status": "complete",
                    "entries_status_reason": "Every readable contents-table item in owned files 670-675 is serialized, including author, work, subdivision, chapter, epistle, conciliar, and sermon headings. The 673→674 and 675→676 continuations are joined once under the owning physical files.",
                },
                "entries": entries,
            }
        ],
        "notes": [
            "Bounded fragment only: it does not assemble the remainder of the volume-end ORDO RERUM.",
            "OCR literals, including uncertain strings such as 'CONCILIUM INC. RTUM' and 'Co'oniæ', are preserved.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(entries)} entries")


if __name__ == "__main__":
    main()
