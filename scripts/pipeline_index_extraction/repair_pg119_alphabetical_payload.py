#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg119_alphabetical_payload.py
# Repairs PG119 alphabetical payload OCR line-break hyphen artifacts, splits three merged entries,
# rewrites sequential entry keys/orders, updates refs, and validates the final JSON.

from __future__ import annotations

import copy
import json
import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG119"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG119_alphabetical_indices.json"
TODO_PATH = PROJECT_ROOT / "data/intermediate_payloads/PG119/todo.json"
OCR_EVIDENCE_FILES = [
    PROJECT_ROOT / "teste/PG119/text/e4823574-8cfd-4f45-beac-849bb3010651-658.txt",
    PROJECT_ROOT / "teste/PG119/text/e4823574-8cfd-4f45-beac-849bb3010651-659.txt",
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"([{WORD_CHARS}])-\s+([{WORD_CHARS}0-9])")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def collapse_ws(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def merge_linebreak_hyphens(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    while True:
        merged = LINEBREAK_HYPHEN_RE.sub(r"\1\2", updated)
        if merged == updated:
            return collapse_ws(merged)
        updated = merged


def find_entry(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for entry in entries:
        if entry["entry_key"] == key:
            return entry
    raise KeyError(key)


def make_identity(entry: dict[str, Any]) -> str:
    return entry.get("_identity") or entry["entry_key"]


def update_entry_text_fields(entry: dict[str, Any]) -> None:
    for field in ("lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "entry_raw", "context_raw"):
        entry[field] = merge_linebreak_hyphens(entry.get(field))


def prepare_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(entries)
    for entry in prepared:
        entry["_identity"] = entry["entry_key"]
    return prepared


def clone_split_entry(base: dict[str, Any], *, identity: str, lemma: str, entry_raw: str, parent_node_key: str, heading_letter: str) -> dict[str, Any]:
    new_entry = copy.deepcopy(base)
    new_entry["_identity"] = identity
    new_entry["parent_node_key"] = parent_node_key
    new_entry["heading_letter"] = heading_letter
    new_entry["lemma_raw"] = lemma
    new_entry["lemma_display"] = lemma
    new_entry["lemma_norm"] = lemma
    new_entry["lemma_sort"] = lemma.casefold()
    new_entry["entry_raw"] = entry_raw
    raw_json = copy.deepcopy(base.get("raw_json") or {})
    raw_json["pg119_split_repair"] = {
        "source_entry_key": base["entry_key"],
        "reason": "Split a merged logical entry after checking cleaned OCR reader output for file 659.",
        "ocr_spot_checks": [str(path) for path in OCR_EVIDENCE_FILES],
    }
    new_entry["raw_json"] = raw_json
    return new_entry


def apply_entry_repairs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = prepare_entries(entries)

    entry_0447 = find_entry(entries, "PG119:entry:0447")
    entry_0447["lemma_raw"] = "Resurrectionis modus in eodem corpore ποιητικόν"
    entry_0447["lemma_display"] = entry_0447["lemma_raw"]
    entry_0447["lemma_norm"] = entry_0447["lemma_raw"]
    entry_0447["lemma_sort"] = entry_0447["lemma_raw"].casefold()
    entry_0447["entry_raw"] = "Resurrectionis modus in eodem corpore ποιητικόν, II, 554."

    entry_0465 = find_entry(entries, "PG119:entry:0465")
    entry_0465["lemma_raw"] = "Sermo et scientia capiuntur multipliciter"
    entry_0465["lemma_display"] = entry_0465["lemma_raw"]
    entry_0465["lemma_norm"] = entry_0465["lemma_raw"]
    entry_0465["lemma_sort"] = entry_0465["lemma_raw"].casefold()
    entry_0465["entry_raw"] = "Sermo et scientia capiuntur multipliciter, I, 685."

    entry_0466 = find_entry(entries, "PG119:entry:0466")
    entry_0466["lemma_raw"] = "Sermones obsceni sunt fomes ad opera"
    entry_0466["lemma_display"] = entry_0466["lemma_raw"]
    entry_0466["lemma_norm"] = entry_0466["lemma_raw"]
    entry_0466["lemma_sort"] = entry_0466["lemma_raw"].casefold()
    entry_0466["entry_raw"] = "Sermones obsceni sunt fomes ad opera, II, 47."

    find_entry(entries, "PG119:entry:0470")["entry_raw"] = "Servi omni honore dominis subdantur, II, 150, etc."
    entry_0470 = find_entry(entries, "PG119:entry:0470")
    entry_0470["lemma_raw"] = "Servi omni honore dominis subdantur"
    entry_0470["lemma_display"] = entry_0470["lemma_raw"]
    entry_0470["lemma_norm"] = entry_0470["lemma_raw"]
    entry_0470["lemma_sort"] = entry_0470["lemma_raw"].casefold()

    entry_0474 = find_entry(entries, "PG119:entry:0474")
    entry_0474["lemma_raw"] = "Simon cur ambiverit potestatem dandi Spiritum sanctum"
    entry_0474["lemma_display"] = entry_0474["lemma_raw"]
    entry_0474["lemma_norm"] = entry_0474["lemma_raw"]
    entry_0474["lemma_sort"] = entry_0474["lemma_raw"].casefold()
    entry_0474["entry_raw"] = "Simon cur ambiverit potestatem dandi Spiritum sanctum, I, 79."

    entry_0484 = find_entry(entries, "PG119:entry:0484")
    entry_0484["lemma_raw"] = "Spiritus sancti sessio super aliquos significat permanentiam"
    entry_0484["lemma_display"] = entry_0484["lemma_raw"]
    entry_0484["lemma_norm"] = entry_0484["lemma_raw"]
    entry_0484["lemma_sort"] = entry_0484["lemma_raw"].casefold()
    entry_0484["entry_raw"] = "Spiritus sancti sessio super aliquos significat permanentiam, ibid."

    entry_0487 = find_entry(entries, "PG119:entry:0487")
    entry_0487["entry_raw"] = "Spiritus, id est, donum precandi, docet quomodo precari oporteat, I, 506."

    entry_0490 = find_entry(entries, "PG119:entry:0490")
    entry_0490["lemma_raw"] = "Spiritus dona dabantur juxta fidei ac purificationis proportionem"
    entry_0490["lemma_display"] = entry_0490["lemma_raw"]
    entry_0490["lemma_norm"] = entry_0490["lemma_raw"]
    entry_0490["lemma_sort"] = entry_0490["lemma_raw"].casefold()
    entry_0490["entry_raw"] = "Spiritus dona dabantur juxta fidei ac purificationis proportionem, I, 556."

    entry_0492 = find_entry(entries, "PG119:entry:0492")
    entry_0492["entry_raw"] = "Stephanus unius multitudinis ferebat accusationem, alterius vero technas ac falsa testimonia, I, 576."

    entry_0497 = find_entry(entries, "PG119:entry:0497")
    entry_0497["entry_raw"] = "Suæ quisque menti satisfaciat, de quibus sit accipiendum, I, 379. 380."

    entry_0524 = find_entry(entries, "PG119:entry:0524")
    entry_0524["lemma_raw"] = "Virginitas in primitiva Ecclesia magno studio servabatur"
    entry_0524["lemma_display"] = entry_0524["lemma_raw"]
    entry_0524["lemma_norm"] = entry_0524["lemma_raw"]
    entry_0524["lemma_sort"] = entry_0524["lemma_raw"].casefold()
    entry_0524["entry_raw"] = "Virginitas in primitiva Ecclesia magno studio servabatur, I, 151."

    entry_0534 = find_entry(entries, "PG119:entry:0534")
    entry_0534["lemma_raw"] = "Zelus Dei incendit interdum et mansuetissimos ad puniendum"
    entry_0534["lemma_display"] = entry_0534["lemma_raw"]
    entry_0534["lemma_norm"] = entry_0534["lemma_raw"]
    entry_0534["lemma_sort"] = entry_0534["lemma_raw"].casefold()
    entry_0534["entry_raw"] = "Zelus Dei incendit interdum et mansuetissimos ad puniendum, I, 45."

    entry_0514 = find_entry(entries, "PG119:entry:0514")
    entry_0514["entry_raw"] = "Tubæ Dei dicuntur angeli, II, 174."

    entry_0515 = find_entry(entries, "PG119:entry:0515")
    entry_0515["entry_raw"] = "Urbanitatis non est nunc nobis tempus, II, 46."

    for entry in entries:
        update_entry_text_fields(entry)

    split_after: dict[str, list[dict[str, Any]]] = defaultdict(list)
    split_after["PG119:entry:0447"].append(
        clone_split_entry(
            entry_0447,
            identity="split:0447:sacerdotium-tempore",
            lemma="Sacerdotium tempore divi Hieronymi assumentes etiam in Orientali Ecclesia, si conjugati erant desinebant esse mariti",
            entry_raw="Sacerdotium tempore divi Hieronymi assumentes etiam in Orientali Ecclesia, si conjugati erant desinebant esse mariti, II, 170.",
            parent_node_key="PG119:node:046",
            heading_letter="S",
        )
    )
    split_after["PG119:entry:0514"].append(
        clone_split_entry(
            entry_0514,
            identity="split:0514:unctio",
            lemma="Unctio quæ nunc fit respondet unctioni Veteris Testamenti",
            entry_raw="Unctio quæ nunc fit respondet unctioni Veteris Testamenti, I, 606.",
            parent_node_key="PG119:node:054",
            heading_letter="U",
        )
    )
    split_after["PG119:entry:0515"].append(
        clone_split_entry(
            entry_0515,
            identity="split:0515:vapor",
            lemma="Vapor quid sit, et quod huic assimiletur vita nostra",
            entry_raw="Vapor quid sit, et quod huic assimiletur vita nostra, II, 472.",
            parent_node_key="PG119:node:055",
            heading_letter="V",
        )
    )

    repaired: list[dict[str, Any]] = []
    for entry in entries:
        repaired.append(entry)
        repaired.extend(split_after.get(entry["entry_key"], []))

    return repaired


def build_key_mapping(entries: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for idx, entry in enumerate(entries, start=1):
        new_key = f"PG119:entry:{idx:04d}"
        entry["entry_order"] = idx
        entry["entry_key"] = new_key
        mapping[make_identity(entry)] = new_key
    return mapping


def repair_refs(refs: list[dict[str, Any]], key_map: dict[str, str]) -> list[dict[str, Any]]:
    refs = copy.deepcopy(refs)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        identity = ref["entry_key"]
        if ref["entry_key"] == "PG119:entry:0447" and ref["ref_order"] == 2:
            identity = "split:0447:sacerdotium-tempore"
        elif ref["entry_key"] == "PG119:entry:0514" and ref["ref_order"] == 2:
            identity = "split:0514:unctio"
        elif ref["entry_key"] == "PG119:entry:0515" and ref["ref_order"] == 2:
            identity = "split:0515:vapor"
        grouped[identity].append(ref)

    repaired: list[dict[str, Any]] = []
    for identity, items in grouped.items():
        new_entry_key = key_map[identity]
        for order, ref in enumerate(items, start=1):
            ref["entry_key"] = new_entry_key
            ref["ref_order"] = order
            for field in ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"):
                ref[field] = merge_linebreak_hyphens(ref.get(field))
            repaired.append(ref)
    repaired.sort(key=lambda item: (item["entry_key"], item["ref_order"]))
    return repaired


def assert_no_linebreak_hyphens(payload: dict[str, Any]) -> None:
    residual: list[str] = []

    def scan(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "_identity":
                    continue
                scan(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                scan(child, f"{path}[{idx}]")
        elif isinstance(value, str) and LINEBREAK_HYPHEN_RE.search(value):
            residual.append(path)

    scan(payload, "")
    if residual:
        raise SystemExit(f"Residual line-break hyphen artifacts remain: {residual[:40]}")


def update_notes(payload: dict[str, Any]) -> None:
    payload["generated_at"] = now_iso()
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": (
                "PG119 rerun repaired validation-blocking OCR line-break hyphen artifacts and split "
                "three merged logical entries confirmed against the cleaned OCR reader output for file 659."
            ),
            "ocr_spot_checks": [str(path) for path in OCR_EVIDENCE_FILES],
        }
    )


def update_todo() -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "PG119 payload repaired and validated after line-break hyphen import failure.",
            "completed": [
                "Reread the exact validation failure objects in the PG119 checkpoint payload",
                "Checked OCR reader output for index files 658-659 before changing flagged entries",
                "Merged the 13 validator-style OCR line-break hyphen artifacts confirmed by OCR",
                "Split the merged Resurrectionis/Sacerdotium, Tubæ/Unctio, and Urbanitatis/Vapor entries",
                "Renumbered entry keys and ref orders consistently after the repaired splits",
                "Validated the final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "The repair stayed inside the existing analytical index section and preserved the prior locator payload.",
                "OCR evidence came from cleaned reader output for files 658-659, not blind global normalization.",
            ],
        },
    )


def strip_internal_fields(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        entry.pop("_identity", None)


def validate() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
            "--print-summary",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    repaired_entries = apply_entry_repairs(payload["entries"])
    key_map = build_key_mapping(repaired_entries)
    repaired_refs = repair_refs(payload["refs"], key_map)
    strip_internal_fields(repaired_entries)

    payload["entries"] = repaired_entries
    payload["refs"] = repaired_refs
    update_notes(payload)
    assert_no_linebreak_hyphens(payload)
    dump_json(PAYLOAD_PATH, payload)
    validate()
    update_todo()


if __name__ == "__main__":
    main()
