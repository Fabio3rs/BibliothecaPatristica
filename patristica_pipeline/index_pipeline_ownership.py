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
    r"scriptur|bible|citation|patristic|concordan|index.?generalis|"
    r"greek.?terms|mots.?grecs|noms.?propres|rerum.?et.?verborum)",
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
    if _CLOSING_INDEX_TOKEN_RE.search(combined):
        return False, "heading/index_kind identifies a closing alphabetical or citation index"
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
    return True, None


__all__ = [
    "alphabetical_section_ownership",
    "general_section_ownership",
]
