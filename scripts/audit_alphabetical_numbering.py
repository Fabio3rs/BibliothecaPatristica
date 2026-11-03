#!/usr/bin/env python3
"""Audit embedded editorial numbering against alphabetical references, offline.

Requires an explicit JSON profile of target volumes, physical bounds and tomes.
Produces diagnostic candidates only: sequence support and a name match do NOT
prove a page-number namespace or fix a damaged entry. Never updates SQLite.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.resolve_alphabetical_unresolved import (
    DEFAULT_DB, cited_start, connect_readonly, load_job, name_head_queries, write_json,
)
from tools.corpus_utils import discover_preferred_pages, page_number
from tools.indexing.alphabetical_compact_driver import _search_queries
from tools.indexing.index_target_locator import normalize_for_search, _best_phrase_similarity
from tools.ocr_xml_utils import parse_ocr_xml_page

NUMBER = re.compile(r"(?<!\w)[0-9]{1,4}(?!\w)")


def supported_sequence_nodes(nodes, min_anchors=7, max_number_gap=2, max_file_gap=3):
    """Linear passes over a bounded DAG; no interpolation of missing numbers."""
    before, after, previous, following = {}, {}, {}, {}
    for numbers, table, links, direction in (
        (sorted(nodes), before, previous, -1),
        (sorted(nodes, reverse=True), after, following, 1),
    ):
        for number in numbers:
            for seq in sorted(nodes[number]):
                options = []
                for step in range(1, max_number_gap + 1):
                    adjacent = number + direction * step
                    for other in nodes.get(adjacent, ()):
                        if 0 <= direction * (other - seq) <= max_file_gap:
                            options.append((adjacent, other))
                best = max(options, key=lambda node: (table[node][0], -node[0], -node[1]), default=None)
                length, endpoint = table[best] if best else (0, seq)
                table[number, seq] = (length + 1, endpoint)
                links[number, seq] = best
    supported = {}
    for number in sorted(nodes):
        for seq in sorted(nodes[number]):
            key = number, seq
            length = before[key][0] + after[key][0] - 1
            if length < min_anchors or after[key][1] - before[key][1] < 3:
                continue
            left, right = [], []
            cursor = previous[key]
            while cursor and len(left) < min_anchors:
                left.append(cursor)
                cursor = previous[cursor]
            cursor = following[key]
            while cursor and len(right) < min_anchors:
                right.append(cursor)
                cursor = following[cursor]
            supported[key] = {"chain_length": length,
                              "witness": list(reversed(left)) + [key] + right}
    return supported


def build_series(spec, corpus_root):
    root = corpus_root / spec["volume"] / "text"
    sources, pages, nodes = [], {}, defaultdict(set)
    for path in discover_preferred_pages(root, volume_id=spec["volume"]):
        seq = page_number(path)
        if not spec["physical_start"] <= seq <= spec["physical_end"]:
            continue
        data = path.read_bytes()
        raw = data.decode("utf-8")
        page = parse_ocr_xml_page(raw)
        bodies = [b.content_clean for b in page.blocks if b.tipo == "texto_principal"
                  and (not spec.get("scripts") or b.script in spec["scripts"])]
        body = "\n".join(bodies)
        occurrences = defaultdict(list)
        for match in NUMBER.finditer(body):
            number = int(match[0])
            if 0 < number <= spec.get("maximum_number", 1500):
                nodes[number].add(seq)
                occurrences[number].append({"body_offset": match.start(),
                    "excerpt": body[max(0, match.start() - 100):match.end() + 140]})
        path_text = str(path.resolve())
        checksum = hashlib.sha256(data).hexdigest()
        sources.append([path_text, checksum])
        pages[seq] = {"file": path_text, "sha256": checksum, "header": page.header_text,
                      "body": normalize_for_search(body), "occurrences": dict(occurrences)}
    anchors = spec.get("reviewed_anchors", [])
    for anchor in anchors:
        seq, number = anchor["physical_sequence"], anchor["number"]
        if (seq not in pages or number not in pages[seq]["occurrences"]
                or pages[seq]["sha256"] != anchor["source_sha256"]):
            raise ValueError("Reviewed anchor no longer matches its OCR source")
    rejected = 0
    for number in list(nodes):
        allowed = {seq for seq in nodes[number] if all(
            seq <= a["physical_sequence"] if number < a["number"] else
            seq >= a["physical_sequence"] if number > a["number"] else
            seq == a["physical_sequence"] for a in anchors)}
        rejected += len(nodes[number]) - len(allowed)
        nodes[number] = allowed
    supported = supported_sequence_nodes(nodes)
    for anchor in anchors:
        key = anchor["number"], anchor["physical_sequence"]
        supported[key] = {"chain_length": 0, "witness": [key], "basis": "reviewed_anchor"}
    return {"spec": spec, "sources": sources, "pages": pages, "nodes": nodes,
            "supported": supported, "anchor_rejected_nodes": rejected}


def reference_tome(row):
    repair = json.loads(row.get("raw_json") or "{}").get("deterministic_repair") or {}
    if repair.get("tome"):
        return repair["tome"]
    literal = re.match(r"\s*([IVX]+)\s*,", row["ref_raw"])
    if literal:
        return literal[1]
    # A source-anchored isolated entry may carry a tome omitted by the old ref.
    citation = repair.get("candidate", {}).get("citation", "")
    tokens = list(re.finditer(r"([IVX]+)\s*,", citation))
    if len(tokens) == 1:
        return tokens[0][1]
    return None


def queries_for(row, greek_case_variants=False):
    queries = [*_search_queries(row), *name_head_queries(row)]
    repair = json.loads(row.get("raw_json") or "{}").get("deterministic_repair") or {}
    if repair.get("action") in {"reassign", "recover"}:
        # Only shorten a head independently recovered from an isolated OCR entry.
        head = repair["candidate"]["lemma"].split()[0].rstrip(".,;:")
        if len(head) >= 5 and head.isupper():
            queries.append(head)
    normalized = list(dict.fromkeys(normalize_for_search(q) for q in queries if q))
    if greek_case_variants:
        # Restrict this experiment to second-declension -os. Do not use a broad
        # fuzzy cutoff that could confuse a place in -ia with a person in -ios.
        for query in tuple(normalized):
            if re.fullmatch(r"[α-ω]{5,}οσ", query):
                normalized.extend(query[:-2] + ending for ending in ("ου", "ον", "ω", "ε"))
    return tuple(dict.fromkeys(normalized))


def audit(db, profile, report_dir, corpus_root=ROOT / "teste", limit=0):
    started = time.monotonic()
    with closing(connect_readonly(db)) as con:
        job = load_job(con, profile["index_volume"], limit)
    series = [build_series(spec, Path(corpus_root)) for spec in profile["series"]]

    @lru_cache(maxsize=16384)
    def text_matches(series_index, seq, queries):
        body = series[series_index]["pages"][seq]["body"]
        matches = []
        for query in queries:
            position = (" " + body + " ").find(" " + query + " ")
            if position >= 0:
                matches.append({"query": query, "matched": query, "similarity": 1.,
                                "excerpt": body[max(0, position - 90):position + len(query) + 130]})
            else:
                similarity, matched = _best_phrase_similarity(query, body)
                if similarity >= .92:
                    matches.append({"query": query, "matched": matched, "similarity": similarity})
        return matches

    records, counts = [], Counter()
    for row in job["rows"]:
        repair = json.loads(row["raw_json"]).get("deterministic_repair") or {}
        reason, candidates = None, []
        number, tome = cited_start(row), reference_tome(row)
        if repair.get("block_reason") in {"grouped_entry_requires_segmentation", "source_anchor_not_unique", "internal_work_locator"}:
            reason = "source_repair_pending"
        elif row["ref_kind"] not in {"editorial_page", "editorial_column", "editorial_page_line", "editorial_range"} or number is None:
            reason = "unsupported_reference"
        elif profile.get("requires_tome") and tome is None:
            reason = "tome_unknown"
        else:
            queries = queries_for(row, profile.get("greek_case_variants", False))
            searched = 0
            for i, item in enumerate(series):
                if item["spec"].get("tome") and item["spec"]["tome"] != tome:
                    continue
                for seq in sorted(item["nodes"].get(number, ())):
                    support = item["supported"].get((number, seq))
                    if not support:
                        continue
                    searched += 1
                    matches = text_matches(i, seq, queries)
                    if not matches:
                        continue
                    page = item["pages"][seq]
                    base_queries = queries_for(row)
                    matches = [{**m, "query_rule": "original" if m["query"] in base_queries
                                else "greek_second_declension_variant"} for m in matches]
                    candidates.append({"file": page["file"], "sha256": page["sha256"],
                        "series": item["spec"]["id"], "number": number, "header": page["header"],
                        "number_evidence": page["occurrences"][number],
                        "sequence_evidence": support, "text_matches": matches})
            files = {c["file"] for c in candidates}
            reason = ("unique_file_candidate" if len(files) == 1 else "multiple_file_candidates" if files
                      else "no_name_match" if searched else "no_supported_number")
        counts[reason] += 1
        records.append({"ref_id": row["ref_id"], "lemma": row["lemma_raw"], "ref_raw": row["ref_raw"],
                        "tome": tome, "reason": reason, "candidates": candidates})
    snapshots = {item["spec"]["id"]: item["sources"] for item in series}
    for sources in snapshots.values():
        for path, checksum in sources:
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != checksum:
                raise ValueError("OCR changed during audit")
    report = {"version": 1, "mode": "diagnostic_only", "application_supported": False,
              "limitations": ["Embedded integers can be footnote numbers, dates or other locators.",
                              "Name and number in the same file need not belong to the same editorial page.",
                              "Pages continuing after the marker are not searched or inferred.",
                              "A unique candidate is not a verified resolution."],
              "db": str(Path(db).resolve()), "profile": profile, "source_snapshots": snapshots,
              "sequence_rules": {"min_anchors": 7, "max_number_gap": 2, "max_file_gap": 3},
              "anchor_rejected_nodes": {s["spec"]["id"]: s["anchor_rejected_nodes"] for s in series},
              "counts": dict(counts), "processed": len(records), "records": records,
              "seconds": round(time.monotonic() - started, 2)}
    write_json(Path(report_dir) / (profile["index_volume"] + ".json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be nonnegative")
    report = audit(args.db, json.loads(args.profile.read_text()), args.report_dir, limit=args.limit)
    print(json.dumps({"processed": report["processed"], "counts": report["counts"], "seconds": report["seconds"]}))


if __name__ == "__main__":
    main()
