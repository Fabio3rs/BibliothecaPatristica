#!/usr/bin/env python3
"""Select diverse distinct pages with volume-isolated blind holdout and resolution pairs."""
from __future__ import annotations

from collections import Counter
import copy
from itertools import combinations
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.knowledge_graph.benchmarks.common import (
    TERMS, common_arguments, distributions, normalize_match_text, physical_key,
    rank, read_jsonl, work_keys, write_json, write_jsonl,
)


def work_families(record):
    """Catalog title+author guard supplements work_key and volume boundaries."""
    return {normalize_match_text((w.get("author_raw") or "") + " " + (w.get("title_raw") or ""))
            for w in record["metadata"].get("works", []) if w.get("title_raw")}


def diverse_pick(records, count, seed, difficult=False):
    selected = []
    volumes, works, categories, statuses, collections, kinds = (Counter() for _ in range(6))
    remaining = sorted(records, key=lambda r: rank(seed, r["candidate_id"]))
    for _ in range(min(count, len(remaining))):
        def score(r):
            s = r["source"]
            risks = set(r["selection"]["risk_flags"])
            value = 5 / (1 + volumes[s["documento"]])
            keys = work_keys(r)
            value += 3 / (1 + min((works[k] for k in keys), default=0))
            value += sum(1 / (1 + categories[c]) for c in r["selection"]["categories"])
            value += 2 / (1 + kinds[s["primary_page_kind"]])
            value += 2 / (1 + collections[s["documento"][:2]])
            value += 1 / (1 + statuses[s["status"]])
            if difficult:
                value += min(3, len(risks))
                value += 2 * bool(risks & {"multiple_works", "minimal_or_illegible", "malformed_xml", "weak_ocr"})
            return value
        chosen = max(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen)
        s = chosen["source"]
        volumes[s["documento"]] += 1
        works.update(work_keys(chosen))
        categories.update(chosen["selection"]["categories"])
        statuses[s["status"]] += 1
        collections[s["documento"][:2]] += 1
        kinds[s["primary_page_kind"]] += 1
    return selected


def select(records, seed=20261031, sample_size=240):
    records = [copy.deepcopy(r) for r in records if r["selection"]["eligible_for_selection"]]
    seen_hashes, deduplicated, duplicate_ids = set(), [], []
    for r in sorted(records, key=lambda r: r["source"]["generation_id"]):
        h = r["source"]["ocr_sha256"]
        if h and h in seen_hashes:
            duplicate_ids.append(r["candidate_id"])
            continue
        if h:
            seen_hashes.add(h)
        deduplicated.append(r)
    records = deduplicated
    if len({physical_key(r) for r in records}) != len(records):
        raise ValueError("Candidate pool contains duplicate physical pages")
    quotas = {"calibration": round(sample_size * .10), "difficult": round(sample_size * .20),
              "blind_holdout": round(sample_size * .20)}
    quotas["representative"] = sample_size - sum(quotas.values())
    volume_counts = Counter(r["source"]["documento"] for r in records)
    holdout_volumes, supply = set(), 0
    for volume in sorted(volume_counts, key=lambda v: rank(seed, "holdout:" + v)):
        if supply >= quotas["blind_holdout"] * 2:
            break
        holdout_volumes.add(volume)
        supply += volume_counts[volume]
    holdout_pool = [r for r in records if r["source"]["documento"] in holdout_volumes]
    holdout = diverse_pick(holdout_pool, quotas["blind_holdout"], seed)
    held_works = set().union(*(work_keys(r) for r in holdout)) if holdout else set()
    held_families = set().union(*(work_families(r) for r in holdout)) if holdout else set()
    remaining = [r for r in records if r["source"]["documento"] not in holdout_volumes
                 and not (work_keys(r) & held_works or work_families(r) & held_families)]
    result = []
    for split in ("calibration", "difficult", "representative", "blind_holdout"):
        options = remaining
        if split == "difficult":
            options = [r for r in remaining if r["selection"]["risk_flags"]]
        chosen = holdout if split == "blind_holdout" else diverse_pick(options, quotas[split], seed, split == "difficult")
        chosen_ids = {r["candidate_id"] for r in chosen}
        remaining = [r for r in remaining if r["candidate_id"] not in chosen_ids]
        for r in chosen:
            r["split"] = split
            r["selection"]["stability_repeat"] = split == "calibration"
            result.append(r)
    result.sort(key=lambda r: (r["split"], r["source"]["documento"], r["source"]["pagina_num"]))
    for i, r in enumerate(result, 1):
        r["benchmark_id"] = f"KG-BENCH-{i:06d}"
    return result, {
        "seed": seed, "requested_quotas": quotas,
        "exact_ocr_duplicates_excluded": duplicate_ids,
        "actual_quotas": dict(Counter(r["split"] for r in result)),
        "reserved_holdout_volumes": sorted(holdout_volumes),
        "held_work_keys": sorted(held_works), "held_catalog_work_families": sorted(held_families),
        "isolation": "Whole volumes reserved; no selected work_key/catalog author-title crosses holdout boundary. Cross-volume textual parallels still require review.",
        "sampling": "Purposive diversity-balanced discovery sample, not a probability sample of the entire corpus.",
        "deficiencies": {k: v - sum(r["split"] == k for r in result) for k, v in quotas.items() if v > sum(r["split"] == k for r in result)},
    }


def occurrence(record, term):
    for segment in record["text"]["page_segments"]:
        summary = segment["summary"]
        match = TERMS[term].search(summary)
        if match:
            start, end = max(0, match.start() - 220), min(len(summary), match.end() + 320)
            return {"benchmark_id": record["benchmark_id"], "generation_id": record["source"]["generation_id"],
                    "documento": record["source"]["documento"], "pagina_num": record["source"]["pagina_num"],
                    "segment_order": segment.get("order"), "work_key": segment.get("work_key") or None,
                    "surface": match.group(), "summary_quote": summary[start:end],
                    "summary_start": start, "summary_end": end,
                    "surface_start": match.start(), "surface_end": match.end(),
                    "context_description": summary[start:end],
                    "ocr_support": record["evidence"]["ocr_support"],
                    "ocr_path": record["source"]["ocr_path"], "ocr_sha256": record["source"]["ocr_sha256"]}
    return None


def resolution_pairs(records, seed, target=40):
    candidates = []
    for term in TERMS:
        occurrences = [(r, occurrence(r, term)) for r in records]
        occurrences = [(r, o) for r, o in occurrences if o]
        for (left, a), (right, b) in combinations(occurrences, 2):
            if left["split"] != right["split"]:
                continue
            different_work = a["work_key"] != b["work_key"]
            different_volume = a["documento"] != b["documento"]
            priority = 3 * different_volume + 2 * different_work + (a["surface"].casefold() != b["surface"].casefold())
            candidates.append((priority, rank(seed, f"{term}:{a['benchmark_id']}:{b['benchmark_id']}"), term, left["split"], a, b))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    pairs, terms, used_pages, page_pairs = [], Counter(), Counter(), set()
    for _, _, term, split, a, b in candidates:
        key = tuple(sorted([a["benchmark_id"], b["benchmark_id"]]))
        if terms[term] >= 4 or max(used_pages[x] for x in key) >= 3 or key in page_pairs:
            continue
        terms[term] += 1
        used_pages.update(key)
        page_pairs.add(key)
        pairs.append({"pair_id": f"KG-PAIR-{len(pairs)+1:04d}", "split": split,
                      "retrieval_topic": term, "occurrences": [a, b],
                      "provisional_relationship": "UNCERTAIN",
                      "relationship_basis": "Contextualized occurrences retrieved from actual v2 segments. A shared surface/topic does not establish identity or equivalence.",
                      "direction": "first_occurrence_to_second_occurrence",
                      "question": "Resolve the referents/senses in these contexts; test EQUIVALENT, BROADER, NARROWER, RELATED, DIFFERENT or UNCERTAIN. Supply OCR evidence and reject false friends/homonyms.",
                      "review": {"status": "pending", "gold_status": "not_gold", "revisable": True}})
        if len(pairs) >= target:
            break
    return pairs


def main():
    p = common_arguments(__doc__)
    p.add_argument("--sample-size", type=int, default=240)
    p.add_argument("--pair-count", type=int, default=40)
    args = p.parse_args()
    if args.sample_size < 1 or args.pair_count < 0:
        p.error("Sample size must be positive; pair count must be nonnegative")
    candidates = list(read_jsonl(args.output_dir / "candidate_pool.jsonl"))
    selected, design = select(candidates, args.seed, args.sample_size)
    pairs = resolution_pairs(selected, args.seed, args.pair_count)
    write_jsonl(args.output_dir / "selected_pages.jsonl", selected)
    write_jsonl(args.output_dir / "resolution_pairs.jsonl", pairs)
    design.update({"distribution": distributions(selected), "pair_count": len(pairs),
                   "pair_target": args.pair_count, "stability_page_ids": [r["benchmark_id"] for r in selected if r["selection"]["stability_repeat"]]})
    write_json(args.output_dir / "sample_design.json", design)
    print(f"Selected {len(selected)} pages, {len(pairs)} pairs: {design['actual_quotas']}")


if __name__ == "__main__":
    main()
