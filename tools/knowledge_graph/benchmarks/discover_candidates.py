#!/usr/bin/env python3
"""Profile summary-v2 and retrieve a bounded, deterministic discovery pool."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import heapq
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.knowledge_graph.benchmarks.common import (
    ROOT, VERSION, FIELDS, TERMS, DuplicateIndex, classify, common_arguments,
    digest, distributions, eligibility, excerpt, parse_generation, rank, read_json,
    readonly, resolve_source, sha256_text, stable_json, summary_quote,
    validate_schema, write_json, write_jsonl,
)
from tools.ocr_xml_utils import parse_ocr_xml_page


class Reservoirs:
    def __init__(self, seed, capacity):
        self.seed, self.capacity = seed, capacity
        self.buckets = defaultdict(list)

    def add(self, key, row, capacity=None):
        capacity = capacity or self.capacity
        heap = self.buckets[key]
        score = rank(self.seed, f"{key}:{row['id']}")
        item = (-score, row["id"], row)
        if len(heap) < capacity:
            heapq.heappush(heap, item)
        elif item[:2] > heap[0][:2]:
            heapq.heapreplace(heap, item)

    def rows(self):
        return {item[1]: item[2] for heap in self.buckets.values() for item in heap}.values()


def profile_and_retrieve(con, where, params, seed, pool_size):
    counts = defaultdict(Counter)
    reservoirs = Reservoirs(seed, max(64, pool_size // 12))
    total, malformed = 0, []
    snapshot_digest = hashlib.sha256()
    for dbrow in con.execute(f"SELECT {','.join(FIELDS)} FROM resumo_generations WHERE {where} ORDER BY id", params):
        total += 1
        snapshot_digest.update((stable_json(dict(dbrow)) + "\n").encode())
        try:
            r = parse_generation(dbrow)
        except (ValueError, TypeError) as exc:
            malformed.append({"id": dbrow["id"], "error": str(exc)})
            continue
        a = r["static_analysis"]
        collection = r["documento"][:2]
        counts["collections"][collection] += 1
        counts["volumes"][r["documento"]] += 1
        counts["statuses"][r["status"]] += 1
        counts["page_kinds"][r["primary_page_kind"]] += 1
        counts["segments_per_page"][len(r["segments"])] += 1
        length = sum(len(s["summary"]) for s in r["segments"])
        counts["summary_length_chars"]["<300" if length < 300 else "300-999" if length < 1000 else "1000-1999" if length < 2000 else "2000-3999" if length < 4000 else ">=4000"] += 1
        counts["scripture_reference_candidates"][str(a.get("scripture_candidate_count", "unknown"))] += 1
        counts["metadata_conflicts"]["present" if r["source_conflicts"] else "absent"] += 1
        for field in ("mixed_scripts", "is_empty", "is_xml", "index_ambiguous", "column_count"):
            counts[field][str(a.get(field, "unknown"))] += 1
        for field, cutoff in (("alpha_ratio", .65), ("single_token_ratio", .12)):
            counts[field]["unknown" if field not in a else f"<{cutoff}" if a[field] < cutoff else f">={cutoff}"] += 1
        for key in {s.get("work_key") for s in r["segments"] if s.get("work_key")}:
            counts["works"][key] += 1
        counts["work_key_presence"]["present" if any(s.get("work_key") for s in r["segments"]) else "absent"] += 1
        for author in {h.get("author_original") for h in a.get("containing_work_candidates", []) if h.get("author_original")}:
            counts["authors_from_generation_index_hints"][author] += 1
        categories, _, risks = classify(r)
        counts["heuristic_categories"].update(categories)
        counts["risk_flags"].update(risks)
        for category in categories:
            reservoirs.add(("category", category, collection, r["status"]), r)
        reservoirs.add(("kind", collection, r["primary_page_kind"]), r, 18)
        reservoirs.add(("volume", r["documento"]), r, 3)
        summary = " ".join(s["summary"] for s in r["segments"])
        for term, pattern in TERMS.items():
            if pattern.search(summary):
                counts["resolution_terms"][term] += 1
                reservoirs.add(("term", term, collection), r, 20)
    return {
        "eligible_generations": total, "malformed_generations": malformed,
        "eligible_rows_sha256": snapshot_digest.hexdigest(),
        "distributions": {k: dict(sorted(v.items(), key=lambda x: str(x[0]))) for k, v in counts.items()},
    }, list(reservoirs.rows())


def interleave_shortlist(rows, seed):
    """Round-robin volumes and categories; quality strata remain explicit."""
    buckets = defaultdict(list)
    for r in rows:
        categories, _, _ = classify(r)
        category = min(categories, key=lambda c: rank(seed, f"{r['id']}:{c}"))
        buckets[(r["documento"], category, r["status"])].append(r)
    for b in buckets.values():
        b.sort(key=lambda r: rank(seed, r["id"]))
    keys = sorted(buckets, key=lambda k: rank(seed, k))
    while keys:
        next_keys = []
        for key in keys:
            yield buckets[key].pop()
            if buckets[key]:
                next_keys.append(key)
        keys = next_keys


def make_record(r, corpus_root, output_dir, versions, indices):
    categories, hits, risks = classify(r)
    segments = r["segments"]
    if not segments:
        return None, {"generation_id": r["id"], "reason": "no_segments"}
    segment = max(segments, key=lambda s: len(s["summary"]))
    summary = segment["summary"]
    path, locator_status = resolve_source(corpus_root, r)
    raw, raw_bytes, error = "", b"", None
    if path:
        try:
            raw_bytes = path.read_bytes()
            raw = raw_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            error = str(exc)
    page = parse_ocr_xml_page(raw)
    historical = None
    if versions is not None and r["ocr_result_id"] is not None:
        old = versions.execute("SELECT volume_id,page_num,text_hash,text_content FROM ocr_results WHERE id=?", (r["ocr_result_id"],)).fetchone()
        if old:
            historical = {
                "text_hash": old["text_hash"], "text_sha256": digest(old["text_content"]),
                "locator_matches": old["volume_id"] == r["documento"] and old["page_num"] == r["pagina_num"],
                "text_matches_current": old["text_content"] == raw,
            }
    historical_matches = historical is not None and historical["locator_matches"] and historical["text_matches_current"]
    if locator_status.startswith("numeric_fallback") and not historical_matches:
        error = "Numeric fallback not proven identical to generation's historical OCR"
    if historical and not historical_matches:
        risks.append("ocr_changed_since_generation")
    if page.is_xml and not page.parse_ok:
        risks.append("malformed_xml")
    output_recomputed = sha256_text(r["summary_display_pt"], r["cumulative_summary"], json.dumps(segments, ensure_ascii=False, sort_keys=True))
    if output_recomputed != r["output_hash"]:
        risks.append("generation_output_hash_mismatch")
    window, start, end = excerpt(raw, summary)
    snapshot_name = f"sources/generation_{r['id']}.json"
    raw_name = f"sources/ocr_{r['id']}.txt"
    snapshot = {k: r[k] for k in FIELDS}
    write_json(output_dir / snapshot_name, snapshot)
    (output_dir / raw_name).write_bytes(raw_bytes)
    works = []
    if indices is not None:
        for key in sorted({s.get("work_key") for s in segments if s.get("work_key")}):
            work = indices.execute("SELECT work_key,volume_id,author_raw,title_raw,start_page,end_page,start_file,end_file,confidence FROM works WHERE work_key=?", (key,)).fetchone()
            if work:
                works.append(dict(work))
    source = {k: r[k] for k in FIELDS if k not in {"id", "segments_json", "contributors_json", "static_analysis_json", "page_kinds_json", "validation_issues_json", "source_conflicts_json", "summary_display_pt", "cumulative_summary"}}
    try:
        ocr_path = str(path.resolve().relative_to(ROOT)) if path else None
    except ValueError:
        ocr_path = str(path.resolve())
    source.update({
        "generation_id": r["id"], "scope": "promoted" if r["is_current"] else "shadow",
        "segment_order": segment.get("order"), "work_key": segment.get("work_key") or None,
        "summary_hash": digest(summary), "page_segments_sha256": digest(r["segments_json"]),
        "ocr_path": ocr_path, "ocr_sha256": digest(raw_bytes) if path and not error else None,
        "locator_status": locator_status, "generation_snapshot": snapshot_name,
        "generation_snapshot_sha256": digest((output_dir / snapshot_name).read_bytes()),
        "ocr_snapshot": raw_name, "historical_ocr_check": historical,
        "output_hash_matches": output_recomputed == r["output_hash"],
        "source_hash_verification": "opaque_composite_preserved; original index/scripture inputs not fully persisted",
        "physical_page_semantics": "numeric filename suffix; not editorial pagination or Migne columns",
    })
    notes = ["Retrieval labels are hypotheses. Portuguese summary evidence is separate from original OCR.",
             "No semantic OCR support is inferred from lexical overlap."]
    if not historical:
        notes.append("Historical OCR content unavailable; current OCR cannot be proved identical to generation input.")
    if error:
        notes.append(error)
    record = {
        "candidate_id": f"KG-CAND-{r['id']:06d}", "split": None,
        "source": source,
        "text": {"segment_summary": summary, "page_segments": segments,
                 "summary_display_pt": r["summary_display_pt"], "context_excerpt": "",
                 "ocr_excerpt": window},
        "context": {"cumulative_summary": r["cumulative_summary"],
                    "role": "context_only_never_current_page_evidence",
                    "previous_generation_id": r["previous_generation_id"]},
        "metadata": {"works": works, "generation_index_hints": r["static_analysis"].get("containing_work_candidates", []),
                     "contributors": r["contributors"], "source_conflicts": r["source_conflicts"],
                     "validation_issues": r["validation_issues"], "static_analysis": r["static_analysis"],
                     "ocr_parser": {"is_xml": page.is_xml, "parse_ok": page.parse_ok, "repairs": page.repairs},
                     "ocr_blocks": [{"index": i, "tag": b.tag_name, "type": b.tipo, "script": b.script, "bbox": b.bbox} for i, b in enumerate(page.blocks)]},
        "selection": {"categories": categories, "difficulty": "hard" if risks else "standard",
                      "reason": "Candidate for " + ", ".join(categories) + "; retrieved from local v2 segments/structural metadata.",
                      "lexical_hits": hits, "risk_flags": risks,
                      "eligible_for_selection": bool(path and raw.strip() and not error and source["output_hash_matches"] and not (historical and not historical_matches))},
        "evidence": {"summary_quote": summary_quote(summary), "ocr_support": "unavailable" if error or not raw else "uncertain",
                     "ocr_quote": window, "ocr_start": start, "ocr_end": end,
                     "offset_unit": "Unicode code points in UTF-8 decoded raw OCR snapshot",
                     "notes": " ".join(notes), "inspection": "mechanical_locator_and_hash_checks_only"},
        "review": {"status": "pending", "gold_status": "not_gold", "expected_nodes": [], "expected_relations": [],
                   "ambiguities": risks.copy(), "questions": [
                       "Which assertions belong to the author, a quoted opponent, or an editor?",
                       "Does the current-page OCR support the summary, or is the fact only in cumulative context?",
                       "Which retrieved labels should be rejected? State exact supporting/contradicting OCR spans."]},
        "extraction_version": VERSION,
    }
    return record, page.body_text


def run(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_ids = sorted(set(read_json(args.shadow_run_ids_file))) if args.shadow_run_ids_file else []
    con = readonly(args.db)
    schema = validate_schema(con)
    con.execute("BEGIN")
    where, params = eligibility(args.scope, run_ids)
    status = [dict(r) for r in con.execute("SELECT status,is_current,tainted_by_page IS NOT NULL AS tainted,count(*) AS total FROM resumo_generations GROUP BY 1,2,3")]
    print("Profiling eligible v2 generations (streaming, no embeddings or legacy summaries)...", flush=True)
    profile, shortlist = profile_and_retrieve(con, where, params, args.seed, args.pool_size)
    con.close()
    profile.update({"schema": schema, "database": str(args.db.resolve()), "database_size_bytes": args.db.stat().st_size,
                    "snapshot_at": datetime.now(timezone.utc).isoformat(), "all_generation_statuses": status,
                    "scope": args.scope, "selected_shadow_run_ids": run_ids, "eligibility_sql": where,
                    "promoted_eligible": sum(r["total"] for r in status if r["is_current"] and not r["tainted"] and r["status"] in {"valid", "metadata_pending"}),
                    "seed": args.seed, "shortlist_size": len(shortlist), "version": VERSION,
                    "periods": "Not assigned automatically; dates in summaries can describe editors, narrated events or authors. Requires review.",
                    "scripture_counts": "Stored deterministic candidates from summary generation; not verified intertextual relations."})
    print(f"Eligible={profile['eligible_generations']}; bounded shortlist={len(shortlist)}", flush=True)
    versions = readonly(args.ocr_versions_db) if args.ocr_versions_db.is_file() else None
    indices = readonly(args.indices_db) if args.indices_db.is_file() else None
    profile["auxiliary_sources"] = {
        "indices_db": str(args.indices_db.resolve()), "indices_available": indices is not None,
        "index_work_rows": indices.execute("SELECT count(*) FROM works").fetchone()[0] if indices else None,
        "ocr_versions_db": str(args.ocr_versions_db.resolve()), "ocr_versions_available": versions is not None,
    }
    records, rejected, physical, raw_hashes = [], [], set(), {}
    summary_dups, ocr_dups = DuplicateIndex(), DuplicateIndex()
    for r in interleave_shortlist(shortlist, args.seed):
        key = (r["documento"], r["pagina_num"])
        if key in physical:
            rejected.append({"generation_id": r["id"], "reason": "same_physical_page"})
            continue
        summary = " ".join(s["summary"] for s in r["segments"])
        dup = summary_dups.check_add(r["id"], summary)
        if dup:
            rejected.append({"generation_id": r["id"], "reason": "summary_duplicate", "duplicate_of": dup[0], "jaccard": dup[1]})
            continue
        record, body = make_record(r, args.corpus_root, args.output_dir, versions, indices)
        if record is None:
            rejected.append(body)
            continue
        raw_hash = record["source"]["ocr_sha256"]
        if raw_hash and raw_hash in raw_hashes:
            rejected.append({"generation_id": r["id"], "reason": "exact_ocr_duplicate", "duplicate_of": raw_hashes[raw_hash]})
            continue
        dup = ocr_dups.check_add(r["id"], body) if body.strip() else None
        if dup:
            rejected.append({"generation_id": r["id"], "reason": "ocr_body_duplicate", "duplicate_of": dup[0], "jaccard": dup[1]})
            continue
        physical.add(key)
        if raw_hash:
            raw_hashes[raw_hash] = r["id"]
        records.append(record)
        if len(records) % 100 == 0:
            print(f"Loaded {len(records)} candidate OCR pages", flush=True)
        if len(records) >= args.pool_size:
            break
    for connection in (versions, indices):
        if connection:
            connection.close()
    records.sort(key=lambda r: r["source"]["generation_id"])
    profile["candidate_distribution"] = distributions(records)
    profile["candidate_count"] = len(records)
    profile["rejected_counts"] = dict(Counter(r["reason"] for r in rejected))
    write_json(args.output_dir / "corpus_profile.json", profile)
    write_jsonl(args.output_dir / "candidate_pool.jsonl", records)
    write_jsonl(args.output_dir / "rejected_candidates.jsonl", rejected)
    write_json(args.output_dir / "discovery_config.json", {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})
    print(f"Wrote {len(records)} candidates; {len(rejected)} rejected; {sum(r['selection']['eligible_for_selection'] for r in records)} selection-eligible", flush=True)


def main():
    p = common_arguments(__doc__)
    p.add_argument("--pool-size", type=int, default=1100)
    p.add_argument("--scope", choices=("promoted", "shadow"), default="promoted")
    p.add_argument("--shadow-run-ids-file", type=Path)
    p.add_argument("--ocr-versions-db", type=Path, default=ROOT / "data/ocr_versions.db")
    p.add_argument("--indices-db", type=Path, default=ROOT / "data/patristic_indices.db")
    args = p.parse_args()
    if args.pool_size <= 0:
        p.error("--pool-size must be positive")
    run(args)


if __name__ == "__main__":
    main()
