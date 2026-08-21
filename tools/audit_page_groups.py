#!/usr/bin/env python3
"""Audit bibliographic identity and exact duplicates by consecutive page groups.

Unlike ``audit_volume_similarity.py``, this pass scans pages throughout each
selected folder.  A title claim found later in a folder starts a new inferred
segment, which makes mixed or concatenated volumes visible without treating the
whole directory as one indivisible document.

Example::

    python tools/audit_page_groups.py --corpus teste --volumes PL020 --group-size 10
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sys
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.limpeza_ocr import clean_ocr_text_optimized  # noqa: E402
from tools.audit_volume_similarity import (  # noqa: E402
    comparable_tokens,
    detect_declared_parts,
    detect_declared_series,
    detect_declared_tomes,
    discover_volumes,
    expected_series,
    expected_tome,
    find_facsimile,
    select_text_pages,
)


GROUP_FIELDS = (
    "group_id",
    "volume_id",
    "group_index",
    "start_page",
    "end_page",
    "page_count",
    "page_numbers",
    "expected_series",
    "expected_tome",
    "declared_series",
    "declared_tomes",
    "declared_parts",
    "claim_evidence",
    "inferred_series",
    "inferred_tome",
    "inferred_part",
    "identity_source_page",
    "identity_status",
    "normalized_text_sha256",
    "facsimile_sequence_sha256",
    "text_files",
    "facsimiles",
    "alternative_ocr_files",
    "clean_tokens",
)
SEGMENT_FIELDS = (
    "segment_id",
    "volume_id",
    "start_page",
    "end_page",
    "page_count",
    "group_count",
    "inferred_series",
    "inferred_tome",
    "inferred_part",
    "identity_source_page",
    "identity_status",
)
DUPLICATE_FIELDS = (
    "match_type",
    "group_a",
    "volume_a",
    "pages_a",
    "group_b",
    "volume_b",
    "pages_b",
    "normalized_text_sha256",
    "facsimile_sequence_sha256",
)


@dataclass
class PageData:
    number: int
    text_path: Path
    facsimile_path: Path | None
    tokens: tuple[str, ...]
    declared_series: tuple[str, ...]
    declared_tomes: tuple[int, ...]
    declared_parts: tuple[str, ...]
    alternative_count: int


@dataclass
class PageGroup:
    group_id: str
    volume_id: str
    group_index: int
    pages: list[PageData]
    expected_series: str | None
    expected_tome: int | None
    declared_series: tuple[str, ...]
    declared_tomes: tuple[int, ...]
    declared_parts: tuple[str, ...]
    claim_evidence: tuple[str, ...]
    inferred_series: str | None
    inferred_tome: int | None
    inferred_part: str | None
    identity_source_page: int | None
    identity_status: str
    normalized_text_sha256: str
    facsimile_sequence_sha256: str


@dataclass
class Segment:
    segment_id: str
    volume_id: str
    groups: list[PageGroup]


def _batched(values: Sequence[PageData], size: int) -> Iterable[list[PageData]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _sha256_bytes(parts: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    found = False
    for part in parts:
        found = True
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest() if found else ""


def normalized_text_hash(pages: Sequence[PageData]) -> str:
    if not any(page.tokens for page in pages):
        return ""
    return _sha256_bytes(" ".join(page.tokens).encode("utf-8") for page in pages)


def facsimile_sequence_hash(pages: Sequence[PageData]) -> str:
    paths = [page.facsimile_path for page in pages]
    if not paths or any(path is None or not path.is_file() for path in paths):
        return ""
    return _sha256_bytes(path.read_bytes() for path in paths if path is not None)


def read_volume_pages(volume_dir: Path) -> list[PageData]:
    volume_id = volume_dir.name
    pages: list[PageData] = []
    for number, text_path, alternative_count in select_text_pages(
        volume_dir / "text", volume_id, None
    ):
        raw = text_path.read_text(encoding="utf-8", errors="replace")
        clean_text, _meta = clean_ocr_text_optimized(raw)
        pages.append(
            PageData(
                number=number,
                text_path=text_path,
                facsimile_path=find_facsimile(volume_dir, volume_id, number),
                tokens=comparable_tokens(clean_text),
                declared_series=detect_declared_series(clean_text),
                declared_tomes=detect_declared_tomes(clean_text),
                declared_parts=detect_declared_parts(clean_text),
                alternative_count=alternative_count,
            )
        )
    return pages


def _unique(values: Iterable[object]) -> tuple:
    return tuple(sorted(set(values)))


def _identity_status(group: PageGroup) -> str:
    if len(group.declared_tomes) > 1:
        return "review_ambiguous_claim"
    if group.declared_tomes:
        if (
            group.inferred_tome != group.expected_tome
            or group.inferred_series != group.expected_series
        ):
            return "explicit_mismatch"
        return "explicit_match"
    if (
        group.inferred_tome != group.expected_tome
        or group.inferred_series != group.expected_series
    ):
        return "inferred_mismatch"
    return "inferred_match"


def build_page_groups(volume_dir: Path, group_size: int) -> list[PageGroup]:
    volume_id = volume_dir.name
    pages = read_volume_pages(volume_dir)
    current_series = expected_series(volume_id)
    current_tome = expected_tome(volume_id)
    current_part: str | None = None
    source_page: int | None = None
    groups: list[PageGroup] = []

    for group_index, group_pages in enumerate(_batched(pages, group_size), start=1):
        declared_series = _unique(
            value for page in group_pages for value in page.declared_series
        )
        declared_tomes = _unique(
            value for page in group_pages for value in page.declared_tomes
        )
        declared_parts = _unique(
            value for page in group_pages for value in page.declared_parts
        )
        evidence: list[str] = []
        identity_claim_pages: list[PageData] = []
        for page in group_pages:
            if page.declared_series or page.declared_tomes or page.declared_parts:
                evidence.append(
                    f"page {page.number}: "
                    f"series={'+'.join(page.declared_series) or '-'},"
                    f"tome={'+'.join(map(str, page.declared_tomes)) or '-'},"
                    f"part={'+'.join(page.declared_parts) or '-'}"
                )
            if page.declared_tomes:
                identity_claim_pages.append(page)

        if len(declared_tomes) == 1:
            tome_changed = current_tome != declared_tomes[0]
            current_tome = declared_tomes[0]
            if tome_changed:
                current_part = None
        title_series = _unique(
            value
            for page in identity_claim_pages
            for value in page.declared_series
        )
        if len(title_series) == 1:
            current_series = title_series[0]
        title_parts = _unique(
            value
            for page in identity_claim_pages
            for value in page.declared_parts
        )
        if len(title_parts) == 1:
            current_part = title_parts[0]
        if identity_claim_pages and (
            len(title_series) <= 1
            and len(declared_tomes) <= 1
            and len(title_parts) <= 1
        ):
            source_page = min(page.number for page in identity_claim_pages)

        group = PageGroup(
            group_id=f"{volume_id}:{group_pages[0].number:04d}-{group_pages[-1].number:04d}",
            volume_id=volume_id,
            group_index=group_index,
            pages=group_pages,
            expected_series=expected_series(volume_id),
            expected_tome=expected_tome(volume_id),
            declared_series=declared_series,
            declared_tomes=declared_tomes,
            declared_parts=declared_parts,
            claim_evidence=tuple(evidence),
            inferred_series=current_series,
            inferred_tome=current_tome,
            inferred_part=current_part,
            identity_source_page=source_page,
            identity_status="",
            normalized_text_sha256=normalized_text_hash(group_pages),
            facsimile_sequence_sha256=facsimile_sequence_hash(group_pages),
        )
        group.identity_status = _identity_status(group)
        groups.append(group)
    return groups


def build_segments(groups: Sequence[PageGroup]) -> list[Segment]:
    segments: list[Segment] = []
    for group in groups:
        identity = (
            group.inferred_series,
            group.inferred_tome,
            group.inferred_part,
            group.identity_status.endswith("mismatch"),
        )
        if segments:
            previous = segments[-1].groups[-1]
            previous_identity = (
                previous.inferred_series,
                previous.inferred_tome,
                previous.inferred_part,
                previous.identity_status.endswith("mismatch"),
            )
            if group.volume_id == previous.volume_id and identity == previous_identity:
                segments[-1].groups.append(group)
                continue
        segment_number = 1 + sum(segment.volume_id == group.volume_id for segment in segments)
        segments.append(
            Segment(
                segment_id=f"{group.volume_id}:S{segment_number:03d}",
                volume_id=group.volume_id,
                groups=[group],
            )
        )
    return segments


def _relative(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_group_csv(path: Path, groups: Sequence[PageGroup]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GROUP_FIELDS)
        writer.writeheader()
        for group in groups:
            writer.writerow(
                {
                    "group_id": group.group_id,
                    "volume_id": group.volume_id,
                    "group_index": group.group_index,
                    "start_page": group.pages[0].number,
                    "end_page": group.pages[-1].number,
                    "page_count": len(group.pages),
                    "page_numbers": " | ".join(str(page.number) for page in group.pages),
                    "expected_series": group.expected_series or "",
                    "expected_tome": group.expected_tome or "",
                    "declared_series": " | ".join(group.declared_series),
                    "declared_tomes": " | ".join(map(str, group.declared_tomes)),
                    "declared_parts": " | ".join(group.declared_parts),
                    "claim_evidence": " | ".join(group.claim_evidence),
                    "inferred_series": group.inferred_series or "",
                    "inferred_tome": group.inferred_tome or "",
                    "inferred_part": group.inferred_part or "",
                    "identity_source_page": group.identity_source_page or "",
                    "identity_status": group.identity_status,
                    "normalized_text_sha256": group.normalized_text_sha256,
                    "facsimile_sequence_sha256": group.facsimile_sequence_sha256,
                    "text_files": " | ".join(_relative(page.text_path) for page in group.pages),
                    "facsimiles": " | ".join(
                        _relative(page.facsimile_path)
                        for page in group.pages
                        if page.facsimile_path is not None
                    ),
                    "alternative_ocr_files": sum(
                        page.alternative_count for page in group.pages
                    ),
                    "clean_tokens": sum(len(page.tokens) for page in group.pages),
                }
            )


def write_segment_csv(path: Path, segments: Sequence[Segment]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SEGMENT_FIELDS)
        writer.writeheader()
        for segment in segments:
            first = segment.groups[0]
            last = segment.groups[-1]
            writer.writerow(
                {
                    "segment_id": segment.segment_id,
                    "volume_id": segment.volume_id,
                    "start_page": first.pages[0].number,
                    "end_page": last.pages[-1].number,
                    "page_count": sum(len(group.pages) for group in segment.groups),
                    "group_count": len(segment.groups),
                    "inferred_series": first.inferred_series or "",
                    "inferred_tome": first.inferred_tome or "",
                    "inferred_part": first.inferred_part or "",
                    "identity_source_page": first.identity_source_page or "",
                    "identity_status": (
                        "mismatch"
                        if first.identity_status.endswith("mismatch")
                        else "match"
                    ),
                }
            )


def exact_duplicate_rows(groups: Sequence[PageGroup]) -> list[dict[str, object]]:
    by_text: dict[str, list[PageGroup]] = defaultdict(list)
    by_image: dict[str, list[PageGroup]] = defaultdict(list)
    for group in groups:
        if group.normalized_text_sha256:
            by_text[group.normalized_text_sha256].append(group)
        if group.facsimile_sequence_sha256:
            by_image[group.facsimile_sequence_sha256].append(group)

    matches: dict[tuple[str, str], set[str]] = defaultdict(set)
    for match_type, index in (("text", by_text), ("facsimile", by_image)):
        for candidates in index.values():
            for position, left in enumerate(candidates):
                for right in candidates[position + 1 :]:
                    if left.volume_id == right.volume_id:
                        continue
                    pair = tuple(sorted((left.group_id, right.group_id)))
                    matches[pair].add(match_type)

    lookup = {group.group_id: group for group in groups}
    rows: list[dict[str, object]] = []
    for pair, match_types in sorted(matches.items()):
        left, right = (lookup[group_id] for group_id in pair)
        rows.append(
            {
                "match_type": "+".join(sorted(match_types)),
                "group_a": left.group_id,
                "volume_a": left.volume_id,
                "pages_a": f"{left.pages[0].number}-{left.pages[-1].number}",
                "group_b": right.group_id,
                "volume_b": right.volume_id,
                "pages_b": f"{right.pages[0].number}-{right.pages[-1].number}",
                "normalized_text_sha256": (
                    left.normalized_text_sha256 if "text" in match_types else ""
                ),
                "facsimile_sequence_sha256": (
                    left.facsimile_sequence_sha256
                    if "facsimile" in match_types
                    else ""
                ),
            }
        )
    return rows


def write_duplicate_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DUPLICATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def selected_volume_dirs(corpus: Path, volume_ids: Sequence[str]) -> list[Path]:
    if not volume_ids:
        return discover_volumes(corpus)
    paths: list[Path] = []
    for volume_id in volume_ids:
        path = corpus / volume_id
        if not (path / "text").is_dir():
            raise FileNotFoundError(f"Volume text directory not found: {path / 'text'}")
        paths.append(path)
    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "teste")
    parser.add_argument("--volumes", nargs="*", default=[])
    parser.add_argument("--group-size", type=int, default=10)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "page_group_audit",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> tuple[list[PageGroup], list[Segment]]:
    if args.group_size < 1:
        raise ValueError("--group-size must be at least 1")
    groups: list[PageGroup] = []
    segments: list[Segment] = []
    for volume_dir in selected_volume_dirs(args.corpus, args.volumes):
        volume_groups = build_page_groups(volume_dir, args.group_size)
        groups.extend(volume_groups)
        segments.extend(build_segments(volume_groups))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_group_csv(args.output_dir / "page_groups.csv", groups)
    write_segment_csv(args.output_dir / "page_segments.csv", segments)
    write_duplicate_csv(
        args.output_dir / "exact_page_group_duplicates.csv",
        exact_duplicate_rows(groups),
    )
    return groups, segments


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        groups, segments = run(args)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    mismatches = sum(group.identity_status.endswith("mismatch") for group in groups)
    print(f"Audited {len(groups)} page groups in {len(set(g.volume_id for g in groups))} volumes")
    print(f"Built {len(segments)} inferred bibliographic segments")
    print(f"Groups outside the folder identity: {mismatches}")
    print(f"Reports: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
