#!/usr/bin/env python3
"""Build compact, shared JSON.gz payloads for the unified Scripture viewer."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from poc_scripture_reference_payloads import (
    cited_pages_by_volume,
    compact_json_bytes,
    load_ocr_titles,
    load_references,
    load_volume_metadata,
    load_work_intervals,
    resolve_raw_files,
    truncate_title,
    work_titles_for_pages,
)


ROOT = Path(__file__).resolve().parents[1]
TITLE_SOURCE_OCR = 1
TITLE_SOURCE_WORK_INDEX = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=ROOT / "web" / "public")
    parser.add_argument("--ocr-root", type=Path, default=ROOT / "teste")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "web" / "public" / "scripture" / "references" / "v1",
    )
    parser.add_argument("--preview-limit", type=int, default=120)
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--title-limit", type=int, default=80)
    return parser.parse_args()


def write_gzip_json(path: Path, payload: object) -> tuple[int, int]:
    raw = compact_json_bytes(payload)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return len(raw), len(compressed)


def main() -> int:
    args = parse_args()
    if args.preview_limit < 0 or args.page_size <= 0 or args.title_limit <= 0:
        raise SystemExit("preview-limit, page-size e title-limit devem ser positivos")

    public_dir = args.public.resolve()
    ocr_root = args.ocr_root.resolve()
    output_dir = args.out.resolve()
    if public_dir not in output_dir.parents:
        raise SystemExit("--out deve estar dentro de --public")

    print("[scripture-references] carregando referências", flush=True)
    references, _scripture_info = load_references(public_dir, args.preview_limit)
    pages_by_volume = cited_pages_by_volume(references)
    unique_pages = sum(len(pages) for pages in pages_by_volume.values())
    volume_meta, _volumes_payload = load_volume_metadata(public_dir)
    raw_files, raw_stats = resolve_raw_files(public_dir, volume_meta, pages_by_volume)
    ocr_titles_full, ocr_stats = load_ocr_titles(ocr_root, raw_files)
    work_intervals, work_stats = load_work_intervals(public_dir, volume_meta, pages_by_volume)
    work_titles_full = work_titles_for_pages(work_intervals, pages_by_volume)
    ocr_titles = {
        key: title
        for key, value in ocr_titles_full.items()
        if (title := truncate_title(value, args.title_limit))
    }
    work_titles = {
        key: title
        for key, value in work_titles_full.items()
        if (title := truncate_title(value, args.title_limit))
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-build-", dir=output_dir.parent)
    )
    raw_total = 0
    gzip_total = 0
    payload_files = 0
    titled_pages: set[tuple[str, int]] = set()
    try:
        for reference_ordinal, reference in enumerate(references, start=1):
            book_slug = reference.book_key.replace(" ", "-")
            page_count = math.ceil(len(reference.locations) / args.page_size)
            for page_index in range(page_count):
                chunk = reference.locations[
                    page_index * args.page_size : (page_index + 1) * args.page_size
                ]
                volumes = list(dict.fromkeys(volume for volume, _page, _mask in chunk))
                volume_ids = {volume: index for index, volume in enumerate(volumes)}
                rows: list[list[object]] = []
                for volume, page, mask in chunk:
                    row: list[object] = [volume_ids[volume], page, mask]
                    page_key = (volume, page)
                    title = ocr_titles.get(page_key)
                    title_source = TITLE_SOURCE_OCR
                    if not title:
                        title = work_titles.get(page_key)
                        title_source = TITLE_SOURCE_WORK_INDEX
                    if title:
                        row.extend([title, title_source])
                        titled_pages.add(page_key)
                    rows.append(row)
                page_number = page_index + 1
                payload = {
                    "v": 1,
                    "b": [reference.book_key, reference.book_label],
                    "r": [reference.slug, reference.locator],
                    "p": [
                        page_number,
                        page_count,
                        len(reference.locations),
                        page_index * args.page_size + 1,
                        min((page_index + 1) * args.page_size, len(reference.locations)),
                    ],
                    "d": [volumes, rows],
                }
                path = (
                    temporary_dir
                    / book_slug
                    / reference.slug
                    / f"page-{page_number}.json.gz"
                )
                raw_size, gzip_size = write_gzip_json(path, payload)
                raw_total += raw_size
                gzip_total += gzip_size
                payload_files += 1
            if reference_ordinal % 100 == 0:
                print(
                    f"[scripture-references] payloads: {reference_ordinal}/{len(references)} referências",
                    flush=True,
                )

        manifest = {
            "v": 1,
            "schema": {
                "book": ["key", "label"],
                "reference": ["slug", "locator"],
                "page": ["number", "count", "total_locations", "range_start", "range_end"],
                "location": ["volume_index", "page", "source_mask", "title?", "title_source?"],
                "title_source": {"1": "ocr_header", "2": "work_index"},
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "preview_limit": args.preview_limit,
            "page_size": args.page_size,
            "references": len(references),
            "payload_files": payload_files,
            "location_occurrences": sum(len(reference.locations) for reference in references),
            "unique_volume_pages": unique_pages,
            "titled_unique_pages": len(titled_pages),
            "title_coverage": round(len(titled_pages) / unique_pages, 6) if unique_pages else 0.0,
            "raw_bytes": raw_total,
            "gzip_bytes": gzip_total,
            "source_diagnostics": {
                "raw_mapping": {
                    **raw_stats,
                    "mapped_pages": len(raw_files),
                    "coverage": round(len(raw_files) / unique_pages, 6) if unique_pages else 0.0,
                },
                "ocr": ocr_stats,
                "works": work_stats,
            },
        }
        (temporary_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if output_dir.exists():
            shutil.rmtree(output_dir)
        temporary_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise

    print(
        f"[scripture-references] {payload_files} arquivos, "
        f"{gzip_total / 1024 / 1024:.2f} MiB gzip, cobertura {len(titled_pages)}/{unique_pages}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
