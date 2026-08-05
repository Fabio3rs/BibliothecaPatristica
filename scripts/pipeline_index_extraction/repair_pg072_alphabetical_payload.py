# Usage: python scripts/pipeline_index_extraction/repair_pg072_alphabetical_payload.py
# Repairs PG072 alphabetical payload line-break hyphen artifacts, restores the
# missing supplement continuation page, fixes the supplement/ORDO boundary, and
# writes refreshed intermediate fragments plus the final JSON.
"""Repair PG072 alphabetical payload after validation failure."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG072"
SOURCE_ROOT = ROOT / "teste/PG072/text"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG072_alphabetical_indices.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG072"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PG072_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG072_helper_output.json"
FILE_491 = SOURCE_ROOT / "e3547f24-7b4d-4a12-9b71-e6670436098d-491.txt"
FILE_492 = SOURCE_ROOT / "e3547f24-7b4d-4a12-9b71-e6670436098d-492.txt"

SUPP_SECTION = "PG072:alpha:alphabetical_general:002"
ORDO_SECTION = "PG072:alpha:ordo_rerum:001"
WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
TRAILING_WORD_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s*$")
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}])")
SORT_STRIP_RE = re.compile(r"[^0-9a-zα-ωἀ-῾]+")
PAGE_RE = re.compile(r"\b(\d{1,3})(?:\s*(?:,|;|ad|et|seq\.|seqq\.|cum adn\.|adn\.|fn\.)\s*\d{1,3})*\.?$")
NUMBER_RE = re.compile(r"\b\d{1,3}\b")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm_sort(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value.casefold().replace("\xa0", " "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return SORT_STRIP_RE.sub(" ", text).strip() or None


def dehyphenate_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    previous = None
    current = value
    while previous != current:
        previous = current
        current = LINEBREAK_HYPHEN_RE.sub(r"\1\2", current)
    return current


def merge_entry_raw(left: str, right: str) -> str:
    return re.sub(r"-\s*$", "", left.rstrip()) + right.lstrip()


def parse_tail_page(entry_raw: str) -> int | None:
    match = PAGE_RE.search(entry_raw.strip())
    if not match:
        return None
    return int(match.group(1))


def lemma_from_entry(entry_raw: str) -> str | None:
    text = entry_raw.strip()
    text = re.sub(r"\s*\b\d{1,3}(?:\s*(?:,|;|ad|et|seq\.|seqq\.|cum adn\.|adn\.|fn\.)\s*\d{1,3})*\.?$", "", text).strip(" ,.;")
    return text or None


def prefix_for_key(entry_key: str) -> str:
    parts = entry_key.split(":")
    return parts[-2]


def merge_terminal_hyphen_entries(payload: dict[str, Any]) -> dict[str, str]:
    entries = payload["entries"]
    refs = payload["refs"]
    remap: dict[str, str] = {}
    merged: list[dict[str, Any]] = []
    i = 0
    while i < len(entries):
        current = deepcopy(entries[i])
        raw = current.get("entry_raw")
        if isinstance(raw, str) and TRAILING_WORD_HYPHEN_RE.search(raw) and i + 1 < len(entries):
            right = entries[i + 1]
            old_right_key = right["entry_key"]
            current["entry_raw"] = dehyphenate_text(merge_entry_raw(raw, str(right.get("entry_raw") or "")))
            current["entry_kind"] = right.get("entry_kind") if current.get("entry_kind") == "editorial_note" else current.get("entry_kind")
            current["lemma_raw"] = lemma_from_entry(current["entry_raw"]) if current.get("entry_kind") != "editorial_note" else None
            current["lemma_display"] = current["lemma_raw"]
            current["lemma_norm"] = norm_sort(current["lemma_raw"])
            current["lemma_sort"] = current["lemma_norm"]
            current["inferred_printed_page"] = right.get("inferred_printed_page") or parse_tail_page(current["entry_raw"])
            current["target_file_best"] = current.get("target_file_best") or right.get("target_file_best")
            current["confidence"] = min(float(current.get("confidence") or 0.88), float(right.get("confidence") or 0.88), 0.88)
            current.setdefault("raw_json", {})["pg072_rerun_linebreak_hyphen_merge"] = {
                "merged_from_entry_key": old_right_key,
                "reason": "Merged an OCR line-break hyphenated word with the immediate continuation entry.",
                "evidence_file": current.get("raw_json", {}).get("source_file"),
            }
            remap[old_right_key] = current["entry_key"]
            i += 2
        else:
            for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "context_raw"):
                repaired = dehyphenate_text(current.get(field))
                if repaired != current.get(field):
                    current[field] = repaired
                    current.setdefault("raw_json", {})["pg072_rerun_inline_hyphen_repair"] = {
                        "reason": "Removed validation-blocking letter-hyphen-whitespace-letter artifact.",
                    }
            if current.get("lemma_raw") is not None:
                current["lemma_sort"] = norm_sort(current.get("lemma_raw"))
            merged.append(current)
            i += 1
            continue
        merged.append(current)

    for ref in refs:
        if ref.get("entry_key") in remap:
            ref.setdefault("raw_json", {})["pg072_rerun_entry_merge"] = {
                "moved_from_entry_key": ref["entry_key"],
                "reason": "Reference belongs to the merged logical entry after OCR line-break hyphen repair.",
            }
            ref["entry_key"] = remap[ref["entry_key"]]
    payload["entries"] = merged
    return remap


def read_xml_text_blocks(path: Path) -> list[str]:
    result = subprocess.run(
        [
            "python",
            "scripts/read_ocr_page_text.py",
            "--view",
            "xml",
            "--show-source",
            str(path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    blocks: list[str] = []
    collecting = False
    current: list[str] = []
    for line in result.stdout.splitlines():
        if '<bloco tipo="texto_principal"' in line:
            collecting = True
            current = []
            after = line.split(">", 1)[1] if ">" in line else ""
            if "</bloco>" in after:
                blocks.append(after.split("</bloco>", 1)[0].strip())
                collecting = False
            elif after.strip():
                current.append(after.strip())
            continue
        if collecting:
            if "</bloco>" in line:
                before = line.split("</bloco>", 1)[0].strip()
                if before:
                    current.append(before)
                blocks.append("\n".join(current).strip())
                collecting = False
            else:
                text = line.strip()
                if text:
                    current.append(text)
    return [block for block in blocks if block]


def split_entries_from_block(block: str) -> list[str]:
    text = re.sub(r"\s+", " ", block.replace("\xa0", " ")).strip()
    text = dehyphenate_text(text)
    pieces = re.split(r"(?<=\.)\s+(?=[A-ZÆŒÀ-ÖØ-ÞĀ-ſΑ-ΩἈ-῾])", text)
    return [piece.strip() for piece in pieces if piece.strip()]


def build_supplement_entry(raw: str, source_file: Path, order_hint: int) -> dict[str, Any]:
    page = parse_tail_page(raw)
    lemma = lemma_from_entry(raw) if page is not None else None
    return {
        "entry_key": f"PG072:entry:supplement:new{order_hint:04d}",
        "section_key": SUPP_SECTION,
        "parent_node_key": None,
        "entry_order": 0,
        "entry_kind": "lemma" if page is not None else "editorial_note",
        "lemma_raw": lemma,
        "lemma_display": lemma,
        "lemma_norm": norm_sort(lemma),
        "lemma_sort": norm_sort(lemma),
        "entry_raw": raw,
        "context_raw": None,
        "heading_letter": infer_letter(raw),
        "inferred_printed_page": page,
        "section_start_file": str(SOURCE_ROOT / "e3547f24-7b4d-4a12-9b71-e6670436098d-490.txt"),
        "editorial_anchor_file": str(source_file),
        "target_file_best": None,
        "confidence": 0.82 if page is not None else 0.68,
        "raw_json": {
            "source_file": str(source_file),
            "section_kind": "alphabetical_general",
            "section_kind_reason": "Supplementary alphabetical index continuation recovered during PG072 rerun.",
            "pg072_rerun_added_missing_file": True,
        },
    }


def infer_letter(raw: str) -> str | None:
    stripped = raw.strip()
    if not stripped:
        return None
    first = stripped[0].upper()
    if "A" <= first <= "Z":
        return first
    return None


def build_refs_for_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    raw = entry.get("entry_raw") or ""
    tail_numbers = NUMBER_RE.findall(raw[-120:])
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in tail_numbers:
        if value in seen:
            continue
        seen.add(value)
        page = int(value)
        if page > 599:
            continue
        refs.append(
            {
                "entry_key": entry["entry_key"],
                "ref_order": len(refs) + 1,
                "ref_kind": "editorial_page",
                "ref_raw": value,
                "page_ref_raw": value,
                "page_ref_int": page,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": None,
                "target_file_probability": None,
                "section_start_file": entry["section_start_file"],
                "editorial_anchor_file": entry["editorial_anchor_file"],
                "confidence": 0.62,
                "raw_json": {
                    "pg072_rerun_added_missing_file": True,
                    "reason": "Parsed conservative material page locator from restored supplement continuation.",
                },
            }
        )
    return refs


def restore_missing_supplement_page(payload: dict[str, Any]) -> None:
    existing_sources = {entry.get("raw_json", {}).get("source_file") for entry in payload["entries"]}
    if str(FILE_491) in existing_sources:
        return
    generated: list[dict[str, Any]] = []
    for block in read_xml_text_blocks(FILE_491):
        generated.extend(build_supplement_entry(raw, FILE_491, len(generated) + 1) for raw in split_entries_from_block(block))
    insert_at = max(
        idx
        for idx, entry in enumerate(payload["entries"])
        if entry.get("section_key") == SUPP_SECTION and entry.get("raw_json", {}).get("source_file", "").endswith("-490.txt")
    ) + 1
    payload["entries"][insert_at:insert_at] = generated
    for entry in generated:
        payload["refs"].extend(build_refs_for_entry(entry))


def fix_supplement_ordo_boundary(payload: dict[str, Any]) -> None:
    for section in payload["sections"]:
        if section["section_key"] == SUPP_SECTION:
            section["page_end"] = 976
            section["file_end"] = str(FILE_492)
            section.setdefault("raw_json", {})["pg072_rerun_boundary_repair"] = (
                "Supplement alphabetical entries continue through the top two text blocks of file 492 before the real ORDO RERUM heading."
            )
            sources = section.setdefault("raw_json", {}).setdefault("source_files", [])
            for file_path in [str(FILE_491), str(FILE_492)]:
                if file_path not in sources:
                    sources.append(file_path)
        elif section["section_key"] == ORDO_SECTION:
            section["page_start"] = 975
            section["file_start"] = str(FILE_492)
            section["file_end"] = str(FILE_492)
            section.setdefault("raw_json", {})["pg072_rerun_boundary_repair"] = (
                "ORDO RERUM starts at the explicit mid-page heading in file 492, not at the page header."
            )

    for entry in payload["entries"]:
        key = entry["entry_key"]
        if key.startswith("PG072:entry:ordo:") and int(key.rsplit(":", 1)[1]) <= 118:
            entry["section_key"] = SUPP_SECTION
            entry["parent_node_key"] = None
            entry["heading_letter"] = infer_letter(entry.get("entry_raw") or "")
            entry["section_start_file"] = str(SOURCE_ROOT / "e3547f24-7b4d-4a12-9b71-e6670436098d-490.txt")
            entry.setdefault("raw_json", {})["pg072_rerun_boundary_repair"] = (
                "This row belongs to the supplement alphabetical continuation above the ORDO RERUM heading."
            )
        elif key in {"PG072:entry:ordo:0119", "PG072:entry:ordo:0120", "PG072:entry:ordo:0121"}:
            entry["section_key"] = ORDO_SECTION
            entry["parent_node_key"] = None
            entry["heading_letter"] = None

    for ref in payload["refs"]:
        entry = next((e for e in payload["entries"] if e["entry_key"] == ref["entry_key"]), None)
        if entry:
            ref["section_start_file"] = entry.get("section_start_file")
            ref["editorial_anchor_file"] = entry.get("editorial_anchor_file")


def rebuild_supplement_nodes(payload: dict[str, Any]) -> dict[str, str]:
    old_to_new: dict[str, str] = {}
    payload["nodes"] = [node for node in payload["nodes"] if node.get("section_key") != SUPP_SECTION]
    letters = []
    for entry in payload["entries"]:
        if entry.get("section_key") == SUPP_SECTION:
            letter = entry.get("heading_letter") or infer_letter(entry.get("entry_raw") or "")
            if letter and letter not in letters:
                letters.append(letter)
    for order, letter in enumerate(letters, start=1):
        node_key = f"PG072:node:supplement:{order:03d}"
        payload["nodes"].append(
            {
                "node_key": node_key,
                "section_key": SUPP_SECTION,
                "parent_node_key": None,
                "node_order": order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.92,
                "raw_json": {"pg072_rerun_rebuilt_supplement_node": True},
            }
        )
    letter_to_node = {node["label_raw"]: node["node_key"] for node in payload["nodes"] if node.get("section_key") == SUPP_SECTION}
    for entry in payload["entries"]:
        if entry.get("section_key") == SUPP_SECTION:
            old = entry.get("parent_node_key")
            letter = entry.get("heading_letter") or infer_letter(entry.get("entry_raw") or "")
            entry["heading_letter"] = letter
            entry["parent_node_key"] = letter_to_node.get(letter)
            if old and entry["parent_node_key"]:
                old_to_new[old] = entry["parent_node_key"]
    return old_to_new


def ensure_ordo_right_column(payload: dict[str, Any]) -> None:
    wanted = [
        "SUPPLEMENTUM AD EDITIONEM JOANNIS AUBERTI.",
        "Fragmenta in Matthæum.",
        "Commentarius in Lucam.",
    ]
    existing = {entry.get("entry_raw") for entry in payload["entries"] if entry.get("section_key") == ORDO_SECTION}
    additions = []
    for raw in wanted:
        if raw in existing:
            continue
        additions.append(
            {
                "entry_key": f"PG072:entry:ordo:new{len(additions) + 1:04d}",
                "section_key": ORDO_SECTION,
                "parent_node_key": None,
                "entry_order": 0,
                "entry_kind": "heading_group",
                "lemma_raw": raw,
                "lemma_display": raw,
                "lemma_norm": norm_sort(raw),
                "lemma_sort": norm_sort(raw),
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": None,
                "section_start_file": str(FILE_492),
                "editorial_anchor_file": str(FILE_492),
                "target_file_best": None,
                "confidence": 0.93,
                "raw_json": {
                    "source_file": str(FILE_492),
                    "section_kind": "ordo_rerum",
                    "section_kind_reason": "Right column of the closing ORDO RERUM block.",
                    "pg072_rerun_added_ordo_right_column": True,
                },
            }
        )
    if additions:
        last_ordo_idx = max(
            idx for idx, entry in enumerate(payload["entries"]) if entry.get("section_key") == ORDO_SECTION
        )
        payload["entries"][last_ordo_idx + 1 : last_ordo_idx + 1] = additions


def renumber_entries_and_refs(payload: dict[str, Any]) -> None:
    old_to_new: dict[str, str] = {}
    counters: dict[str, int] = {"main": 0, "supplement": 0, "ordo": 0}
    for order, entry in enumerate(payload["entries"], start=1):
        prefix = prefix_for_key(entry["entry_key"])
        if entry.get("section_key") == SUPP_SECTION:
            prefix = "supplement"
        elif entry.get("section_key") == ORDO_SECTION:
            prefix = "ordo"
        counters[prefix] = counters.get(prefix, 0) + 1
        new_key = f"PG072:entry:{prefix}:{counters[prefix]:04d}"
        old_to_new[entry["entry_key"]] = new_key
        entry["entry_key"] = new_key
        entry["entry_order"] = order
    for ref in payload["refs"]:
        if ref["entry_key"] in old_to_new:
            ref["entry_key"] = old_to_new[ref["entry_key"]]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ref in payload["refs"]:
        grouped.setdefault(ref["entry_key"], []).append(ref)
    for refs in grouped.values():
        seen: set[tuple[Any, ...]] = set()
        refs.sort(key=lambda item: (item.get("ref_order") or 9999, item.get("ref_raw") or ""))
        order = 1
        for ref in refs:
            identity = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("line_ref_raw"),
                ref.get("range_start_raw"),
                ref.get("range_end_raw"),
            )
            if identity in seen:
                ref["_drop_duplicate"] = True
                continue
            seen.add(identity)
            ref["ref_order"] = order
            order += 1
    payload["refs"] = [ref for ref in payload["refs"] if not ref.pop("_drop_duplicate", False)]


def remove_residual_text_hyphens(payload: dict[str, Any]) -> None:
    for entry in payload["entries"]:
        for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "context_raw"):
            old = entry.get(field)
            new = dehyphenate_text(old)
            if new != old:
                entry[field] = new
        if isinstance(entry.get("lemma_raw"), str) and entry["lemma_raw"].endswith("-"):
            entry["lemma_raw"] = entry["lemma_raw"].rstrip("-").strip() or None
            entry["lemma_display"] = entry["lemma_raw"]
            entry["lemma_norm"] = norm_sort(entry["lemma_raw"])
            entry["lemma_sort"] = entry["lemma_norm"]
            entry.setdefault("raw_json", {})["pg072_rerun_lemma_terminal_hyphen_repair"] = {
                "reason": "The full entry text is not a line-break artifact, but the parsed lemma carried a terminal hyphen."
            }
    for ref in payload["refs"]:
        for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
            ref[field] = dehyphenate_text(ref.get(field))


def assert_valid_relationships(payload: dict[str, Any]) -> None:
    sections = {section["section_key"] for section in payload["sections"]}
    nodes = {node["node_key"] for node in payload["nodes"]}
    entries = {entry["entry_key"] for entry in payload["entries"]}
    bad = [
        entry["entry_key"]
        for entry in payload["entries"]
        if entry.get("section_key") not in sections or (entry.get("parent_node_key") and entry["parent_node_key"] not in nodes)
    ]
    bad_refs = [ref["entry_key"] for ref in payload["refs"] if ref.get("entry_key") not in entries]
    residual = []
    for idx, entry in enumerate(payload["entries"], start=1):
        for field in ("entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "context_raw"):
            value = entry.get(field)
            if isinstance(value, str) and (TRAILING_WORD_HYPHEN_RE.search(value) or LINEBREAK_HYPHEN_RE.search(value)):
                residual.append(f"entries[{idx}].{field}: {value[:80]}")
    if bad or bad_refs or residual:
        raise SystemExit(
            f"relationship/residual check failed bad_entries={bad[:5]} bad_refs={bad_refs[:5]} residual={residual[:10]}"
        )


def refresh_metadata(payload: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload["generated_at"] = now
    payload["coverage"] = {
        "entries_status": "partial_recovery",
        "entries_status_reason": (
            "Recovered PG072 main alphabetical index, supplement continuation through files 490-492, "
            "and the closing ORDO RERUM block. This rerun repaired validation-blocking OCR line-break "
            "hyphen artifacts and restored the previously omitted file 491 supplement page."
        ),
        "evidence_files": [
            str(SOURCE_ROOT / f"e3547f24-7b4d-4a12-9b71-e6670436098d-{seq:03d}.txt")
            for seq in range(480, 493)
            if seq != 491 or FILE_491.exists()
        ],
    }
    notes = payload.setdefault("notes", [])
    notes.append(
        {
            "note_type": "rerun_repair",
            "message": (
                "PG072 rerun merged OCR line-break hyphen continuations, restored missing supplement file 491, "
                "and moved the pre-ORDO file 492 alphabetical continuation into the supplement section."
            ),
            "created_at": now,
        }
    )
    manifest = {
        "volume_id": VOLUME_ID,
        "generated_at": now,
        "updated_at": now,
        "source_root": str(SOURCE_ROOT),
        "helper_request_json": str(HELPER_REQUEST),
        "helper_output_json": str(HELPER_OUTPUT),
        "output_file": str(PAYLOAD_PATH),
    }
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now,
        "current_focus": "Payload repaired and validated after PG072 line-break hyphen failure.",
        "completed": [
            "Read current import validation failure",
            "Verified file 480, 489, 490, 491, and 492 OCR structure through read_ocr_page_text.py",
            "Merged OCR line-break hyphen continuations and remapped refs",
            "Restored missing supplement continuation file 491",
            "Corrected supplement/ORDO boundary in file 492",
        ],
        "pending": ["Run import_alphabetical_index_json.py --validate-only"],
        "blocked": [],
        "notes": [
            "The helper output was preserved where already present; newly restored file 491 refs remain target_file null pending material locator rerun.",
            "The true ORDO RERUM starts at the explicit mid-page heading in file 492.",
        ],
    }
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(INTERMEDIATE_DIR / "manifest.json", manifest)
    write_json(INTERMEDIATE_DIR / "todo.json", todo)


def write_intermediates(payload: dict[str, Any]) -> None:
    for key in ("volume", "sections", "nodes", "entries", "refs", "scripture_refs", "coverage", "notes"):
        write_json(INTERMEDIATE_DIR / f"{key}.json", payload[key])


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    restore_missing_supplement_page(payload)
    fix_supplement_ordo_boundary(payload)
    while True:
        remap = merge_terminal_hyphen_entries(payload)
        if not remap:
            break
    ensure_ordo_right_column(payload)
    rebuild_supplement_nodes(payload)
    remove_residual_text_hyphens(payload)
    renumber_entries_and_refs(payload)
    assert_valid_relationships(payload)
    refresh_metadata(payload)
    write_intermediates(payload)
    write_json(PAYLOAD_PATH, payload)
    print(
        json.dumps(
            {
                "entries": len(payload["entries"]),
                "refs": len(payload["refs"]),
                "sections": len(payload["sections"]),
                "output": str(PAYLOAD_PATH),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
