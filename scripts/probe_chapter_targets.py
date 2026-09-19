#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.corpus_utils import page_number, page_sort_key
from tools.indexing.index_target_locator import normalize_for_search
from tools.ocr_xml_utils import clean_visible_text, read_ocr_page


GREEK_NUMERAL_CHARS = "ΑΒΓΔΕϚϜΖΗΘΙΚΛΜΝΞΟΠϞΡΣΤΥΦΧΨΩϠ"
ORDINAL_ATOM_PATTERN = rf"(?:[IVXLCDM]+|\d{{1,3}}|[{GREEK_NUMERAL_CHARS}{GREEK_NUMERAL_CHARS.lower()}]+[΄'’]?)"
ORDINAL_ATOM_RE = re.compile(
    rf"(?<![A-Za-zΑ-ω])(?P<ordinal>{ORDINAL_ATOM_PATTERN})(?![A-Za-zΑ-ω])",
    re.IGNORECASE,
)
ORDINAL_LIST_PATTERN = rf"{ORDINAL_ATOM_PATTERN}(?:\s*(?:,|ET|[-–—])\s*{ORDINAL_ATOM_PATTERN})*"
CAP_ABBREVIATION_PATTERN = r"CAPP?(?:\.|(?=\s))"
CAP_HEADING_ABBREVIATION_PATTERN = r"CAP(?:\.|(?=\s))"
CHAPTER_LABEL_PATTERN = rf"(?:{CAP_ABBREVIATION_PATTERN}|CAPUT|INTERROGATIO|INTERR?\.?|QUAESTIO|ΚΕΦ(?:ΑΛΑΙΟΝ)?\.?)"
LABELED_ORDINAL_PREFIX_RE = re.compile(
    rf"^\s*(?P<label>{CHAPTER_LABEL_PATTERN})\s*(?P<ordinals>{ORDINAL_LIST_PATTERN})\s*[.),]",
    re.IGNORECASE,
)
ORDINAL_PREFIX_RE = re.compile(
    rf"^\s*(?P<ordinals>(?:\d{{1,3}}|[IVXLCDM]+|[{GREEK_NUMERAL_CHARS}{GREEK_NUMERAL_CHARS.lower()}]+[΄'’]?)(?:\s*(?:,|ET|[-–—])\s*(?:\d{{1,3}}|[IVXLCDM]+|[{GREEK_NUMERAL_CHARS}{GREEK_NUMERAL_CHARS.lower()}]+[΄'’]?))*)\s*[.),]",
    re.IGNORECASE,
)
TRAILING_CHAPTER_REFERENCE_RE = re.compile(
    rf"\b(?P<label>{CAP_ABBREVIATION_PATTERN}|CAPUT)\s*(?P<ordinals>{ORDINAL_LIST_PATTERN})",
    re.IGNORECASE,
)
WORD_ORDINAL_PREFIX_RE = re.compile(
    r"^\s*(?P<label>CAPUT|CAP\.|INTERROGATIO|INTERR?\.|QUAESTIO)\s+"
    r"(?P<ordinal>PRIM(?:US|A|UM)|SECUND(?:US|A|UM)|TERTI(?:US|A|UM)|"
    r"QUART(?:US|A|UM)|QUINT(?:US|A|UM)|SEXT(?:US|A|UM)|SEPTIM(?:US|A|UM)|"
    r"OCTAV(?:US|A|UM)|NON(?:US|A|UM)|DECIM(?:US|A|UM)|"
    r"UNDECIM(?:US|A|UM)|DUODECIM(?:US|A|UM))(?![A-Za-z])",
    re.IGNORECASE,
)
EXPLICIT_HEADING_RE = re.compile(
    r"^\s*(?:\d{1,4}[.)]?\s+)?(?:[A-H]\s+)?[([]?"
    rf"(?P<label>CAPUT|{CAP_HEADING_ABBREVIATION_PATTERN}|INTERROGATIO|INTERR?\.|QUAESTIO|ΚΕΦ(?:ΑΛΑΙΟΝ)?\.?)\s+"
    rf"(?P<ordinal>[IVXLCDM]+|\d{{1,3}}|[{GREEK_NUMERAL_CHARS}{GREEK_NUMERAL_CHARS.lower()}]+[΄'’]?|PRIM(?:US|A|UM)|SECUND(?:US|A|UM)|TERTI(?:US|A|UM)|"
    r"QUART(?:US|A|UM)|QUINT(?:US|A|UM)|SEXT(?:US|A|UM)|SEPTIM(?:US|A|UM)|"
    r"OCTAV(?:US|A|UM)|NON(?:US|A|UM)|DECIM(?:US|A|UM)|"
    r"UNDECIM(?:US|A|UM)|DUODECIM(?:US|A|UM))(?![A-Za-z])",
    re.IGNORECASE,
)
ORDINAL_ONLY_HEADING_RE = re.compile(
    r"^\s*(?:\d{1,4}\s+)?(?P<ordinal>[IVXLCDM]+)[.'’]\s+",
    re.IGNORECASE,
)
LATIN_WORD_ORDINALS = {
    "PRIMUS": 1,
    "PRIMA": 1,
    "PRIMUM": 1,
    "SECUNDUS": 2,
    "SECUNDA": 2,
    "SECUNDUM": 2,
    "TERTIUS": 3,
    "TERTIA": 3,
    "TERTIUM": 3,
    "QUARTUS": 4,
    "QUARTA": 4,
    "QUARTUM": 4,
    "QUINTUS": 5,
    "QUINTA": 5,
    "QUINTUM": 5,
    "SEXTUS": 6,
    "SEXTA": 6,
    "SEXTUM": 6,
    "SEPTIMUS": 7,
    "SEPTIMA": 7,
    "SEPTIMUM": 7,
    "OCTAVUS": 8,
    "OCTAVA": 8,
    "OCTAVUM": 8,
    "NONUS": 9,
    "NONA": 9,
    "NONUM": 9,
    "DECIMUS": 10,
    "DECIMA": 10,
    "DECIMUM": 10,
    "UNDECIMUS": 11,
    "UNDECIMA": 11,
    "UNDECIMUM": 11,
    "DUODECIMUS": 12,
    "DUODECIMA": 12,
    "DUODECIMUM": 12,
}
ROMAN_VALUES = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}
MATCH_TOKEN_RE = re.compile(r"[a-z]{3,}")
LATIN_MATCH_STOPWORDS = {
    "ad",
    "de",
    "et",
    "ex",
    "in",
    "per",
    "qui",
    "quae",
    "quod",
    "cum",
    "non",
    "sunt",
    "est",
}


def int_to_roman(value: int) -> str:
    if not 0 < value < 4000:
        raise ValueError(f"Roman numeral out of range: {value}")
    parts: list[str] = []
    remainder = value
    for number, literal in (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ):
        count, remainder = divmod(remainder, number)
        parts.append(literal * count)
    return "".join(parts)


def roman_to_int(raw: str) -> int | None:
    token = raw.strip().upper()
    if not token or any(char not in ROMAN_VALUES for char in token):
        return None
    total = 0
    previous = 0
    for char in reversed(token):
        current = ROMAN_VALUES[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    if not 0 < total < 4000 or int_to_roman(total) != token:
        return None
    return total


def marker_style(raw: str) -> str:
    upper = raw.strip().upper()
    if upper.startswith("CAPP"):
        return "capp"
    if upper.startswith("CAPUT"):
        return "caput"
    if upper.startswith("CAP."):
        return "cap"
    if upper.startswith(("INTERROGATIO", "INTER.", "INTERR.")):
        return "interrogatio"
    if upper.startswith("QUAESTIO"):
        return "quaestio"
    if upper.startswith("ΚΕΦ"):
        return "kephalaion"
    return "ordinal"


def marker_family(style: str) -> str:
    if style in {"cap", "capp", "caput"}:
        return "chapter_label"
    if style == "kephalaion":
        return "kephalaion"
    return style


GREEK_NUMERAL_VALUES = {
    "Α": 1,
    "Β": 2,
    "Γ": 3,
    "Δ": 4,
    "Ε": 5,
    "Ϛ": 6,
    "Ϝ": 6,
    "Ζ": 7,
    "Η": 8,
    "Θ": 9,
    "Ι": 10,
    "Κ": 20,
    "Λ": 30,
    "Μ": 40,
    "Ν": 50,
    "Ξ": 60,
    "Ο": 70,
    "Π": 80,
    "Ϟ": 90,
    "Ρ": 100,
    "Σ": 200,
    "Τ": 300,
    "Υ": 400,
    "Φ": 500,
    "Χ": 600,
    "Ψ": 700,
    "Ω": 800,
    "Ϡ": 900,
}


def greek_numeral_to_int(raw: str) -> int | None:
    token = raw.strip(" ΄'’")
    if token.endswith("ς"):
        token = token[:-1] + "Ϛ"
    token = token.upper()
    if not token or any(char not in GREEK_NUMERAL_VALUES for char in token):
        return None
    values = [GREEK_NUMERAL_VALUES[char] for char in token]
    if any(left < right for left, right in zip(values, values[1:])):
        return None
    value = sum(values)
    return value if 0 < value < 4000 else None


def ordinal_token_to_int(raw: str) -> int | None:
    token = raw.strip(" ΄'’")
    if token.isdigit():
        value = int(token)
        return value if 0 < value < 4000 else None
    return roman_to_int(token) or greek_numeral_to_int(token)


def parse_ordinal_list(raw: str) -> list[int]:
    tokens = [match.group("ordinal") for match in ORDINAL_ATOM_RE.finditer(raw)]
    values = [value for token in tokens if (value := ordinal_token_to_int(token)) is not None]
    if len(values) == 2 and re.search(r"[-–—]", raw):
        start, end = values
        if start <= end and end - start <= 200:
            values = list(range(start, end + 1))
    return list(dict.fromkeys(values))


def normalize_ocr_evidence(text: str) -> str:
    """Apply the production OCR cleanup only to transient comparison text."""

    return normalize_for_search(clean_visible_text(text or ""))


def ordered_ocr_search_text(path: Path) -> str:
    page = read_ocr_page(path)
    parts = [
        block.content_clean
        for block in page.blocks
        if block.content_clean
        and block.tag_name != "rodape"
        and block.tipo not in {"rodape", "nota", "nota_marginal"}
    ]
    return "\n".join(parts)


def strip_entry_marker(text: str) -> str:
    word_match = WORD_ORDINAL_PREFIX_RE.match(text or "")
    if word_match:
        return (text or "")[word_match.end() :].lstrip(" .,:;—–-")
    match = LABELED_ORDINAL_PREFIX_RE.match(text or "") or ORDINAL_PREFIX_RE.match(text or "")
    if match:
        return (text or "")[match.end() :].lstrip(" .,:;—–-")
    return text or ""


def entry_query_raw(entry: dict[str, Any]) -> str:
    target = strip_entry_marker(str(entry.get("target_raw") or ""))
    if len(MATCH_TOKEN_RE.findall(normalize_ocr_evidence(target))) >= 2:
        return target
    return strip_entry_marker(str(entry.get("entry_raw") or ""))


def normalized_text_similarity(query: str, candidate: str) -> float:
    query_norm = normalize_ocr_evidence(query)
    candidate_norm = normalize_ocr_evidence(candidate)
    if not query_norm or not candidate_norm:
        return 0.0
    query_tokens = {
        token
        for token in MATCH_TOKEN_RE.findall(query_norm)
        if token not in LATIN_MATCH_STOPWORDS
    }
    title_fragment = strip_entry_marker(candidate)
    title_fragment = re.split(r"(?<=[.!?])\s", title_fragment, maxsplit=1)[0]
    candidate_variants = {
        candidate_norm,
        normalize_ocr_evidence(title_fragment),
    }
    scores: list[float] = []
    for variant in candidate_variants:
        if not variant:
            continue
        if query_norm in variant or variant in query_norm:
            scores.append(1.0)
            continue
        candidate_tokens = {
            token
            for token in MATCH_TOKEN_RE.findall(variant)
            if token not in LATIN_MATCH_STOPWORDS
        }
        coverage = (
            len(query_tokens & candidate_tokens) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        prefix_ratio = difflib.SequenceMatcher(
            None,
            query_norm[:240],
            variant[:240],
            autojunk=False,
        ).ratio()
        scores.append(max(coverage, prefix_ratio))
    return round(max(scores, default=0.0), 6)


def extract_entry_ordinals(entry: dict[str, Any]) -> tuple[str, list[int]]:
    raw_json = entry.get("raw_json")
    explicit = None
    if isinstance(raw_json, dict):
        explicit = next(
            (
                raw_json.get(field)
                for field in ("ordinal_raw", "chapter_number_raw", "structural_number_raw")
                if raw_json.get(field)
            ),
            None,
        )
    source = str(explicit or entry.get("entry_raw") or "").strip()
    if explicit:
        prefix_match = LABELED_ORDINAL_PREFIX_RE.match(source) or ORDINAL_PREFIX_RE.match(source)
        values = (
            parse_ordinal_list(prefix_match.group("ordinals"))
            if prefix_match
            else parse_ordinal_list(source)
        )
        if not values:
            values = [
                value
                for token in re.findall(r"[A-Za-z]+", source.upper())
                if (value := LATIN_WORD_ORDINALS.get(token)) is not None
            ]
        return source, list(dict.fromkeys(values))
    word_match = WORD_ORDINAL_PREFIX_RE.match(source)
    if word_match:
        return word_match.group(0).strip(), [LATIN_WORD_ORDINALS[word_match.group("ordinal").upper()]]
    match = LABELED_ORDINAL_PREFIX_RE.match(source) or ORDINAL_PREFIX_RE.match(source)
    if match:
        return match.group(0).strip(), parse_ordinal_list(match.group("ordinals"))

    trailing = list(TRAILING_CHAPTER_REFERENCE_RE.finditer(source))
    if trailing:
        values: list[int] = []
        for trailing_match in trailing:
            values.extend(parse_ordinal_list(trailing_match.group("ordinals")))
        marker_raw = " | ".join(match.group(0).strip() for match in trailing)
        return marker_raw, list(dict.fromkeys(values))
    return str(explicit or ""), []


def entry_ordinal_source(entry: dict[str, Any]) -> str:
    raw_json = entry.get("raw_json")
    if isinstance(raw_json, dict) and any(
        raw_json.get(field)
        for field in ("ordinal_raw", "chapter_number_raw", "structural_number_raw")
    ):
        return "explicit_metadata"
    source = str(entry.get("entry_raw") or "").strip()
    if WORD_ORDINAL_PREFIX_RE.match(source) or LABELED_ORDINAL_PREFIX_RE.match(source):
        return "labeled_prefix"
    if ORDINAL_PREFIX_RE.match(source):
        return "ordinal_prefix"
    if TRAILING_CHAPTER_REFERENCE_RE.search(source):
        return "trailing_chapter_reference"
    return "unparsed"


def find_latin_chapter_headings(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    # ordered_ocr_search_text already cleaned each OCR block. Cleaning the
    # assembled page again could join a trailing hyphen to the next block.
    cleaned_lines = (text or "").splitlines()
    for line_number, line in enumerate(cleaned_lines, start=1):
        compact = " ".join(line.split())
        if not compact:
            continue
        context = " ".join(
            " ".join(value.split())
            for value in cleaned_lines[line_number - 1 : line_number + 2]
            if value.strip()
        )[:500]
        explicit_found = False
        for match in EXPLICIT_HEADING_RE.finditer(compact):
            label_raw = match.group("label")
            title_raw = compact[match.end() :].strip(" .,:;—–-")
            citation_like = (
                label_raw.casefold().rstrip(".") == "cap"
                and label_raw != label_raw.upper()
                and bool(re.match(r"(?:p(?:ag)?|n|l)\.?\s*\d", title_raw, re.IGNORECASE))
            )
            if citation_like:
                continue
            raw_ordinal = match.group("ordinal").strip(" ΄'’").upper()
            ordinal = LATIN_WORD_ORDINALS.get(raw_ordinal)
            if ordinal is None:
                ordinal = ordinal_token_to_int(raw_ordinal)
            if ordinal is None:
                continue
            explicit_found = True
            hits.append(
                {
                    "ordinal": ordinal,
                    "heading_raw": match.group(0),
                    "title_raw": title_raw,
                    "line": line_number,
                    "context": context,
                    "style": marker_style(match.group("label")),
                    "qualified": bool(re.search(r"\bbis\b", compact[match.end() : match.end() + 24], re.I)),
                }
            )
        ordinal_match = None if explicit_found else ORDINAL_ONLY_HEADING_RE.match(compact)
        if ordinal_match:
            raw_ordinal = ordinal_match.group("ordinal").upper()
            ordinal = roman_to_int(raw_ordinal)
            trailing_words = [
                token
                for token in re.findall(r"[A-Za-zÆŒÀ-ÿ]{2,}", compact[ordinal_match.end() :])
                if roman_to_int(token) is None
            ]
            plausible_prose = len(trailing_words) >= 2 or any(len(token) >= 6 for token in trailing_words)
            if ordinal is not None and plausible_prose:
                hits.append(
                    {
                        "ordinal": ordinal,
                        "heading_raw": ordinal_match.group(0).strip(),
                        "title_raw": compact[ordinal_match.end() :].strip(" .,:;—–-"),
                        "line": line_number,
                        "context": context,
                        "style": "ordinal",
                        "qualified": bool(
                            re.search(r"\bbis\b", compact[ordinal_match.end() : ordinal_match.end() + 24], re.I)
                        ),
                    }
                )
    return hits


def split_local_marker_runs(hits: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split repeated chapter sequences instead of stitching separate works together."""

    if not hits:
        return []
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    high_water = 0
    for hit in sorted(hits, key=lambda item: item["position"]):
        ordinal = int(hit["ordinal"])
        if (
            current
            and ordinal == 1
            and high_water > 1
            and hit["position"] > current[-1]["position"]
        ):
            runs.append(current)
            current = []
            high_water = 0
        current.append(hit)
        high_water = max(high_water, ordinal)
    if current:
        runs.append(current)
    return runs


def split_expected_ref_runs(
    expected_refs: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Split an index into books/parts when chapter numbering restarts at one."""

    if not expected_refs:
        return []
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    high_water = 0
    for expected in expected_refs:
        ordinal = int(expected["ref"]["ordinal"])
        if current and ordinal == 1 and high_water > 1:
            runs.append(current)
            current = []
            high_water = 0
        current.append(expected)
        high_water = max(high_water, ordinal)
    if current:
        runs.append(current)
    return runs


def score_marker_run(
    expected_refs: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    family: str,
) -> dict[str, Any]:
    match_bonus = 0.2
    states: dict[int, tuple[float, int, float, tuple[int | None, ...]]] = {
        -1: (0.0, 0, 0.0, ())
    }
    for expected_index, expected in enumerate(expected_refs):
        next_states: dict[
            int, tuple[float, int, float, tuple[int | None, ...]]
        ] = {}
        ordinal = expected["ref"]["ordinal"]
        exact_ordinal_similarity = max(
            (
                normalized_text_similarity(
                    expected["query_raw"], hit.get("title_raw") or hit.get("context") or ""
                )
                for hit in hits
                if hit["ordinal"] == ordinal
            ),
            default=0.0,
        )
        compatible_hits: list[tuple[int, float]] = []
        for hit_index, hit in enumerate(hits):
            score = normalized_text_similarity(
                expected["query_raw"], hit.get("title_raw") or hit.get("context") or ""
            )
            ordinal_distance = abs(int(hit["ordinal"]) - int(ordinal))
            if hit["ordinal"] == ordinal or (
                ordinal_distance <= 3
                and score >= 0.55
                and score >= exact_ordinal_similarity + 0.15
            ):
                compatible_hits.append((hit_index, score))
        for previous_index, (utility, resolved, similarity, path) in states.items():
            skipped = (utility, resolved, similarity, path + (None,))
            current = next_states.get(previous_index)
            if current is None or skipped[:3] > current[:3]:
                next_states[previous_index] = skipped
            for hit_index, score in compatible_hits:
                repeated_expected_ordinal = (
                    expected_index > 0
                    and ordinal == expected_refs[expected_index - 1]["ref"]["ordinal"]
                )
                if hit_index < previous_index or (
                    hit_index == previous_index and not repeated_expected_ordinal
                ):
                    continue
                hit = hits[hit_index]
                if hit.get("excluded") or (
                    hit.get("qualified") and not expected["allows_qualified"]
                ):
                    continue
                chosen = (
                    utility + match_bonus + score,
                    resolved + 1,
                    similarity + score,
                    path + (hit_index,),
                )
                current = next_states.get(hit_index)
                if current is None or chosen[:3] > current[:3]:
                    next_states[hit_index] = chosen
        states = next_states

    if not states:
        return {
            "family": family,
            "hits": hits,
            "path": tuple(None for _ in expected_refs),
            "resolved": 0,
            "similarity": 0.0,
            "last_index": -1,
        }
    last_index, best = max(
        states.items(),
        key=lambda item: (
            round(item[1][0], 6),
            item[1][1],
            round(item[1][2], 6),
            -next((index for index in item[1][3] if index is not None), len(hits)),
            -item[0],
        ),
    )
    _, resolved, similarity, path = best
    return {
        "family": family,
        "hits": hits,
        "path": path,
        "resolved": resolved,
        "similarity": round(similarity, 6),
        "last_index": last_index,
    }


def select_best_marker_run(
    expected_refs: list[dict[str, Any]],
    candidate_hits: list[dict[str, Any]],
    *,
    minimum_coverage: float = 0.8,
    prefer_title_evidence: bool = False,
) -> dict[str, Any]:
    """Choose one local monotonic marker-family run by coverage and normalized evidence."""

    families = sorted({marker_family(hit["style"]) for hit in candidate_hits})
    expected_runs = split_expected_ref_runs(expected_refs)
    segmented_candidates: list[dict[str, Any]] = []
    if len(expected_runs) > 1:
        for family in families:
            family_hits = [
                hit
                for hit in candidate_hits
                if marker_family(hit["style"]) == family and not hit.get("excluded")
            ]
            marker_runs = split_local_marker_runs(family_hits)
            if len(marker_runs) != len(expected_runs):
                continue
            scored_runs = [
                score_marker_run(expected_run, marker_run, family)
                for expected_run, marker_run in zip(expected_runs, marker_runs)
            ]
            coverages = [
                score["resolved"] / len(expected_run)
                for score, expected_run in zip(scored_runs, expected_runs)
            ]
            similarity_means = [
                score["similarity"] / max(score["resolved"], 1)
                for score in scored_runs
            ]
            if not all(coverage >= minimum_coverage for coverage in coverages) or not all(
                similarity >= 0.45 for similarity in similarity_means
            ):
                continue
            path_hits: list[dict[str, Any] | None] = []
            for score in scored_runs:
                path_hits.extend(
                    score["hits"][index] if index is not None else None
                    for index in score["path"]
                )
            segmented_candidates.append(
                {
                    "family": family,
                    "path_hits": path_hits,
                    "resolved": sum(score["resolved"] for score in scored_runs),
                    "similarity": round(sum(score["similarity"] for score in scored_runs), 6),
                    "segment_coverages": [round(value, 4) for value in coverages],
                    "segment_similarity_means": [
                        round(value, 6) for value in similarity_means
                    ],
                }
            )
    if segmented_candidates:
        best_segmented = max(
            segmented_candidates,
            key=lambda item: (item["resolved"], item["similarity"]),
        )
        coverage = best_segmented["resolved"] / len(expected_refs)
        return {
            "family": best_segmented["family"],
            "path_hits": best_segmented["path_hits"],
            "proposed_path_hits": best_segmented["path_hits"],
            "resolved": best_segmented["resolved"],
            "similarity": best_segmented["similarity"],
            "coverage": round(coverage, 4),
            "accepted": True,
            "segmented_run_count": len(expected_runs),
            "segment_coverages": best_segmented["segment_coverages"],
            "segment_similarity_means": best_segmented["segment_similarity_means"],
        }
    family_runs: list[dict[str, Any]] = []
    for family in families:
        family_hits = [
            hit
            for hit in candidate_hits
            if marker_family(hit["style"]) == family and not hit.get("excluded")
        ]
        for hits in split_local_marker_runs(family_hits):
            family_runs.append(score_marker_run(expected_refs, hits, family))

    if not family_runs:
        return {
            "family": None,
            "path_hits": [None] * len(expected_refs),
            "proposed_path_hits": [None] * len(expected_refs),
            "resolved": 0,
            "similarity": 0.0,
            "coverage": 0.0,
            "accepted": False,
            "segmented_run_count": 0,
            "segment_coverages": [],
            "segment_similarity_means": [],
        }
    eligible_runs = [
        run
        for run in family_runs
        if expected_refs and run["resolved"] / len(expected_refs) >= minimum_coverage
    ]
    if prefer_title_evidence and eligible_runs:
        best_run = max(
            eligible_runs,
            key=lambda run: (
                run["resolved"] / len(expected_refs)
                + run["similarity"] / max(run["resolved"], 1),
                run["similarity"] / max(run["resolved"], 1),
                run["resolved"],
                -next((index for index in run["path"] if index is not None), len(run["hits"])),
            ),
        )
    else:
        best_run = max(
            family_runs,
            key=lambda run: (
                run["resolved"],
                run["similarity"],
                -next((index for index in run["path"] if index is not None), len(run["hits"])),
            ),
        )
    proposed_path_hits = [
        best_run["hits"][index] if index is not None else None
        for index in best_run["path"]
    ]
    coverage = best_run["resolved"] / len(expected_refs) if expected_refs else 0.0
    accepted = coverage >= minimum_coverage
    return {
        "family": best_run["family"],
        "path_hits": proposed_path_hits if accepted else [None] * len(expected_refs),
        "proposed_path_hits": proposed_path_hits,
        "resolved": best_run["resolved"],
        "similarity": best_run["similarity"],
        "coverage": round(coverage, 4),
        "accepted": accepted,
        "segmented_run_count": 1,
        "segment_coverages": [round(coverage, 4)],
        "segment_similarity_means": [
            round(best_run["similarity"] / max(best_run["resolved"], 1), 6)
        ],
    }


def is_chapter_section(section: dict[str, Any]) -> bool:
    labels = " ".join(
        str(section.get(field) or "")
        for field in ("index_kind", "heading_raw", "heading_norm")
    ).casefold()
    raw_json = section.get("raw_json")
    if isinstance(raw_json, dict):
        labels += " " + str(raw_json.get("section_kind") or "").casefold()
    return "capit" in labels or "chapter_index" in labels


def existing_path(
    value: Any,
    project_root: Path,
    source_root: Path | None = None,
) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        resolved = path.resolve()
        return resolved if resolved.is_file() else None
    for root in (source_root, project_root):
        if root is None:
            continue
        resolved = (root / path).resolve()
        if resolved.is_file():
            return resolved
    return None


def unique_positions(paths: Iterable[Path], positions: dict[Path, int]) -> list[int]:
    return sorted({positions[path] for path in paths if path in positions})


def associated_work_for_section(
    section: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any] | None:
    return next(
        (
            work
            for work in payload.get("works") or []
            if isinstance(work, dict) and work.get("work_key") == section.get("work_key")
        ),
        None,
    )


def is_retrospective_section(section: dict[str, Any]) -> bool:
    labels = " ".join(
        str(section.get(field) or "")
        for field in ("section_key", "scope_kind")
    ).casefold()
    return any(
        marker in labels
        for marker in ("volume_end", "volume-end", "volume_back", "volume-back", "retrospective")
    )


def section_body_bounds(
    section: dict[str, Any],
    payload: dict[str, Any],
    files: list[Path],
    positions: dict[Path, int],
    project_root: Path,
) -> tuple[int, int]:
    source_root = files[0].parent
    section_file = existing_path(
        section.get("file_end") or section.get("file_start"),
        project_root,
        source_root,
    )
    if section_file is None or section_file not in positions:
        raise ValueError(f"Section has no usable physical file: {section.get('section_key')}")
    start = positions[section_file]

    later_section_positions = unique_positions(
        filter(
            None,
            (
                existing_path(other.get("file_start"), project_root, source_root)
                for other in payload.get("sections") or []
                if isinstance(other, dict) and other is not section
            ),
        ),
        positions,
    )
    later_section_positions = [
        position for position in later_section_positions if position > start + 1
    ]
    next_distant_section = min(later_section_positions) if later_section_positions else None

    associated_work = associated_work_for_section(section, payload)
    if associated_work:
        associated_work_key = associated_work.get("work_key")
        later_linked_section_positions = unique_positions(
            filter(
                None,
                (
                    existing_path(other.get("file_start"), project_root, source_root)
                    for other in payload.get("sections") or []
                    if isinstance(other, dict)
                    and other is not section
                    and other.get("work_key")
                    and other.get("work_key") != associated_work_key
                ),
            ),
            positions,
        )
        later_linked_section_positions = [
            position
            for position in later_linked_section_positions
            if position > start + 1
        ]
        next_linked_section = (
            min(later_linked_section_positions)
            if later_linked_section_positions
            else None
        )
        work_start_file = existing_path(
            associated_work.get("start_file"), project_root, source_root
        )
        work_end_file = existing_path(
            associated_work.get("end_file"), project_root, source_root
        )
        if (
            work_start_file in positions
            and work_end_file in positions
            and positions[work_start_file] <= positions[work_end_file]
        ):
            work_start = positions[work_start_file]
            work_end = positions[work_end_file] + 1
            terminal_index = start >= work_end - 2 and work_start < start
            if terminal_index:
                return work_start, work_end
            if next_distant_section is not None:
                work_end = min(work_end, next_distant_section + 1)
            if work_start <= start < work_end:
                return start, work_end
            return work_start, work_end
        if work_start_file in positions:
            work_start = positions[work_start_file]
            direct_front_evidence = (
                section.get("scope_kind") == "work_front"
                and associated_work.get("source_section_key") == section.get("section_key")
            )
            body_start = start if direct_front_evidence else max(start, work_start)
            next_positions: list[int] = []
            works = [work for work in payload.get("works") or [] if isinstance(work, dict)]
            work_index = next(
                (
                    index
                    for index, work in enumerate(works)
                    if work is associated_work
                    or work.get("work_key") == associated_work.get("work_key")
                ),
                len(works),
            )
            for later_index, later_work in enumerate(works):
                if not isinstance(later_work, dict) or later_work is associated_work:
                    continue
                later_start_file = existing_path(
                    later_work.get("start_file"), project_root, source_root
                )
                if later_start_file not in positions:
                    continue
                later_position = positions[later_start_file]
                if later_index > work_index and later_position == work_start:
                    shared_boundary = later_position + 1
                    if shared_boundary > body_start:
                        next_positions.append(shared_boundary)
                if later_position > body_start:
                    next_positions.append(later_position)
            if next_linked_section is not None:
                next_positions.append(next_linked_section + 1)
            return body_start, min(next_positions) if next_positions else len(files)

    work_starts = unique_positions(
        filter(
            None,
            (
                existing_path(work.get("start_file"), project_root, source_root)
                for work in payload.get("works") or []
                if isinstance(work, dict)
            ),
        ),
        positions,
    )
    later_starts = [position for position in work_starts if position > start]
    if later_starts:
        return start, later_starts[0]

    return start, next_distant_section + 1 if next_distant_section is not None else len(files)


def prepare_entry_refs(
    section: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entry_results: list[dict[str, Any]] = []
    chapter_results: list[dict[str, Any]] = []
    expected_refs: list[dict[str, Any]] = []
    for entry in section.get("entries") or []:
        ordinal_raw, ordinals = extract_entry_ordinals(entry)
        ordinal_source = entry_ordinal_source(entry)
        refs: list[dict[str, Any]] = []
        for ordinal in ordinals:
            ref = {
                "ordinal": ordinal,
                "ordinal_roman": int_to_roman(ordinal),
                "status": "pending",
                "candidates": [],
            }
            refs.append(ref)
            chapter_results.append(ref)
            expected_refs.append(
                {
                    "ref": ref,
                    "index_style": marker_style(str(entry.get("entry_raw") or ordinal_raw)),
                    "allows_qualified": bool(re.search(r"\bbis\b", ordinal_raw, re.I)),
                    "query_raw": entry_query_raw(entry),
                    "ordinal_source": ordinal_source,
                }
            )
        entry_results.append(
            {
                "entry_key": entry.get("entry_key"),
                "ordinal_raw": ordinal_raw,
                "ordinal_source": ordinal_source,
                "status": "pending" if refs else "unparsed",
                "chapter_refs": refs,
            }
        )
    return entry_results, chapter_results, expected_refs


def rejected_section_result(
    section: dict[str, Any],
    entry_results: list[dict[str, Any]],
    chapter_results: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    for ref in chapter_results:
        ref["status"] = "missing"
    for entry in entry_results:
        if entry["chapter_refs"]:
            entry["status"] = "partial"
    return {
        "section_key": section.get("section_key"),
        "work_key": section.get("work_key"),
        "body_window": {
            "start_file": section.get("file_start"),
            "end_file": section.get("file_end"),
            "file_count": 0,
        },
        "strategy": "v3_safe_window_rejection",
        "search_eligible": False,
        "rejection_reason": reason,
        "selected_marker_family": None,
        "marker_run_accepted": False,
        "proposed_marker_coverage": 0.0,
        "normalized_similarity_total": 0.0,
        "entry_count": len(entry_results),
        "chapter_ref_count": len(chapter_results),
        "resolved_entry_count": 0,
        "resolved_chapter_count": 0,
        "proposed_resolved_chapter_count": 0,
        "proposed_missing_ordinals": sorted({ref["ordinal"] for ref in chapter_results}),
        "missing_chapter_count": len(chapter_results),
        "ambiguous_chapter_count": 0,
        "unparsed_entry_count": sum(item["status"] == "unparsed" for item in entry_results),
        "chapter_coverage": 0.0,
        "entry_coverage": 0.0,
        "resolved_targets_monotonic": True,
        "entries": entry_results,
    }


def locate_section(
    section: dict[str, Any],
    payload: dict[str, Any],
    files: list[Path],
    positions: dict[Path, int],
    project_root: Path,
) -> dict[str, Any]:
    entry_results, chapter_results, expected_refs = prepare_entry_refs(section)
    if is_retrospective_section(section):
        return rejected_section_result(
            section,
            entry_results,
            chapter_results,
            reason="retrospective_section_requires_backward_target_evidence",
        )

    start, end = section_body_bounds(section, payload, files, positions, project_root)
    source_root = files[0].parent
    section_anchor = existing_path(
        section.get("file_end") or section.get("file_start"),
        project_root,
        source_root,
    )
    search_direction = (
        "backward_bounded_work"
        if section_anchor in positions and start < positions[section_anchor]
        else "forward_bounded_work"
    )
    candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, str, int, str]] = set()

    for path in files[start:end]:
        searchable = ordered_ocr_search_text(path)
        for hit in find_latin_chapter_headings(searchable):
            key = (hit["ordinal"], str(path), hit["line"], hit["style"])
            if key in seen:
                continue
            seen.add(key)
            candidates[hit["ordinal"]].append(
                {
                    "ordinal": hit["ordinal"],
                    "target_file": str(path),
                    "physical_page": page_number(path),
                    "heading_raw": hit["heading_raw"],
                    "title_raw": hit["title_raw"],
                    "context": hit["context"],
                    "line": hit["line"],
                    "style": hit["style"],
                    "qualified": hit["qualified"],
                    "position": [positions[path.resolve()], hit["line"]],
                }
            )

    source_anchors = [
        path
        for value in (section.get("file_start"), section.get("file_end"))
        if (path := existing_path(value, project_root, source_root)) is not None
        and path in positions
    ]
    if source_anchors:
        source_first = min(positions[path] for path in source_anchors)
        source_last = max(positions[path] for path in source_anchors)
        source_paths = {path.resolve() for path in files[source_first : source_last + 1]}
    else:
        source_paths = set()
    excluded: set[tuple[int, str, int, str]] = set()
    for expected in expected_refs:
        ref = expected["ref"]
        compatible = [
            hit
            for hit in candidates.get(ref["ordinal"], [])
            if Path(hit["target_file"]) in source_paths
            and hit["style"] == expected["index_style"]
            and normalized_text_similarity(
                expected["query_raw"], hit.get("title_raw") or hit.get("context") or ""
            )
            >= 0.75
            and (ref["ordinal"], hit["target_file"], hit["line"], hit["style"]) not in excluded
        ]
        if compatible:
            hit = min(compatible, key=lambda item: item["position"])
            excluded.add((ref["ordinal"], hit["target_file"], hit["line"], hit["style"]))
            hit["excluded"] = True

    all_candidates = [hit for hits in candidates.values() for hit in hits]
    trailing_only = bool(expected_refs) and all(
        expected["ordinal_source"] == "trailing_chapter_reference"
        for expected in expected_refs
    )
    selected_run = select_best_marker_run(
        expected_refs,
        all_candidates,
        prefer_title_evidence=not trailing_only,
    )
    proposed_missing_ordinals = sorted(
        {
            expected["ref"]["ordinal"]
            for expected, hit in zip(expected_refs, selected_run["proposed_path_hits"])
            if hit is None
        }
    )
    resolved_for_score = max(int(selected_run["resolved"]), 1)
    mean_similarity = selected_run["similarity"] / resolved_for_score
    rejection_reason = None
    strong_closed_backward_sequence = (
        search_direction == "backward_bounded_work"
        and selected_run["coverage"] >= 0.9
    )
    if (
        selected_run["accepted"]
        and not trailing_only
        and mean_similarity < 0.45
        and not strong_closed_backward_sequence
    ):
        selected_run["accepted"] = False
        selected_run["path_hits"] = [None] * len(expected_refs)
        rejection_reason = "weak_title_evidence_for_non_trailing_ordinal_run"
    for expected, chosen in zip(expected_refs, selected_run["path_hits"]):
        ref = expected["ref"]
        hits = [
            hit
            for hit in candidates.get(ref["ordinal"], [])
            if (ref["ordinal"], hit["target_file"], hit["line"], hit["style"]) not in excluded
            and (expected["allows_qualified"] or not hit["qualified"])
            and marker_family(hit["style"]) == selected_run["family"]
        ]
        hits.sort(key=lambda item: item["position"])
        if chosen is not None:
            chosen = dict(chosen)
            chosen["normalized_similarity"] = normalized_text_similarity(
                expected["query_raw"], chosen.get("title_raw") or chosen.get("context") or ""
            )
            ref["status"] = "resolved"
            ref["target"] = chosen
            ref["candidates"] = hits
        else:
            ref["status"] = "missing"

    for entry in entry_results:
        refs = entry["chapter_refs"]
        if not refs:
            continue
        entry["status"] = (
            "resolved" if all(ref["status"] == "resolved" for ref in refs) else "partial"
        )

    resolved_chapters = sum(ref["status"] == "resolved" for ref in chapter_results)
    resolved_entries = sum(item["status"] == "resolved" for item in entry_results)
    resolved_positions = [
        positions[Path(ref["target"]["target_file"])]
        for ref in chapter_results
        if ref["status"] == "resolved"
    ]
    monotonic = all(left <= right for left, right in zip(resolved_positions, resolved_positions[1:]))

    return {
        "section_key": section.get("section_key"),
        "work_key": section.get("work_key"),
        "body_window": {
            "start_file": str(files[start]),
            "end_file": str(files[end - 1]) if end > start else None,
            "file_count": end - start,
        },
        "strategy": (
            "v3_local_monotonic_marker_family_run_with_in_memory_ocr_normalization_"
            + search_direction
        ),
        "search_eligible": True,
        "rejection_reason": rejection_reason,
        "selected_marker_family": selected_run["family"],
        "segmented_run_count": selected_run.get("segmented_run_count", 0),
        "segment_coverages": selected_run.get("segment_coverages", []),
        "segment_similarity_means": selected_run.get("segment_similarity_means", []),
        "marker_run_accepted": selected_run["accepted"],
        "proposed_marker_coverage": selected_run["coverage"],
        "normalized_similarity_total": selected_run["similarity"],
        "normalized_similarity_mean": round(mean_similarity, 6),
        "ordinal_sources": sorted({expected["ordinal_source"] for expected in expected_refs}),
        "entry_count": len(entry_results),
        "chapter_ref_count": len(chapter_results),
        "resolved_entry_count": resolved_entries,
        "resolved_chapter_count": resolved_chapters,
        "proposed_resolved_chapter_count": selected_run["resolved"],
        "proposed_missing_ordinals": proposed_missing_ordinals,
        "missing_chapter_count": sum(ref["status"] == "missing" for ref in chapter_results),
        "ambiguous_chapter_count": sum(ref["status"] == "ambiguous" for ref in chapter_results),
        "unparsed_entry_count": sum(item["status"] == "unparsed" for item in entry_results),
        "chapter_coverage": round(resolved_chapters / len(chapter_results), 4) if chapter_results else 0.0,
        "entry_coverage": round(resolved_entries / len(entry_results), 4) if entry_results else 0.0,
        "resolved_targets_monotonic": monotonic,
        "entries": entry_results,
    }


def run_probe(payload_path: Path) -> dict[str, Any]:
    project_root = PROJECT_ROOT
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    volume = payload.get("volume") or {}
    source_root = Path(str(volume.get("source_root") or ""))
    if not source_root.is_absolute():
        source_root = project_root / source_root
    source_root = source_root.resolve()
    files = sorted(source_root.glob("*.txt"), key=page_sort_key)
    if not files:
        raise FileNotFoundError(f"No OCR text files found in {source_root}")
    positions = {path.resolve(): index for index, path in enumerate(files)}

    sections = [
        section
        for section in payload.get("sections") or []
        if isinstance(section, dict)
        and is_chapter_section(section)
        and bool(section.get("entries"))
    ]
    results = [
        locate_section(section, payload, files, positions, project_root)
        for section in sections
    ]
    chapter_total = sum(result["chapter_ref_count"] for result in results)
    chapter_resolved = sum(result["resolved_chapter_count"] for result in results)
    entry_total = sum(result["entry_count"] for result in results)
    entry_resolved = sum(result["resolved_entry_count"] for result in results)
    return {
        "schema_version": 1,
        "experiment": "deterministic_chapter_target_probe",
        "volume_id": volume.get("volume_id"),
        "source_root": str(source_root),
        "section_count": len(results),
        "entry_count": entry_total,
        "chapter_ref_count": chapter_total,
        "resolved_entry_count": entry_resolved,
        "resolved_chapter_count": chapter_resolved,
        "entry_coverage": round(entry_resolved / entry_total, 4) if entry_total else 0.0,
        "chapter_coverage": round(chapter_resolved / chapter_total, 4) if chapter_total else 0.0,
        "sections": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only proof of concept for deterministic INDEX CAPITUM target localization."
    )
    parser.add_argument("payload", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_probe(args.payload.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "sections"}
    if args.output:
        summary["output"] = str(args.output.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
