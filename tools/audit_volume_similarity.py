#!/usr/bin/env python3
"""Audit OCR volume identity and likely duplicates from a small page sample.

The audit is intentionally read-only with respect to the corpus.  It selects the
first physical pages available in each ``<volume>/text`` directory, cleans the
OCR with ``scripts.limpeza_ocr``, compares volume-level fingerprints, and writes
two UTF-8 CSV files plus human-readable diffs for candidate pairs.

Example::

    python tools/audit_volume_similarity.py \
        --corpus teste --pages 10 --output-dir data/volume_similarity_audit
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import difflib
import hashlib
import html
import math
from pathlib import Path
import re
import sys
import textwrap
import unicodedata
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.limpeza_ocr import clean_ocr_text_optimized  # noqa: E402


PAGE_FILE_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)
VOLUME_NAME_RE = re.compile(r"^(?P<series>PL|PG|PO)(?P<number>\d+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
TITLE_TOME_RE = re.compile(
    r"patrologiae(?:\s+[a-z]{2,20}){0,4}?\s+tomus\s+([mdclxvi]{1,12})\b",
    re.IGNORECASE,
)
TITLE_SERIES_RE = re.compile(
    r"patrologiae\s+(latinae|graecae)\b", re.IGNORECASE
)
TITLE_PART_RE = re.compile(r"\bpars\s+(prior|prima|secunda|altera)\b", re.IGNORECASE)
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
CSV_FIELDS_INVENTORY = (
    "volume_id",
    "expected_series",
    "expected_tome",
    "declared_series",
    "declared_tomes",
    "declared_parts",
    "claim_evidence",
    "name_status",
    "pages_read",
    "page_numbers",
    "text_files",
    "facsimiles",
    "alternative_ocr_files",
    "clean_tokens",
)
CSV_FIELDS_PAIRS = (
    "group_id",
    "group_size",
    "rank_in_group",
    "similarity_rank",
    "similarity",
    "token_cosine",
    "shingle_jaccard",
    "exact_facsimile_count",
    "exact_facsimile_pages",
    "review_hint",
    "volume_a",
    "volume_b",
    "declared_tomes_a",
    "declared_tomes_b",
    "declared_parts_a",
    "declared_parts_b",
    "name_status_a",
    "name_status_b",
    "best_page_matches",
    "facsimiles_a",
    "facsimiles_b",
    "diff_file",
)


@dataclass(frozen=True)
class PageSample:
    number: int
    text_path: Path
    facsimile_path: Path | None
    clean_text: str
    tokens: tuple[str, ...]
    alternative_count: int


@dataclass
class VolumeSample:
    volume_id: str
    path: Path
    pages: list[PageSample]
    tokens: tuple[str, ...]
    token_counts: Counter[str]
    shingles: set[tuple[str, ...]]
    expected_series: str | None
    expected_tome: int | None
    declared_series: tuple[str, ...]
    declared_tomes: tuple[int, ...]
    declared_parts: tuple[str, ...]
    claim_evidence: tuple[str, ...]
    name_status: str


@dataclass
class PairScore:
    volume_a: VolumeSample
    volume_b: VolumeSample
    similarity: float
    token_cosine: float
    shingle_jaccard: float
    exact_facsimile_matches: str = ""
    exact_facsimile_count: int = 0
    review_hint: str = ""
    best_page_matches: str = ""
    group_id: str = ""
    group_size: int = 0
    rank_in_group: int = 0
    similarity_rank: int = 0
    diff_file: str = ""


class DisjointSet:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def page_number(path: Path) -> int | None:
    match = PAGE_FILE_RE.search(path.name)
    return int(match.group(1)) if match else None


def _text_candidate_rank(path: Path, volume_id: str) -> tuple[int, int, str]:
    stem_without_page = PAGE_FILE_RE.sub("", path.name)
    canonical = stem_without_page.casefold() == volume_id.casefold()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return (0 if canonical else 1, -size, path.name.casefold())


def select_text_pages(
    text_dir: Path, volume_id: str, limit: int | None
) -> list[tuple[int, Path, int]]:
    by_number: dict[int, list[Path]] = defaultdict(list)
    for path in text_dir.iterdir():
        if not path.is_file():
            continue
        number = page_number(path)
        if number is not None:
            by_number[number].append(path)

    selected: list[tuple[int, Path, int]] = []
    numbers = sorted(by_number)
    if limit is not None:
        numbers = numbers[:limit]
    for number in numbers:
        candidates = sorted(
            by_number[number], key=lambda path: _text_candidate_rank(path, volume_id)
        )
        selected.append((number, candidates[0], len(candidates) - 1))
    return selected


def find_facsimile(volume_dir: Path, volume_id: str, number: int) -> Path | None:
    image_dir = volume_dir / "images"
    if not image_dir.is_dir():
        return None
    padded = (f"{number:03d}", f"{number:04d}", str(number))
    for page_label in padded:
        matches = sorted(image_dir.glob(f"{volume_id}-{page_label}.*"))
        if matches:
            return matches[0]
    suffix_re = re.compile(rf"-0*{number}\.[^.]+$", re.IGNORECASE)
    for path in sorted(image_dir.iterdir()):
        if path.is_file() and suffix_re.search(path.name):
            return path
    return None


def normalize_token(token: str) -> str:
    token = html.unescape(token).casefold().replace("æ", "ae").replace("œ", "oe")
    decomposed = unicodedata.normalize("NFKD", token)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def comparable_tokens(clean_text: str) -> tuple[str, ...]:
    return tuple(normalize_token(token) for token in TOKEN_RE.findall(clean_text))


def make_shingles(tokens: Sequence[str], width: int) -> set[tuple[str, ...]]:
    if len(tokens) < width:
        return set()
    return {tuple(tokens[index : index + width]) for index in range(len(tokens) - width + 1)}


def roman_to_int(value: str) -> int | None:
    value = value.upper()
    if not value or any(ch not in ROMAN_VALUES for ch in value):
        return None
    total = 0
    previous = 0
    for ch in reversed(value):
        current = ROMAN_VALUES[ch]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total if total > 0 else None


def detect_declared_tomes(clean_text: str) -> tuple[int, ...]:
    normalized = normalize_token(clean_text)
    detected = {
        parsed
        for match in TITLE_TOME_RE.finditer(normalized)
        if (parsed := roman_to_int(match.group(1))) is not None
    }
    return tuple(sorted(detected))


def detect_declared_series(clean_text: str) -> tuple[str, ...]:
    mapping = {"latinae": "PL", "graecae": "PG"}
    return tuple(
        sorted({mapping[match.group(1).casefold()] for match in TITLE_SERIES_RE.finditer(normalize_token(clean_text))})
    )


def detect_declared_parts(clean_text: str) -> tuple[str, ...]:
    mapping = {"prima": "prior", "altera": "secunda"}
    detected = {
        mapping.get(match.group(1).casefold(), match.group(1).casefold())
        for match in TITLE_PART_RE.finditer(normalize_token(clean_text))
    }
    return tuple(sorted(detected))


def expected_series(volume_id: str) -> str | None:
    match = VOLUME_NAME_RE.match(volume_id)
    return match.group("series").upper() if match else None


def expected_tome(volume_id: str) -> int | None:
    match = VOLUME_NAME_RE.match(volume_id)
    return int(match.group("number")) if match else None


def classify_name(
    expected_series_value: str | None,
    expected_tome_value: int | None,
    declared_series: tuple[str, ...],
    declared_tomes: tuple[int, ...],
) -> str:
    if expected_tome_value is None or expected_series_value is None:
        return "unsupported_name"
    if declared_series and expected_series_value not in declared_series:
        return "review_series_mismatch"
    if not declared_tomes:
        return "undetected"
    if declared_tomes == (expected_tome_value,):
        return "match"
    if expected_tome_value in declared_tomes:
        return "review_multiple_declared_tomes"
    return "review_tome_mismatch"


def build_volume_sample(volume_dir: Path, page_limit: int, shingle_width: int) -> VolumeSample:
    volume_id = volume_dir.name
    selections = select_text_pages(volume_dir / "text", volume_id, page_limit)
    pages: list[PageSample] = []
    all_tokens: list[str] = []
    clean_texts: list[str] = []
    claim_evidence: list[str] = []
    for number, text_path, alternative_count in selections:
        raw = text_path.read_text(encoding="utf-8", errors="replace")
        clean_text, _meta = clean_ocr_text_optimized(raw)
        tokens = comparable_tokens(clean_text)
        pages.append(
            PageSample(
                number=number,
                text_path=text_path,
                facsimile_path=find_facsimile(volume_dir, volume_id, number),
                clean_text=clean_text,
                tokens=tokens,
                alternative_count=alternative_count,
            )
        )
        all_tokens.extend(tokens)
        clean_texts.append(clean_text)
        page_series = detect_declared_series(clean_text)
        page_tomes = detect_declared_tomes(clean_text)
        page_parts = detect_declared_parts(clean_text)
        if page_series or page_tomes or page_parts:
            claims = "/".join(
                filter(
                    None,
                    (
                        "+".join(page_series),
                        "+".join(str(value) for value in page_tomes),
                        "+".join(page_parts),
                    ),
                )
            )
            facsimile = pages[-1].facsimile_path
            claim_evidence.append(
                f"page {number}: {claims} [{facsimile.as_posix() if facsimile else text_path.as_posix()}]"
            )

    token_tuple = tuple(all_tokens)
    declared = detect_declared_tomes("\n".join(clean_texts))
    declared_series = detect_declared_series("\n".join(clean_texts))
    declared_parts = detect_declared_parts("\n".join(clean_texts))
    expected_tome_value = expected_tome(volume_id)
    expected_series_value = expected_series(volume_id)
    return VolumeSample(
        volume_id=volume_id,
        path=volume_dir,
        pages=pages,
        tokens=token_tuple,
        token_counts=Counter(token_tuple),
        shingles=make_shingles(token_tuple, shingle_width),
        expected_series=expected_series_value,
        expected_tome=expected_tome_value,
        declared_series=declared_series,
        declared_tomes=declared,
        declared_parts=declared_parts,
        claim_evidence=tuple(claim_evidence),
        name_status=classify_name(
            expected_series_value, expected_tome_value, declared_series, declared
        ),
    )


def discover_volumes(corpus: Path) -> list[Path]:
    if not corpus.is_dir():
        raise FileNotFoundError(f"Corpus directory not found: {corpus}")
    return sorted(
        (path for path in corpus.iterdir() if (path / "text").is_dir()),
        key=lambda path: path.name.casefold(),
    )


def document_frequency(samples: Sequence[VolumeSample]) -> tuple[Counter[str], Counter[tuple[str, ...]]]:
    token_df: Counter[str] = Counter()
    shingle_df: Counter[tuple[str, ...]] = Counter()
    for sample in samples:
        token_df.update(sample.token_counts.keys())
        shingle_df.update(sample.shingles)
    return token_df, shingle_df


def _weighted_token_vector(
    sample: VolumeSample,
    token_df: Counter[str],
    document_count: int,
    max_document_frequency: int,
) -> tuple[dict[str, float], float]:
    vector: dict[str, float] = {}
    squared_norm = 0.0
    for token, count in sample.token_counts.items():
        frequency = token_df[token]
        if frequency > max_document_frequency:
            continue
        idf = math.log((1 + document_count) / (1 + frequency)) + 1.0
        weight = (1.0 + math.log(count)) * idf
        vector[token] = weight
        squared_norm += weight * weight
    return vector, math.sqrt(squared_norm)


def _cosine(
    left: dict[str, float], left_norm: float, right: dict[str, float], right_norm: float
) -> float:
    if not left_norm or not right_norm:
        return 0.0
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    dot = sum(weight * larger.get(token, 0.0) for token, weight in smaller.items())
    return dot / (left_norm * right_norm)


def _jaccard(left: set[object], right: set[object]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    return intersection / (len(left) + len(right) - intersection)


def compare_samples(
    samples: Sequence[VolumeSample],
    *,
    max_df_ratio: float,
    report_threshold: float,
) -> list[PairScore]:
    if len(samples) < 2:
        return []
    token_df, shingle_df = document_frequency(samples)
    max_df = max(2, math.ceil(len(samples) * max_df_ratio))
    vectors = {
        sample.volume_id: _weighted_token_vector(sample, token_df, len(samples), max_df)
        for sample in samples
    }
    filtered_shingles = {
        sample.volume_id: {
            shingle for shingle in sample.shingles if shingle_df[shingle] <= max_df
        }
        for sample in samples
    }

    pairs: list[PairScore] = []
    for index, left in enumerate(samples):
        left_vector, left_norm = vectors[left.volume_id]
        for right in samples[index + 1 :]:
            right_vector, right_norm = vectors[right.volume_id]
            cosine = _cosine(left_vector, left_norm, right_vector, right_norm)
            jaccard = _jaccard(
                filtered_shingles[left.volume_id], filtered_shingles[right.volume_id]
            )
            similarity = (0.70 * cosine) + (0.30 * jaccard)
            if similarity >= report_threshold:
                pairs.append(
                    PairScore(
                        volume_a=left,
                        volume_b=right,
                        similarity=similarity,
                        token_cosine=cosine,
                        shingle_jaccard=jaccard,
                    )
                )
    pairs.sort(key=lambda pair: (-pair.similarity, pair.volume_a.volume_id, pair.volume_b.volume_id))
    return pairs


def best_page_matches(left: VolumeSample, right: VolumeSample, width: int = 3) -> str:
    candidates: list[tuple[float, int, int]] = []
    for left_page in left.pages:
        left_shingles = make_shingles(left_page.tokens, width)
        for right_page in right.pages:
            right_shingles = make_shingles(right_page.tokens, width)
            score = _jaccard(left_shingles, right_shingles)
            if score:
                candidates.append((score, left_page.number, right_page.number))
    candidates.sort(reverse=True)
    used_left: set[int] = set()
    used_right: set[int] = set()
    selected: list[str] = []
    for score, left_number, right_number in candidates:
        if left_number in used_left or right_number in used_right:
            continue
        selected.append(f"{left_number}<->{right_number}:{score:.3f}")
        used_left.add(left_number)
        used_right.add(right_number)
        if len(selected) == 5:
            break
    return " | ".join(selected)


def _file_sha256(path: Path, cache: dict[Path, str]) -> str:
    if path not in cache:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        cache[path] = digest.hexdigest()
    return cache[path]


def exact_facsimile_matches(
    left: VolumeSample, right: VolumeSample, cache: dict[Path, str]
) -> tuple[int, str]:
    left_by_size: dict[int, list[PageSample]] = defaultdict(list)
    right_by_size: dict[int, list[PageSample]] = defaultdict(list)
    for page in left.pages:
        if page.facsimile_path and page.facsimile_path.is_file():
            left_by_size[page.facsimile_path.stat().st_size].append(page)
    for page in right.pages:
        if page.facsimile_path and page.facsimile_path.is_file():
            right_by_size[page.facsimile_path.stat().st_size].append(page)

    matches: list[tuple[int, int]] = []
    for size in left_by_size.keys() & right_by_size.keys():
        left_by_hash: dict[str, list[int]] = defaultdict(list)
        right_by_hash: dict[str, list[int]] = defaultdict(list)
        for page in left_by_size[size]:
            assert page.facsimile_path is not None
            left_by_hash[_file_sha256(page.facsimile_path, cache)].append(page.number)
        for page in right_by_size[size]:
            assert page.facsimile_path is not None
            right_by_hash[_file_sha256(page.facsimile_path, cache)].append(page.number)
        for digest in left_by_hash.keys() & right_by_hash.keys():
            matches.extend(zip(sorted(left_by_hash[digest]), sorted(right_by_hash[digest])))
    matches.sort()
    return len(matches), " | ".join(f"{left_page}<->{right_page}" for left_page, right_page in matches)


def pair_review_hint(pair: PairScore) -> str:
    left_tomes = set(pair.volume_a.declared_tomes)
    right_tomes = set(pair.volume_b.declared_tomes)
    left_parts = set(pair.volume_a.declared_parts)
    right_parts = set(pair.volume_b.declared_parts)
    if pair.exact_facsimile_count >= 3:
        return "exact_facsimile_duplicate"
    if left_tomes & right_tomes and left_parts and right_parts and left_parts.isdisjoint(right_parts):
        return "same_tome_different_parts"
    if left_tomes and right_tomes and left_tomes.isdisjoint(right_tomes):
        return "conflicting_declared_tomes"
    if left_tomes & right_tomes:
        return "same_declared_tome"
    return "text_similarity_only"


def pair_can_form_group(pair: PairScore, threshold: float) -> bool:
    return pair.similarity >= threshold and pair.review_hint not in {
        "conflicting_declared_tomes",
        "same_tome_different_parts",
    }


def assign_groups(pairs: list[PairScore], samples: Sequence[VolumeSample], threshold: float) -> None:
    sets = DisjointSet(sample.volume_id for sample in samples)
    for pair in pairs:
        if pair_can_form_group(pair, threshold):
            sets.union(pair.volume_a.volume_id, pair.volume_b.volume_id)

    members: dict[str, set[str]] = defaultdict(set)
    for sample in samples:
        members[sets.find(sample.volume_id)].add(sample.volume_id)
    pair_components: dict[str, list[PairScore]] = defaultdict(list)
    for pair in pairs:
        if pair_can_form_group(pair, threshold):
            pair_components[sets.find(pair.volume_a.volume_id)].append(pair)

    ordered_components = sorted(
        pair_components,
        key=lambda root: (
            -max(pair.similarity for pair in pair_components[root]),
            sorted(members[root]),
        ),
    )
    for group_number, root in enumerate(ordered_components, start=1):
        group_id = f"G{group_number:03d}"
        component_pairs = sorted(
            pair_components[root],
            key=lambda pair: (-pair.similarity, pair.volume_a.volume_id, pair.volume_b.volume_id),
        )
        for rank, pair in enumerate(component_pairs, start=1):
            pair.group_id = group_id
            pair.group_size = len(members[root])
            pair.rank_in_group = rank

    for rank, pair in enumerate(sorted(pairs, key=lambda item: -item.similarity), start=1):
        pair.similarity_rank = rank


def _relative(path: Path | None, base: Path) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _join_paths(paths: Iterable[Path | None], base: Path) -> str:
    return " | ".join(_relative(path, base) for path in paths if path is not None)


def write_inventory_csv(path: Path, samples: Sequence[VolumeSample], base: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS_INVENTORY)
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "volume_id": sample.volume_id,
                    "expected_series": sample.expected_series or "",
                    "expected_tome": sample.expected_tome or "",
                    "declared_series": " | ".join(sample.declared_series),
                    "declared_tomes": " | ".join(map(str, sample.declared_tomes)),
                    "declared_parts": " | ".join(sample.declared_parts),
                    "claim_evidence": " | ".join(sample.claim_evidence),
                    "name_status": sample.name_status,
                    "pages_read": len(sample.pages),
                    "page_numbers": " | ".join(str(page.number) for page in sample.pages),
                    "text_files": _join_paths((page.text_path for page in sample.pages), base),
                    "facsimiles": _join_paths((page.facsimile_path for page in sample.pages), base),
                    "alternative_ocr_files": sum(page.alternative_count for page in sample.pages),
                    "clean_tokens": len(sample.tokens),
                }
            )


def _diff_lines(sample: VolumeSample) -> list[str]:
    lines: list[str] = []
    for page in sample.pages:
        lines.append(f"===== PAGE {page.number} | {page.text_path.name} =====\n")
        wrapped = textwrap.wrap(" ".join(page.tokens), width=110) or [""]
        lines.extend(line + "\n" for line in wrapped)
    return lines


def write_pair_diff(pair: PairScore, diff_dir: Path, base: Path) -> str:
    diff_dir.mkdir(parents=True, exist_ok=True)
    name = f"{pair.volume_a.volume_id}__{pair.volume_b.volume_id}.diff"
    path = diff_dir / name
    lines = difflib.unified_diff(
        _diff_lines(pair.volume_a),
        _diff_lines(pair.volume_b),
        fromfile=pair.volume_a.volume_id,
        tofile=pair.volume_b.volume_id,
        n=2,
    )
    path.write_text("".join(lines), encoding="utf-8")
    return _relative(path, base)


def write_pairs_csv(path: Path, pairs: Sequence[PairScore], base: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        pairs,
        key=lambda pair: (
            pair.group_id or "ZZZ",
            -pair.similarity,
            pair.volume_a.volume_id,
            pair.volume_b.volume_id,
        ),
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS_PAIRS)
        writer.writeheader()
        for pair in ordered:
            writer.writerow(
                {
                    "group_id": pair.group_id,
                    "group_size": pair.group_size,
                    "rank_in_group": pair.rank_in_group,
                    "similarity_rank": pair.similarity_rank,
                    "similarity": f"{pair.similarity:.6f}",
                    "token_cosine": f"{pair.token_cosine:.6f}",
                    "shingle_jaccard": f"{pair.shingle_jaccard:.6f}",
                    "exact_facsimile_count": pair.exact_facsimile_count,
                    "exact_facsimile_pages": pair.exact_facsimile_matches,
                    "review_hint": pair.review_hint,
                    "volume_a": pair.volume_a.volume_id,
                    "volume_b": pair.volume_b.volume_id,
                    "declared_tomes_a": " | ".join(map(str, pair.volume_a.declared_tomes)),
                    "declared_tomes_b": " | ".join(map(str, pair.volume_b.declared_tomes)),
                    "declared_parts_a": " | ".join(pair.volume_a.declared_parts),
                    "declared_parts_b": " | ".join(pair.volume_b.declared_parts),
                    "name_status_a": pair.volume_a.name_status,
                    "name_status_b": pair.volume_b.name_status,
                    "best_page_matches": pair.best_page_matches,
                    "facsimiles_a": _join_paths(
                        (page.facsimile_path for page in pair.volume_a.pages), base
                    ),
                    "facsimiles_b": _join_paths(
                        (page.facsimile_path for page in pair.volume_b.pages), base
                    ),
                    "diff_file": pair.diff_file,
                }
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "teste")
    parser.add_argument("--pages", type=int, default=10, help="First unique physical pages per volume")
    parser.add_argument("--shingle-width", type=int, default=4, help="Words per volume shingle")
    parser.add_argument(
        "--threshold", type=float, default=0.30, help="Minimum similarity written to the report"
    )
    parser.add_argument(
        "--group-threshold",
        type=float,
        default=0.40,
        help="Minimum similarity used to connect volumes into a group",
    )
    parser.add_argument(
        "--max-df-ratio",
        type=float,
        default=0.20,
        help="Ignore tokens/shingles present in more than this share of volumes",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data" / "volume_similarity_audit"
    )
    parser.add_argument(
        "--no-diffs", action="store_true", help="Do not create human-readable unified diff files"
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.pages < 1:
        raise ValueError("--pages must be at least 1")
    if args.shingle_width < 2:
        raise ValueError("--shingle-width must be at least 2")
    for name in ("threshold", "group_threshold", "max_df_ratio"):
        value = getattr(args, name)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"--{name.replace('_', '-')} must be between 0 and 1")
    if args.group_threshold < args.threshold:
        raise ValueError("--group-threshold must be greater than or equal to --threshold")


def run(args: argparse.Namespace) -> tuple[list[VolumeSample], list[PairScore]]:
    validate_args(args)
    volume_dirs = discover_volumes(args.corpus)
    samples = [
        build_volume_sample(path, args.pages, args.shingle_width) for path in volume_dirs
    ]
    pairs = compare_samples(
        samples, max_df_ratio=args.max_df_ratio, report_threshold=args.threshold
    )
    facsimile_hashes: dict[Path, str] = {}
    for pair in pairs:
        pair.best_page_matches = best_page_matches(pair.volume_a, pair.volume_b)
        pair.exact_facsimile_count, pair.exact_facsimile_matches = exact_facsimile_matches(
            pair.volume_a, pair.volume_b, facsimile_hashes
        )
        pair.review_hint = pair_review_hint(pair)
    assign_groups(pairs, samples, args.group_threshold)

    output_dir = args.output_dir.resolve()
    base = PROJECT_ROOT
    write_inventory_csv(output_dir / "volume_inventory.csv", samples, base)
    if not args.no_diffs:
        for pair in pairs:
            pair.diff_file = write_pair_diff(pair, output_dir / "diffs", base)
    write_pairs_csv(output_dir / "similarity_groups.csv", pairs, base)
    return samples, pairs


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        samples, pairs = run(args)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    grouped = len({pair.group_id for pair in pairs if pair.group_id})
    mismatches = sum("mismatch" in sample.name_status for sample in samples)
    print(f"Volumes audited: {len(samples)}")
    print(f"Candidate pairs: {len(pairs)} in {grouped} groups")
    print(f"OCR title mismatches requiring facsimile review: {mismatches}")
    print(f"Inventory: {args.output_dir / 'volume_inventory.csv'}")
    print(f"Similarity report: {args.output_dir / 'similarity_groups.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
