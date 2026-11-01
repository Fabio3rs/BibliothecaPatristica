#!/usr/bin/env python3
"""Validate frozen provenance, exact evidence, duplicate removal and split isolation."""
from __future__ import annotations

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.knowledge_graph.benchmarks.common import (
    FIELDS, DuplicateIndex, common_arguments, digest, physical_key, read_json,
    read_jsonl, readonly, resolve_source, sha256_text, work_keys, write_json,
)
from tools.knowledge_graph.benchmarks.select_samples import work_families


def validate_record(record, output_dir, corpus_root, con=None):
    errors, warnings = [], []
    s, t, e = record["source"], record["text"], record["evidence"]
    snapshot_path = output_dir / s["generation_snapshot"]
    if not snapshot_path.is_file():
        return ["missing_generation_snapshot"], warnings
    if digest(snapshot_path.read_bytes()) != s["generation_snapshot_sha256"]:
        errors.append("generation_snapshot_hash_changed")
    snapshot = read_json(snapshot_path)
    import json
    segments = json.loads(snapshot["segments_json"])
    recomputed = sha256_text(snapshot["summary_display_pt"], snapshot["cumulative_summary"],
                             json.dumps(segments, ensure_ascii=False, sort_keys=True))
    if recomputed != snapshot["output_hash"]:
        errors.append("generation_output_hash_mismatch")
    focal = [v for v in segments if v.get("order") == s["segment_order"]]
    if len(focal) != 1 or focal[0]["summary"] != t["segment_summary"]:
        errors.append("segment_not_in_generation")
    if t["page_segments"] != segments:
        errors.append("page_segments_changed")
    if not e["summary_quote"] or e["summary_quote"] not in t["segment_summary"]:
        errors.append("summary_quote_not_exact")
    if digest(t["segment_summary"]) != s["summary_hash"]:
        errors.append("summary_hash_changed")
    for field in ("source_hash", "output_hash", "documento", "pagina_num", "pagina_file", "run_id", "status", "is_current", "previous_generation_id"):
        if s[field] != snapshot[field]:
            errors.append(f"snapshot_field_changed:{field}")
    if s["generation_id"] != snapshot["id"]:
        errors.append("generation_id_changed")
    if s["scope"] != ("promoted" if s["is_current"] else "shadow"):
        errors.append("scope_mismatch")
    if s["status"] not in {"valid", "metadata_pending"} or s["tainted_by_page"] is not None:
        errors.append("ineligible_generation")
    if con is not None:
        live = con.execute(f"SELECT {','.join(FIELDS)} FROM resumo_generations WHERE id=?", (s["generation_id"],)).fetchone()
        if live is None or dict(live) != snapshot:
            errors.append("generation_changed_since_snapshot")
    raw_path = output_dir / s["ocr_snapshot"]
    if not raw_path.is_file():
        errors.append("missing_ocr_snapshot")
        return errors, warnings
    raw_bytes = raw_path.read_bytes()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        if e["ocr_support"] != "unavailable":
            errors.append("invalid_utf8_not_flagged")
        return errors, warnings
    if s["ocr_sha256"] is not None and digest(raw_bytes) != s["ocr_sha256"]:
        errors.append("ocr_snapshot_hash_changed")
    if e["ocr_quote"] != raw[e["ocr_start"]:e["ocr_end"]]:
        errors.append("ocr_quote_not_exact_at_offsets")
    if t["ocr_excerpt"] not in raw:
        errors.append("ocr_excerpt_not_exact")
    path, _ = resolve_source(corpus_root, s)
    if path:
        if s["ocr_sha256"] is not None and digest(path.read_bytes()) != s["ocr_sha256"]:
            errors.append("current_ocr_hash_changed")
    elif e["ocr_support"] != "unavailable":
        warnings.append("current_ocr_unavailable_frozen_snapshot_retained")
    if e["ocr_support"] not in {"verified", "uncertain", "unavailable", "contradictory"}:
        errors.append("unknown_ocr_support")
    if e["ocr_support"] == "verified" and e["inspection"] == "mechanical_locator_and_hash_checks_only":
        errors.append("mechanical_check_cannot_verify_semantics")
    if record["review"].get("gold_status") != "not_gold":
        errors.append("unreviewed_gold_claim")
    return errors, warnings


def validate_splits(records):
    errors = []
    seen, hashes = {}, {}
    summary_dups = DuplicateIndex()
    scopes = {r["source"]["scope"] for r in records}
    if len(scopes) > 1:
        errors.append("mixed_promoted_and_shadow_scopes")
    for r in records:
        key = physical_key(r)
        if key in seen:
            errors.append(f"duplicate_page:{key}")
        seen[key] = r["split"]
        h = r["source"]["ocr_sha256"]
        if h and h in hashes:
            errors.append(f"duplicate_ocr:{r.get('benchmark_id')}")
        hashes[h] = r.get("benchmark_id")
        dup = summary_dups.check_add(r["candidate_id"], " ".join(s["summary"] for s in r["text"]["page_segments"]))
        if dup:
            errors.append(f"near_duplicate_summary:{r['candidate_id']}:{dup[0]}")
    held = [r for r in records if r["split"] == "blind_holdout"]
    dev = [r for r in records if r["split"] != "blind_holdout"]
    for key, getter in (("volumes", lambda r: {r["source"]["documento"]}),
                        ("works", work_keys), ("work_families", work_families)):
        a = set().union(*(getter(r) for r in held)) if held else set()
        b = set().union(*(getter(r) for r in dev)) if dev else set()
        if a & b:
            errors.append(f"holdout_leakage:{key}")
    return errors


def validate_pairs(pairs, records, output_dir=None):
    by_id = {r["benchmark_id"]: r for r in records}
    errors = []
    for pair in pairs:
        for occurrence in pair["occurrences"]:
            r = by_id.get(occurrence["benchmark_id"])
            if r is None:
                errors.append(f"pair_missing_page:{pair['pair_id']}")
                continue
            if pair["split"] != r["split"]:
                errors.append(f"pair_crosses_split:{pair['pair_id']}")
            segments = [s for s in r["text"]["page_segments"] if s.get("order") == occurrence["segment_order"]]
            if len(segments) != 1:
                errors.append(f"pair_missing_segment:{pair['pair_id']}")
            else:
                summary = segments[0]["summary"]
                if summary[occurrence["summary_start"]:occurrence["summary_end"]] != occurrence["summary_quote"]:
                    errors.append(f"pair_quote_not_exact:{pair['pair_id']}")
                if summary[occurrence["surface_start"]:occurrence["surface_end"]] != occurrence["surface"]:
                    errors.append(f"pair_surface_not_exact:{pair['pair_id']}")
        if pair["provisional_relationship"] not in {"EQUIVALENT", "BROADER", "NARROWER", "RELATED", "DIFFERENT", "UNCERTAIN"}:
            errors.append(f"invalid_pair_relationship:{pair['pair_id']}")
        if output_dir is not None:
            by_generation = {r["source"]["generation_id"]: r for r in records}
            for evidence in pair.get("ocr_evidence", []):
                if evidence["generation_id"] not in {o["generation_id"] for o in pair["occurrences"]}:
                    errors.append(f"pair_evidence_wrong_occurrence:{pair['pair_id']}")
                r = by_generation.get(evidence["generation_id"])
                if r is None:
                    errors.append(f"pair_evidence_missing_generation:{pair['pair_id']}")
                    continue
                raw = (output_dir / r["source"]["ocr_snapshot"]).read_bytes().decode("utf-8")
                if raw[evidence["start"]:evidence["end"]] != evidence["quote"]:
                    errors.append(f"pair_ocr_quote_not_exact:{pair['pair_id']}")
    return errors


def main():
    p = common_arguments(__doc__)
    p.add_argument("--check-db", action="store_true", help="Also compare frozen generation fields against live read-only SQLite")
    args = p.parse_args()
    records = list(read_jsonl(args.output_dir / "selected_pages.jsonl"))
    pairs = list(read_jsonl(args.output_dir / "resolution_pairs.jsonl"))
    con = readonly(args.db) if args.check_db else None
    errors, warnings = validate_splits(records), []
    for record in records:
        bad, warn = validate_record(record, args.output_dir, args.corpus_root, con)
        errors.extend(f"{record['benchmark_id']}:{x}" for x in bad)
        warnings.extend(f"{record['benchmark_id']}:{x}" for x in warn)
    errors.extend(validate_pairs(pairs, records, args.output_dir))
    if con:
        con.close()
    report = {"checked_pages": len(records), "checked_pairs": len(pairs), "live_db_checked": args.check_db,
              "errors": errors, "warnings": warnings,
              "semantic_validation": "Exact spans, hashes and isolation only; no claim of verified gold or historical truth."}
    write_json(args.output_dir / "validation_report.json", report)
    print(f"Validated {len(records)} pages and {len(pairs)} pairs: {len(errors)} errors, {len(warnings)} warnings")
    if errors:
        print("\n".join(errors[:20]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
