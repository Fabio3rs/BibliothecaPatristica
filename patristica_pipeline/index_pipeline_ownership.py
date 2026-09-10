"""Shared ownership rules for the general and alphabetical index pipelines."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


GENERAL_NON_OWNED_SCOPE_KINDS = {
    "alphabetical_table",
    "post_volume_editorial_matter",
    "publisher_advertisement",
    "publisher_catalogue",
    "retrospective_alphabetical_table",
    "volume_index_alphabetical",
    "volume_index_analytical",
    "work_index_alphabetical",
    "work_index_analytic",
    "work_index_analytical",
    "work_index_nominal",
    "work_index_scripture",
}

_CLOSING_INDEX_TOKEN_RE = re.compile(
    r"(?:alphabet|onomast|index.?nomin|nominum.?propriorum|proper.?names|"
    r"patristic|concordan|index.?generalis|index.?locorum|index.?verborum|"
    r"index.?scriptorum|greek.?terms|mots.?grecs|noms.?propres|"
    r"index.?locupletissimus|index.?titulorum|nomenclator.?vocum|"
    r"rerum.?et.?verborum|verborum.{0,40}rerum)",
    re.IGNORECASE,
)
_AUTHOR_SYLLABUS_RE = re.compile(r"\bsyllabus\s+auctorum\b", re.IGNORECASE)
_ORDER_CROSSWALK_RE = re.compile(
    r"\bordo\b.{0,60}\b(?:novus|vetus)\b.{0,60}\b(?:collat\w*|comparat\w*)\b|"
    r"\bordo\b.{0,60}\b(?:collat\w*|comparat\w*)\b.{0,60}\b(?:novus|vetus)\b",
    re.IGNORECASE,
)
_ANALYTICAL_TOKEN_RE = re.compile(r"analyt", re.IGNORECASE)
_CLOSING_ANALYTICAL_SCOPES = {
    "volume_end",
    "volume_index",
    "volume_index_analytical",
    "work_index",
    "work_index_analytic",
    "work_index_analytical",
}
_ALPHABETICAL_FORBIDDEN_SECTION_KINDS = {
    "ordo_rerum",
    "editorial_closure",
}
_GENERAL_WORKS_HEADING_RE = re.compile(
    r"\b(?:ordo\s+rerum|index\s+capitum)\b|"
    r"\b(?:elenchus|syllabus|index)[^\w]+auctorum\s+et\s+operum\b"
    r".*\bcontinentur\b|"
    r"\b(?:elenchus|syllabus|index)[^\w]+operum\b"
    r".*\b(?:tom[oi]|volumin[ei])\b.*\bcontinentur\b|"
    r"\b(?:table\s+du\s+tome|table\s+of\s+contents)\b",
    re.IGNORECASE,
)

_GENERAL_STRUCTURAL_INDEX_KINDS = {
    "capitula",
    "contents",
    "index_capitum",
    "ordo_rerum",
    "ordo_rerum_volume_contents",
    "work_internal_chapter_index",
}
_EXPLICIT_INDEX_HEADING_RE = re.compile(r"\b(?:index|concordan\w*)\b", re.IGNORECASE)
_EXPLICIT_SCRIPTURE_REFERENCE_INDEX_RE = re.compile(
    r"\b(?:index|indices)\s+(?!capit\w*\b|commentar\w*\b)[^\n]{0,80}\b"
    r"(?:scriptur\w*|bibl(?:e?s?|ia\w*|ique?s?|ic(?:a|ae|am|arum|i|is|o|orum|um|us))|"
    r"citation\w*)\b|"
    r"\b(?:table|tabula)\b[^\n]{0,80}\b(?:citation\w*|scriptur\w*|"
    r"bibl(?:e?s?|ia\w*|ique?s?|ic(?:a|ae|am|arum|i|is|o|orum|um|us)))\b|"
    r"\bconcordan\w*\b[^\n]{0,80}\b(?:scriptur\w*|bibl(?:e?s?|ia\w*|ique?s?))\b|"
    r"^loca\s+ex\s+(?:psalm\w*|scriptur\w*)\b",
    re.IGNORECASE,
)
_GENERAL_WORK_TITLE_RE = re.compile(
    r"^(?:(?:liber|opus|tractatus|sermo|homilia|homilie|expositio|enarratio|"
    r"commentarius|commentaria|explicatio|interpretatio)\b|"
    r"(?:caput|capitulum|cap\.)\s+(?:[ivxlcdm]+|\d+)\b)",
    re.IGNORECASE,
)


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def general_section_ownership(section: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether a section belongs in the opening/general payload.

    An explicit non-general ``raw_json.pipeline_owner`` is honored. A claimed general owner does
    not override unambiguous closing-index signals. The fallback rules intentionally allow a
    work-internal analytical table to remain a table of contents.
    """

    raw_json = section.get("raw_json")
    explicit_owner = ""
    if isinstance(raw_json, dict):
        explicit_owner = _normalized_text(raw_json.get("pipeline_owner"))
    if explicit_owner in {"alphabetical", "alphabetical_index", "closing_index"}:
        return False, f"explicit pipeline_owner={explicit_owner}"

    scope_kind = _normalized_text(section.get("scope_kind")).replace(" ", "_")
    if scope_kind in GENERAL_NON_OWNED_SCOPE_KINDS:
        if scope_kind in {
            "post_volume_editorial_matter",
            "publisher_advertisement",
            "publisher_catalogue",
        }:
            return False, f"scope_kind={scope_kind} is external post-volume publisher matter"
        return False, f"scope_kind={scope_kind} belongs to the closing-index pipeline"

    index_kind = _normalized_text(section.get("index_kind")).replace(" ", "_")
    heading = _normalized_text(section.get("heading_raw"))
    combined = f"{index_kind} {heading}"
    if _EXPLICIT_SCRIPTURE_REFERENCE_INDEX_RE.search(heading):
        return False, "heading identifies an explicit scripture-reference index"
    if (
        index_kind in _GENERAL_STRUCTURAL_INDEX_KINDS
        and not _EXPLICIT_INDEX_HEADING_RE.search(heading)
    ):
        return True, None
    if _CLOSING_INDEX_TOKEN_RE.search(combined):
        return False, "heading/index_kind identifies a closing alphabetical or citation index"
    if _AUTHOR_SYLLABUS_RE.search(combined) and not _GENERAL_WORKS_HEADING_RE.search(heading):
        return False, "author syllabus without works/contents context belongs to the closing-index pipeline"
    if _ORDER_CROSSWALK_RE.search(combined):
        return False, "heading identifies a concordance between old and new editorial orders"
    if (
        _ANALYTICAL_TOKEN_RE.search(combined)
        and scope_kind in _CLOSING_ANALYTICAL_SCOPES
    ):
        return False, "analytical index is classified as closing/index scope"
    return True, None


def alphabetical_section_ownership(
    section: dict[str, Any],
) -> tuple[bool, str | None]:
    """Return whether a section may be emitted by the alphabetical pipeline."""

    raw_json = section.get("raw_json")
    if not isinstance(raw_json, dict):
        raw_json = {}
    explicit_owner = _normalized_text(raw_json.get("pipeline_owner"))
    alphabetical_role = _normalized_text(raw_json.get("alphabetical_role"))
    if explicit_owner and explicit_owner not in {
        "alphabetical",
        "alphabetical_index",
        "closing_index",
    }:
        return False, f"explicit pipeline_owner={explicit_owner}"
    if alphabetical_role in {"stop_boundary", "boundary"}:
        return False, f"alphabetical_role={alphabetical_role}"

    section_kind = _normalized_text(section.get("section_kind")).replace(" ", "_")
    if section_kind in _ALPHABETICAL_FORBIDDEN_SECTION_KINDS:
        return False, f"section_kind={section_kind} belongs to the general pipeline"

    heading = _normalized_text(section.get("heading_raw"))
    if _GENERAL_WORKS_HEADING_RE.search(heading):
        return False, "heading identifies a structural works/contents inventory"
    if _GENERAL_WORK_TITLE_RE.search(heading):
        return False, "heading identifies a work or structural unit"
    return True, None


__all__ = [
    "alphabetical_section_ownership",
    "general_section_ownership",
]
