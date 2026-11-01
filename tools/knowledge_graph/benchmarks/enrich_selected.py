#!/usr/bin/env python3
"""Apply explicit revisable source inspections and rerun the existing scripture detector."""
from __future__ import annotations

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.knowledge_graph.benchmarks.common import (
    common_arguments, digest, distributions, read_json, read_jsonl, read_ocr, write_json, write_jsonl,
)


def apply_inspection(record, annotation, raw):
    if record["split"] == "blind_holdout":
        raise ValueError("Development inspections may not annotate blind holdout")
    quote = annotation["summary_quote"]
    if not quote or quote not in record["text"]["segment_summary"]:
        raise ValueError(f"Inspection summary quote is not exact: {record['candidate_id']}")
    ocr_quote = annotation["ocr_quote"]
    start = annotation["ocr_start"]
    if not ocr_quote or raw[start:start+len(ocr_quote)] != ocr_quote:
        raise ValueError(f"Inspection OCR quote is not exact: {record['candidate_id']}")
    evidence = record["evidence"]
    evidence.update(summary_quote=quote, ocr_quote=ocr_quote, ocr_start=start,
                    ocr_end=start+len(ocr_quote), ocr_support=annotation["ocr_support"],
                    inspection="codex_source_reading_provisional_not_independent_gold",
                    notes=annotation["notes"], scope="Only the quoted claim/issue was checked, not all statements on the page.")
    record["text"]["ocr_excerpt"] = ocr_quote
    selection = record["selection"]
    selection["reason"] = annotation["reason"]
    selection["categories"] = sorted((set(selection["categories"]) | set(annotation.get("categories_add", []))) - set(annotation.get("categories_remove", [])))
    record["review"].update(status="source_inspected_pending_independent_review", gold_status="not_gold",
                            questions=annotation["questions"],
                            rejected_candidate_relations=annotation.get("rejected_candidate_relations", []),
                            showcase_title=annotation.get("showcase_title"))


def main():
    p = common_arguments(__doc__)
    p.add_argument("--curation", type=Path)
    p.add_argument("--scripture", action="store_true")
    args = p.parse_args()
    records = list(read_jsonl(args.output_dir / "selected_pages.jsonl"))
    pairs = list(read_jsonl(args.output_dir / "resolution_pairs.jsonl"))
    curation = read_json(args.curation) if args.curation else {"pages": [], "pairs": []}
    by_generation = {r["source"]["generation_id"]: r for r in records}
    for annotation in curation["pages"]:
        record = by_generation[annotation["generation_id"]]
        raw = read_ocr(args.output_dir / record["source"]["ocr_snapshot"])
        apply_inspection(record, annotation, raw)
    if args.scripture:
        from tools.scripture.citation_index import DETECTOR_VERSION, ScanTask, scan_file_task
        for record in records:
            s = record["source"]
            path = args.corpus_root / s["documento"] / "text" / Path(s["ocr_path"]).name
            if digest(path.read_bytes()) != s["ocr_sha256"]:
                raise ValueError(f"OCR changed before scripture scan: {path}")
            scan = scan_file_task(ScanTask(volume_id=s["documento"], collection=s["documento"][:2],
                source_root=str(path.parent.parent), file_path=str(path), physical_index=s["pagina_num"],
                is_index_source=s["primary_page_kind"] == "index", observed_aliases=(), estimator_pages=(),
                profile_fingerprint="kg-benchmark-frozen-ocr-v1"))
            record["metadata"]["scripture_rescan"] = {
                "detector_version": DETECTOR_VERSION, "groups": scan["groups"],
                "editorial_page_candidates": scan["pages"],
                "warning": "Deterministic reference candidates only. Offsets are in detector-normalized OCR block text, not raw page offsets. No exegetical relation is inferred."}
    pair_by_id = {p["pair_id"]: p for p in pairs}
    rejected = []
    for annotation in curation.get("pairs", []):
        pair = pair_by_id[annotation["pair_id"]]
        actual_ids = sorted(o["generation_id"] for o in pair["occurrences"])
        expected_ids = annotation.get("expected_generation_ids")
        if expected_ids is not None and actual_ids != sorted(expected_ids):
            raise ValueError(f"Curated pair changed: {pair['pair_id']}; expected {expected_ids}, got {actual_ids}")
        if pair["split"] == "blind_holdout":
            raise ValueError("Holdout pair annotation prohibited during development")
        if annotation.get("reject"):
            rejected.append({**pair, "rejection_reason": annotation["reason"]})
            pairs.remove(pair)
            continue
        for support in annotation.get("ocr_evidence", []):
            if support["generation_id"] not in actual_ids:
                raise ValueError("Pair evidence belongs to an unrelated generation")
            r = by_generation[support["generation_id"]]
            raw = read_ocr(args.output_dir / r["source"]["ocr_snapshot"])
            if raw[support["start"]:support["end"]] != support["quote"]:
                raise ValueError("Pair OCR evidence is not exact")
        pair["provisional_relationship"] = annotation["relationship"]
        pair["relationship_basis"] = annotation["reason"]
        pair["ocr_evidence"] = annotation.get("ocr_evidence", [])
        pair["review"]["status"] = "source_inspected_pending_independent_review"
    for pair in curation.get("new_pairs", []):
        if pair["split"] == "blind_holdout" or pair["pair_id"] in pair_by_id:
            raise ValueError("New curated pairs must have unique IDs and exclude holdout")
        pairs.append(pair)
    write_jsonl(args.output_dir / "selected_pages.jsonl", records)
    write_jsonl(args.output_dir / "resolution_pairs.jsonl", pairs)
    write_jsonl(args.output_dir / "rejected_resolution_pairs.jsonl", rejected)
    design = read_json(args.output_dir / "sample_design.json")
    design["distribution"] = distributions(records)
    design["pair_count"] = len(pairs)
    design["rejected_pair_count"] = len(rejected)
    design["curation_file"] = str(args.curation) if args.curation else None
    design["curation_sha256"] = digest(args.curation.read_bytes()) if args.curation else None
    write_json(args.output_dir / "sample_design.json", design)
    print(f"Applied {len(curation['pages'])} source inspections; retained {len(pairs)} pairs ({len(rejected)} rejected)")


if __name__ == "__main__":
    main()
