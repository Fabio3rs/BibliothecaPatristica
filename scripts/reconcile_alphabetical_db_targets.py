#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.indexing.alphabetical_compact_pipeline import (
    build_deterministic_locator_results,
    normalize_editorial_page_map,
)
from tools.indexing.editorial_page_estimator import estimate_editorial_pages
from tools.scripture.evidence_locator import (
    ScriptureEvidenceConfig,
    add_scripture_evidence_candidates,
)
from scripts.alphabetical_index_db import (
    DEFAULT_DB,
    connect_db,
    init_schema,
    refresh_volume_quality,
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_path(value: Any) -> Path:
    path = Path(str(value or "")).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 10000 else None


def _cited_pages(row: Any) -> list[int]:
    pages: list[int] = []
    for field in ("page_ref_int", "range_start_raw", "range_end_raw"):
        number = _positive_int(row[field])
        if number is not None:
            pages.append(number)
    start = _positive_int(row["range_start_raw"])
    end = _positive_int(row["range_end_raw"])
    if start is not None and end is not None:
        lower, upper = sorted((start, end))
        if upper - lower <= 32:
            pages.extend(range(lower, upper + 1))
    return list(dict.fromkeys(pages))


def _load_volume_items(
    con: Any,
    *,
    volume_id: str,
    page_map: dict[int, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], int]]:
    rows = con.execute(
        """
        SELECT r.ref_id, r.entry_key, r.ref_order, r.scripture_ref_order,
               r.ref_kind, r.ref_raw, r.page_ref_raw, r.page_ref_int,
               r.page_ref_col, r.line_ref_raw, r.range_start_raw,
               r.range_end_raw, r.section_start_file, r.editorial_anchor_file,
               e.entry_order, e.lemma_raw, e.entry_raw, e.context_raw,
               e.section_key, s.section_kind, s.heading_raw,
               s.file_start AS section_file_start,
               s.file_end AS section_file_end,
               sr.ref_raw AS scripture_ref_raw, sr.ref_role,
               sr.book_raw, sr.book_norm, sr.book_key,
               sr.chapter_start, sr.verse_start,
               sr.chapter_end, sr.verse_end
        FROM alphabetical_refs r
        JOIN alphabetical_entries e ON e.entry_key = r.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        JOIN alphabetical_scripture_refs sr
          ON sr.entry_key = r.entry_key
         AND sr.ref_order = r.scripture_ref_order
        WHERE s.volume_id = ?
          AND r.target_file IS NULL
          AND r.page_ref_int IS NOT NULL
        ORDER BY e.entry_order, r.ref_order
        """,
        (volume_id,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    ref_ids: dict[tuple[str, int], int] = {}
    for row in rows:
        pages = _cited_pages(row)
        candidates: list[dict[str, Any]] = []
        seen_files: set[str] = set()
        for page in pages:
            for raw_candidate in page_map.get(page, []):
                file_path = str(raw_candidate.get("file") or "").strip()
                if not file_path or file_path in seen_files:
                    continue
                seen_files.add(file_path)
                candidates.append(
                    {
                        **dict(raw_candidate),
                        "matched_page": page,
                    }
                )
        pair = (str(row["entry_key"]), int(row["ref_order"]))
        ref_ids[pair] = int(row["ref_id"])
        items.append(
            {
                "locator_key": f"{pair[0]}::ref:{pair[1]:06d}",
                "entry_key": pair[0],
                "ref_order": pair[1],
                "scripture_ref_order": int(row["scripture_ref_order"]),
                "entry_order": row["entry_order"],
                "section_key": row["section_key"],
                "section_kind": row["section_kind"],
                "section_heading": row["heading_raw"],
                "section_file_start": row["section_file_start"],
                "section_file_end": row["section_file_end"],
                "lemma_raw": row["lemma_raw"],
                "entry_excerpt": str(row["entry_raw"] or "")[:300],
                "context_excerpt": str(row["context_raw"] or "")[:300],
                "ref_kind": row["ref_kind"],
                "ref_raw": row["ref_raw"],
                "page_ref_raw": row["page_ref_raw"],
                "page_ref_int": row["page_ref_int"],
                "page_ref_col": row["page_ref_col"],
                "line_ref_raw": row["line_ref_raw"],
                "range_start_raw": row["range_start_raw"],
                "range_end_raw": row["range_end_raw"],
                "cited_pages": pages,
                "section_start_file": row["section_start_file"],
                "editorial_anchor_file": row["editorial_anchor_file"],
                "candidates": candidates,
                "scripture_ref": {
                    "ref_order": int(row["scripture_ref_order"]),
                    "ref_role": row["ref_role"],
                    "ref_raw": row["scripture_ref_raw"],
                    "book_raw": row["book_raw"],
                    "book_norm": row["book_norm"],
                    "book_key": row["book_key"],
                    "chapter_start": row["chapter_start"],
                    "verse_start": row["verse_start"],
                    "chapter_end": row["chapter_end"],
                    "verse_end": row["verse_end"],
                },
            }
        )
    return items, ref_ids


def _candidate_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_key": item["entry_key"],
        "ref_order": item["ref_order"],
        "cited_pages": item.get("cited_pages") or [],
        "scripture_ref": item.get("scripture_ref"),
        "candidates": [
            {
                "file": candidate.get("file"),
                "probability": candidate.get("probability"),
                "evidence_kinds": [
                    evidence.get("kind")
                    for evidence in candidate.get("evidence") or []
                    if isinstance(evidence, dict)
                ],
            }
            for candidate in item.get("candidates") or []
            if isinstance(candidate, dict)
        ],
    }


def reconcile_volume(
    con: Any,
    *,
    volume_id: str,
    apply: bool,
    max_candidates: int = 6,
) -> dict[str, Any]:
    volume = con.execute(
        """
        SELECT volume_id, collection, source_root
        FROM alphabetical_volumes
        WHERE volume_id = ?
        """,
        (volume_id,),
    ).fetchone()
    if volume is None:
        raise ValueError(f"volume not found in alphabetical DB: {volume_id}")
    collection = str(volume["collection"])
    source_root = _source_path(volume["source_root"])
    if not source_root.is_dir():
        raise FileNotFoundError(f"source_root not found for {volume_id}: {source_root}")
    estimator: dict[str, Any] = {}
    page_map: dict[int, list[dict[str, Any]]] = {}
    if collection in {"PG", "PL"}:
        estimator = estimate_editorial_pages(
            volume_id=volume_id,
            collection=collection,
            source_root=source_root,
            window=4,
        )
        page_map = normalize_editorial_page_map(estimator)
    items, ref_ids = _load_volume_items(
        con,
        volume_id=volume_id,
        page_map=page_map,
    )
    enriched, artifact = add_scripture_evidence_candidates(
        items,
        source_root=source_root,
        collection=collection,
        config=ScriptureEvidenceConfig(max_candidates=max_candidates),
    )
    resolved, pending = build_deterministic_locator_results(enriched)
    applied = 0
    touched_entries: set[str] = set()
    if apply:
        for result in resolved:
            pair = (str(result["entry_key"]), int(result["ref_order"]))
            ref_id = ref_ids[pair]
            row = con.execute(
                "SELECT raw_json FROM alphabetical_refs WHERE ref_id = ?",
                (ref_id,),
            ).fetchone()
            raw_json = _json_object(row["raw_json"] if row is not None else None)
            raw_json["compact_locator"] = {
                "status": "resolved",
                "method": "deterministic_dual_evidence",
                "evidence": result["evidence"],
            }
            con.execute(
                """
                UPDATE alphabetical_refs
                SET target_file = ?,
                    target_file_probability = ?,
                    locator_status = 'resolved',
                    raw_json = ?
                WHERE ref_id = ?
                  AND target_file IS NULL
                """,
                (
                    result["target_file"],
                    result["confidence"],
                    json.dumps(raw_json, ensure_ascii=False, sort_keys=True),
                    ref_id,
                ),
            )
            if con.execute("SELECT changes()").fetchone()[0]:
                applied += 1
                touched_entries.add(pair[0])
        for entry_key in touched_entries:
            best = con.execute(
                """
                SELECT target_file
                FROM alphabetical_refs
                WHERE entry_key = ?
                  AND locator_status = 'resolved'
                  AND target_file IS NOT NULL
                ORDER BY target_file_probability DESC, ref_order
                LIMIT 1
                """,
                (entry_key,),
            ).fetchone()
            con.execute(
                "UPDATE alphabetical_entries SET target_file_best = ? WHERE entry_key = ?",
                (best["target_file"] if best is not None else None, entry_key),
            )
        refresh_volume_quality(con, volume_id)
        con.commit()
    return {
        "volume_id": volume_id,
        "collection": collection,
        "source_root": str(source_root),
        "eligible_ref_count": len(items),
        "files_read": artifact["files_read"],
        "deterministic_resolved_count": len(resolved),
        "pending_count": len(pending),
        "applied_count": applied,
        "resolved": resolved,
        "items": [_candidate_summary(item) for item in enriched],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile unresolved alphabetical scripture targets from printed-page "
            "and OCR citation evidence. Runs per volume and is dry-run by default."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--volume-id", action="append", dest="volume_ids")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Explicitly process every volume in the DB; may scan a large corpus.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=6)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.volume_ids and not args.all:
        raise SystemExit("pass --volume-id (repeatable) or explicitly use --all")
    with connect_db(args.db) as con:
        init_schema(con)
        volume_ids = list(dict.fromkeys(args.volume_ids or []))
        if args.all:
            volume_ids = [
                str(row["volume_id"])
                for row in con.execute(
                    "SELECT volume_id FROM alphabetical_volumes ORDER BY volume_id"
                )
            ]
        reports = [
            reconcile_volume(
                con,
                volume_id=volume_id,
                apply=args.apply,
                max_candidates=args.max_candidates,
            )
            for volume_id in volume_ids
        ]
    payload = {
        "mode": "apply" if args.apply else "dry-run",
        "volume_count": len(reports),
        "eligible_ref_count": sum(item["eligible_ref_count"] for item in reports),
        "deterministic_resolved_count": sum(
            item["deterministic_resolved_count"] for item in reports
        ),
        "applied_count": sum(item["applied_count"] for item in reports),
        "volumes": reports,
    }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
