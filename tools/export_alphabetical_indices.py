#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/alphabetical_indices.db")
DEFAULT_OUT = Path("web/public/alpha")
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patristica_pipeline.scripture_book_catalog import (
    canonical_book_key,
    canonical_book_label,
)
from patristica_pipeline.index_pipeline_ownership import (
    alphabetical_section_ownership,
)

TARGET_BYTES = 350_000
PAGE_SPECIFIC_EVIDENCE_KINDS = {
    "cited_page_match",
    "direct_editorial_page",
    "editorial_header_match",
    "editorial_page_match",
    "header_pair",
    "neighbor_fit",
    "neighbor_sequence",
    "page_drift_explanation",
    "page_number_match",
    "pagination_sequence",
}

BASE_QUERY = """
select
  v.volume_id,
  v.collection,
  s.section_key,
  s.section_kind,
  s.heading_raw,
  s.pipeline_owner,
  s.alphabetical_role,
  s.file_start as section_file_start,
  s.file_end as section_file_end,
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
  e.target_file_best,
  e.confidence,
  r.ref_id,
  r.ref_order,
  r.scripture_ref_order,
  r.ref_kind,
  r.ref_raw,
  r.page_ref_raw,
  r.page_ref_int,
  r.target_file,
  r.locator_status,
  r.raw_json as ref_raw_json,
  sr.ref_role,
  sr.ref_raw as sref_raw,
  sr.ref_norm,
  sr.book_raw,
  sr.book_norm,
  sr.book_key,
  sr.chapter_start,
  sr.verse_start,
  sr.chapter_end,
  sr.verse_end,
  sr.is_range
from alphabetical_entries e
join alphabetical_sections s on s.section_key = e.section_key
join alphabetical_volumes v on v.volume_id = s.volume_id
join alphabetical_refs r on r.entry_key = e.entry_key
left join alphabetical_scripture_refs sr
  on sr.entry_key = r.entry_key
 and sr.ref_order = r.scripture_ref_order
where e.entry_kind not in ('heading_group', 'editorial_note')
  and s.pipeline_owner = 'alphabetical'
  and s.alphabetical_role = 'owned_section'
"""


@dataclass
class Record:
    domain: str
    label: str
    sort_key: str
    group_key: str
    page_group: str
    entry: dict[str, Any]
    approx_bytes: int
    book_slug: str | None = None
    book_label: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000;")
    return con


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate_excerpt(value: Any, max_chars: int = 220) -> str:
    text = normalize_space(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip(" ,;:.–—-") + " …"


def normalize_key(value: Any) -> str:
    text = normalize_space(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def slugify(value: Any) -> str:
    text = normalize_space(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "item"


def safe_group(key_norm: str) -> str:
    if not key_norm:
        return "#"
    head = key_norm[0]
    return head if re.match(r"[A-Z0-9]", head) else "#"


def repo_relative_path(value: Any) -> str | None:
    text = normalize_space(value)
    if not text:
        return None
    path = Path(text)
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return path.as_posix().lstrip("./")


def parse_file_page(value: Any) -> int | None:
    text = normalize_space(value)
    if not text:
        return None
    match = re.search(r"-(\d+)\.txt$", Path(text).name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def row_value(row: sqlite3.Row | dict[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def target_has_verified_evidence(row: sqlite3.Row | dict[str, Any]) -> bool:
    if row_value(row, "locator_status") not in (None, "resolved"):
        return False
    raw_json = row_value(row, "ref_raw_json")
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return False
    if not isinstance(raw_json, dict):
        return False
    locator = raw_json.get("compact_locator")
    if not isinstance(locator, dict) or locator.get("status") != "resolved":
        return False
    evidence = locator.get("evidence")
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        kind = normalize_space(item.get("kind")).casefold()
        if kind in PAGE_SPECIFIC_EVIDENCE_KINDS or any(
            marker in kind
            for marker in ("page", "pagina", "header", "neighbor", "pagination")
        ):
            return True
    return False


def target_is_index_source(row: sqlite3.Row | dict[str, Any]) -> bool:
    target = normalize_space(row_value(row, "target_file"))
    if not target:
        return False
    start = normalize_space(row_value(row, "section_file_start"))
    end = normalize_space(row_value(row, "section_file_end"))
    if target in {start, end}:
        return True
    target_page = parse_file_page(target)
    start_page = parse_file_page(start)
    end_page = parse_file_page(end)
    if target_page is None or start_page is None or end_page is None:
        return False
    if Path(target).parent != Path(start).parent or Path(target).parent != Path(end).parent:
        return False
    lower, upper = sorted((start_page, end_page))
    return lower <= target_page <= upper


def pick_target_file(row: sqlite3.Row | dict[str, Any]) -> str | None:
    target = normalize_space(row_value(row, "target_file"))
    if (
        not target
        or target_is_index_source(row)
        or not target_has_verified_evidence(row)
    ):
        return None
    return target


def pick_page(row: sqlite3.Row) -> int | None:
    for value in (row["page_ref_int"], row["inferred_printed_page"]):
        if isinstance(value, int):
            return value
    return None


def pick_viewer_page(row: sqlite3.Row) -> int | None:
    return parse_file_page(pick_target_file(row))


def public_shard_path(domain: str, path: str) -> str:
    normalized = str(path or "").lstrip("/")
    if normalized.startswith("alpha/"):
        return normalized
    return f"alpha/{domain}/{normalized}"


def classify_domain(row: sqlite3.Row) -> str | None:
    section_kind = row["section_kind"]
    entry_kind = row["entry_kind"]
    owned_by_alphabetical, _ = alphabetical_section_ownership(
        {
            "section_kind": section_kind,
            "heading_raw": row_value(row, "heading_raw"),
            "raw_json": {
                "pipeline_owner": row_value(row, "pipeline_owner"),
                "alphabetical_role": row_value(row, "alphabetical_role"),
            },
        }
    )
    if not owned_by_alphabetical:
        return None
    if (
        entry_kind in {"scripture_citation", "scripture_pericope"}
        and row_value(row, "ref_role") is None
    ):
        return None
    if section_kind in {"analytic_subject", "alphabetical_general"} and entry_kind == "lemma":
        return "subjects"
    if section_kind in {"scripture_index", "pericope_index", "concordance_index"} and entry_kind in {
        "scripture_citation",
        "scripture_pericope",
        "concordance_item",
        "lemma",
    }:
        return "scripture"
    if section_kind in {"onomastic_person", "onomastic_place", "onomastic_mixed", "author_index"} and entry_kind in {
        "lemma",
        "sublemma",
        "cross_reference",
    }:
        return "names"
    return None


def make_entry(row: sqlite3.Row, domain: str) -> dict[str, Any]:
    label = normalize_space(row["lemma_display"] or row["lemma_raw"] or row["sref_raw"] or row["entry_raw"])
    snippet_source = row["context_raw"] if row["context_raw"] and row["context_raw"] != row["entry_raw"] else row["entry_raw"]
    book_label = normalize_space(row["book_norm"] or row["book_raw"])
    book_tradition = (
        "vulgate_migne"
        if row["collection"] in {"PG", "PL"}
        else "po_french_editorial"
        if row["collection"] == "PO"
        else None
    )
    book_key = (
        normalize_space(row_value(row, "book_key"))
        or canonical_book_key(
            row["book_raw"],
            tradition=book_tradition,
        )
        or canonical_book_key(
            row["book_norm"],
            tradition=book_tradition,
        )
    )
    if book_key:
        book_label = canonical_book_label(book_key) or book_label
    ref_order = row["ref_order"]
    target_file = pick_target_file(row)
    occurrence_id = (
        f"{row['entry_key']}:ref:{int(ref_order):04d}"
        if isinstance(ref_order, int)
        else row["entry_key"]
    )
    return {
        "occurrence_id": occurrence_id,
        "entry_key": row["entry_key"],
        "ref_order": ref_order,
        "scripture_ref_order": row_value(row, "scripture_ref_order"),
        "label": label,
        "translation": None,
        "collection": row["collection"],
        "volume_id": row["volume_id"],
        "section_key": row["section_key"],
        "section_kind": row["section_kind"],
        "entry_kind": row["entry_kind"],
        "heading": normalize_space(row["heading_raw"]),
        "snippet": truncate_excerpt(snippet_source),
        "ref_kind": row["ref_kind"] or row["ref_role"] or "",
        "ref_label": normalize_space(row["page_ref_raw"] or row["ref_raw"] or row["sref_raw"]),
        "page_ref": pick_page(row),
        "viewer_page": pick_viewer_page(row),
        "target_file": repo_relative_path(target_file),
        "locator_status": (
            "suspect_index_source"
            if row["target_file"] and target_is_index_source(row)
            else "unverified"
            if row["target_file"] and not target_has_verified_evidence(row)
            else "resolved"
            if target_file
            else "unresolved"
        ),
        "confidence": row["confidence"],
        "book_label": book_label or None,
        "book_slug": (
            slugify(book_key)
            if domain == "scripture" and book_key
            else None
        ),
        "chapter_start": row["chapter_start"],
        "verse_start": row["verse_start"],
        "chapter_end": row["chapter_end"],
        "verse_end": row["verse_end"],
        "is_range": bool(row["is_range"]) if row["is_range"] is not None else False,
    }


def make_record(row: sqlite3.Row) -> Record | None:
    domain = classify_domain(row)
    if domain is None:
        return None
    entry = make_entry(row, domain)
    label = entry["label"] or normalize_space(row["entry_raw"])
    normalized = normalize_key(row["lemma_sort"] or row["lemma_norm"] or row["ref_norm"] or label)
    group = safe_group(normalized)
    page_group = "Outros" if group == "#" else group
    sort_key = normalized or normalize_key(label) or slugify(label)
    if domain == "scripture":
        book_label = entry["book_label"] or "Outros"
        book_slug = entry["book_slug"] or "outros"
        chapter = int(row["chapter_start"] or 0)
        verse = int(row["verse_start"] or 0)
        sort_key = "|".join(
            [
                normalize_key(book_label) or "ZZZ",
                f"{chapter:05d}",
                f"{verse:05d}",
                sort_key or "ZZZ",
            ]
        )
        page_group = book_label
        group = book_slug
    sort_key = "|".join(
        [
            sort_key,
            f"{int(row['ref_order'] or 0):08d}",
            str(row["entry_key"]),
        ]
    )
    payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Record(
        domain=domain,
        label=label,
        sort_key=sort_key,
        group_key=group,
        page_group=page_group,
        entry=entry,
        approx_bytes=len(payload),
        book_slug=entry["book_slug"],
        book_label=entry["book_label"],
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.suffix == ".gz":
        with gzip.open(path, "wb", compresslevel=9) as fh:
            fh.write(data)
        return
    path.write_bytes(data)


def prune_stale_generated_files(
    directory: Path,
    *,
    pattern: str,
    expected_names: set[str],
) -> int:
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob(pattern):
        if path.is_file() and path.name not in expected_names:
            path.unlink()
            removed += 1
    return removed


def paginate(items: list[Record], page_size: int, page_id_prefix: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for idx in range(0, len(items), page_size):
        chunk = items[idx:idx + page_size]
        pages.append(
            {
                "id": f"{page_id_prefix}-p{len(pages) + 1:03d}",
                "group": chunk[0].page_group,
                "headword_start": chunk[0].label,
                "headword_end": chunk[-1].label,
                "item_count": len(chunk),
                "items": [item.entry for item in chunk],
            }
        )
    return pages


def build_balanced_shards(records: list[Record], page_size: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[Record]] = defaultdict(list)
    for record in sorted(records, key=lambda item: item.sort_key):
        grouped[safe_group(record.sort_key)].append(record)

    shards: list[dict[str, Any]] = []
    pages_meta: list[dict[str, Any]] = []
    groups_meta: list[dict[str, Any]] = []
    group_to_pages: dict[str, list[str]] = defaultdict(list)
    group_to_items: dict[str, int] = defaultdict(int)

    for group in sorted(grouped.keys(), key=lambda item: (item == "#", item)):
        items = grouped[group]
        label = "Outros" if group == "#" else group
        bucket: list[Record] = []
        bucket_bytes = 0
        shard_index = 1
        for item in items:
            if bucket and bucket_bytes + item.approx_bytes > TARGET_BYTES:
                shard_id = f"{group}-{shard_index:03d}"
                pages = paginate(bucket, page_size, shard_id)
                shards.append(
                    {
                        "id": shard_id,
                        "group": label,
                        "path": f"{shard_id}.json.gz",
                        "item_count": len(bucket),
                        "page_count": len(pages),
                        "headword_start": bucket[0].label,
                        "headword_end": bucket[-1].label,
                        "pages": pages,
                    }
                )
                for position, page in enumerate(pages):
                    page_meta = {
                        "id": page["id"],
                        "group": label,
                        "headword_start": page["headword_start"],
                        "headword_end": page["headword_end"],
                        "item_count": page["item_count"],
                        "shard_id": shard_id,
                        "shard_path": f"shards/{shard_id}.json.gz",
                        "position": len(pages_meta),
                        "group_position": position,
                    }
                    pages_meta.append(page_meta)
                    group_to_pages[label].append(page["id"])
                    group_to_items[label] += page["item_count"]
                shard_index += 1
                bucket = []
                bucket_bytes = 0
            bucket.append(item)
            bucket_bytes += item.approx_bytes
        if bucket:
            shard_id = f"{group}-{shard_index:03d}"
            pages = paginate(bucket, page_size, shard_id)
            shards.append(
                {
                    "id": shard_id,
                    "group": label,
                    "path": f"{shard_id}.json.gz",
                    "item_count": len(bucket),
                    "page_count": len(pages),
                    "headword_start": bucket[0].label,
                    "headword_end": bucket[-1].label,
                    "pages": pages,
                }
            )
            for position, page in enumerate(pages):
                page_meta = {
                    "id": page["id"],
                    "group": label,
                    "headword_start": page["headword_start"],
                    "headword_end": page["headword_end"],
                    "item_count": page["item_count"],
                    "shard_id": shard_id,
                    "shard_path": f"shards/{shard_id}.json.gz",
                    "position": len(pages_meta),
                    "group_position": position,
                }
                pages_meta.append(page_meta)
                group_to_pages[label].append(page["id"])
                group_to_items[label] += page["item_count"]

    for idx, page in enumerate(pages_meta):
        page["prev_page_id"] = pages_meta[idx - 1]["id"] if idx > 0 else None
        page["next_page_id"] = pages_meta[idx + 1]["id"] if idx + 1 < len(pages_meta) else None

    for label in sorted(group_to_pages.keys(), key=lambda item: (item == "Outros", item)):
        group_pages = [page for page in pages_meta if page["group"] == label]
        groups_meta.append(
            {
                "id": slugify(label),
                "label": label,
                "page_count": len(group_pages),
                "item_count": group_to_items[label],
                "headword_start": group_pages[0]["headword_start"],
                "headword_end": group_pages[-1]["headword_end"],
                "page_ids": [page["id"] for page in group_pages],
            }
        )

    return shards, pages_meta, groups_meta


def build_scripture(records: list[Record], page_size: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_book: dict[str, list[Record]] = defaultdict(list)
    book_labels: dict[str, str] = {}
    for record in sorted(records, key=lambda item: item.sort_key):
        slug = record.book_slug or "outros"
        label = "Outros" if slug == "outros" else record.book_label or "Outros"
        by_book[slug].append(record)
        book_labels[slug] = label

    shards: list[dict[str, Any]] = []
    pages_meta: list[dict[str, Any]] = []
    books_meta: list[dict[str, Any]] = []
    groups_meta: list[dict[str, Any]] = []

    for slug in sorted(by_book.keys()):
        items = by_book[slug]
        label = book_labels[slug]
        bucket: list[Record] = []
        bucket_bytes = 0
        shard_index = 1
        book_page_ids: list[str] = []
        for item in items:
            if bucket and bucket_bytes + item.approx_bytes > TARGET_BYTES:
                shard_id = f"{slug}-{shard_index:03d}"
                pages = paginate(bucket, page_size, shard_id)
                shards.append(
                    {
                        "id": shard_id,
                        "group": label,
                        "book_slug": slug,
                        "path": f"{shard_id}.json.gz",
                        "item_count": len(bucket),
                        "page_count": len(pages),
                        "headword_start": bucket[0].label,
                        "headword_end": bucket[-1].label,
                        "pages": pages,
                    }
                )
                for position, page in enumerate(pages):
                    page_meta = {
                        "id": page["id"],
                        "group": label,
                        "book_slug": slug,
                        "book_label": label,
                        "headword_start": page["headword_start"],
                        "headword_end": page["headword_end"],
                        "item_count": page["item_count"],
                        "shard_id": shard_id,
                        "shard_path": f"shards/{shard_id}.json.gz",
                        "position": len(pages_meta),
                        "book_position": len(book_page_ids) + position,
                    }
                    pages_meta.append(page_meta)
                    book_page_ids.append(page["id"])
                shard_index += 1
                bucket = []
                bucket_bytes = 0
            bucket.append(item)
            bucket_bytes += item.approx_bytes
        if bucket:
            shard_id = f"{slug}-{shard_index:03d}"
            pages = paginate(bucket, page_size, shard_id)
            shards.append(
                {
                    "id": shard_id,
                    "group": label,
                    "book_slug": slug,
                    "path": f"{shard_id}.json.gz",
                    "item_count": len(bucket),
                    "page_count": len(pages),
                    "headword_start": bucket[0].label,
                    "headword_end": bucket[-1].label,
                    "pages": pages,
                }
            )
            for position, page in enumerate(pages):
                page_meta = {
                    "id": page["id"],
                    "group": label,
                    "book_slug": slug,
                    "book_label": label,
                    "headword_start": page["headword_start"],
                    "headword_end": page["headword_end"],
                    "item_count": page["item_count"],
                    "shard_id": shard_id,
                    "shard_path": f"shards/{shard_id}.json.gz",
                    "position": len(pages_meta),
                    "book_position": len(book_page_ids) + position,
                }
                pages_meta.append(page_meta)
                book_page_ids.append(page["id"])

        own_pages = [page for page in pages_meta if page.get("book_slug") == slug]
        books_meta.append(
            {
                "slug": slug,
                "label": label,
                "item_count": sum(page["item_count"] for page in own_pages),
                "page_count": len(own_pages),
                "headword_start": own_pages[0]["headword_start"],
                "headword_end": own_pages[-1]["headword_end"],
                "page_ids": [page["id"] for page in own_pages],
            }
        )
        groups_meta.append(
            {
                "id": slug,
                "label": label,
                "page_count": len(own_pages),
                "item_count": sum(page["item_count"] for page in own_pages),
                "headword_start": own_pages[0]["headword_start"],
                "headword_end": own_pages[-1]["headword_end"],
                "page_ids": [page["id"] for page in own_pages],
            }
        )

    for book in books_meta:
        own_pages = [page for page in pages_meta if page.get("book_slug") == book["slug"]]
        for idx, page in enumerate(own_pages):
            page["prev_page_id"] = own_pages[idx - 1]["id"] if idx > 0 else None
            page["next_page_id"] = own_pages[idx + 1]["id"] if idx + 1 < len(own_pages) else None

    return shards, pages_meta, groups_meta, books_meta


def collect_volume_count(records: list[Record]) -> int:
    return len({record.entry["volume_id"] for record in records})


def collect_section_count(records: list[Record]) -> int:
    return len({record.entry["section_key"] for record in records})


def build_domain(domain: str, records: list[Record], out_dir: Path, generated_at: str) -> dict[str, Any]:
    page_size = 84 if domain == "subjects" else 72
    if domain == "scripture":
        shards, pages_meta, groups_meta, books_meta = build_scripture(records, page_size)
    else:
        shards, pages_meta, groups_meta = build_balanced_shards(records, page_size)
        books_meta = []

    domain_dir = out_dir / domain
    shards_dir = domain_dir / "shards"
    for shard in shards:
        write_json(
            shards_dir / shard["path"],
            {
                "domain": domain,
                "shard_id": shard["id"],
                "group": shard["group"],
                "book_slug": shard.get("book_slug"),
                "headword_start": shard["headword_start"],
                "headword_end": shard["headword_end"],
                "item_count": shard["item_count"],
                "page_count": shard["page_count"],
                "pages": shard["pages"],
            },
        )
    prune_stale_generated_files(
        shards_dir,
        pattern="*.json.gz",
        expected_names={str(shard["path"]) for shard in shards},
    )

    group_pages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages_meta:
        key = page.get("book_slug") if domain == "scripture" else slugify(page["group"])
        group_pages[key].append(page)

    enriched_groups = []
    for group in groups_meta:
        group_id = group["id"]
        group_manifest_path = f"alpha/{domain}/groups/{group_id}.json"
        pages_for_group = group_pages.get(group_id, [])
        write_json(
            domain_dir / "groups" / f"{group_id}.json",
            {
                "domain": domain,
                "generated_at": generated_at,
                "group": {
                    **group,
                    "manifest_path": group_manifest_path,
                },
                "pages": pages_for_group,
            },
        )
        enriched_groups.append(
            {
                **{k: v for k, v in group.items() if k != "page_ids"},
                "manifest_path": group_manifest_path,
            }
        )
    prune_stale_generated_files(
        domain_dir / "groups",
        pattern="*.json",
        expected_names={f"{group['id']}.json" for group in groups_meta},
    )

    enriched_books = []
    for book in books_meta:
        group_id = book["slug"]
        enriched_books.append(
            {
                **book,
                "manifest_path": f"alpha/{domain}/groups/{group_id}.json",
            }
        )

    manifest = {
        "domain": domain,
        "generated_at": generated_at,
        "stats": {
            "item_count": len(records),
            "page_count": len(pages_meta),
            "shard_count": len(shards),
            "volume_count": collect_volume_count(records),
            "section_count": collect_section_count(records),
        },
        "groups": enriched_groups,
        "books": enriched_books,
    }
    write_json(domain_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    args = parse_args()
    generated_at = now_iso()
    args.out.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[Record]] = defaultdict(list)

    with connect(args.db) as con:
        for row in con.execute(BASE_QUERY):
            record = make_record(row)
            if record is None:
                continue
            grouped[record.domain].append(record)

    domain_meta: list[dict[str, Any]] = []
    for domain in ("subjects", "names", "scripture"):
        records = grouped.get(domain, [])
        manifest = build_domain(domain, records, args.out, generated_at)
        domain_meta.append(
            {
                "id": domain,
                "manifest_path": f"alpha/{domain}/manifest.json",
                "item_count": manifest["stats"]["item_count"],
                "page_count": manifest["stats"]["page_count"],
                "shard_count": manifest["stats"]["shard_count"],
                "volume_count": manifest["stats"]["volume_count"],
                "section_count": manifest["stats"]["section_count"],
                "group_count": len(manifest["groups"]),
                "book_count": len(manifest["books"]),
            }
        )

    write_json(
        args.out / "manifest.json",
        {
            "generated_at": generated_at,
            "source_db": args.db.as_posix(),
            "domains": domain_meta,
        },
    )
    print(f"[OK] Exported alphabetical indices to {args.out}")


if __name__ == "__main__":
    main()
