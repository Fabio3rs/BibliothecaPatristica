#!/usr/bin/env python3
# Usage: python scripts/pipeline_index_extraction/repair_pg118_hyphen_artifacts.py
# Repairs PG118 ORDO RERUM OCR line-break hyphen artifacts confirmed against OCR files 702-704.

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG118"
PAYLOAD_PATH = PROJECT_ROOT / "data/alphabetical_index_payloads/PG118_alphabetical_indices.json"
INTERMEDIATE_DIR = PROJECT_ROOT / "data/intermediate_payloads/PG118"
PARSED_ENTRIES_PATH = INTERMEDIATE_DIR / "parsed_entries.json"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"
EVIDENCE_FILES = [
    PROJECT_ROOT / "teste/PG118/text/e1ec9100-8bbb-4725-afbc-c2f4a449fa45-702.txt",
    PROJECT_ROOT / "teste/PG118/text/e1ec9100-8bbb-4725-afbc-c2f4a449fa45-703.txt",
    PROJECT_ROOT / "teste/PG118/text/e1ec9100-8bbb-4725-afbc-c2f4a449fa45-704.txt",
]

ENTRY_KEYS = {
    "PG118:entry:0010",
    "PG118:entry:0012",
    "PG118:entry:0014",
    "PG118:entry:0015",
    "PG118:entry:0017",
    "PG118:entry:0018",
    "PG118:entry:0019",
    "PG118:entry:0020",
    "PG118:entry:0021",
    "PG118:entry:0022",
    "PG118:entry:0024",
    "PG118:entry:0025",
    "PG118:entry:0026",
    "PG118:entry:0027",
    "PG118:entry:0029",
    "PG118:entry:0030",
    "PG118:entry:0031",
    "PG118:entry:0033",
    "PG118:entry:0034",
    "PG118:entry:0035",
    "PG118:entry:0036",
    "PG118:entry:0037",
    "PG118:entry:0123",
    "PG118:entry:0125",
    "PG118:entry:0126",
    "PG118:entry:0127",
    "PG118:entry:0137",
}

REPLACEMENTS = {
    "re- surrectione": "resurrectione",
    "ap- positione": "appositione",
    "apo- stolorum": "apostolorum",
    "com- munione": "communione",
    "pro- hibitionem": "prohibitionem",
    "compre- hendentes": "comprehendentes",
    "per- suasibilis": "persuasibilis",
    "pro- bationibus": "probationibus",
    "adver- sus": "adversus",
    "Ja- cob": "Jacob",
    "Stepha- nus": "Stephanus",
    "Ste- phani": "Stephani",
    "mul- tos": "multos",
    "credi- dit": "credidit",
    "Joan- nis": "Joannis",
    "objurga- tione": "objurgatione",
    "argu- mento": "argumento",
    "vo- catione": "vocatione",
    "ba- ptismate": "baptismate",
    "Corne- lio": "Cornelio",
    "instru- ctio": "instructio",
    "do- num": "donum",
    "credi- derant": "crediderant",
    "eo- rum": "eorum",
    "disce- ptantibus": "disceptantibus",
    "fa- me": "fame",
    "Antio- chiæ": "Antiochiæ",
    "narra- tur": "narratur",
    "an- gelus": "angelus",
    "fra- tribus": "fratribus",
    "evan- gelica": "evangelica",
    "transla- tione": "translatione",
    "persecu- tione": "persecutione",
    "apo- stoli": "apostoli",
    "apo- stolos": "apostolos",
    "Jo- dais": "Jodais",
    "Ma- cedoniam": "Macedoniam",
    "in- cluserunt": "incluserunt",
    "credi- disset": "credidisset",
    "bapti- zatus": "baptizatus",
    "prædi- cationem": "prædicationem",
    "Atbe- nis": "Atbenis",
    "incre- dulitate": "incredulitate",
    "reve- latum": "revelatum",
    "qui- busdam": "quibusdam",
    "Corin- thi": "Corinthi",
    "ad- versus": "adversus",
    "chari- tatem": "charitatem",
    "con- formes": "conformes",
    "sermoni- bus": "sermonibus",
    "Chri- stum": "Christum",
    "com- munes": "communes",
}

LINEBREAK_HYPHEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ſͰ-Ͽἀ-῿]-\s+[A-Za-zÀ-ÖØ-öø-ÿĀ-ſͰ-Ͽἀ-῿]")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repair_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    for old, new in REPLACEMENTS.items():
        updated = updated.replace(old, new)
    return updated


def repair_nested(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changes = 0
        for key, child in list(value.items()):
            repaired, child_changes = repair_nested(child)
            value[key] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, list):
        changes = 0
        for idx, child in enumerate(value):
            repaired, child_changes = repair_nested(child)
            value[idx] = repaired
            changes += child_changes
        return value, changes
    if isinstance(value, str):
        repaired = repair_text(value)
        return repaired, int(repaired != value)
    return value, 0


def assert_clean(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert_clean(child, f"{path}.{key}" if path else key)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            assert_clean(child, f"{path}[{idx}]")
        return
    if isinstance(value, str) and LINEBREAK_HYPHEN_RE.search(value):
        raise SystemExit(f"Residual line-break hyphen artifact at {path}: {value}")


def repair_payload(payload: dict[str, Any]) -> tuple[int, int]:
    entry_changes = 0
    nested_changes = 0
    for entry in payload.get("entries", []):
        if entry.get("entry_key") not in ENTRY_KEYS:
            continue
        before = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        for field in (
            "lemma_raw",
            "lemma_display",
            "lemma_norm",
            "lemma_sort",
            "entry_raw",
            "context_raw",
        ):
            entry[field] = repair_text(entry.get(field))
        raw_json = entry.get("raw_json")
        if isinstance(raw_json, dict):
            _, nested = repair_nested(raw_json)
            nested_changes += nested
        if json.dumps(entry, ensure_ascii=False, sort_keys=True) != before:
            entry_changes += 1
            entry.setdefault("raw_json", {})["pg118_rerun_linebreak_hyphen_repaired"] = {
                "reason": "Merged verified OCR line-break hyphen artifacts after checking cleaned OCR reader output for files 702-704.",
                "evidence_files": [str(path) for path in EVIDENCE_FILES],
            }
    return entry_changes, nested_changes


def repair_parsed_entries(parsed_entries: list[dict[str, Any]]) -> int:
    changes = 0
    for row in parsed_entries:
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        row["lemma_raw"] = repair_text(row.get("lemma_raw"))
        row["entry_raw"] = repair_text(row.get("entry_raw"))
        if json.dumps(row, ensure_ascii=False, sort_keys=True) != before:
            changes += 1
    return changes


def update_notes(payload: dict[str, Any], entry_changes: int, nested_changes: int) -> None:
    payload["generated_at"] = now_iso()
    volume = payload.setdefault("volume", {})
    volume_notes = volume.setdefault("notes", [])
    if isinstance(volume_notes, list):
        note = (
            "PG118 rerun repaired validation-blocking OCR line-break hyphen artifacts in the closing ORDO RERUM entries after OCR checks on files 702-704."
        )
        if note not in volume_notes:
            volume_notes.append(note)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation_repair",
            "created_at": now_iso(),
            "message": "Fixed PG118 import failure caused by OCR line-break hyphen artifacts in 27 ORDO RERUM entries; refs and locator structure were preserved.",
            "entry_changes": entry_changes,
            "nested_raw_json_string_changes": nested_changes,
            "evidence_files": [str(path) for path in EVIDENCE_FILES],
        }
    )


def update_todo(entry_changes: int, parsed_changes: int) -> None:
    dump_json(
        TODO_PATH,
        {
            "volume_id": VOLUME_ID,
            "updated_at": now_iso(),
            "current_focus": "Payload repaired and validated after PG118 line-break hyphen import failure.",
            "completed": [
                "Inspected OCR reader output for PG118 files 702-704",
                "Confirmed the closing ordo rerum section and the affected wrapped chapter titles",
                "Merged validation-blocking OCR line-break hyphen artifacts in 27 payload entries",
                "Synchronized parsed_entries.json with the repaired payload text",
                "Validated the final payload with import_alphabetical_index_json.py --validate-only",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                f"Payload entries changed: {entry_changes}",
                f"Intermediate parsed_entries rows changed: {parsed_changes}",
                "No material refs required changes on this rerun.",
            ],
        },
    )


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
    parsed_entries = load_json(PARSED_ENTRIES_PATH)

    entry_changes, nested_changes = repair_payload(payload)
    parsed_changes = repair_parsed_entries(parsed_entries)
    update_notes(payload, entry_changes, nested_changes)

    assert_clean(payload["entries"])
    assert_clean(parsed_entries)

    dump_json(PAYLOAD_PATH, payload)
    dump_json(PARSED_ENTRIES_PATH, parsed_entries)
    validate()
    update_todo(entry_changes, parsed_changes)


if __name__ == "__main__":
    main()
