"""Conservative syntax gate for line recovery; never infer locator namespaces.

Eligibility permits exposing separate literal owner/reference proposals only.
Even a plain comma list may encode page/line pairs, so this module never grants
permission to turn its numbers into pages or destinations. Rejected proposals
can remain searchable audit metadata with their original source spans.
"""

import re


VERSION = "line-recovery-eligibility-v1"
PLAIN_REFERENCE = re.compile(r"[0-9]+(?:\s*[,;–—-]\s*[0-9]+)*")
TYPED_LOCATOR = re.compile(
    r"\b(?:cap(?:ut)?|chap|c|vers(?:us)?|v|psal|epist|not(?:a)?|adnot|"
    r"pag(?:ina)?|p|lin(?:ea)?|l|tit|app|Socr|Soz|Leo|Cedren(?:o|us)?)\."
    r"|\b(?:caput|versus|psalmus|epistola|nota|pagina|linea|Leo)\b|§", re.I)
DATE_CONTEXT = re.compile(r"\b(?:anno|annum|annis|annos|ann|an|aetatis|aetate)\b", re.I)
NAMESPACE_SUFFIX = re.compile(r"(?:\b[IVXLCDM]+|\b[A-Za-z])\s*[,;:.]?\s*$")
NUMERIC_TYPOGRAPHY = re.compile(r"[₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹]")
PRESERVED_AMBIGUITIES = {
    "subclause_context_requires_preservation",
    "numeric_continuation_after_period_requires_ocr_review",
}


def assess_line_recovery(group, proposal):
    """Return whether a proposal is safe for *literal* line segmentation.

No corpus files are read. This consumes the parser's hash/span-checked proposal,
preserving all original flags outside this module. Two independent logical
rows provide minimum repeated-layout evidence. Inherited clauses remain usable
only with their explicit parent spans; incomplete tails remain warned literals.
Uncertain boundaries exclude their affected entries, not independent siblings.
Returned exclusion indices address the unchanged proposal entry list.
    """
    reasons = set()
    entries = proposal.get("entries", [])
    if proposal.get("status") != "candidate_repair":
        reasons.add("parser_abstained")
    ambiguities = set(proposal.get("ambiguities", []))
    warnings = ambiguities & PRESERVED_AMBIGUITIES
    boundary_warning = "possible_unrecognized_entry_boundary"
    excluded = {i for i, entry in enumerate(entries)
                if boundary_warning in entry.get("flags", [])}
    # A clause inheriting an uncertain owner cannot become safe just because
    # its own local text contains no boundary marker.
    uncertain_owners = {tuple(entries[i].get("owner_span", [])) for i in excluded}
    excluded.update(i for i, entry in enumerate(entries)
                    if tuple(entry.get("parent_owner_span", [])) in uncertain_owners)
    localized_ambiguities = {boundary_warning} if excluded else set()
    warnings.update(localized_ambiguities)
    if ambiguities - PRESERVED_AMBIGUITIES - localized_ambiguities:
        reasons.add("parser_ambiguities_require_review")
    if proposal.get("existing_reference_entry") is None:
        reasons.add("original_reference_not_preserved")
    if proposal.get("existing_reference_entry") in excluded:
        reasons.add("original_reference_entry_excluded")
    logical_rows = {tuple(e.get("logical_entry_span", [])) for i, e in enumerate(entries)
                    if i not in excluded}
    if len(logical_rows) < 2 or () in logical_rows:
        reasons.add("insufficient_independent_numeric_rows")
    source_scope = group.get("owner_raw", "") + " " + group.get("raw", "")
    if DATE_CONTEXT.search(source_scope):
        reasons.add("date_or_age_numbers_in_text")
    if TYPED_LOCATOR.search(source_scope):
        reasons.add("typed_locator_requires_scoped_parser")
    if NUMERIC_TYPOGRAPHY.search(source_scope):
        reasons.add("numeric_typography_requires_scoped_parser")
    for i, entry in enumerate(entries):
        if i in excluded:
            continue
        owner = entry.get("owner_raw", "")
        if any(c.isnumeric() for c in owner):
            reasons.add("numeric_owner_requires_review")
        if NAMESPACE_SUFFIX.search(owner):
            reasons.add("namespace_marker_requires_scoped_parser")
        if not PLAIN_REFERENCE.fullmatch(entry.get("reference_raw", "")):
            reasons.add("non_plain_numeric_reference")
        if ("inherits_parent_context" in entry.get("flags", []) or
                entry.get("parent_owner_span") != entry.get("owner_span")):
            if not entry.get("parent_owner_span") or not entry.get("parent_owner_raw"):
                reasons.add("missing_parent_context")
            else:
                warnings.add("subclause_context_requires_preservation")
    if not proposal.get("coverage") or any(
            row["kind"] in {"overlap", "residual_text"}
            for row in proposal.get("coverage", [])):
        reasons.add("ambiguous_or_unaccounted_source_spans")
    notation = group.get("notation")
    if notation and notation != "plain_page_list":
        reasons.add("declared_notation_requires_scoped_parser")
    return {"eligible": not reasons, "reasons": sorted(reasons),
            "warnings": sorted(warnings),
            "excluded_entry_indices": sorted(excluded),
            "version": VERSION, "scope": "literal_segmentation_only",
            "reference_interpretation": "unchanged",
            "destination_inference_allowed": False}
