#!/usr/bin/env python3
"""Analyze public-export shard strategies for alphabetical index data.

Usage:
  python scripts/index_extraction/analyze_alpha_sharding.py
  python scripts/index_extraction/analyze_alpha_sharding.py --domain subjects --target-bytes 350000
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "alphabetical_indices.db"


BASE_QUERY = """
select
  v.volume_id,
  v.collection,
  s.section_key,
  s.section_kind,
  s.heading_raw,
  e.entry_key,
  e.entry_kind,
  e.lemma_raw,
  e.lemma_display,
  e.lemma_norm,
  e.lemma_sort,
  e.entry_raw,
  e.context_raw,
  e.heading_letter,
  e.inferred_printed_page,
  e.editorial_anchor_file,
  e.target_file_best,
  e.confidence,
  r.ref_kind,
  r.ref_raw,
  r.page_ref_raw,
  r.page_ref_int,
  r.page_ref_col,
  r.target_file,
  sr.ref_role,
  sr.ref_raw as sref_raw,
  sr.ref_norm,
  sr.book_norm,
  sr.chapter_start,
  sr.verse_start,
  sr.chapter_end,
  sr.verse_end,
  sr.is_range
from alphabetical_entries e
join alphabetical_sections s on s.section_key = e.section_key
join alphabetical_volumes v on v.volume_id = s.volume_id
left join alphabetical_refs r on r.entry_key = e.entry_key and r.ref_order = 1
left join alphabetical_scripture_refs sr on sr.entry_key = e.entry_key and sr.ref_order = 1
where e.entry_kind not in ('heading_group', 'editorial_note')
"""


@dataclass
class PublicRecord:
    domain: str
    key_norm: str
    key_1: str
    key_2: str
    raw_bytes: int
    gzip_bytes: int


def norm_space(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate_excerpt(text: str, *, max_chars: int = 280) -> str:
    cleaned = norm_space(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip(" ,;:.–—-") + " …"


def normalize_key(value: object) -> str:
    text = norm_space(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def safe_bucket_1(key_norm: str) -> str:
    if not key_norm:
        return "#"
    head = key_norm[0]
    return head if re.match(r"[A-Z0-9]", head) else "#"


def safe_bucket_2(key_norm: str) -> str:
    if not key_norm:
        return "#"
    if len(key_norm) == 1:
        return safe_bucket_1(key_norm)
    head = key_norm[:2]
    if all(re.match(r"[A-Z0-9]", ch) for ch in head):
        return head
    return safe_bucket_1(key_norm)


def build_public_row(row: sqlite3.Row) -> dict[str, object]:
    title = norm_space(row["lemma_display"] or row["lemma_raw"] or row["sref_raw"] or row["entry_raw"])
    snippet_source = row["context_raw"] if row["context_raw"] and row["context_raw"] != row["entry_raw"] else row["entry_raw"]
    page = row["page_ref_int"] or row["inferred_printed_page"]
    return {
        "volumeId": row["volume_id"],
        "collection": row["collection"],
        "sectionKey": row["section_key"],
        "sectionKind": row["section_kind"],
        "heading": norm_space(row["heading_raw"]),
        "entryKey": row["entry_key"],
        "entryKind": row["entry_kind"],
        "title": title,
        "lemmaNorm": row["lemma_norm"] or "",
        "lemmaSort": row["lemma_sort"] or "",
        "headingLetter": row["heading_letter"] or "",
        "snippet": truncate_excerpt(snippet_source or "", max_chars=280),
        "refKind": row["ref_kind"] or "",
        "ref": norm_space(row["page_ref_raw"] or row["ref_raw"] or row["sref_raw"]),
        "page": page,
        "bookNorm": row["book_norm"] or "",
        "refNorm": row["ref_norm"] or "",
        "refRole": row["ref_role"] or "",
        "isRange": row["is_range"] if row["is_range"] is not None else 0,
        "confidence": row["confidence"],
    }


def classify_domain(row: sqlite3.Row) -> str | None:
    section_kind = row["section_kind"]
    entry_kind = row["entry_kind"]
    if section_kind in {"analytic_subject", "alphabetical_general", "ordo_rerum"} and entry_kind == "lemma":
        return "subjects"
    if (
        section_kind in {"scripture_index", "pericope_index", "concordance_index"}
        or row["ref_norm"] is not None
    ) and entry_kind in {"scripture_citation", "scripture_pericope", "concordance_item", "lemma"}:
        return "scripture"
    if section_kind in {"onomastic_person", "onomastic_place", "onomastic_mixed", "author_index"} and entry_kind in {
        "lemma",
        "sublemma",
        "cross_reference",
    }:
        return "names"
    return None


def iter_public_records(con: sqlite3.Connection) -> list[PublicRecord]:
    records: list[PublicRecord] = []
    for row in con.execute(BASE_QUERY):
        domain = classify_domain(row)
        if domain is None:
            continue
        public_row = build_public_row(row)
        payload = json.dumps(public_row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        key_norm = normalize_key(public_row["title"] or public_row["lemmaNorm"] or public_row["refNorm"])
        records.append(
            PublicRecord(
                domain=domain,
                key_norm=key_norm,
                key_1=safe_bucket_1(key_norm),
                key_2=safe_bucket_2(key_norm),
                raw_bytes=len(payload),
                gzip_bytes=len(gzip.compress(payload, compresslevel=9)),
            )
        )
    return records


def summarize_shards(name: str, shard_map: dict[str, list[PublicRecord]], *, size_attr: str) -> dict[str, object]:
    sizes = [sum(getattr(item, size_attr) for item in items) for items in shard_map.values()]
    sizes.sort(reverse=True)
    counts = [len(items) for items in shard_map.values()]
    counts.sort(reverse=True)
    return {
        "strategy": name,
        "shards": len(shard_map),
        "max_bytes": sizes[0] if sizes else 0,
        "p95_bytes": sizes[max(0, int(len(sizes) * 0.05) - 1)] if sizes else 0,
        "median_bytes": int(statistics.median(sizes)) if sizes else 0,
        "max_items": counts[0] if counts else 0,
        "median_items": int(statistics.median(counts)) if counts else 0,
        "top_shards": [
            {
                "key": key,
                "items": len(items),
                "bytes": sum(getattr(item, size_attr) for item in items),
            }
            for key, items in sorted(
                shard_map.items(),
                key=lambda kv: sum(getattr(item, size_attr) for item in kv[1]),
                reverse=True,
            )[:20]
        ],
    }


def make_alpha_shards(records: list[PublicRecord], key_fn: Callable[[PublicRecord], str]) -> dict[str, list[PublicRecord]]:
    shards: dict[str, list[PublicRecord]] = defaultdict(list)
    for record in records:
        shards[key_fn(record)].append(record)
    return dict(shards)


def make_balanced_shards(records: list[PublicRecord], *, target_bytes: int, size_attr: str) -> dict[str, list[PublicRecord]]:
    by_prefix: dict[str, list[PublicRecord]] = defaultdict(list)
    for record in records:
        by_prefix[record.key_1].append(record)

    shards: dict[str, list[PublicRecord]] = {}
    for prefix, items in sorted(by_prefix.items()):
        items = sorted(items, key=lambda item: item.key_norm)
        bucket: list[PublicRecord] = []
        bucket_bytes = 0
        bucket_index = 1
        for item in items:
            item_bytes = getattr(item, size_attr)
            if bucket and bucket_bytes + item_bytes > target_bytes:
                shards[f"{prefix}-{bucket_index:03d}"] = bucket
                bucket_index += 1
                bucket = []
                bucket_bytes = 0
            bucket.append(item)
            bucket_bytes += item_bytes
        if bucket:
            shards[f"{prefix}-{bucket_index:03d}"] = bucket
    return shards


def print_domain_report(records: list[PublicRecord], *, target_bytes: int, size_attr: str) -> None:
    total_bytes = sum(getattr(record, size_attr) for record in records)
    one_char = make_alpha_shards(records, lambda item: item.key_1)
    two_char = make_alpha_shards(records, lambda item: item.key_2)
    balanced = make_balanced_shards(records, target_bytes=target_bytes, size_attr=size_attr)

    print(
        json.dumps(
            {
                "records": len(records),
                "bytes_field": size_attr,
                "total_bytes": total_bytes,
                "one_char": summarize_shards("one_char", one_char, size_attr=size_attr),
                "two_char": summarize_shards("two_char", two_char, size_attr=size_attr),
                "balanced": summarize_shards(
                    f"balanced_{target_bytes}",
                    balanced,
                    size_attr=size_attr,
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze alphabetical-index shard strategies for frontend export.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB path")
    parser.add_argument(
        "--domain",
        choices=["subjects", "scripture", "names", "all"],
        default="all",
        help="Which public domain to analyze",
    )
    parser.add_argument(
        "--target-bytes",
        type=int,
        default=350_000,
        help="Target shard size for balanced strategy (default: 350000)",
    )
    parser.add_argument(
        "--size-attr",
        choices=["raw_bytes", "gzip_bytes"],
        default="raw_bytes",
        help="Use per-record raw or gzip size for balancing and summaries",
    )
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    records = iter_public_records(con)

    domains = ["subjects", "scripture", "names"] if args.domain == "all" else [args.domain]
    for domain in domains:
        subset = [record for record in records if record.domain == domain]
        print(f"## {domain}")
        print_domain_report(subset, target_bytes=args.target_bytes, size_attr=args.size_attr)


if __name__ == "__main__":
    main()
