#!/usr/bin/env python3
"""
Usage:
  python scripts/pipeline_index_extraction/fix_pl042_payload.py

Rebuild the PL042 alphabetical payload by merging the validated assembled fragments,
cleaning header-bleed corruption in the main contents index, and writing the final
canonical JSON payload back to the volume output path.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
OUTPUT = ROOT / "data/alphabetical_index_payloads/PL042_alphabetical_indices.json"
BASE = OUTPUT
FRAGMENTS = ROOT / "data/intermediate_payloads/PL042/assembled_fragments.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_entry(entry: dict, updates: dict) -> dict:
    out = deepcopy(entry)
    for key, value in updates.items():
        if key == "raw_json" and isinstance(value, dict):
            out.setdefault("raw_json", {})
            out["raw_json"].update(value)
        else:
            out[key] = value
    return out


def main() -> None:
    base = load_json(BASE)
    fragments = load_json(FRAGMENTS)

    section2 = deepcopy(fragments["data"]["sections"][1])
    section2_nodes = deepcopy(fragments["data"]["nodes"])
    section2_entries = deepcopy(fragments["data"]["entries"])
    section2_refs = deepcopy(fragments["data"]["refs"])

    entries = []
    refs = []

    entry_updates = {
        33: {
            "entry_raw": "XVII. Bores boni populus quam utilitor auctoritate persuasi. Ecclesiæ catholicæ auctoritas. 90",
            "context_raw": "XVII. Bores boni populus quam utilitor auctoritate persuasi. Ecclesiæ catholicæ auctoritas. 90",
            "inferred_printed_page": 90,
            "raw_json": {
                "page_hint_ints": [90],
                "correction_note": "Stripped header bleed from the next page; the entry itself ends at 90.",
            },
        },
        105: {
            "lemma_raw": "CAPUT PRIMUM. Deus summum bonum et incommutabile; a quo cætera omnia bona spiritualia et corporalia",
            "lemma_display": "CAPUT PRIMUM. Deus summum bonum et incommutabile; a quo cætera omnia bona spiritualia et corporalia",
            "lemma_norm": "caput primum deus summum bonum et incommutabile a quo cætera omnia bona spiritualia et corporalia",
            "lemma_sort": "caput primum deus summum bonum et incommutabile a quo cætera omnia bona spiritualia et corporalia",
            "entry_raw": "CAPUT PRIMUM. Deus summum bonum et incommutabile; a quo cætera omnia bona spiritualia et corporalia. 551-552",
            "context_raw": "CAPUT PRIMUM. Deus summum bonum et incommutabile; a quo cætera omnia bona spiritualia et corporalia. 551-552",
            "inferred_printed_page": 551,
            "target_file_best": "/homessddata/Projects/pdfocr/teste/PL042/text/1ab77665-1aea-4261-b55e-f26fdeccba51-279.txt",
            "confidence": 0.996,
            "raw_json": {
                "page_hint_ints": [551, 552],
                "correction_note": "Merged the broken 'cor- / poral.i.' seam against the body OCR and removed the stray header fragment.",
            },
        },
        201: {
            "entry_raw": "XI. Regula qua intelligitur Filius in Scripturis, nunc æqualis, nunc minor. 855",
            "context_raw": "XI. Regula qua intelligitur Filius in Scripturis, nunc æqualis, nunc minor. 855",
            "inferred_printed_page": 855,
            "target_file_best": "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-616.txt",
            "confidence": 0.997,
            "raw_json": {
                "page_hint_ints": [855],
                "correction_note": "Removed the trailing running-header digit bleed.",
            },
        },
        262: {
            "lemma_raw": "CAPUT PRIMUM. Quid a Deo, quid a lectore auctor exposcat. In Deo nihil mutabile et corporei cogitandum",
            "lemma_display": "CAPUT PRIMUM. Quid a Deo, quid a lectore auctor exposcat. In Deo nihil mutabile et corporei cogitandum",
            "lemma_norm": "caput primum quid a deo quid a lectore auctor exposcat in deo nihil mutabile et corporei cogitandum",
            "lemma_sort": "caput primum quid a deo quid a lectore auctor exposcat in deo nihil mutabile et corporei cogitandum",
            "entry_raw": "CAPUT PRIMUM. Quid a Deo, quid a lectore auctor exposcat. In Deo nihil mutabile et corporei cogitandum. 855-856",
            "context_raw": "CAPUT PRIMUM. Quid a Deo, quid a lectore auctor exposcat. In Deo nihil mutabile et corporei cogitandum. 855-856",
            "inferred_printed_page": 855,
            "target_file_best": "/homessddata/Projects/pdfocr/teste/PL042/text/c0bea1f0-94b0-4f51-8bde-425cf303907c-459.txt",
            "confidence": 0.988,
            "raw_json": {
                "page_hint_ints": [855, 856],
                "correction_note": "Normalized the seam across the page header and cross-checked against the body OCR.",
            },
        },
        393: {
            "entry_raw": "IX. An justitia et cæteræ virtutes desinant in futura vita. 1045",
            "context_raw": "IX. An justitia et cæteræ virtutes desinant in futura vita. 1045",
            "inferred_printed_page": 1045,
            "target_file_best": "/homessddata/Projects/pdfocr/teste/PL042/text/2838326b-c32d-4ffa-a174-0149c18217ab-526.txt",
            "confidence": 0.995,
            "raw_json": {
                "page_hint_ints": [1045],
                "correction_note": "Removed the next-page header bleed from the end of the line.",
            },
        },
        490: {
            "entry_raw": "XIII. Filius vajor virtutis divinæ. 1131",
            "context_raw": "XIII. Filius vajor virtutis divinæ. 1131",
            "inferred_printed_page": 1131,
            "confidence": 0.989,
            "raw_json": {
                "page_hint_ints": [1131],
                "correction_note": "Removed the running header contamination at the end of the line.",
            },
        },
    }

    delete_entry_orders = {106, 202, 330}

    for entry in base["entries"]:
        if entry["entry_order"] in delete_entry_orders:
            continue
        if entry["entry_order"] in entry_updates:
            entry = normalize_entry(entry, entry_updates[entry["entry_order"]])
        entries.append(entry)

    ref_updates = {
        "PL042:entry:0033": {
            "ref_raw": "90",
            "page_ref_raw": "90",
            "page_ref_int": 90,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-614.txt",
            "raw_json": {
                "correction_note": "Stripped the page-header bleed from the reference.",
            },
        },
        "PL042:entry:0105": {
            "ref_raw": "551-552",
            "page_ref_raw": "551-552",
            "page_ref_int": 551,
            "range_start_raw": "551",
            "range_end_raw": "552",
            "target_file": "/homessddata/Projects/pdfocr/teste/PL042/text/1ab77665-1aea-4261-b55e-f26fdeccba51-279.txt",
            "raw_json": {
                "correction_note": "Recovered the intended page range from the body OCR and removed the stray header fragment.",
            },
        },
        "PL042:entry:0201": {
            "ref_raw": "855",
            "page_ref_raw": "855",
            "page_ref_int": 855,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-616.txt",
            "raw_json": {
                "correction_note": "Removed the trailing header digit from the reference.",
            },
        },
        "PL042:entry:0262": {
            "ref_raw": "855-856",
            "page_ref_raw": "855-856",
            "page_ref_int": 855,
            "range_start_raw": "855",
            "range_end_raw": "856",
            "target_file": "/homessddata/Projects/pdfocr/teste/PL042/text/c0bea1f0-94b0-4f51-8bde-425cf303907c-459.txt",
            "raw_json": {
                "correction_note": "Normalized the reference after matching the chapter heading in the body OCR.",
            },
        },
        "PL042:entry:0393": {
            "ref_raw": "1045",
            "page_ref_raw": "1045",
            "page_ref_int": 1045,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": "/homessddata/Projects/pdfocr/teste/PL042/text/2838326b-c32d-4ffa-a174-0149c18217ab-526.txt",
            "raw_json": {
                "correction_note": "Removed the header bleed appended to the page reference.",
            },
        },
        "PL042:entry:0490": {
            "ref_raw": "1131",
            "page_ref_raw": "1131",
            "page_ref_int": 1131,
            "range_start_raw": None,
            "range_end_raw": None,
            "target_file": "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-620.txt",
            "raw_json": {
                "correction_note": "Removed the header bleed appended to the page reference.",
            },
        },
    }

    filtered_refs = []
    for ref in base["refs"]:
        if ref["entry_key"] in {"PL042:entry:0106", "PL042:entry:0202", "PL042:entry:0330"}:
            continue
        if ref["entry_key"] in ref_updates:
            ref = normalize_entry(ref, ref_updates[ref["entry_key"]])
        filtered_refs.append(ref)

    final = deepcopy(base)
    final["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    final["sections"] = [deepcopy(base["sections"][0]), section2]
    final["nodes"] = section2_nodes
    final["entries"] = entries + section2_entries
    final["refs"] = filtered_refs + section2_refs
    final["scripture_refs"] = []
    final["volume"] = deepcopy(base["volume"])
    final["volume"]["notes"] = [
        "The recoverable material is an ordo rerum / contents list, not a lemma index proper.",
        "A validated secondary works-list section (INDEX OPUSCULORUM ALIORUM AUGUSTINI) is included alongside the main contents block.",
        "Header bleed at several page seams was normalized against neighboring OCR and body-text matches.",
    ]
    final["coverage"] = {
        "entries_status": "complete",
        "entries_status_reason": "Recovered 786 logical entries across two ordo-rerum sections; cleaned header bleed at several page seams and merged the validated secondary works-list section.",
        "evidence_files": [
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-614.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-615.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-616.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-617.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-618.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-619.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-620.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/a787cd05-0964-4e22-ae94-733b31de45a1-621.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/1ab77665-1aea-4261-b55e-f26fdeccba51-279.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/c0bea1f0-94b0-4f51-8bde-425cf303907c-459.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/2838326b-c32d-4ffa-a174-0149c18217ab-526.txt",
            "/homessddata/Projects/pdfocr/teste/PL042/text/3d7736fe-7e17-4c35-945e-3a3607e90cdf-304.txt",
        ],
    }
    final["notes"] = [
        "The main recoverable block is an ordo rerum / contents list, not a lemma index proper.",
        "A second ordo rerum section listing other Augustinian works is preserved as a separate section.",
        "Several entries near page-seam OCR bleed were cleaned by cross-checking the neighboring OCR and body-text matches.",
    ]

    with OUTPUT.open("w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()
