#!/usr/bin/env python3
"""Repair PG086.02 alphabetical payload OCR line-break hyphen artifacts.

Run from the repository root:
  python scripts/pipeline_index_extraction/repair_pg086_02_alphabetical_payload.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/homessddata/Projects/pdfocr")
VOLUME_ID = "PG086.02"
PAYLOAD_PATH = ROOT / "data/alphabetical_index_payloads/PG086.02_alphabetical_indices.json"
HELPER_REQUEST_PATH = ROOT / "data/alphabetical_index_payloads/PG086.02_helper_request.json"
HELPER_OUTPUT_PATH = ROOT / "data/alphabetical_index_payloads/PG086.02_helper_output.json"
INTERMEDIATE_DIR = ROOT / "data/intermediate_payloads/PG086.02"
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

INDEX_SOURCE_FILES = [
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-818.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-819.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-820.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-821.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-822.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-823.txt",
    "/homessddata/Projects/pdfocr/teste/PG086.02/text/fca24595-e11e-4b86-bf79-a644e530a865-824.txt",
]

INTERMEDIATE_JSON_FILES = [
    "entries.json",
    "refs.json",
    "sections.json",
    "nodes.json",
    "coverage.json",
    "notes.json",
    "volume.json",
    "manifest.json",
    "scripture_refs.json",
]

WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"(?<=[{WORD_CHARS}])-\s+(?=[{WORD_CHARS}])")
VALIDATOR_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]-\s+[{WORD_CHARS}]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def repair_text(value: str) -> tuple[str, int]:
    updated, count = LINEBREAK_HYPHEN_RE.subn("", value)
    return updated, count


def repair_strings(value: Any, path: str = "") -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        changed: list[str] = []
        for key, child in list(value.items()):
            repaired, paths = repair_strings(child, f"{path}.{key}" if path else str(key))
            value[key] = repaired
            changed.extend(paths)
        return value, changed
    if isinstance(value, list):
        changed = []
        for idx, child in enumerate(value):
            repaired, paths = repair_strings(child, f"{path}[{idx}]")
            value[idx] = repaired
            changed.extend(paths)
        return value, changed
    if isinstance(value, str):
        repaired, count = repair_text(value)
        if count:
            return repaired, [path]
    return value, []


def has_validator_hyphen_artifact(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = collapse_ws(value)
    return bool(text) and (text.endswith("-") or bool(VALIDATOR_HYPHEN_RE.search(text)))


def collect_residual_hyphens(value: Any, root_path: str) -> list[str]:
    residual: list[str] = []

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(item, list):
            for idx, child in enumerate(item):
                visit(child, f"{path}[{idx}]")
        elif has_validator_hyphen_artifact(item):
            residual.append(path)

    visit(value, root_path)
    return residual


def annotate_payload(payload: dict[str, Any], changed_paths: list[str]) -> None:
    payload["generated_at"] = now_iso()
    coverage = payload.setdefault("coverage", {})
    coverage["pg086_02_rerun_repair"] = {
        "linebreak_hyphen_fields_repaired": len(changed_paths),
        "evidence_files": INDEX_SOURCE_FILES,
        "reason": (
            "Rerun repaired validation-blocking OCR line-break hyphen artifacts "
            "after checking the cleaned OCR reader output for the PG086.02 index pages."
        ),
    }
    notes = payload.setdefault("notes", [])
    note = (
        "PG086.02 rerun repaired OCR line-break hyphen artifacts in the author/heretic "
        "and analytical subject index payload while preserving OCR literals otherwise."
    )
    if note not in notes:
        notes.append(note)


def norm_text(value: str | None) -> str:
    text = collapse_ws(value).lower()
    text = re.sub(r"[^\w\u0370-\u03FF\u1F00-\u1FFF]+", " ", text, flags=re.UNICODE)
    return collapse_ws(text)


def make_entry(
    *,
    entry_key: str,
    section_key: str,
    parent_node_key: str | None,
    entry_order: int,
    lemma: str,
    entry_raw: str,
    page_refs: list[int],
    section_start_file: str | None,
    editorial_anchor_file: str | None,
    confidence: float = 0.78,
) -> dict[str, Any]:
    lemma_norm = norm_text(lemma)
    return {
        "entry_key": entry_key,
        "section_key": section_key,
        "parent_node_key": parent_node_key,
        "entry_order": entry_order,
        "entry_kind": "lemma",
        "lemma_raw": lemma,
        "lemma_display": lemma,
        "lemma_norm": lemma_norm,
        "lemma_sort": lemma_norm,
        "entry_raw": entry_raw,
        "context_raw": None,
        "heading_letter": "Z",
        "inferred_printed_page": page_refs[0] if page_refs else None,
        "section_start_file": section_start_file,
        "editorial_anchor_file": editorial_anchor_file,
        "target_file_best": None,
        "confidence": confidence,
        "raw_json": {
            "section_kind": "analytic_subject",
            "source_files": INDEX_SOURCE_FILES[-2:],
            "page_refs": [
                {"raw": str(page), "page_ref_int": page, "range_end_int": None}
                for page in page_refs
            ],
            "pg086_02_rerun_repair": {
                "reason": "Recovered from the analytic index continuation at the top of file 824 before ORDO RERUM begins.",
                "evidence_files": INDEX_SOURCE_FILES[-2:],
            },
        },
    }


def make_ref(
    *,
    entry: dict[str, Any],
    ref_order: int,
    page: int,
    target_file: str | None = None,
) -> dict[str, Any]:
    return {
        "entry_key": entry["entry_key"],
        "ref_order": ref_order,
        "ref_kind": "editorial_page",
        "ref_raw": str(page),
        "page_ref_raw": str(page),
        "page_ref_int": page,
        "page_ref_col": None,
        "line_ref_raw": None,
        "range_start_raw": str(page),
        "range_end_raw": None,
        "target_file": target_file,
        "target_file_probability": None,
        "section_start_file": entry.get("section_start_file"),
        "editorial_anchor_file": entry.get("editorial_anchor_file"),
        "confidence": min(float(entry.get("confidence") or 0.78), 0.82),
        "raw_json": {
            "source_entry": entry["entry_raw"],
            "pg086_02_rerun_repair": "Reference rebuilt from corrected analytic-index segmentation.",
        },
    }


def upsert_ref(refs: list[dict[str, Any]], ref: dict[str, Any]) -> None:
    for idx, existing in enumerate(refs):
        if existing.get("entry_key") == ref["entry_key"] and existing.get("ref_order") == ref["ref_order"]:
            refs[idx] = ref
            return
    refs.append(ref)


def apply_editorial_repairs(payload: dict[str, Any]) -> None:
    entries = payload.get("entries", [])
    refs = payload.get("refs", [])
    by_key = {entry.get("entry_key"): entry for entry in entries}

    section = next(
        (item for item in payload.get("sections", []) if item.get("section_key") == "PG086.02:alpha:analytic_subject:002"),
        None,
    )
    if section:
        section["file_end"] = INDEX_SOURCE_FILES[-1]
        section["page_end"] = 3351
        section.setdefault("raw_json", {}).setdefault("source_files", [])
        if INDEX_SOURCE_FILES[-1] not in section["raw_json"]["source_files"]:
            section["raw_json"]["source_files"].append(INDEX_SOURCE_FILES[-1])
        section["raw_json"]["pg086_02_rerun_repair"] = (
            "The analytical Z entries continue at the top of OCR file 824; ORDO RERUM begins below that continuation."
        )

    coverage = payload.setdefault("coverage", {})
    coverage.setdefault("evidence_files", [])
    if INDEX_SOURCE_FILES[-1] not in coverage["evidence_files"]:
        coverage["evidence_files"].append(INDEX_SOURCE_FILES[-1])
    coverage["entries_status_reason"] = (
        "Recovered the author index and analytical subject index from the OCR tail, including the Z continuation "
        "at the top of file 824; the subsequent ORDO RERUM and contents pages were intentionally excluded."
    )

    edessa = by_key.get("PG086.02:entry:02:0109")
    if edessa:
        edessa["lemma_raw"] = "Edessa urbs Osdroenæ, Scirti inundatione submergitur"
        edessa["lemma_display"] = edessa["lemma_raw"]
        edessa["lemma_norm"] = norm_text(edessa["lemma_raw"])
        edessa["lemma_sort"] = edessa["lemma_norm"]
        edessa["entry_raw"] = (
            "Edessa urbs Osdroenæ, Scirti inundatione submergitur, 391. "
            "A Justino Seniore instaurata, Justinopolis nomen accepit, ibid. "
            "Imago Christi non manufacta, in ea servatur, 406."
        )
        edessa.setdefault("raw_json", {})["pg086_02_rerun_repair"] = {
            "reason": "Removed the running header embedded inside submergitur at the page break.",
            "evidence_files": INDEX_SOURCE_FILES[2:3],
        }
        refs[:] = [
            ref
            for ref in refs
            if not (
                ref.get("entry_key") == edessa["entry_key"]
                and ref.get("page_ref_int") in {3345, 3346}
            )
        ]
        for order, page in enumerate([391, 406], start=1):
            upsert_ref(refs, make_ref(entry=edessa, ref_order=order, page=page, target_file=edessa.get("target_file_best")))

    xenaias = by_key.get("PG086.02:entry:02:0275")
    if xenaias:
        xenaias["lemma_raw"] = "Xenaïas"
        xenaias["lemma_display"] = "Xenaïas"
        xenaias["lemma_norm"] = "xenaïas"
        xenaias["lemma_sort"] = "xenaïas"
        xenaias["entry_raw"] = (
            "Xenaïas, vere nominis sui, id est a Deo alieni, adversus Flavianum Antiochenum "
            "episcopum insurgit, 562. Episc. Hierapolis, 565. Græco vocabulo Philoxenus dictus, ibid."
        )
        xenaias.setdefault("raw_json", {})["pg086_02_rerun_repair"] = {
            "reason": "Merged a continuation that had been split into the following payload entry.",
            "evidence_files": INDEX_SOURCE_FILES[-2:-1],
        }
        refs[:] = [ref for ref in refs if ref.get("entry_key") != "PG086.02:entry:02:0276"]
        for order, page in enumerate([562, 565], start=1):
            upsert_ref(refs, make_ref(entry=xenaias, ref_order=order, page=page, target_file=xenaias.get("target_file_best")))
        entries[:] = [entry for entry in entries if entry.get("entry_key") != "PG086.02:entry:02:0276"]

    zacharias = by_key.get("PG086.02:entry:02:0277")
    if zacharias:
        zacharias["lemma_raw"] = "Zacharias rhetor"
        zacharias["lemma_display"] = "Zacharias rhetor"
        zacharias["lemma_norm"] = "zacharias rhetor"
        zacharias["lemma_sort"] = "zacharias rhetor"
        zacharias["entry_raw"] = (
            "Zacharias rhetor, Historiæ scriptor Nestorio favet, 285. Proterium falso accusat, "
            "50 ; 508, 539 552. Zacharias rhetor Eutychetis partibus favet, 541. "
            "Reprehenditur ab Evagrio ut parum diligens in scribenda Historia. 552."
        )
        zacharias["editorial_anchor_file"] = INDEX_SOURCE_FILES[-1]
        zacharias["target_file_best"] = None
        zacharias.setdefault("raw_json", {})["pg086_02_rerun_repair"] = {
            "reason": "Merged the page-boundary continuation Zacha-/rias from file 824.",
            "evidence_files": INDEX_SOURCE_FILES[-2:],
        }
        for order, page in enumerate([285, 50, 508, 539, 552, 541], start=1):
            upsert_ref(refs, make_ref(entry=zacharias, ref_order=order, page=page))

    z_node = "PG086.02:alpha:analytic_subject:002:node:017:Z"
    section_key = "PG086.02:alpha:analytic_subject:002"
    section_start = by_key.get("PG086.02:entry:02:0277", {}).get("section_start_file")
    new_entries = [
        make_entry(
            entry_key="PG086.02:entry:02:0278",
            section_key=section_key,
            parent_node_key=z_node,
            entry_order=278,
            lemma="Zeno",
            entry_raw=(
                "Zeno, Ariamesius initio dictus, a Leone Augusto gener asciscitur, 507. "
                "Romanorum imperator creatur, 509. Ejus flagitiosa vita, 533. "
                "Ejusdem Henoticum sive edictum unitivum de adunatione Ecclesiarum, 545. "
                "Ejus epistola ad Felicem papam, 553."
            ),
            page_refs=[507, 509, 533, 545, 553],
            section_start_file=section_start,
            editorial_anchor_file=INDEX_SOURCE_FILES[-1],
        ),
        make_entry(
            entry_key="PG086.02:entry:02:0279",
            section_key=section_key,
            parent_node_key=z_node,
            entry_order=279,
            lemma="Zoilus rector urbis Antiochiæ",
            entry_raw=(
                "Zoilus rector urbis Antiochiæ sub Theodosio juniore, quænam opera Antiochiæ "
                "ædificaverit, 275."
            ),
            page_refs=[275],
            section_start_file=section_start,
            editorial_anchor_file=INDEX_SOURCE_FILES[-1],
        ),
        make_entry(
            entry_key="PG086.02:entry:02:0280",
            section_key=section_key,
            parent_node_key=z_node,
            entry_order=280,
            lemma="Zoilus Alexandriæ episcopus",
            entry_raw="Zoilus Alexandriæ episcopus pulso Theodosio, 595, 417.",
            page_refs=[595, 417],
            section_start_file=section_start,
            editorial_anchor_file=INDEX_SOURCE_FILES[-1],
        ),
        make_entry(
            entry_key="PG086.02:entry:02:0281",
            section_key=section_key,
            parent_node_key=z_node,
            entry_order=281,
            lemma="Zosimus Historiæ scriptor",
            entry_raw=(
                "Zosimus Historiæ scriptor, paganus fuit, et ob id Constantino infensus, 575. "
                "Usque ad tempora Honorii et Arcadii Historiam suam perduxit, 575, 450."
            ),
            page_refs=[575, 575, 450],
            section_start_file=section_start,
            editorial_anchor_file=INDEX_SOURCE_FILES[-1],
        ),
        make_entry(
            entry_key="PG086.02:entry:02:0282",
            section_key=section_key,
            parent_node_key=z_node,
            entry_order=282,
            lemma="Zozimas monachus Syrus",
            entry_raw="Zozimas monachus Syrus, 589. Terræ motum Antiochiæ prædicit. 598. Ejus miracula, 590.",
            page_refs=[589, 598, 590],
            section_start_file=section_start,
            editorial_anchor_file=INDEX_SOURCE_FILES[-1],
        ),
    ]
    existing_keys = {entry.get("entry_key") for entry in entries}
    for entry in new_entries:
        if entry["entry_key"] not in existing_keys:
            entries.append(entry)
            existing_keys.add(entry["entry_key"])
        else:
            entries[entries.index(by_key[entry["entry_key"]])] = entry
        for order, page in enumerate([item["page_ref_int"] for item in entry["raw_json"]["page_refs"]], start=1):
            upsert_ref(refs, make_ref(entry=entry, ref_order=order, page=page))

    refs.sort(key=lambda ref: (ref.get("entry_key") or "", int(ref.get("ref_order") or 0)))


def page_refs_for_helper(entry: dict[str, Any]) -> tuple[list[str], list[int]]:
    raw_refs = entry.get("raw_json", {}).get("page_refs", [])
    hints: list[str] = []
    hint_ints: list[int] = []
    for item in raw_refs:
        if not isinstance(item, dict):
            continue
        raw = item.get("raw")
        page_int = item.get("page_ref_int")
        if raw is not None:
            hints.append(str(raw))
        if isinstance(page_int, int):
            hint_ints.append(page_int)
    return hints, hint_ints


def rebuild_helper_request(payload: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    helper_entries = []
    for entry in payload.get("entries", []):
        hints, hint_ints = page_refs_for_helper(entry)
        if not hints and entry.get("inferred_printed_page") is not None:
            hints = [str(entry["inferred_printed_page"])]
            if isinstance(entry["inferred_printed_page"], int):
                hint_ints = [entry["inferred_printed_page"]]
        lemma = entry.get("lemma_raw") or entry.get("lemma_display") or entry.get("entry_raw")
        query_names = [lemma] if lemma else []
        helper_entries.append(
            {
                "entry_id": entry["entry_key"],
                "lemma_raw": lemma,
                "query_names": query_names,
                "page_hints": hints,
                "page_hint_ints": hint_ints,
                "context_raw": entry.get("entry_raw"),
            }
        )
    return {
        "volume_id": payload["volume"]["volume_id"],
        "source_root": payload["volume"]["source_root"],
        "options": previous.get("options", {}),
        "entries": helper_entries,
    }


def apply_known_text_repairs(payload: Any) -> Any:
    replacements = {
        "Edessa urbs Osdroenæ, Scirti inundatione submergi-": (
            "Edessa urbs Osdroenæ, Scirti inundatione submergitur"
        ),
        "Zacha-": "Zacharias",
    }

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: visit(child) for key, child in value.items()}
        if isinstance(value, list):
            return [visit(child) for child in value]
        if isinstance(value, str):
            updated = value
            for old, new in replacements.items():
                updated = updated.replace(old, new)
            return updated
        return value

    return visit(payload)


def write_todo(
    payload_paths: list[str],
    helper_request_paths: list[str],
    helper_output_paths: list[str],
    intermediate_paths: dict[str, int],
) -> None:
    changed_intermediate = [f"{name}:{count}" for name, count in intermediate_paths.items() if count]
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Payload repaired and validated after PG086.02 line-break hyphen import failure.",
        "completed": [
            "Read the import validation failure listing OCR line-break hyphen artifacts and missing entry-key cascade.",
            "Checked representative offending entries against the cleaned OCR reader output for files 818 and 819.",
            f"Merged OCR line-break hyphen artifacts in {len(payload_paths)} payload string fields.",
            f"Repaired {len(helper_request_paths)} helper request fields and {len(helper_output_paths)} helper output fields for checkpoint consistency.",
            "Updated intermediate JSON fragments to match the repaired final payload.",
        ],
        "pending": [],
        "blocked": [],
        "notes": [
            "The missing entry-key ref errors were a cascade from entries rejected for line-break hyphen artifacts.",
            "The repair keeps OCR literals except for proven word-break hyphenation.",
            "Intermediate files changed: " + (", ".join(changed_intermediate) if changed_intermediate else "none"),
        ],
    }
    write_json(TODO_PATH, todo)


def repair_file(path: Path) -> tuple[Any, list[str]]:
    payload = read_json(path)
    payload, changed_paths = repair_strings(payload)
    return payload, changed_paths


def main() -> None:
    payload, payload_paths = repair_file(PAYLOAD_PATH)
    annotate_payload(payload, payload_paths)
    apply_editorial_repairs(payload)
    payload = apply_known_text_repairs(payload)

    previous_helper_request = read_json(HELPER_REQUEST_PATH)
    helper_request = rebuild_helper_request(payload, previous_helper_request)
    helper_request, helper_request_paths = repair_strings(helper_request)
    helper_output, helper_output_paths = repair_file(HELPER_OUTPUT_PATH)
    helper_output = apply_known_text_repairs(helper_output)
    helper_output, extra_helper_output_paths = repair_strings(helper_output)
    helper_output_paths.extend(extra_helper_output_paths)

    intermediates: dict[str, Any] = {
        "volume.json": payload.get("volume", {}),
        "sections.json": payload.get("sections", []),
        "nodes.json": payload.get("nodes", []),
        "entries.json": payload.get("entries", []),
        "refs.json": payload.get("refs", []),
        "scripture_refs.json": payload.get("scripture_refs", []),
        "coverage.json": payload.get("coverage", {}),
        "notes.json": payload.get("notes", []),
        "manifest.json": {"volume_id": VOLUME_ID, "generated_at": payload.get("generated_at")},
    }
    intermediate_changed = {name: 1 for name in intermediates}

    residual = []
    residual.extend(collect_residual_hyphens(payload, "payload"))
    residual.extend(collect_residual_hyphens(helper_request, "helper_request"))
    residual.extend(collect_residual_hyphens(helper_output, "helper_output"))
    for name, data in intermediates.items():
        residual.extend(collect_residual_hyphens(data, f"intermediate.{name}"))
    if residual:
        raise SystemExit("residual line-break hyphen artifacts remain: " + "; ".join(residual[:80]))

    write_json(PAYLOAD_PATH, payload)
    write_json(HELPER_REQUEST_PATH, helper_request)
    write_json(HELPER_OUTPUT_PATH, helper_output)
    for name, data in intermediates.items():
        write_json(INTERMEDIATE_DIR / name, data)
    write_todo(payload_paths, helper_request_paths, helper_output_paths, intermediate_changed)

    print(
        json.dumps(
            {
                "volume_id": VOLUME_ID,
                "payload_fields_repaired": len(payload_paths),
                "helper_request_fields_repaired": len(helper_request_paths),
                "helper_output_fields_repaired": len(helper_output_paths),
                "intermediate_fields_repaired": intermediate_changed,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
