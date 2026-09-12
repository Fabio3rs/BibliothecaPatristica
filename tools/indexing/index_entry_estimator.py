"""Estimate how many index entries an OCR region may contain from line-shape signals.

Counts are deliberately expressed as lower/likely/upper bounds. The estimator was created as a
coverage alarm for chunked extraction, not as a parser: regex cannot reliably join OCR wraps,
inherit topic scope, or distinguish editorial topics and subtopics, so Codex still performs the
final NLP segmentation.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from tools.ocr_xml_utils import read_ocr_page


_SPACE_RE = re.compile(r"\s+")
_EDITORIAL_REF_AT_END_RE = re.compile(
    r"(?:\.{2,}\s*)?(?:\d{1,4}(?:\s*(?:[-–—,;]|à|ad|et|e)\s*\d{1,4})*"
    r"(?:\s*(?:col\.?|lin\.?|n\.?)\s*\d{1,4})?[a-z]?)\s*[.)\]]?\s*$",
    re.IGNORECASE,
)
_LEADER_RE = re.compile(r"(?:\.{3,}|·{3,}|…)")
_ENTRY_PREFIX_RE = re.compile(
    r"^(?:[-–—]\s*)?(?:[A-ZÀ-ÖØ-ÞΑ-Ω][A-ZÀ-ÖØ-ÞΑ-Ω'’ÆŒ\-]{1,}"
    r"|(?:CAP(?:UT)?|LIB(?:ER)?|TOM(?:US)?|TRACT(?:ATUS)?|HOM(?:ILIA)?|"
    r"EPIST(?:OLA)?|PSALM(?:US)?|ART(?:ICULUS)?|§)\b)",
)
_SUBENTRY_PREFIX_RE = re.compile(
    r"^(?:[-–—•*]\s+|(?:[IVXLCDM]+|\d{1,3}|[a-z])\s*[.)]\s+)",
    re.IGNORECASE,
)
_STRUCTURAL_LINE_RE = re.compile(
    r"^(?:INDEX|INDICES|TABLE|TABULA|CONTENTS?|CAPITA|ARGUMENTUM|"
    r"[A-Z]\.?|[IVXLCDM]+\.?)$",
    re.IGNORECASE,
)
_TRAILING_HYPHEN_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿΑ-ω][-‐‑]\s*$")
_RIGHT_CONTINUATION_RE = re.compile(r"^(?:[a-zà-öø-ÿ]|[,;:.)\]])")
_RIGHT_NUMERIC_CONTINUATION_RE = re.compile(
    r"^[\[(]?\d{1,4}(?:(?:er|e|ème)\b|[_.,₀-₉-]|\s)",
    re.IGNORECASE,
)
_RIGHT_REFERENCE_LIST_RE = re.compile(r"^[\[(]?\d{1,4}(?:[_₀-₉]|[-–]\s*\d)")
_SCANNER_BOILERPLATE_RE = re.compile(
    r"(?:Digitized\s+by\s+Google|Original\s+from|Generated\s+at)\s*$",
    re.IGNORECASE,
)


def _clean_lines(text: str) -> list[str]:
    return [
        _SPACE_RE.sub(" ", line).strip()
        for line in (text or "").splitlines()
        if _SPACE_RE.sub(" ", line).strip()
    ]


def _page_body_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for line in _clean_lines(read_ocr_page(path).body_text):
        line = _SCANNER_BOILERPLATE_RE.sub("", line).strip()
        if line:
            lines.append(line)
    return lines


def _catchword(page_footer: str, right_first_line: str) -> str | None:
    footer_lines = _clean_lines(page_footer)
    for line in reversed(footer_lines):
        line = _SCANNER_BOILERPLATE_RE.sub("", line).strip()
        if not line or line.isdigit() or len(line) > 40:
            continue
        normalized = unicodedata.normalize("NFKC", line).casefold().strip(" .,:;[]()")
        right = unicodedata.normalize("NFKC", right_first_line).casefold()
        if normalized and any(char.isalpha() for char in normalized) and right.startswith(normalized):
            return line
    return None


def estimate_page_entries(path: Path) -> dict[str, Any]:
    try:
        page = read_ocr_page(path)
        lines = _clean_lines(page.body_text)
        error = None
    except OSError as exc:
        lines = []
        error = str(exc)

    strong = 0
    probable = 0
    possible = 0
    continuation_like = 0
    structural = 0
    for line in lines:
        if _STRUCTURAL_LINE_RE.fullmatch(line):
            structural += 1
            continue
        has_reference = bool(_EDITORIAL_REF_AT_END_RE.search(line))
        has_leader = bool(_LEADER_RE.search(line))
        has_entry_prefix = bool(_ENTRY_PREFIX_RE.search(line))
        has_subentry_prefix = bool(_SUBENTRY_PREFIX_RE.search(line))
        if has_reference and (has_entry_prefix or has_leader):
            strong += 1
        elif has_reference or (has_entry_prefix and len(line) >= 8) or has_subentry_prefix:
            probable += 1
        elif len(line) >= 8:
            possible += 1
        else:
            continuation_like += 1

    lower = strong
    likely = strong + probable
    upper = strong + probable + possible
    return {
        "physical_file": str(path),
        "line_count": len(lines),
        "strong_entry_line_count": strong,
        "probable_entry_line_count": probable,
        "possible_entry_line_count": possible,
        "continuation_or_short_line_count": continuation_like,
        "structural_line_count": structural,
        "estimated_entry_count": {
            "lower_bound": lower,
            "likely": likely,
            "upper_bound": upper,
        },
        "method": (
            "Line-shape estimate from terminal editorial-reference patterns, leaders, and "
            "topic/subtopic prefixes. It measures likely boundaries; it does not segment entries."
        ),
        "read_error": error,
    }


def analyze_page_boundary(left_path: Path, right_path: Path) -> dict[str, Any]:
    """Describe whether one logical index entry may cross a physical scan boundary."""
    try:
        left_page = read_ocr_page(left_path)
        right_page = read_ocr_page(right_path)
        left_lines = _clean_lines(left_page.body_text)
        right_lines = _clean_lines(right_page.body_text)
        left_lines = [
            cleaned
            for line in left_lines
            if (cleaned := _SCANNER_BOILERPLATE_RE.sub("", line).strip())
        ]
        right_lines = [
            cleaned
            for line in right_lines
            if (cleaned := _SCANNER_BOILERPLATE_RE.sub("", line).strip())
        ]
        error = None
    except OSError as exc:
        left_page = None
        left_lines = []
        right_lines = []
        error = str(exc)

    left_line = left_lines[-1] if left_lines else ""
    right_line = right_lines[0] if right_lines else ""
    left_signal_text = unicodedata.normalize("NFKC", left_line)
    right_signal_text = unicodedata.normalize("NFKC", right_line)
    trailing_hyphen = bool(_TRAILING_HYPHEN_RE.search(left_signal_text))
    right_starts_as_continuation = bool(_RIGHT_CONTINUATION_RE.search(right_signal_text))
    right_starts_numeric = bool(_RIGHT_NUMERIC_CONTINUATION_RE.search(right_signal_text))
    right_starts_reference_list = bool(_RIGHT_REFERENCE_LIST_RE.search(right_line))
    left_has_terminal_reference = bool(_EDITORIAL_REF_AT_END_RE.search(left_signal_text))
    left_has_terminal_punctuation = left_line.endswith((".", "!", "?", ";", ":"))
    right_starts_as_entry = bool(
        _ENTRY_PREFIX_RE.search(right_line) or _SUBENTRY_PREFIX_RE.search(right_line)
    )
    catchword = _catchword(left_page.footer_text, right_line) if left_page is not None else None

    signals: list[str] = []
    if trailing_hyphen:
        signals.append("left_trailing_linebreak_hyphen")
    if right_starts_as_continuation:
        signals.append("right_starts_lowercase_or_punctuation")
    if right_starts_numeric:
        signals.append("right_starts_with_numeric_reference")
    if left_line and not left_has_terminal_reference and not left_has_terminal_punctuation:
        signals.append("left_has_no_terminal_reference_or_punctuation")
    if right_starts_as_entry:
        signals.append("right_looks_like_new_entry")
    if catchword:
        signals.append("left_footer_catchword_repeats_right_start")

    if (
        trailing_hyphen
        or right_starts_reference_list
        or (
            right_starts_numeric
            and left_line
            and not left_has_terminal_reference
            and not left_has_terminal_punctuation
        )
        or (
        right_starts_as_continuation
        and left_line
        and not left_has_terminal_reference
        and not left_has_terminal_punctuation
        )
        or (
            catchword
            and left_line
            and not left_has_terminal_reference
            and not left_has_terminal_punctuation
        )
    ):
        likelihood = "high"
    elif (
        left_line
        and right_line
        and not left_has_terminal_reference
        and not left_has_terminal_punctuation
        and not right_starts_as_entry
    ):
        likelihood = "medium"
    else:
        likelihood = "low"

    return {
        "physical_left_file": str(left_path),
        "physical_right_file": str(right_path),
        "likelihood": likelihood,
        "signals": signals,
        "left_last_body_line": left_line[-240:] or None,
        "right_first_body_line": right_line[:240] or None,
        "left_footer_catchword": catchword,
        "ownership_rule": (
            "If this is one logical entry, the chunk owning physical_left_file emits the joined "
            "entry and records both source files; the right-file chunk must not emit it again."
        ),
        "read_error": error,
    }


def combine_entry_estimates(page_estimates: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("lower_bound", "likely", "upper_bound")
    combined = {
        key: sum(
            int((item.get("estimated_entry_count") or {}).get(key) or 0)
            for item in page_estimates
        )
        for key in keys
    }
    return {
        "physical_file_count": len(page_estimates),
        "estimated_entry_count": combined,
        "strong_entry_line_count": sum(
            int(item.get("strong_entry_line_count") or 0) for item in page_estimates
        ),
        "probable_entry_line_count": sum(
            int(item.get("probable_entry_line_count") or 0) for item in page_estimates
        ),
        "method": (
            "Sum of per-file line-shape estimates. Overlapping chunks must not be summed "
            "without deduplicating their physical files."
        ),
        "interpretation": (
            "Coverage hint only. Codex must use semantic reading to join wrapped lines and split "
            "topics/subtopics; regex output is never the final entry list."
        ),
    }
