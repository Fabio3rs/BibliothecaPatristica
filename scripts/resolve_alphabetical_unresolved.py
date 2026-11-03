#!/usr/bin/env python3
"""Resolve alphabetical material references without an API or agent.

Dry-run (one process per volume, OCR loaded once per task):
  python scripts/resolve_alphabetical_unresolved.py --volume PG110 --volume PL048 \
    --workers 2 --limit-per-volume 12 --report-dir /tmp/alpha-locator-pilot
Full dry-run, resumable from matching per-volume reports:
  python scripts/resolve_alphabetical_unresolved.py --all --workers 4 \
    --report-dir data/alphabetical_locator_run
Apply those exact proposals later, without running the locator again:
  python scripts/resolve_alphabetical_unresolved.py \
    --apply-report data/alphabetical_locator_run

Only unresolved material references in owned alphabetical sections are selected.
Scripture links use the separate scripture reconciler. --include-unverified also
selects unverified references with no target. Existing targets are never replaced.
Reports retain rejected candidates. Approval requires body text plus a literal
header number or pagination validated by observed headers on both sides.
Ranges target their printed START only, without expanding intermediate pages.
All literal-header and validated-sequence candidates are checked by OCR file. Multiple
occurrences in one file are not ambiguous; multiple viable files remain pending.
Paragraphs/lines are not resolved. The existing multi-range locator runs by default,
using persistent result and editorial-estimator caches. Ranked candidates remain
separate from validated proposals; scores are not calibrated probabilities.
--search-mode literal skips multi-range ranking for a header-only comparison.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.corpus_utils import discover_preferred_pages, page_number
from tools.indexing.alphabetical_compact_driver import _helper_request, _search_queries
from tools.indexing.index_target_locator import (
    normalize_for_search,
    _best_phrase_similarity,
    parse_ocr_page_path,
    resolve_index_targets,
)
from tools.indexing import editorial_page_estimator
from scripts.alphabetical_index_db import refresh_volume_quality

VERSION = 3
DEFAULT_DB = ROOT / "data/alphabetical_indices.db"
DEFAULT_CACHE_DIR = ROOT / "data/alphabetical_locator_cache"
BODY_EVIDENCE = {"body_name_match", "body_name_fuzzy", "body_context_exact"}
EDITORIAL_KINDS = {"editorial_page", "editorial_column", "editorial_page_column",
                   "editorial_page_line", "editorial_range"}
QUERY = """
SELECT r.*, e.lemma_raw, e.lemma_display, e.entry_raw, e.context_raw,
       e.entry_order, e.section_key, e.entry_kind,
       s.section_kind, s.heading_raw AS section_heading,
       COALESCE(s.index_ocr_file_start, s.file_start) AS section_file_start,
       COALESCE(s.index_ocr_file_end, s.file_end) AS section_file_end,
       s.pipeline_owner, s.alphabetical_role, s.material_reference_mode,
       s.volume_id
FROM alphabetical_refs r
JOIN alphabetical_entries e USING(entry_key)
JOIN alphabetical_sections s USING(section_key)
WHERE s.volume_id = ?
ORDER BY s.section_order, e.entry_order, r.ref_order, r.ref_id
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def connect_readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def code_fingerprint():
    paths = [Path(__file__), ROOT / "tools/corpus_utils.py", ROOT / "tools/ocr_xml_utils.py"]
    paths.extend(sorted((ROOT / "tools/indexing").glob("*.py")))
    try:
        fuzzy_version = package_version("rapidfuzz")
    except PackageNotFoundError:
        fuzzy_version = "python_fallback"
    return digest({"files": [(str(p.relative_to(ROOT)), hashlib.sha256(p.read_bytes()).hexdigest())
                             for p in paths],
                   "python": sys.version, "rapidfuzz": fuzzy_version})


def source_snapshot(root):
    files = discover_preferred_pages(Path(root), volume_id=Path(root).parent.name)
    if not files:
        raise ValueError(f"No OCR pages found: {root}")
    return [(str(p.resolve()), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]


def source_stats(root):
    return [(str(p), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for p in discover_preferred_pages(Path(root), volume_id=Path(root).parent.name)]


def is_eligible(row, include_unverified=False):
    statuses = {"unresolved", "unverified"} if include_unverified else {"unresolved"}
    return (row["locator_status"] in statuses
            and not str(row["target_file"] or "").strip()
            and not str(row["resolved_target_ocr_file"] or "").strip()
            and row["pipeline_owner"] == "alphabetical"
            and row["alphabetical_role"] == "owned_section"
            and row["material_reference_mode"] != "source_only"
            and row["scripture_ref_order"] is None
            and row["ref_kind"] != "scripture")


def positive_int(value):
    text = str(value or "").strip()
    return int(text) if re.fullmatch(r"[0-9]{1,4}", text) and 0 < int(text) < 10000 else None


def cited_start(row):
    for field in ("cited_editorial_page_start_number", "range_start_raw", "page_ref_int"):
        number = positive_int(row.get(field))
        if number is not None:
            return number
    # Never split a compound page/line or book/chapter notation into page numbers.
    return positive_int(row.get("page_ref_raw"))


def absolute_source(value):
    p = Path(value).expanduser()
    return str(p.resolve() if p.is_absolute() else (ROOT / p).resolve())


def load_job(con, volume_id, limit=0, include_unverified=False, section_kinds=(), repaired_only=False):
    volume = con.execute("SELECT * FROM alphabetical_volumes WHERE volume_id=?",
                         (volume_id,)).fetchone()
    if volume is None:
        raise ValueError(f"Unknown volume: {volume_id}")
    rows = [dict(r) for r in con.execute(QUERY, (volume_id,))]
    eligible = [r for r in rows if is_eligible(r, include_unverified)
                and (not section_kinds or r["section_kind"] in section_kinds)]
    if repaired_only:
        eligible = [r for r in eligible if ":repair:" in r["entry_key"]]
    # Spread the pilot across the ordered references, instead of taking one lemma's refs.
    if limit and len(eligible) > limit:
        selected = [eligible[i * (len(eligible) - 1) // max(1, limit - 1)]
                    for i in range(limit)]
    else:
        selected = eligible
    intervals = [dict(r) for r in con.execute("""
        SELECT section_key,
               COALESCE(index_ocr_file_start,file_start) AS index_ocr_file_start,
               COALESCE(index_ocr_file_end,file_end) AS index_ocr_file_end
        FROM alphabetical_sections WHERE volume_id=? ORDER BY section_key
        """, (volume_id,))]
    return {"volume_id": volume_id, "source_root": absolute_source(volume["source_root"]),
            "collection": volume["collection"], "eligible_count": len(eligible),
            "rows": selected, "intervals": intervals,
            "include_unverified": include_unverified, "section_kinds": list(section_kinds),
            "limit_per_volume": limit, **({"repaired_only": True} if repaired_only else {})}


def make_items(job):
    items = []
    for row in job["rows"]:
        number = cited_start(row)
        items.append({**row, "locator_key": str(row["ref_id"]),
                      "entry_excerpt": row["entry_raw"],
                      "cited_pages": [number] if number else [],
                      "excluded_index_intervals": job["intervals"], "candidates": []})
    return items


def name_head_queries(row):
    """Separate a Latin proper-name head from an unpunctuated editorial gloss."""
    if not row["section_kind"].startswith("onomastic_"):
        return []
    words = str(row["lemma_raw"] or "").split()
    head = []
    for word in words[:3]:
        if not re.fullmatch(r"[A-ZÆŒ][A-Za-zÀ-ÖØ-öø-ÿÆŒæœ]+", word):
            break
        head.append(word)
    if not head or len(head[0]) < 5 or len(head) == len(words):
        return []
    return _search_queries({"lemma_raw": " ".join(head), "section_kind": row["section_kind"]})


def is_index_target(path, intervals):
    number = page_number(Path(path))
    for interval in intervals:
        start = interval.get("index_ocr_file_start")
        end = interval.get("index_ocr_file_end")
        if path in (start, end):
            return True
        a = page_number(Path(start)) if start else None
        b = page_number(Path(end)) if end else None
        if number is not None and a is not None and b is not None and min(a, b) <= number <= max(a, b):
            return True
    return False


def body_matches(parsed, evidence):
    body = normalize_for_search(parsed["body_text"])
    matches = []
    for item in evidence:
        if item.get("kind") not in BODY_EVIDENCE:
            continue
        raw = item.get("raw") or item.get("detail") or ""
        query, matched, similarity = raw, raw, 1.0
        if item["kind"] == "body_name_fuzzy":
            match = re.fullmatch(r"(.*?) => (.*?) \(levenshtein_similarity=([0-9.]+)\)", raw)
            if not match:
                continue
            query, matched, similarity_raw = match.groups()
            similarity = float(similarity_raw)
            if similarity < 0.92:
                continue
        normalized = normalize_for_search(matched)
        position = (" " + body + " ").find(" " + normalized + " ") if normalized else -1
        if position < 0:
            continue
        matches.append({"query": query, "matched": matched, "similarity": similarity,
                        "normalized_excerpt": body[max(0, position - 180):position + len(normalized) + 300]})
    return matches


def header_file_index(sources):
    by_number = {}
    for path, _ in sources:
        parsed = parse_ocr_page_path(Path(path))
        for number in set(re.findall(r"\b[0-9]{1,4}\b", parsed["header_text"])):
            by_number.setdefault(int(number), []).append(path)
    return by_number


def search_literal_files(row, headers, ranked_candidates, editorial_map=None):
    """Check all literal and neighbor-validated files, independent of top-k ranking."""
    blocked = repair_block_reason(row)
    if blocked:
        return {"complete_for_literal_headers": False, "queries": [], "candidates": [],
                "blocked_reason": blocked}
    queries = list(dict.fromkeys([*_search_queries(row), *name_head_queries(row)]))
    ranked = {str(Path(c["file"]).resolve()): c for c in ranked_candidates}
    candidates = []
    editorial_map = editorial_map or {}
    paths = list(dict.fromkeys([*headers.get(cited_start(row), []),
                               *(path for path, proof in editorial_map.items()
                                 if proof.get("validated") and cited_start(row) in proof["pages"])]))
    for path in paths:
        body = normalize_for_search(parse_ocr_page_path(Path(path))["body_text"])
        evidence = []
        for query in queries:
            normalized = normalize_for_search(query)
            if not normalized:
                continue
            if " " + normalized + " " in " " + body + " ":
                evidence.append({"kind": "body_name_match", "raw": query, "weight": 1.0})
            else:
                similarity, matched = _best_phrase_similarity(normalized, body)
                if similarity >= .92:
                    evidence.append({"kind": "body_name_fuzzy", "weight": similarity,
                                     "raw": f"{query} => {matched} (levenshtein_similarity={similarity:.4f})"})
        candidates.append({"file": path, "candidate_role": "target_candidate",
                           "probability": ranked.get(path, {}).get("probability"),
                           **({"sequence_validation": editorial_map[path]} if path in editorial_map else {}),
                           "evidence": evidence})
    return {"complete_for_literal_headers": True, "queries": queries, "candidates": candidates}


def validate_file_candidate(row, best, job):
    target = Path(best["file"]).resolve()
    if not target.is_relative_to(Path(job["source_root"]).resolve()) or not target.is_file():
        return None, "target_outside_volume_or_missing"
    if best.get("candidate_role") != "target_candidate" or is_index_target(str(target), job["intervals"]):
        return None, "index_or_non_target"
    if row["ref_kind"] not in EDITORIAL_KINDS or cited_start(row) is None:
        return None, "internal_or_unparsed_locator_requires_review"
    printed_start = re.match(r"\s*(?:(?:col(?:umn)?|p(?:ag)?)\.?\s*)?([0-9]{1,4})(?![0-9])",
                             row["ref_raw"], flags=re.IGNORECASE)
    if printed_start and int(printed_start[1]) != cited_start(row):
        return None, "printed_numeric_start_conflict"
    evidence = best.get("evidence") or []
    if not BODY_EVIDENCE.intersection(e["kind"] for e in evidence):
        return None, "no_body_text_match"
    parsed = parse_ocr_page_path(target)
    matches = body_matches(parsed, evidence)
    if not matches:
        return None, "weak_or_stale_body_match"
    # Literal pagination must come from running headers, never apparatus numbers.
    marginal_text = parsed["header_text"]
    numbers = {int(n) for n in re.findall(r"\b[0-9]{1,4}\b", marginal_text)}
    number = cited_start(row)
    proof = best.get("sequence_validation") or {}
    sequence_validated = proof.get("validated") and number in proof.get("pages", [])
    if proof.get("validated") and not sequence_validated:
        return None, "literal_header_conflicts_with_validated_sequence"
    if number not in numbers and not sequence_validated:
        return None, "no_literal_editorial_number_in_header"
    # Only add page evidence actually observed here, never infer it from the filename.
    evidence = [dict(e, detail=e.get("detail") or e.get("raw", "")) for e in evidence]
    if number in numbers:
        evidence.append({"kind": "direct_editorial_page", "editorial_page": number,
                         "file": str(target), "detail": marginal_text[:1200]})
    else:
        evidence.append({"kind": "neighbor_validated_editorial_page", "inferred_editorial_page": number,
                         "file": str(target), "sequence_validation": proof,
                         "detail": "Pagination confirmed by independent observed headers before and after the target."})
    return {"target_file": str(target), "ranking_score": best.get("probability"),
            "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "evidence": evidence, "body_matches": matches}, "accepted"


def repair_block_reason(row):
    raw = json.loads(row.get("raw_json") or "{}")
    reviewed = raw.get("reviewed_editorial")
    if reviewed:
        return reviewed.get("block_reason")
    repair = raw.get("deterministic_repair") or {}
    return repair.get("block_reason")


def decide(row, result, job):
    blocked = repair_block_reason(row)
    if blocked:
        return None, blocked
    search = result.get("file_search")
    candidates = search["candidates"] if search else result.get("candidates", [])
    viable, rejected = {}, []
    for candidate in candidates:
        proposal, reason = validate_file_candidate(row, candidate, job)
        if proposal is None:
            rejected.append(reason)
            continue
        key = proposal["target_file"]
        if key not in viable:
            viable[key] = proposal
        else:
            for match in proposal["body_matches"]:
                if match not in viable[key]["body_matches"]:
                    viable[key]["body_matches"].append(match)
    if not viable:
        return None, rejected[0] if rejected else "no_literal_header_candidates"
    if len(viable) > 1:
        return None, "multiple_viable_ocr_files"
    proposal = next(iter(viable.values()))
    proposal.update(resolution_level="ocr_file", position_status="not_resolved")
    proposal["evidence"].append({"kind": "unique_viable_ocr_file",
                                 "detail": "All viable pagination/text matches identify one OCR file; paragraph/line not resolved."})
    return proposal, "accepted"


def locator_request(job):
    request = _helper_request(make_items(job), volume_id=job["volume_id"],
                              source_root=Path(job["source_root"]), workers=1)
    # Prevent the helper's fallback from interpreting line/chapter endpoints as pages.
    for row, entry in zip(job["rows"], request["entries"]):
        entry["page_hints"] = [str(n) for n in entry["page_hint_ints"]]
        entry["query_names"] = list(dict.fromkeys([*entry["query_names"], *name_head_queries(row)]))
    request["options"]["top_k"] = 5
    request["options"]["include_editorial_page_map"] = True
    blocked_ids = {str(row["ref_id"]) for row in job["rows"] if repair_block_reason(row)}
    request["entries"] = [entry for entry in request["entries"] if entry["entry_id"] not in blocked_ids]
    return request


def estimator_overrides(db_path, sources):
    """Read manual pagination rules without creating or updating the estimator DB."""
    if not Path(db_path).exists():
        return []
    with closing(connect_readonly(db_path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='page_overrides'").fetchone():
            return []
        paths = {path for path, _ in sources}
        return [tuple(row) for row in con.execute(
            "SELECT file_path,volume_id,pages_json,note FROM page_overrides ORDER BY file_path")
                if row["file_path"] in paths]


def cached_candidates(job, sources, implementation_hash, cache_dir, estimator_db, overrides):
    request = locator_request(job)
    namespace = digest({"sources": sources, "code": implementation_hash,
                        "overrides": overrides, "volume_id": request["volume_id"],
                        "source_root": request["source_root"], "options": request["options"]})
    cache_file = Path(cache_dir) / (digest(str(Path(job["source_root"]).resolve()))[:24] + ".sqlite")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cache_file, timeout=30)) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS results
            (cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL)""")
        map_key = "pagination:" + namespace
        cached_map = con.execute("SELECT result_json FROM results WHERE cache_key=?", (map_key,)).fetchone()
        editorial_map = json.loads(cached_map[0]) if cached_map else None
        results, missing = {}, {}
        keys = []
        for entry in request["entries"]:
            key = digest([namespace, {k: v for k, v in entry.items() if k != "entry_id"}])
            keys.append(key)
            cached = con.execute("SELECT result_json FROM results WHERE cache_key=?", (key,)).fetchone()
            if cached:
                results[key] = json.loads(cached[0])
            else:
                missing.setdefault(key, entry)
        con.commit()
    if missing or editorial_map is None:
        guard = source_stats(job["source_root"])
        response = resolve_index_targets({**request, "entries": list(missing.values())},
                                         estimator_db_path=estimator_db, require_estimator=True)
        returned = {str(result["entry_id"]): result for result in response["entries"]}
        editorial_map = response["editorial_page_map"]
        for key, entry in missing.items():
            results[key] = returned[str(entry["entry_id"])]
        if (source_stats(job["source_root"]) != guard
                or source_snapshot(job["source_root"]) != sources
                or estimator_overrides(estimator_db, sources) != overrides):
            raise ValueError("OCR or editorial overrides changed during ranking; rerun dry-run")
        with closing(sqlite3.connect(cache_file, timeout=30)) as con:
            con.executemany("INSERT OR REPLACE INTO results VALUES (?,?)",
                            [(key, json.dumps(results[key], ensure_ascii=False)) for key in missing])
            con.execute("INSERT OR REPLACE INTO results VALUES (?,?)",
                        (map_key, json.dumps(editorial_map, ensure_ascii=False)))
            con.commit()
    by_id = {str(entry["entry_id"]): {**results[key], "entry_id": entry["entry_id"]}
             for entry, key in zip(request["entries"], keys)}
    return by_id, {"hits": sum(key not in missing for key in keys),
                   "misses": sum(key in missing for key in keys),
                   "computed": len(missing), "path": str(cache_file)}, editorial_map


def process_volume(job, report_dir, implementation_hash, diagnostics=False,
                   search_mode="multi-range", cache_dir=None, estimator_db=None):
    started = time.monotonic()
    root = job["source_root"]
    before = source_stats(root)
    sources = source_snapshot(root)
    search_mode = "multi-range" if diagnostics else search_mode
    estimator_db = Path(estimator_db or editorial_page_estimator.DEFAULT_ESTIMATOR_DB)
    overrides = estimator_overrides(estimator_db, sources) if search_mode == "multi-range" else []
    fingerprint = digest({"job": job, "sources": sources, "code": implementation_hash,
                          "search_mode": search_mode, "overrides": overrides})
    output = Path(report_dir) / (job["volume_id"] + ".json")
    if output.exists():
        previous = json.loads(output.read_text())
        if previous.get("fingerprint") == fingerprint:
            return {**previous["summary"], "reused": True}
    phase_started = time.monotonic()
    timings = {"source_snapshot": phase_started - started}
    by_id, cache = {}, {"hits": 0, "misses": 0, "computed": 0}
    editorial_map = {}
    if search_mode == "multi-range":
        by_id, cache, editorial_map = cached_candidates(job, sources, implementation_hash,
                                        cache_dir or DEFAULT_CACHE_DIR, estimator_db, overrides)
    timings["multi_range"] = time.monotonic() - phase_started
    phase_started = time.monotonic()
    headers = header_file_index(sources)
    timings["header_index"] = time.monotonic() - phase_started
    phase_started = time.monotonic()
    records = []
    for row in job["rows"]:
        candidate_result = by_id.get(str(row["ref_id"]), {
            "entry_id": str(row["ref_id"]), "status": "not_run", "candidates": []})
        candidate_result["file_search"] = search_literal_files(
            row, headers, candidate_result["candidates"], editorial_map)
        proposal, reason = decide(row, candidate_result, job)
        ranked = candidate_result.get("best_candidate") if not repair_block_reason(row) else None
        if ranked:
            best = candidate_result["candidates"][0]
            matches = body_matches(parse_ocr_page_path(Path(best["file"])), best.get("evidence", []))
            ranked = {**ranked, "validation_status": "unverified",
                      "body_text_supported": bool(matches), "body_matches": matches}
        records.append({"ref_id": row["ref_id"], "lemma": row["lemma_raw"],
                        "ref_raw": row["ref_raw"], "reason": reason,
                        "proposal": proposal, "ranked_proposal": ranked,
                        "locator_result": candidate_result})
    timings["matching"] = time.monotonic() - phase_started
    if before != source_stats(root):
        raise ValueError(f"OCR changed during scan: {job['volume_id']}")
    if search_mode == "multi-range" and estimator_overrides(estimator_db, sources) != overrides:
        raise ValueError("Editorial overrides changed during scan; rerun dry-run")
    accepted = sum(r["proposal"] is not None for r in records)
    summary = {"volume_id": job["volume_id"], "eligible": job["eligible_count"],
               "processed": len(records), "accepted": accepted,
               "accepted_by_neighbors": sum(bool(r["proposal"]) and any(
                   e["kind"] == "neighbor_validated_editorial_page" for e in r["proposal"]["evidence"])
                   for r in records),
               "pending": len(records) - accepted, "source_files": len(sources),
               "ranked_pending": sum(r["proposal"] is None and r["ranked_proposal"] is not None for r in records),
               "ranked_text_pending": sum(r["proposal"] is None and bool(
                   r["ranked_proposal"] and r["ranked_proposal"]["body_text_supported"]) for r in records),
               "cache_hits": cache["hits"], "cache_misses": cache["misses"],
               "seconds": round(time.monotonic() - started, 2)}
    write_json(output, {"version": VERSION, "fingerprint": fingerprint,
                        "code_fingerprint": implementation_hash, "source_fingerprint": digest(sources),
                        "search_mode": search_mode, "cache": cache,
                        "editorial_page_map": editorial_map,
                        "estimator_db": str(estimator_db), "estimator_overrides": overrides,
                        "phase_seconds": {k: round(v, 4) for k, v in timings.items()},
                        "job": job, "summary": summary, "records": records})
    return summary


def scan(db, volumes, report_dir, workers=2, limit=0, include_unverified=False, section_kinds=(),
         diagnostics=False, search_mode="multi-range", cache_dir=None, estimator_db=None, repaired_only=False):
    started = time.monotonic()
    with closing(connect_readonly(db)) as con:
        con.execute("BEGIN")
        jobs = [load_job(con, v, limit, include_unverified, section_kinds, repaired_only) for v in volumes]
    # Start the largest tasks first so small volumes don't delay the longest worker.
    jobs.sort(key=lambda job: (-len(job["rows"]), job["volume_id"]))
    implementation_hash = code_fingerprint()
    summaries, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_volume, job, report_dir, implementation_hash, diagnostics,
                               search_mode, cache_dir, estimator_db): job["volume_id"]
                   for job in jobs if job["rows"]}
        for future in as_completed(futures):
            try:
                summary = future.result()
                summaries.append(summary)
                print(json.dumps(summary), flush=True)
            except Exception as exc:
                errors.append({"volume_id": futures[future], "error": str(exc)})
                print(json.dumps(errors[-1]), file=sys.stderr, flush=True)
    manifest = {"version": VERSION, "db": str(Path(db).resolve()), "mode": "dry_run",
                "volumes": sorted(summaries, key=lambda s: s["volume_id"]), "errors": errors,
                "empty_volumes": [j["volume_id"] for j in jobs if not j["rows"]],
                "search_mode": "multi-range" if diagnostics else search_mode,
                "seconds": round(time.monotonic() - started, 2)}
    manifest["totals"] = {k: sum(s[k] for s in summaries)
                          for k in ("eligible", "processed", "accepted", "pending", "ranked_pending",
                                    "ranked_text_pending", "accepted_by_neighbors")}
    write_json(Path(report_dir) / "manifest.json", manifest)
    return manifest


def apply_reports(db, report_dir):
    report_dir = Path(report_dir)
    manifest = json.loads((report_dir / "manifest.json").read_text())
    if manifest["db"] != str(Path(db).resolve()) or manifest["errors"]:
        raise ValueError("Report DB differs or scan contains errors; run a clean dry-run first")
    reports = [json.loads((report_dir / (s["volume_id"] + ".json")).read_text())
               for s in manifest["volumes"] if s["accepted"]]
    if not reports:
        result = {"applied": 0, "backup": None}
        write_json(report_dir / "application.json", result)
        return result
    implementation_hash = code_fingerprint()
    source_guards = {}
    for report in reports:
        if report["version"] != VERSION or report["code_fingerprint"] != implementation_hash:
            raise ValueError("Locator implementation changed; rerun dry-run")
        sources = source_snapshot(report["job"]["source_root"])
        if digest(sources) != report["source_fingerprint"]:
            raise ValueError("OCR changed; rerun dry-run")
        if report["search_mode"] == "multi-range" and digest(estimator_overrides(
                report["estimator_db"], sources)) != digest(report["estimator_overrides"]):
            raise ValueError("Editorial overrides changed; rerun dry-run")
        source_guards[report["job"]["source_root"]] = source_stats(report["job"]["source_root"])
    backup_path = report_dir / ("before-apply-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".sqlite")
    with closing(connect_readonly(db)) as source, closing(sqlite3.connect(backup_path)) as backup:
        source.backup(backup)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    applied = 0
    try:
        con.execute("BEGIN IMMEDIATE")
        for report in reports:
            job = report["job"]
            current = load_job(con, job["volume_id"], job["limit_per_volume"],
                               job["include_unverified"], job["section_kinds"], job.get("repaired_only", False))
            if current != job:
                raise ValueError(f"DB changed for {job['volume_id']}; rerun dry-run")
            touched = set()
            by_id = {r["ref_id"]: r for r in job["rows"]}
            sources = source_snapshot(job["source_root"])
            if digest(sources) != report["source_fingerprint"]:
                raise ValueError("OCR changed during apply; rerun dry-run")
            headers = header_file_index(sources)
            editorial_map = {}
            if report["search_mode"] == "multi-range" and job["collection"] in {"PG", "PL"}:
                estimated = editorial_page_estimator.estimate_editorial_pages(
                    volume_id=job["volume_id"], source_root=Path(job["source_root"]),
                    collection=job["collection"], db_path=Path(report["estimator_db"]), use_cache=False)
                editorial_map = {item["file"]: item["sequence_validation"] for item in estimated["files"]
                                 if item["sequence_validation"].get("validated")}
            if editorial_map != report["editorial_page_map"]:
                raise ValueError("Neighbor validation changed; rerun dry-run")
            for record in report["records"]:
                if record["proposal"] is None:
                    continue
                row = by_id[record["ref_id"]]
                current_search = search_literal_files(row, headers, record["locator_result"]["candidates"], editorial_map)
                if current_search != record["locator_result"].get("file_search"):
                    raise ValueError("Candidate set changed; rerun dry-run")
                proposal, _ = decide(row, record["locator_result"], job)
                if proposal != record["proposal"]:
                    raise ValueError("Proposal no longer validates; rerun dry-run")
                raw = json.loads(row["raw_json"])
                raw["compact_locator"] = {"status": "resolved", "method": "deterministic_unresolved_pool_v3",
                                           "resolution_level": "ocr_file", "position_status": "not_resolved",
                                           "evidence": proposal["evidence"],
                                           "report_fingerprint": report["fingerprint"]}
                con.execute("""UPDATE alphabetical_refs SET target_file=?, resolved_target_ocr_file=?,
                    target_file_probability=?, target_ocr_file_candidate_score=?, locator_status='resolved', raw_json=?
                    WHERE ref_id=? AND locator_status=? AND COALESCE(target_file,'')=''
                      AND COALESCE(resolved_target_ocr_file,'')=''""",
                            (proposal["target_file"], proposal["target_file"], proposal["ranking_score"],
                             proposal["ranking_score"], json.dumps(raw, ensure_ascii=False),
                             row["ref_id"], row["locator_status"]))
                con.execute("DELETE FROM alphabetical_locator_evidence WHERE ref_id=?", (row["ref_id"],))
                for order, evidence in enumerate(proposal["evidence"], 1):
                    con.execute("""INSERT INTO alphabetical_locator_evidence
                        (ref_id,evidence_order,evidence_kind,evidence_ocr_file_path,
                         observed_editorial_page_raw,observed_editorial_page_number,detail,weight,raw_json)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                                (row["ref_id"], order, evidence["kind"], evidence.get("file"),
                                 str(evidence["editorial_page"]) if "editorial_page" in evidence else None,
                                 evidence.get("editorial_page"), evidence.get("detail"),
                                 min(1., max(0., evidence["weight"])) if "weight" in evidence else None,
                                 json.dumps(evidence, ensure_ascii=False)))
                touched.add(row["entry_key"])
                applied += 1
            for key in touched:
                best = con.execute("""SELECT target_file FROM alphabetical_refs WHERE entry_key=?
                    AND locator_status='resolved' AND target_file IS NOT NULL
                    ORDER BY target_file_probability DESC,ref_order LIMIT 1""", (key,)).fetchone()
                con.execute("UPDATE alphabetical_entries SET target_file_best=?,resolved_target_ocr_file=? WHERE entry_key=?",
                            (best[0], best[0], key))
            refresh_volume_quality(con, job["volume_id"])
        if any(source_stats(root) != guard for root, guard in source_guards.items()):
            raise ValueError("OCR changed during apply; rerun dry-run")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    result = {"applied": applied, "backup": str(backup_path)}
    write_json(report_dir / "application.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--volume", action="append")
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--apply-report", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit-per-volume", type=int, default=0)
    parser.add_argument("--include-unverified", action="store_true")
    parser.add_argument("--repaired-only", action="store_true",
                        help="Locate only source-anchored child entries created by the deterministic repair")
    parser.add_argument("--diagnostics", action="store_true",
                        help="Compatibility alias for --search-mode multi-range (now the default)")
    parser.add_argument("--search-mode", choices=("multi-range", "literal"), default="multi-range")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--estimator-db", type=Path,
                        help="Persistent editorial estimator DB; defaults to the existing shared cache")
    parser.add_argument("--section-kind", action="append", default=[])
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    if args.workers < 1 or args.limit_per_volume < 0:
        parser.error("workers must be positive; limit-per-volume must be nonnegative")
    if args.apply_report:
        print(json.dumps(apply_reports(args.db, args.apply_report)))
        return
    if not args.report_dir:
        parser.error("--report-dir is required for dry-run")
    volumes = sorted(set(args.volume or []))
    if args.all:
        with closing(connect_readonly(args.db)) as con:
            volumes = [r[0] for r in con.execute("SELECT volume_id FROM alphabetical_volumes ORDER BY volume_id")]
    result = scan(args.db, volumes, args.report_dir, args.workers, args.limit_per_volume,
                  args.include_unverified, args.section_kind, args.diagnostics,
                  args.search_mode, args.cache_dir, args.estimator_db, args.repaired_only)
    print(json.dumps(result["totals"]))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
