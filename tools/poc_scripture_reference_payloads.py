#!/usr/bin/env python3
"""Measure compact JSON alternatives for Scripture reference result pages.

The production Scripture book shards already encode every reference/location pair.
This POC compares reusing those shards with emitting per-reference payloads and
with attaching page titles extracted from OCR headers or the published work index.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import html
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ocr_xml_utils import parse_ocr_xml_page  # noqa: E402


SPACE_RE = re.compile(r"\s+")
LETTER_RE = re.compile(r"[A-Za-zÀ-ÿÆŒæœΑ-Ωα-ω]")
UPPER_RE = re.compile(r"[A-ZÀ-ÝÆŒΑ-Ω]")
LOWER_RE = re.compile(r"[a-zà-ÿæœα-ω]")
EDGE_NUMBER_RE = re.compile(
    r"(?:\[\s*)?(?:f(?:ol)?\.?\s*)?[0-9OIl|SB]{1,4}(?:\s*\])?",
    re.IGNORECASE,
)
MAX_OCR_HEADER_CHARS = 240


@dataclass(slots=True)
class ReferenceRecord:
    book_key: str
    book_label: str
    slug: str
    locator: str
    locations: list[tuple[str, int, int]]


@dataclass(slots=True)
class WorkInterval:
    start: int
    end: int
    title: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=ROOT / "web" / "public")
    parser.add_argument("--ocr-root", type=Path, default=ROOT / "teste")
    parser.add_argument("--dist", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audits" / "scripture_reference_payload_poc.json",
    )
    parser.add_argument("--sample-dir", type=Path)
    parser.add_argument("--preview-limit", type=int, default=120)
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--title-limit", type=int, default=80)
    parser.add_argument("--locales", type=int, default=4)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def size_record(raw_bytes: int, gzip_bytes: int, files: int) -> dict[str, Any]:
    return {
        "files": files,
        "raw_bytes": raw_bytes,
        "gzip_bytes": gzip_bytes,
        "raw_mib": round(raw_bytes / 1024 / 1024, 3),
        "gzip_mib": round(gzip_bytes / 1024 / 1024, 3),
    }


def measure_payloads(payloads: Iterable[Any]) -> dict[str, Any]:
    raw_total = 0
    gzip_total = 0
    files = 0
    for payload in payloads:
        raw = compact_json_bytes(payload)
        raw_total += len(raw)
        gzip_total += len(gzip.compress(raw, compresslevel=9, mtime=0))
        files += 1
    return size_record(raw_total, gzip_total, files)


def scripture_segment_slug(segment: list[int]) -> str:
    start_chapter, start_verse, end_chapter, end_verse = segment
    if not start_verse and not end_verse:
        if start_chapter == end_chapter:
            return f"chapter-{start_chapter}"
        return f"chapter-{start_chapter}-to-chapter-{end_chapter}"
    start = f"chapter-{start_chapter}-verse-{start_verse}"
    if start_chapter == end_chapter and start_verse == end_verse:
        return start
    return f"{start}-to-chapter-{end_chapter}-verse-{end_verse}"


def scripture_reference_slug(flat_segments: list[int]) -> str:
    segments = [flat_segments[index : index + 4] for index in range(0, len(flat_segments), 4)]
    return "--and--".join(scripture_segment_slug(segment) for segment in segments)


def format_scripture_segment(segment: list[int]) -> str:
    start_chapter, start_verse, end_chapter, end_verse = segment
    if not start_verse and not end_verse:
        return str(start_chapter) if start_chapter == end_chapter else f"{start_chapter}–{end_chapter}"
    start = f"{start_chapter}:{start_verse}"
    if start_chapter == end_chapter and start_verse == end_verse:
        return start
    if start_chapter == end_chapter:
        return f"{start}–{end_verse}"
    return f"{start}–{end_chapter}:{end_verse}"


def format_scripture_locator(flat_segments: list[int]) -> str:
    segments = [flat_segments[index : index + 4] for index in range(0, len(flat_segments), 4)]
    formatted: list[str] = []
    for index, segment in enumerate(segments):
        if index:
            previous = segments[index - 1]
            same_chapter = segment[0] == previous[0] and segment[2] == previous[2]
            if same_chapter and segment[1]:
                formatted.append(
                    str(segment[1]) if segment[1] == segment[3] else f"{segment[1]}–{segment[3]}"
                )
                continue
        formatted.append(format_scripture_segment(segment))
    return "; ".join(formatted)


def decode_locations(shard: dict[str, Any], reference: list[Any]) -> list[tuple[str, int, int]]:
    locations: list[tuple[str, int, int]] = []
    for volume_posting in reference[2] if len(reference) > 2 else []:
        volume = str(shard.get("volumes", [])[int(volume_posting[0])])
        page = 0
        postings = volume_posting[1] or []
        for index in range(0, len(postings), 2):
            page += int(postings[index] or 0)
            locations.append((volume, page, int(postings[index + 1] or 0)))
    return locations


def load_references(
    public_dir: Path,
    preview_limit: int,
) -> tuple[list[ReferenceRecord], dict[str, Any]]:
    scripture_dir = public_dir / "scripture" / "v3"
    manifest = load_json(scripture_dir / "manifest.json")
    records: list[ReferenceRecord] = []
    shard_raw = 0
    shard_gzip = 0
    shard_files = 0
    for book_key, route in (manifest.get("routes") or {}).items():
        url = route.get("url")
        if not url:
            continue
        shard_path = scripture_dir / url
        shard = load_json(shard_path)
        shard_files += 1
        shard_gzip += shard_path.stat().st_size
        shard_raw += len(compact_json_bytes(shard))
        for reference in shard.get("references") or []:
            locations = decode_locations(shard, reference)
            if len(locations) <= preview_limit:
                continue
            records.append(
                ReferenceRecord(
                    book_key=str(book_key),
                    book_label=str(route.get("label") or shard.get("book", [book_key, book_key])[1]),
                    slug=scripture_reference_slug(reference[1] or []),
                    locator=format_scripture_locator(reference[1] or []),
                    locations=locations,
                )
            )
    return records, {
        "manifest": manifest,
        "shards": size_record(shard_raw, shard_gzip, shard_files),
    }


def normalize_title(text: str) -> str:
    value = str(text or "")
    for _attempt in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    value = value.replace("\xa0", " ").replace("\u2007", " ").replace("\u202f", " ")
    return SPACE_RE.sub(" ", value).strip(" \t\r\n—–-|:;,.·")


def is_numeric_edge(text: str) -> bool:
    normalized = normalize_title(text)
    match = EDGE_NUMBER_RE.fullmatch(normalized)
    return bool(match and any(character.isdigit() for character in normalized))


def strip_outer_page_numbers(text: str) -> str:
    value = normalize_title(text)
    matches = list(EDGE_NUMBER_RE.finditer(value))
    if matches and matches[0].start() == 0 and any(char.isdigit() for char in matches[0].group(0)):
        remainder = value[matches[0].end() :]
        if LETTER_RE.search(remainder):
            value = remainder.lstrip(" \t—–-|:;,.·")
    matches = list(EDGE_NUMBER_RE.finditer(value))
    if matches and matches[-1].end() == len(value) and any(char.isdigit() for char in matches[-1].group(0)):
        remainder = value[: matches[-1].start()]
        if LETTER_RE.search(remainder):
            value = remainder.rstrip(" \t—–-|:;,.·")
    return normalize_title(value)


def is_header_like(line: str) -> bool:
    if not line or is_numeric_edge(line):
        return False
    letters = len(LETTER_RE.findall(line))
    if letters < 4:
        return False
    upper = len(UPPER_RE.findall(line))
    lower = len(LOWER_RE.findall(line))
    return upper / max(letters, 1) >= 0.55 or (upper > lower and letters >= 10)


def extract_header_block_prefix(text: str) -> str:
    lines = [normalize_title(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    if len(lines) <= 1:
        return lines[0] if lines else ""
    pieces: list[str] = []
    for line in lines:
        if is_numeric_edge(line):
            if pieces:
                break
            continue
        if not pieces:
            if len(LETTER_RE.findall(line)) >= 4:
                pieces.append(line)
            continue
        if is_header_like(line) and len(line) <= 240:
            pieces.append(line)
            if len(pieces) >= 3:
                break
            continue
        break
    return " ".join(pieces)


def extract_ocr_header_title(raw_text: str) -> str | None:
    stripped = raw_text.lstrip()
    if stripped.startswith("<?xml"):
        page_offset = stripped.find("<pagina")
        if page_offset >= 0:
            stripped = stripped[page_offset:]
    page = parse_ocr_xml_page(stripped)
    if page.is_xml:
        pieces = [
            extract_header_block_prefix(block.content_clean)
            for block in page.blocks
            if block.tag_name == "cabecalho" or block.tipo == "cabecalho"
        ]
        pieces = [piece for piece in pieces if piece]
        while len(pieces) > 1 and is_numeric_edge(pieces[0]):
            pieces.pop(0)
        while len(pieces) > 1 and is_numeric_edge(pieces[-1]):
            pieces.pop()
        title = strip_outer_page_numbers(" ".join(pieces))
    else:
        lines = [normalize_title(line) for line in raw_text.splitlines()[:16]]
        pieces = []
        for line in lines:
            if not line or is_numeric_edge(line):
                continue
            if is_header_like(line):
                pieces.append(line)
                if len(pieces) >= 3:
                    break
            elif pieces:
                break
        title = strip_outer_page_numbers(" ".join(pieces))
    if len(LETTER_RE.findall(title)) < 4:
        return None
    if len(title) > MAX_OCR_HEADER_CHARS:
        return None
    return title


def truncate_title(title: str | None, limit: int) -> str | None:
    if not title:
        return None
    if len(title) <= limit:
        return title
    shortened = title[: max(1, limit - 1)].rstrip(" \t—–-|:;,.")
    return f"{shortened}…"


def cited_pages_by_volume(references: list[ReferenceRecord]) -> dict[str, set[int]]:
    pages: dict[str, set[int]] = defaultdict(set)
    for reference in references:
        for volume, page, _mask in reference.locations:
            pages[volume].add(page)
    return pages


def load_volume_metadata(public_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_json(public_dir / "volumes.json")
    volumes = {str(item["id"]): item for item in payload.get("volumes") or []}
    return volumes, payload


def resolve_raw_files(
    public_dir: Path,
    volume_meta: dict[str, Any],
    pages_by_volume: dict[str, set[int]],
) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    raw_files: dict[tuple[str, int], str] = {}
    missing_volumes = 0
    for ordinal, (volume, needed_pages) in enumerate(sorted(pages_by_volume.items()), start=1):
        volume_entry = volume_meta.get(volume)
        if not volume_entry or not volume_entry.get("meta_url"):
            missing_volumes += 1
            continue
        manifest = load_json(public_dir / volume_entry["meta_url"])
        for block in manifest.get("page_blocks") or []:
            page_first = int(block.get("page_first") or 0)
            page_last = int(block.get("page_last") or 0)
            if not any(page_first <= page <= page_last for page in needed_pages):
                continue
            shard = load_json(public_dir / block["file"])
            for page_record in shard.get("pages") or []:
                page = int(page_record.get("page") or 0)
                if page not in needed_pages:
                    continue
                raw_file = (page_record.get("raw") or {}).get("file")
                if raw_file:
                    raw_files[(volume, page)] = str(raw_file)
        if ordinal % 50 == 0:
            print(f"[poc] metadados: {ordinal}/{len(pages_by_volume)} volumes", flush=True)
    return raw_files, {"missing_volumes": missing_volumes}


def load_ocr_titles(
    ocr_root: Path,
    raw_files: dict[tuple[str, int], str],
) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    titles: dict[tuple[str, int], str] = {}
    missing_files = 0
    parse_failures = 0
    for ordinal, ((volume, page), raw_file) in enumerate(sorted(raw_files.items()), start=1):
        path = ocr_root / volume / "text" / raw_file
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing_files += 1
            continue
        try:
            title = extract_ocr_header_title(raw_text)
        except (ValueError, TypeError, IndexError):
            parse_failures += 1
            title = None
        if title:
            titles[(volume, page)] = title
        if ordinal % 10000 == 0:
            print(f"[poc] OCR: {ordinal}/{len(raw_files)} páginas", flush=True)
    return titles, {"missing_files": missing_files, "parse_failures": parse_failures}


def display_original(value: Any) -> str:
    if isinstance(value, dict):
        return normalize_title(value.get("original") or "")
    return normalize_title(str(value or ""))


def load_work_intervals(
    public_dir: Path,
    volume_meta: dict[str, Any],
    pages_by_volume: dict[str, set[int]],
) -> tuple[dict[str, list[WorkInterval]], dict[str, int]]:
    manifest = load_json(public_dir / "indices" / "manifest.json")
    index_routes = {str(item.get("volume_id")): item for item in manifest.get("volumes") or []}
    intervals: dict[str, list[WorkInterval]] = {}
    volumes_with_index = 0
    ambiguous_starts = 0
    works_considered = 0
    for volume in sorted(pages_by_volume):
        route = index_routes.get(volume)
        if not route or not route.get("path"):
            continue
        payload = load_json(public_dir / route["path"])
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for work in payload.get("works") or []:
            start = work.get("reference_start_page")
            try:
                start_int = int(start)
            except (TypeError, ValueError):
                continue
            author = display_original(work.get("author_display"))
            title = display_original(work.get("title_display"))
            label = normalize_title(f"{author} — {title}" if author and title else author or title)
            if not label:
                continue
            candidates[start_int].append({**work, "_label": label})
            works_considered += 1
        if not candidates:
            continue
        volumes_with_index += 1
        starts = sorted(candidates)
        selected: list[WorkInterval] = []
        volume_last = int((volume_meta.get(volume) or {}).get("page_last") or max(pages_by_volume[volume]))
        for position, start in enumerate(starts):
            options = candidates[start]
            if len(options) > 1:
                ambiguous_starts += 1
            options.sort(
                key=lambda item: (
                    item.get("reference_end_page") is not None,
                    item.get("confidence") == "high",
                    bool(display_original(item.get("author_display"))),
                    -len(str(item.get("_label") or "")),
                ),
                reverse=True,
            )
            chosen = options[0]
            explicit_end = chosen.get("reference_end_page")
            try:
                end = int(explicit_end)
            except (TypeError, ValueError):
                end = starts[position + 1] - 1 if position + 1 < len(starts) else volume_last
            if end < start:
                end = start
            selected.append(WorkInterval(start=start, end=end, title=str(chosen["_label"])))
        intervals[volume] = selected
    return intervals, {
        "volumes_with_index": volumes_with_index,
        "ambiguous_starts": ambiguous_starts,
        "works_considered": works_considered,
    }


def work_titles_for_pages(
    intervals: dict[str, list[WorkInterval]],
    pages_by_volume: dict[str, set[int]],
) -> dict[tuple[str, int], str]:
    titles: dict[tuple[str, int], str] = {}
    for volume, pages in pages_by_volume.items():
        volume_intervals = intervals.get(volume) or []
        starts = [interval.start for interval in volume_intervals]
        for page in pages:
            position = bisect.bisect_right(starts, page) - 1
            while position >= 0:
                interval = volume_intervals[position]
                if interval.start <= page <= interval.end:
                    titles[(volume, page)] = interval.title
                    break
                position -= 1
    return titles


def iter_reference_payloads(
    references: list[ReferenceRecord],
    page_size: int,
    titles: dict[tuple[str, int], str] | None = None,
) -> Iterable[dict[str, Any]]:
    for reference in references:
        page_count = math.ceil(len(reference.locations) / page_size)
        for page_index in range(page_count):
            chunk = reference.locations[page_index * page_size : (page_index + 1) * page_size]
            volumes = list(dict.fromkeys(volume for volume, _page, _mask in chunk))
            volume_ids = {volume: index for index, volume in enumerate(volumes)}
            rows = []
            for volume, page, mask in chunk:
                row: list[Any] = [volume_ids[volume], page, mask]
                title = titles.get((volume, page)) if titles else None
                if title:
                    row.append(title)
                rows.append(row)
            yield {
                "v": 1,
                "b": reference.book_key,
                "r": reference.slug,
                "p": page_index + 1,
                "n": page_count,
                "t": len(reference.locations),
                "d": [volumes, rows],
            }


def iter_title_shards(
    pages_by_volume: dict[str, set[int]],
    titles: dict[tuple[str, int], str],
) -> Iterable[dict[str, Any]]:
    for volume, pages in sorted(pages_by_volume.items()):
        rows = [[page, titles[(volume, page)]] for page in sorted(pages) if (volume, page) in titles]
        if rows:
            yield {"v": 1, "volume": volume, "d": rows}


def global_title_payload(
    pages_by_volume: dict[str, set[int]],
    titles: dict[tuple[str, int], str],
) -> dict[str, Any]:
    volumes = []
    for volume, pages in sorted(pages_by_volume.items()):
        rows = [[page, titles[(volume, page)]] for page in sorted(pages) if (volume, page) in titles]
        if rows:
            volumes.append([volume, rows])
    return {"v": 1, "d": volumes}


def iter_work_interval_shards(intervals: dict[str, list[WorkInterval]]) -> Iterable[dict[str, Any]]:
    for volume, values in sorted(intervals.items()):
        yield {
            "v": 1,
            "volume": volume,
            "d": [[item.start, item.end, item.title] for item in values],
        }


def global_work_interval_payload(intervals: dict[str, list[WorkInterval]]) -> dict[str, Any]:
    return {
        "v": 1,
        "d": [
            [volume, [[item.start, item.end, item.title] for item in values]]
            for volume, values in sorted(intervals.items())
        ],
    }


def measure_current_html(dist_dir: Path) -> dict[str, Any] | None:
    if not dist_dir.is_dir():
        return None
    paths = [
        path
        for path in dist_dir.rglob("page-*/index.html")
        if "indices-alfabeticos/scripture" in path.as_posix()
    ]
    if not paths:
        return None
    total = 0
    observations: list[tuple[int, int]] = []
    for path in paths:
        raw = path.read_bytes()
        size = len(raw)
        link_count = raw.count(b"viewer?doc=")
        total += size
        observations.append((link_count, size))
    count = len(observations)
    mean_x = sum(item[0] for item in observations) / count
    mean_y = sum(item[1] for item in observations) / count
    denominator = sum((item[0] - mean_x) ** 2 for item in observations)
    slope = (
        sum((item[0] - mean_x) * (item[1] - mean_y) for item in observations) / denominator
        if denominator
        else 0.0
    )
    intercept = max(0.0, mean_y - slope * mean_x)
    return {
        "files": count,
        "raw_bytes": total,
        "raw_mib": round(total / 1024 / 1024, 3),
        "viewer_links": sum(item[0] for item in observations),
        "estimated_bytes_per_link": round(slope, 2),
        "estimated_shell_bytes_per_route": round(intercept),
        "estimated_light_static_shells_mib": round(intercept * count / 1024 / 1024, 3),
        "estimated_four_unified_viewers_mib": round(intercept * 4 / 1024 / 1024, 3),
    }


def coverage_record(total_pages: int, titles: dict[tuple[str, int], str]) -> dict[str, Any]:
    count = len(titles)
    return {
        "pages": count,
        "coverage": round(count / total_pages, 4) if total_pages else 0.0,
    }


def title_diagnostics(titles: dict[tuple[str, int], str], limit: int) -> dict[str, Any]:
    lengths = sorted(len(value) for value in titles.values())
    keys = sorted(titles)
    if not lengths:
        return {"pages": 0, "examples": []}
    example_count = min(12, len(keys))
    example_positions = sorted(
        {round(index * (len(keys) - 1) / max(1, example_count - 1)) for index in range(example_count)}
    )
    longest = sorted(titles.items(), key=lambda item: len(item[1]), reverse=True)[:8]
    return {
        "pages": len(titles),
        "characters": {
            "mean": round(statistics.fmean(lengths), 2),
            "median": round(statistics.median(lengths), 2),
            "p95": lengths[min(len(lengths) - 1, math.floor(len(lengths) * 0.95))],
            "max": lengths[-1],
            "over_limit": sum(length > limit for length in lengths),
        },
        "examples": [
            {"volume": keys[position][0], "page": keys[position][1], "title": titles[keys[position]]}
            for position in example_positions
        ],
        "longest_examples": [
            {
                "volume": key[0],
                "page": key[1],
                "characters": len(title),
                "title_prefix": title[:240],
            }
            for key, title in longest
        ],
    }


def write_samples(
    sample_dir: Path,
    references: list[ReferenceRecord],
    page_size: int,
    variants: dict[str, dict[tuple[str, int], str] | None],
) -> list[str]:
    sample_dir.mkdir(parents=True, exist_ok=True)
    preferred = next(
        (
            item
            for item in references
            if item.book_key == "joao" and item.slug == "chapter-1"
        ),
        references[0],
    )
    written = []
    for label, titles in variants.items():
        payload = next(iter(iter_reference_payloads([preferred], page_size, titles)))
        path = sample_dir / f"{preferred.book_key}-{preferred.slug}-page-1.{label}.json"
        path.write_bytes(compact_json_bytes(payload))
        written.append(str(path))
    return written


def main() -> int:
    args = parse_args()
    if args.preview_limit < 0 or args.page_size <= 0 or args.title_limit <= 0:
        raise SystemExit("preview-limit, page-size e title-limit devem ser positivos")

    print("[poc] carregando referências bíblicas", flush=True)
    references, scripture_info = load_references(args.public, args.preview_limit)
    pages_by_volume = cited_pages_by_volume(references)
    unique_pages = sum(len(pages) for pages in pages_by_volume.values())
    location_occurrences = sum(len(reference.locations) for reference in references)
    reference_pages = sum(math.ceil(len(reference.locations) / args.page_size) for reference in references)

    print(f"[poc] {len(references)} referências, {unique_pages} páginas OCR únicas", flush=True)
    volume_meta, _volumes_payload = load_volume_metadata(args.public)
    raw_files, raw_stats = resolve_raw_files(args.public, volume_meta, pages_by_volume)
    ocr_titles_full, ocr_stats = load_ocr_titles(args.ocr_root, raw_files)
    work_intervals, work_stats = load_work_intervals(args.public, volume_meta, pages_by_volume)
    work_titles_full = work_titles_for_pages(work_intervals, pages_by_volume)

    ocr_titles = {key: truncate_title(value, args.title_limit) for key, value in ocr_titles_full.items()}
    ocr_titles = {key: value for key, value in ocr_titles.items() if value}
    work_titles = {key: truncate_title(value, args.title_limit) for key, value in work_titles_full.items()}
    work_titles = {key: value for key, value in work_titles.items() if value}
    preferred_titles = dict(ocr_titles)
    preferred_titles.update(work_titles)
    ocr_preferred_titles = dict(work_titles)
    ocr_preferred_titles.update(ocr_titles)
    combined_titles = {
        key: truncate_title(
            " · ".join(
                value
                for value in (work_titles_full.get(key), ocr_titles_full.get(key))
                if value
            ),
            args.title_limit,
        )
        for key in set(work_titles_full) | set(ocr_titles_full)
    }
    combined_titles = {key: value for key, value in combined_titles.items() if value}

    print("[poc] medindo payloads JSON", flush=True)
    per_reference = {
        "locations_only": measure_payloads(
            iter_reference_payloads(references, args.page_size)
        ),
        "locations_plus_ocr_title": measure_payloads(
            iter_reference_payloads(references, args.page_size, ocr_titles)
        ),
        "locations_plus_work_title": measure_payloads(
            iter_reference_payloads(references, args.page_size, work_titles)
        ),
        "locations_plus_preferred_title": measure_payloads(
            iter_reference_payloads(references, args.page_size, preferred_titles)
        ),
        "locations_plus_ocr_then_work_title": measure_payloads(
            iter_reference_payloads(references, args.page_size, ocr_preferred_titles)
        ),
        "locations_plus_combined_title": measure_payloads(
            iter_reference_payloads(references, args.page_size, combined_titles)
        ),
    }
    shared_titles = {
        "ocr_full": measure_payloads(iter_title_shards(pages_by_volume, ocr_titles_full)),
        "ocr_truncated": measure_payloads(iter_title_shards(pages_by_volume, ocr_titles)),
        "work_full": measure_payloads(iter_title_shards(pages_by_volume, work_titles_full)),
        "work_truncated": measure_payloads(iter_title_shards(pages_by_volume, work_titles)),
        "preferred_truncated": measure_payloads(iter_title_shards(pages_by_volume, preferred_titles)),
        "ocr_then_work_truncated": measure_payloads(
            iter_title_shards(pages_by_volume, ocr_preferred_titles)
        ),
        "combined_truncated": measure_payloads(iter_title_shards(pages_by_volume, combined_titles)),
        "work_intervals": measure_payloads(iter_work_interval_shards(work_intervals)),
    }
    global_titles = {
        "ocr_full": measure_payloads([global_title_payload(pages_by_volume, ocr_titles_full)]),
        "ocr_truncated": measure_payloads([global_title_payload(pages_by_volume, ocr_titles)]),
        "work_full": measure_payloads([global_title_payload(pages_by_volume, work_titles_full)]),
        "work_truncated": measure_payloads([global_title_payload(pages_by_volume, work_titles)]),
        "preferred_truncated": measure_payloads([global_title_payload(pages_by_volume, preferred_titles)]),
        "ocr_then_work_truncated": measure_payloads(
            [global_title_payload(pages_by_volume, ocr_preferred_titles)]
        ),
        "combined_truncated": measure_payloads([global_title_payload(pages_by_volume, combined_titles)]),
        "work_intervals": measure_payloads([global_work_interval_payload(work_intervals)]),
    }

    sample_files = []
    if args.sample_dir:
        sample_files = write_samples(
            args.sample_dir,
            references,
            args.page_size,
            {
                "locations": None,
                "ocr": ocr_titles,
                "work": work_titles,
                "preferred": preferred_titles,
                "ocr-then-work": ocr_preferred_titles,
            },
        )

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "preview_limit": args.preview_limit,
            "page_size": args.page_size,
            "title_limit": args.title_limit,
            "locales": args.locales,
        },
        "corpus": {
            "references_over_preview_limit": len(references),
            "reference_pages": reference_pages,
            "static_locale_pages": reference_pages * args.locales,
            "location_occurrences": location_occurrences,
            "unique_volume_pages": unique_pages,
            "volumes": len(pages_by_volume),
        },
        "current_html": measure_current_html(args.dist),
        "existing_scripture_book_shards": scripture_info["shards"],
        "source_diagnostics": {
            "raw_mapping": {
                **raw_stats,
                "mapped_pages": len(raw_files),
                "coverage": round(len(raw_files) / unique_pages, 4) if unique_pages else 0.0,
            },
            "ocr": ocr_stats,
            "works": work_stats,
        },
        "title_coverage": {
            "ocr_header": coverage_record(unique_pages, ocr_titles_full),
            "work_index": coverage_record(unique_pages, work_titles_full),
            "preferred_work_then_ocr": coverage_record(unique_pages, preferred_titles),
            "preferred_ocr_then_work": coverage_record(unique_pages, ocr_preferred_titles),
            "combined_available": coverage_record(unique_pages, combined_titles),
        },
        "title_diagnostics": {
            "ocr_header_full": title_diagnostics(ocr_titles_full, args.title_limit),
            "work_index_full": title_diagnostics(work_titles_full, args.title_limit),
        },
        "payloads": {
            "per_reference_page_json": per_reference,
            "shared_title_shards_by_volume": shared_titles,
            "single_global_title_dictionary": global_titles,
        },
        "sample_schema": {
            "v": 1,
            "b": "joao",
            "r": "chapter-1",
            "p": 1,
            "n": 3,
            "t": 2154,
            "d": [["PG001"], [[0, 120, 4, "S. CLEMENTIS — EPISTOLA"]]],
        },
        "sample_files": sample_files,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[poc] relatório: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
