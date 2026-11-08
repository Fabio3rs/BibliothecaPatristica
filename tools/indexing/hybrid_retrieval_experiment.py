"""Offline editorial/semantic shortlist experiment. No inference or production writes."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from tools.indexing.index_target_locator import extract_zone_numbers, parse_ocr_page_path
from tools.indexing.semantic_retrieval_experiment import ROOT, digest, readonly, write_json

VERSION = "hybrid-index-shortlist-v1"
KNOWN_INTERNAL_NUMBERING = {"PG069:alpha:analytic_subject:001"}


def load_citations(db_path, queries):
    """Only source-side fields; never read target paths, resolution or Jev evidence."""
    result = {}
    con = readonly(db_path)
    try:
        for q in queries:
            entry = con.execute(
                "SELECT e.entry_raw,e.lemma_raw,e.section_key,s.volume_id FROM alphabetical_entries e "
                "JOIN alphabetical_sections s USING(section_key) WHERE e.entry_key=?", (q["entry_key"],)
            ).fetchone()
            if (not entry or entry["entry_raw"] != q["entry_raw"] or entry["lemma_raw"] != q["query"]
                    or entry["section_key"] != q["section_key"] or entry["volume_id"] != q["volume"]):
                raise ValueError(f"Index source differs from frozen query: {q['query_id']}")
            rows = [dict(r) for r in con.execute(
                "SELECT ref_id,ref_order,ref_raw,ref_kind FROM alphabetical_refs "
                "WHERE entry_key=? ORDER BY ref_order,ref_id", (q["entry_key"],))]
            by_id = {r["ref_id"]: r for r in rows}
            for r in q["references"]:
                if r["ref_id"] not in by_id or by_id[r["ref_id"]]["ref_raw"] != r["ref_raw"]:
                    raise ValueError("Printed reference differs from frozen sample")
            for r in rows:
                raw = (r["ref_raw"] or "").strip()
                # Deliberately no digit extraction from page/line, ranges or Roman tomes.
                supported = (r["ref_kind"] == "editorial_page" and raw.isascii()
                             and raw.isdigit() and 0 < int(raw) < 10000)
                r["number"] = int(raw) if supported else None
                r["interpretation"] = "literal_single_editorial_number" if supported else "unsupported_literal"
            result[q["query_id"]] = rows
    finally:
        con.close()
    return result


def load_headers(inventory, paths, root):
    """Re-read original OCR: embedding cleaning intentionally removes headings."""
    result = {}
    for name in sorted(paths):
        source = inventory.get(name)
        path = Path(name)
        if not source or path.resolve().parent != (root / source["volume"] / "text").resolve():
            raise ValueError(f"Missing or out-of-volume source inventory: {name}")
        if digest(path.read_bytes()) != source["ocr_sha256"]:
            raise ValueError(f"OCR changed after embedding run: {name}")
        header = parse_ocr_page_path(path)["header_text"]
        result[name] = {"path": name, "volume": source["volume"],
                        "physical_page": source["physical_page"], "ocr_sha256": source["ocr_sha256"],
                        "header_text": header, "numbers": extract_zone_numbers(header)}
    return result


def editorial_candidates(citations, semantic_rows, headers, radius):
    """Numerical hints generate candidates, not resolved locations or probabilities."""
    ordered = sorted(semantic_rows, key=lambda r: (r["physical_page"], r["path"]))
    positions = {r["path"]: i for i, r in enumerate(ordered)}
    scores = {r["path"]: r["score"] for r in semantic_rows}
    evidence = {}
    queues = []
    for ref in citations:
        number = ref["number"]
        if number is None:
            continue
        candidates = {}
        for row in ordered:
            path = row["path"]
            numbers = headers[path]["numbers"]
            if not numbers:
                continue
            distance = min(abs(n - number) for n in numbers)
            if distance <= radius:
                candidates[path] = (0 if distance == 0 else 2, distance, path)
        exact = [p for p, item in candidates.items() if item[0] == 0]
        for anchor in exact:
            i = positions[anchor]
            for neighbor in ordered[max(0, i - 1):i + 2]:
                path = neighbor["path"]
                # File suffix only measures adjacency, never yields editorial numbering.
                if abs(neighbor["physical_page"] - headers[anchor]["physical_page"]) != 1:
                    continue
                candidate = (1, 1, anchor)
                if path not in candidates or candidate[:2] < candidates[path][:2]:
                    candidates[path] = candidate
        queue = sorted(candidates, key=lambda p: (candidates[p][0], candidates[p][1], -scores[p], p))
        queues.append(queue)
        for path in queue:
            tier, distance, anchor = candidates[path]
            evidence.setdefault(path, []).append({
                "ref_id": ref["ref_id"], "ref_raw": ref["ref_raw"], "number": number,
                "kind": ("header_number_match", "physical_neighbor_of_match", "nearby_header_number")[tier],
                "distance": distance, "anchor_file": anchor,
                "observed_header_numbers": headers[anchor]["numbers"],
                "interpretation": "candidate_only_configured_header_namespace",
            })
    # Diversity across printed occurrences prevents one long neighborhood taking the quota.
    merged, seen = [], set()
    while any(queues):
        for queue in queues:
            while queue and queue[0] in seen:
                queue.pop(0)
            if queue:
                path = queue.pop(0)
                merged.append(path)
                seen.add(path)
    return merged, evidence


def shortlist(semantic_rows, citations, headers, *, enabled, top_k=20, editorial_slots=10, radius=2):
    """Input projection excludes labels. Semantic-only fallback for unmapped sections."""
    semantic = [r["path"] for r in sorted(semantic_rows, key=lambda r: (-r["score"], r["path"]))]
    if len(set(semantic)) != len(semantic):
        raise ValueError("Duplicate candidate pages")
    mechanical, evidence = editorial_candidates(citations, semantic_rows, headers, radius) if enabled else ([], {})
    prefix = mechanical[:editorial_slots]
    hybrid = prefix + [p for p in semantic if p not in set(prefix)]
    variants = {"semantic_only": semantic[:top_k], "editorial_only": mechanical[:top_k],
                "hybrid": hybrid[:top_k]}
    return variants, evidence


def metrics(selected, gold, total):
    if not gold:
        raise ValueError("Empty positive set")
    found = set(selected) & gold
    return {"known_positive_count": len(gold), "known_positives_retrieved": len(found),
            "recall": len(found) / len(gold), "hit": int(bool(found)),
            "all_gold": int(found == gold), "selected_count": len(selected),
            "candidate_fraction": len(selected) / total,
            "gold_ranks_in_shortlist": {p: selected.index(p) + 1 if p in selected else None for p in sorted(gold)}}


def run(args):
    if args.top_k < 1 or not 0 <= args.editorial_slots <= args.top_k or args.numeric_radius < 0:
        raise ValueError("Require top-k > 0, 0 <= editorial-slots <= top-k, numeric-radius >= 0")
    if set(args.header_section) & KNOWN_INTERNAL_NUMBERING:
        raise ValueError("Section cites internal bold numbers, not header columns; "
                         "see docs/pg069_rachel_editorial_audit.md")
    args.out.mkdir(parents=True, exist_ok=False)
    results_path = args.run / "results.json"
    sample_path = args.run / "sample.json"
    inventory_path = args.run / "sources.json"
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config.update(version=VERSION, input_hashes={p.name: digest(p.read_bytes())
                  for p in (results_path, sample_path, inventory_path)},
                  header_namespace_policy="explicit per-section experimental same-number-space assumption; no edition conversion",
                  inference_calls=0, production_db_updates=0)
    write_json(args.out / "config.json", config)
    queries = json.loads(sample_path.read_text())["queries"]
    if not queries:
        raise ValueError("Empty sample")
    by_query = {q["query_id"]: q for q in queries}
    scoped = set(args.header_section)
    if scoped - {q["section_key"] for q in queries}:
        raise ValueError("Unknown header-section; use a section_key present in the sample")
    results = [r for r in json.loads(results_path.read_text()) if r.get("status") == "evaluated"
               and r["variant"] == args.variant and r["query_mode"] == args.query_mode]
    if Counter(r["query_id"] for r in results) != Counter({q: 1 for q in by_query}):
        raise ValueError("Need exactly one saved ranking per query for selected variant/mode")
    citations = load_citations(args.db, queries)
    inventory = {p["path"]: p for p in json.loads(inventory_path.read_text())}
    headers = load_headers(inventory, {r["path"] for q in results for r in q["ranking"]}, args.root)
    write_json(args.out / "citations.json", citations)
    write_json(args.out / "headers.json", headers)
    output = []
    for result in results:
        query = by_query[result["query_id"]]
        # This projection is the complete input to selection; never pass is_gold or known targets.
        rows = [{k: r[k] for k in ("path", "score", "physical_page")} for r in result["ranking"]]
        if any(headers[r["path"]]["volume"] != query["volume"] or
               headers[r["path"]]["physical_page"] != r["physical_page"] for r in rows):
            raise ValueError("Ranking/source volume or physical identity mismatch")
        enabled = query["section_key"] in scoped
        selections, evidence = shortlist(rows, citations[query["query_id"]], headers, enabled=enabled,
            top_k=args.top_k, editorial_slots=args.editorial_slots, radius=args.numeric_radius)
        # Labels are used only AFTER selection for evaluation.
        gold = set(query["gold_files"])
        if not gold <= {r["path"] for r in rows}:
            raise ValueError("Saved candidate universe misses a known positive")
        if any(headers[p]["ocr_sha256"] != query["gold_files"][p] for p in gold):
            raise ValueError("Known positive snapshot differs from original OCR")
        by_path = {r["path"]: r for r in result["ranking"]}
        for variant, selected in selections.items():
            output.append({"query_id": query["query_id"], "query": query["query"],
                "section_key": query["section_key"], "variant": variant, "top_k": args.top_k,
                "header_scope_enabled": enabled, "label_origin": query["label_origin"],
                "candidate_count": len(rows), "metrics": metrics(selected, gold, len(rows)),
                "unsupported_citations": [r for r in citations[query["query_id"]] if r["number"] is None],
                "selection": [{"rank": i + 1, "path": p, "physical_page": by_path[p]["physical_page"],
                               "semantic_score": by_path[p]["score"], "is_known_positive": p in gold,
                               "editorial_evidence": evidence.get(p, []), "snippet": by_path[p]["snippet"]}
                              for i, p in enumerate(selected)]})
    aggregates = []
    for variant in ("semantic_only", "editorial_only", "hybrid"):
        rows = [r for r in output if r["variant"] == variant]
        aggregates.append({"variant": variant, "queries": len(rows), **{
            name: sum(r["metrics"][name] for r in rows) / len(rows)
            for name in ("recall", "hit", "all_gold", "selected_count", "candidate_fraction")}})
    summary = {"version": VERSION, "top_k": args.top_k, "metrics": aggregates,
               "inference_calls": 0, "production_db_updates": 0, "out": str(args.out)}
    write_json(args.out / "results.json", output)
    write_json(args.out / "summary.json", summary)
    lines = ["# Seleção híbrida experimental", "",
             "Números editoriais são pistas sob hipótese explícita por seção; não resoluções. "
             "Destinos conhecidos entram somente após a seleção. Rótulos não equivalem a revisão humana.", "",
             "| Variante | Recall | Hit | AllGold | Candidatos retidos |", "|---|---:|---:|---:|---:|"]
    for r in aggregates:
        lines.append(f"| {r['variant']} | {r['recall']:.3f} | {r['hit']:.3f} | {r['all_gold']:.3f} | {r['candidate_fraction']:.3f} |")
    for r in output:
        lines += ["", f"## {r['query_id']} / {r['variant']}", "", r["query"], ""]
        lines += [f"- {p['rank']}. arquivo físico {p['physical_page']}, cosseno {p['semantic_score']:.4f}; "
                  f"positivo conhecido: {p['is_known_positive']}; evidências: "
                  + ", ".join(f"{e['ref_raw']}:{e['kind']}" for e in p['editorial_evidence'])
                  for p in r["selection"]]
    (args.out / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Existing embedding benchmark directory")
    parser.add_argument("--db", type=Path, default=ROOT / "data/alphabetical_indices.db")
    parser.add_argument("--root", type=Path, default=ROOT / "teste")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--header-section", action="append", default=[],
                        help="Explicit section_key hypothesis: printed refs and headers share number space")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--editorial-slots", type=int, default=10)
    parser.add_argument("--numeric-radius", type=int, default=2)
    parser.add_argument("--variant", default="chunks", choices=["chunks", "page", "summary"])
    parser.add_argument("--query-mode", default="instruct")
    run(parser.parse_args())
