#!/usr/bin/env python3
"""Repair PG024 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg024_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG024"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG024_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG024_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG024_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG024"
SOURCE_ROOT = ROOT / "teste/PG024/text"

INDEX_EVIDENCE_FILES = [
    str(SOURCE_ROOT / "42c50714-dcb1-4bab-a408-d3779fd70eb6-495.txt"),
    str(SOURCE_ROOT / "42c50714-dcb1-4bab-a408-d3779fd70eb6-496.txt"),
    str(SOURCE_ROOT / "42c50714-dcb1-4bab-a408-d3779fd70eb6-501.txt"),
    str(SOURCE_ROOT / "42c50714-dcb1-4bab-a408-d3779fd70eb6-502.txt"),
    str(SOURCE_ROOT / "42c50714-dcb1-4bab-a408-d3779fd70eb6-503.txt"),
    str(SOURCE_ROOT / "42c50714-dcb1-4bab-a408-d3779fd70eb6-504.txt"),
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")

REPAIRED_ENTRY_RAW: dict[str, str] = {
    "PG024:alpha:analytic_subject:001:entry:0410": "El assimilantur Judæi, ibid.",
    "PG024:alpha:analytic_subject:001:entry:0411": "Osee ex sententia Hieronymi uxorem meretricem non habuisse videtur, 125.",
    "PG024:alpha:analytic_subject:001:entry:0420": "Per triz Pascha Dominus venit in Jerusalem, 390.",
    "PG024:alpha:analytic_subject:001:entry:0435": "Prædicationes ejus, 355.",
    "PG024:alpha:analytic_subject:001:entry:0446": "Fœnerandum Christo in pauperibus, 946.",
    "PG024:alpha:analytic_subject:001:entry:0470": "Omnis creatura condolet super peccatis hominum, 847.",
    "PG024:alpha:analytic_subject:001:entry:0482": "Qui volunt converti, non suo merito, sed Dei clementia conservantur, 711.",
    "PG024:alpha:analytic_subject:001:entry:0490": "Difficile invenitur qui non deprimatur vinculis peccati, 694.",
    "PG024:alpha:analytic_subject:001:entry:0492": "Unusquisque pro qualitate peccati ignem sibi succendit, 584.",
    "PG024:alpha:analytic_subject:001:entry:0507": "Tormenta adhibentur eis ut divino igne purgentur, 148.",
    "PG024:alpha:analytic_subject:001:entry:0509": "Interfectio eorum, significat iram consummatam, 946.",
    "PG024:alpha:analytic_subject:001:entry:0515": "In dimidio dierum suorum moriuntur, 471.",
    "PG024:alpha:analytic_subject:001:entry:0526": "In tempore persecutionis nemini credendum, 905.",
    "PG024:alpha:analytic_subject:001:entry:0528": "Tempore persecutionis in quo condendum, 855.",
    "PG024:alpha:analytic_subject:001:entry:0534": "Persecutiones Ecclesiæ a Nerone usque ad Maximinum, 955.",
    "PG024:alpha:analytic_subject:001:entry:0540": "Omne eorum opprobrium pertransibit, 590.",
    "PG024:alpha:analytic_subject:001:entry:0561": "Philosophi clarissimi habuerunt publice concubinas, 35.",
    "PG024:alpha:analytic_subject:001:entry:0572": "Ordo et utilitas pœnarum et pœnitentiæ fructus, 758.",
    "PG024:alpha:analytic_subject:001:entry:0575": "Universa pœna non videre Deum in sua majestate regnantem, 549.",
    "PG024:alpha:analytic_subject:001:entry:0593": "Victoria pœnitentibus conceditur gratia Domini, 592.",
    "PG024:alpha:analytic_subject:001:entry:0600": "Porphyrium arguit Hieronymus, 402.",
    "PG024:alpha:analytic_subject:001:entry:0612": "Dispensatoria fuit inter eos contentio, 541.",
    "PG024:alpha:analytic_subject:001:entry:0632": "Omnes de Christi adventu cecinerunt, 940.",
    "PG024:alpha:analytic_subject:001:entry:0635": "Habulus prophetarum, qui? 211.",
    "PG024:alpha:analytic_subject:001:entry:0637": "Propheta vaticinii fine monstrator, 1058.",
    "PG024:alpha:analytic_subject:001:entry:0638": "Consuetudo prophetælis, quæ? 57, 73, 83.",
    "PG024:alpha:analytic_subject:001:entry:0711": "Romanum regnum fortissimum, 231.",
    "PG024:alpha:analytic_subject:001:entry:0819": "Quid observant Apostoli in recilandis testimoniis Scripturarum, 622, 665.",
    "PG024:alpha:analytic_subject:001:entry:0821": "Apostoli testimonia Scripturarum juxta Hebraicum sumebant, 378, 394.",
    "PG024:alpha:analytic_subject:001:entry:0832": "Vitium scriptorum ostenditur, 993.",
    "PG024:alpha:analytic_subject:001:entry:0835": "Securitas negligentiam, negligentia contemptum parit, 551, 1089.",
    "PG024:alpha:analytic_subject:001:entry:0843": "Damnantur qui sensibus luxuriant, 552.",
    "PG024:alpha:analytic_subject:001:entry:0855": "Septenarius et septuagesimus numerus significat perfectam pœnitentiam, 527.",
    "PG024:alpha:analytic_subject:001:entry:0867": "Additamentum LXX verum confessum, 121.",
    "PG024:alpha:analytic_subject:001:entry:0877": "Defendit Hieronymus LXX Interpretes, 415.",
    "PG024:alpha:analytic_subject:001:entry:0879": "LXX editionem interpretari cogitur Hieronymus, 420.",
    "PG024:alpha:analytic_subject:001:entry:0881": "LXX imitantur gentium fabulas in interpretatione sua, 215.",
    "PG024:alpha:analytic_subject:001:entry:0887": "Multa desunt in codicibus LXX Græcis et Latinis, 1035.",
    "PG024:alpha:analytic_subject:001:entry:0896": "Quidam duo Seraphim, Filium et Spiritum sanctum intelligentes arguuntur, 92.",
    "PG024:alpha:analytic_subject:001:entry:0898": "Alii per eos Vetus et Novum Instrumentum volunt significari, 93.",
    "PG024:alpha:analytic_subject:001:entry:0902": "Utitur verbis humanæ consuetudinis, 487.",
    "PG024:alpha:analytic_subject:001:entry:0910": "Signum grandis sterilitatis, in quo, 938.",
    "PG024:alpha:analytic_subject:001:entry:0918": "Quandoque appellatur caput, ibid.",
    "PG024:alpha:analytic_subject:001:entry:0929": "Mortifero crimine animas demergunt in profundum, 246.",
    "PG024:alpha:analytic_subject:001:entry:0931": "Quid per eas intelligit Hieronymus, 175.",
    "PG024:alpha:analytic_subject:001:entry:0941": "Somniatores in Ecclesia jactantes errores suos, ibid",
    "PG024:alpha:analytic_subject:001:entry:0944": "Spe futurorum, sustinemus tribulationem, 680.",
    "PG024:alpha:analytic_subject:001:entry:0988": "Templum Domini in vertice Gethsemani, 572.",
    "PG024:alpha:analytic_subject:001:entry:0994": "Ab angelis potestatibus portantur, 529.",
    "PG024:alpha:analytic_subject:001:entry:0996": "Terra quasi punctum, et homines quasi locustæ dicuntur, 474.",
    "PG024:alpha:analytic_subject:001:entry:1008": "Novum Testamentum testimoniis Veteris roboratur, 510.",
    "PG024:alpha:analytic_subject:001:entry:1010": "Novo Testamento aliud non succedit, 795.",
}

REMOVE_NEXT: dict[str, int] = {
    "PG024:alpha:analytic_subject:001:entry:0106": 1,
    "PG024:alpha:analytic_subject:001:entry:0420": 1,
    "PG024:alpha:analytic_subject:001:entry:0435": 1,
    "PG024:alpha:analytic_subject:001:entry:0446": 1,
    "PG024:alpha:analytic_subject:001:entry:0470": 1,
    "PG024:alpha:analytic_subject:001:entry:0482": 1,
    "PG024:alpha:analytic_subject:001:entry:0490": 1,
    "PG024:alpha:analytic_subject:001:entry:0492": 1,
    "PG024:alpha:analytic_subject:001:entry:0507": 1,
    "PG024:alpha:analytic_subject:001:entry:0509": 1,
    "PG024:alpha:analytic_subject:001:entry:0515": 1,
    "PG024:alpha:analytic_subject:001:entry:0526": 1,
    "PG024:alpha:analytic_subject:001:entry:0528": 1,
    "PG024:alpha:analytic_subject:001:entry:0534": 1,
    "PG024:alpha:analytic_subject:001:entry:0540": 1,
    "PG024:alpha:analytic_subject:001:entry:0561": 1,
    "PG024:alpha:analytic_subject:001:entry:0572": 1,
    "PG024:alpha:analytic_subject:001:entry:0575": 1,
    "PG024:alpha:analytic_subject:001:entry:0593": 1,
    "PG024:alpha:analytic_subject:001:entry:0600": 1,
    "PG024:alpha:analytic_subject:001:entry:0612": 1,
    "PG024:alpha:analytic_subject:001:entry:0632": 1,
    "PG024:alpha:analytic_subject:001:entry:0635": 1,
    "PG024:alpha:analytic_subject:001:entry:0711": 1,
    "PG024:alpha:analytic_subject:001:entry:0819": 1,
    "PG024:alpha:analytic_subject:001:entry:0821": 2,
    "PG024:alpha:analytic_subject:001:entry:0832": 1,
    "PG024:alpha:analytic_subject:001:entry:0835": 1,
    "PG024:alpha:analytic_subject:001:entry:0843": 1,
    "PG024:alpha:analytic_subject:001:entry:0855": 1,
    "PG024:alpha:analytic_subject:001:entry:0867": 1,
    "PG024:alpha:analytic_subject:001:entry:0877": 1,
    "PG024:alpha:analytic_subject:001:entry:0879": 1,
    "PG024:alpha:analytic_subject:001:entry:0881": 1,
    "PG024:alpha:analytic_subject:001:entry:0887": 1,
    "PG024:alpha:analytic_subject:001:entry:0896": 1,
    "PG024:alpha:analytic_subject:001:entry:0898": 1,
    "PG024:alpha:analytic_subject:001:entry:0902": 1,
    "PG024:alpha:analytic_subject:001:entry:0910": 1,
    "PG024:alpha:analytic_subject:001:entry:0929": 1,
    "PG024:alpha:analytic_subject:001:entry:0931": 1,
    "PG024:alpha:analytic_subject:001:entry:0944": 1,
    "PG024:alpha:analytic_subject:001:entry:0988": 1,
    "PG024:alpha:analytic_subject:001:entry:0994": 1,
    "PG024:alpha:analytic_subject:001:entry:0996": 1,
    "PG024:alpha:analytic_subject:001:entry:1008": 1,
    "PG024:alpha:analytic_subject:001:entry:1010": 1,
}

SPLIT_NEXT_REFS = {
    "PG024:alpha:analytic_subject:001:entry:0637": {
        "next_key": "PG024:alpha:analytic_subject:001:entry:0638",
        "move_ref_raws": {"1058"},
    }
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text))


def lemma_from_entry_raw(entry_raw: str) -> str:
    text = entry_raw.strip()
    text = re.sub(r",?\s+(?:ibid\.?|[0-9][0-9,\s]*\.?)$", "", text, flags=re.IGNORECASE)
    return text.strip()


def update_entry_text(entry: dict[str, Any], entry_raw: str) -> None:
    lemma = lemma_from_entry_raw(entry_raw)
    entry["entry_raw"] = entry_raw
    entry["lemma_raw"] = lemma
    entry["lemma_display"] = lemma
    entry["lemma_norm"] = lemma.casefold()
    entry["lemma_sort"] = re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſ]+", " ", lemma.casefold()).strip()
    raw_json = entry.setdefault("raw_json", {})
    raw_json.setdefault("repair_notes", []).append(
        "PG024 rerun repaired OCR line-break hyphenation against cleaned OCR reader output."
    )
    raw_json["rerun_hyphen_repair_evidence_files"] = INDEX_EVIDENCE_FILES


def is_inherited_ref(ref: dict[str, Any]) -> bool:
    raw_json = ref.get("raw_json") if isinstance(ref.get("raw_json"), dict) else {}
    return bool(raw_json.get("inherited_from_previous_ref")) or raw_json.get("mapping_kind") == "inherited"


def renumber_refs(refs: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in refs:
        grouped[ref["entry_key"]].append(ref)
    for group in grouped.values():
        group.sort(key=lambda r: (r.get("ref_order") or 0, r.get("ref_raw") or ""))
        seen: set[tuple[Any, ...]] = set()
        order = 1
        for ref in group:
            sig = (
                ref.get("ref_kind"),
                ref.get("ref_raw"),
                ref.get("page_ref_raw"),
                ref.get("page_ref_int"),
                ref.get("target_file"),
            )
            if sig in seen:
                ref["_drop_duplicate"] = True
                continue
            seen.add(sig)
            ref["ref_order"] = order
            order += 1
    refs[:] = [ref for ref in refs if not ref.pop("_drop_duplicate", False)]


def set_best_target_from_refs(entry: dict[str, Any], refs: list[dict[str, Any]]) -> None:
    entry_refs = sorted(
        [ref for ref in refs if ref["entry_key"] == entry["entry_key"]],
        key=lambda ref: ref.get("ref_order") or 0,
    )
    if entry_refs:
        entry["target_file_best"] = entry_refs[0].get("target_file")
        return
    entry["target_file_best"] = None


def repair_payload(payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload["entries"]
    refs = payload["refs"]
    key_to_index = {entry["entry_key"]: idx for idx, entry in enumerate(entries)}
    removed_keys: set[str] = set()
    changed = {
        "entry_text_repairs": 0,
        "removed_continuation_entries": 0,
        "remapped_refs": 0,
        "dropped_inherited_refs": 0,
    }

    for key, entry_raw in REPAIRED_ENTRY_RAW.items():
        if key in key_to_index:
            update_entry_text(entries[key_to_index[key]], entry_raw)
            changed["entry_text_repairs"] += 1

    for key, next_count in REMOVE_NEXT.items():
        idx = key_to_index[key]
        primary = entries[idx]
        next_keys = [entries[idx + offset]["entry_key"] for offset in range(1, next_count + 1)]
        if key == "PG024:alpha:analytic_subject:001:entry:0106":
            next_entry = entries[idx + 1]
            primary["entry_raw"] = primary["entry_raw"].rstrip("-") + next_entry["entry_raw"]
            primary.setdefault("raw_json", {}).setdefault("repair_notes", []).append(
                "PG024 rerun joined the Deus entry across OCR files 495-496."
            )
        if key != "PG024:alpha:analytic_subject:001:entry:0106":
            before = len(refs)
            refs[:] = [
                ref
                for ref in refs
                if not (ref["entry_key"] == key and is_inherited_ref(ref))
            ]
            changed["dropped_inherited_refs"] += before - len(refs)
        for ref in refs:
            if ref["entry_key"] in next_keys:
                old_key = ref["entry_key"]
                ref["entry_key"] = key
                ref.setdefault("raw_json", {})["rerun_remapped_from_entry_key"] = old_key
                changed["remapped_refs"] += 1
        removed_keys.update(next_keys)
        primary.setdefault("raw_json", {})["removed_continuation_entry_keys"] = next_keys

    for key, spec in SPLIT_NEXT_REFS.items():
        idx = key_to_index[key]
        primary = entries[idx]
        before = len(refs)
        refs[:] = [
            ref
            for ref in refs
            if not (ref["entry_key"] == key and is_inherited_ref(ref))
        ]
        changed["dropped_inherited_refs"] += before - len(refs)
        for ref in refs:
            if ref["entry_key"] == spec["next_key"] and ref.get("ref_raw") in spec["move_ref_raws"]:
                ref["entry_key"] = key
                ref.setdefault("raw_json", {})["rerun_split_from_entry_key"] = spec["next_key"]
                changed["remapped_refs"] += 1
        primary.setdefault("raw_json", {})["split_continuation_entry_key"] = spec["next_key"]

    entries[:] = [entry for entry in entries if entry["entry_key"] not in removed_keys]
    changed["removed_continuation_entries"] = len(removed_keys)

    renumber_refs(refs)
    for entry in entries:
        set_best_target_from_refs(entry, refs)

    assert_payload_clean(payload)
    return changed


def assert_payload_clean(payload: dict[str, Any]) -> None:
    bad = []
    for collection_name, fields in {
        "entries": ("lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "entry_raw", "context_raw"),
        "refs": ("ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"),
        "scripture_refs": ("ref_raw", "book_raw", "book_norm"),
    }.items():
        for idx, obj in enumerate(payload.get(collection_name, []), start=1):
            for field in fields:
                if has_validator_hyphen_artifact(obj.get(field)):
                    bad.append({"collection": collection_name, "index": idx, "key": obj.get("entry_key"), "field": field})
    if bad:
        raise SystemExit(json.dumps({"residual_hyphen_artifacts": bad[:80]}, ensure_ascii=False, indent=2))

    entry_keys = {entry["entry_key"] for entry in payload["entries"]}
    missing_refs = [
        {"index": idx, "entry_key": ref["entry_key"]}
        for idx, ref in enumerate(payload["refs"], start=1)
        if ref["entry_key"] not in entry_keys
    ]
    if missing_refs:
        raise SystemExit(json.dumps({"missing_ref_entry_keys": missing_refs[:80]}, ensure_ascii=False, indent=2))


def update_metadata(payload: dict[str, Any], changed: dict[str, Any]) -> str:
    timestamp = now_iso()
    payload["generated_at"] = timestamp
    message = (
        "PG024 rerun repaired validation-blocking OCR line-break hyphen artifacts in the "
        "INDEX RERUM ET VERBORUM payload; refs reported as missing in the previous failure "
        "were cascade errors from rejected entries."
    )
    volume_notes = payload.setdefault("volume", {}).setdefault("notes", [])
    if message not in volume_notes:
        volume_notes.append(message)
    payload.setdefault("notes", []).append(
        {
            "type": "rerun_validation",
            "created_at": timestamp,
            "message": message,
            "changed": changed,
            "evidence_files": INDEX_EVIDENCE_FILES,
        }
    )
    return timestamp


def write_intermediates(payload: dict[str, Any], timestamp: str, changed: dict[str, Any]) -> None:
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "payload_path": str(PAYLOAD_PATH),
            "helper_request_path": str(HELPER_REQUEST_PATH),
            "helper_output_path": str(HELPER_OUTPUT_PATH),
            "changed": changed,
        },
    )
    write_json(
        INTERMEDIATE_DIR / "todo.json",
        {
            "volume_id": VOLUME_ID,
            "updated_at": timestamp,
            "current_focus": "Payload repaired and validated after PG024 line-break hyphen import failure.",
            "completed": [
                "Read the previous validation failure for PG024",
                "Checked cleaned OCR reader output for files 495, 496, 501, 502, 503, and 504",
                "Repaired terminal OCR line-break hyphen artifacts in entry text fields",
                "Remapped continuation-entry refs to the repaired entry keys",
                "Refreshed final payload and intermediate checkpoints",
            ],
            "pending": [],
            "blocked": [],
            "notes": [
                "OCR file suffixes, printed index page numbers, and cited references remain separate.",
                "Helper request/output already had no validation-style hyphen artifacts; material locators were preserved.",
                "The ORDO RERUM section remains distinct from the analytic subject index.",
            ],
        },
    )


def run_helper() -> None:
    subprocess.run(
        [
            "python",
            "scripts/index_target_locator.py",
            "--input",
            str(HELPER_REQUEST_PATH),
            "--output",
            str(HELPER_OUTPUT_PATH),
            "--pretty",
        ],
        cwd=ROOT,
        check=True,
    )


def validate_payload() -> None:
    subprocess.run(
        [
            "python",
            "scripts/import_alphabetical_index_json.py",
            "--input",
            str(PAYLOAD_PATH),
            "--validate-only",
            "--print-summary",
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    payload = load_json(PAYLOAD_PATH)
    changed = repair_payload(payload)
    timestamp = update_metadata(payload, changed)
    write_json(PAYLOAD_PATH, payload)
    write_intermediates(payload, timestamp, changed)
    run_helper()
    validate_payload()
    print(json.dumps({"status": "ok", "changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
