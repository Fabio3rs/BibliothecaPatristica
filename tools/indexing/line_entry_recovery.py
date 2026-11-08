"""Recover line-oriented index owners without replacing the source extraction.

Absolute offsets always address the supplied, hash-checked OCR. Line starts alone
are insufficient evidence for a new entry: numeric wraps and subclauses survive.
This grammar produces proposals, not certified transcription or destinations.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter

from tools.indexing.editorial_ocr_blocks import blocks


VERSION = "line-entry-recovery-v1"
NUMBER = re.compile(r"(?<!\w)\d+(?:\s*(?:[,;]|[-–—])\s*\d+)*")
END_REFERENCE = re.compile(r"\d\s*[.]\s*$")
HEADING = re.compile(r"^\s*([^\W\d_])[.]\s*$", re.UNICODE)
GREEK = "αβγδεζηθικλμνξοπρστυφχψω"


def _initial(text):
    text = "".join(c for c in unicodedata.normalize("NFD", text.casefold())
                   if not unicodedata.combining(c))
    return next((c for c in text if c.isalpha()), "")


def _trim(raw, start, end):
    while start < end and raw[start].isspace():
        start += 1
    while end > start and raw[end - 1].isspace():
        end -= 1
    return start, end


def _segments(raw, start, end):
    """Split complete numeric rows with compatible alphabetic progression.

    The alphabetic check is evidence, not a universal guarantee. It deliberately
    leaves unrelated initials together, retaining subordinate descriptions.
    """
    boundaries = [start]
    leftovers = []
    initial = _initial(raw[start:end])
    # A generic owner may start in the final subclause of a previous headword.
    # Repeated initials among complete following rows can reset that context.
    lines = raw[start:end].splitlines(keepends=True)
    row_initials = Counter(_initial(line) for previous, line in zip(lines, lines[1:])
                           if END_REFERENCE.search(previous) and line.strip()
                           and line.strip()[0].isalpha())
    repeated_initials = {letter for letter, count in row_initials.items() if count >= 2}
    previous = ""
    pos = start
    for line in lines:
        stripped = line.strip()
        heading = HEADING.fullmatch(stripped)
        if heading:
            if pos > boundaries[-1]:
                boundaries.append(pos)
            boundaries.append(pos + len(line))
            leftovers.append({"span": list(_trim(raw, pos, pos + len(line))),
                              "kind": "alphabetic_heading"})
            initial = _initial(stripped)
        elif stripped:
            new_initial = _initial(stripped)
            starts_word = stripped[0].isalpha()
            adjacent = (initial in GREEK and new_initial in GREEK and
                        GREEK.index(new_initial) == GREEK.index(initial) + 1)
            if (pos > boundaries[-1] and starts_word and
                    END_REFERENCE.search(previous) and
                    (new_initial == initial or adjacent or new_initial in repeated_initials)):
                boundaries.append(pos)
                initial = new_initial
            previous = stripped
        pos += len(line)
    boundaries.append(end)
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if a < b], leftovers


def _coverage(raw, start, end, entries, leftovers):
    intervals = [(entry[f"{kind}_span"], kind)
                 for entry in entries for kind in ("owner", "reference")]
    intervals.extend((row["span"], "alphabetic_heading") for row in leftovers
                     if row["kind"] == "alphabetic_heading")
    boundaries = sorted({start, end, *(point for span, _ in intervals for point in span)})
    result = []
    for left, right in zip(boundaries, boundaries[1:]):
        kinds = [kind for (a, b), kind in intervals if a <= left and right <= b]
        kind = kinds[0] if len(kinds) == 1 else "overlap" if kinds else (
            "residual_text" if any(c.isalnum() for c in raw[left:right]) else "separator")
        result.append({"span": [left, right], "kind": kind})
    return result


def repair_line_owner(raw: str, group: dict) -> dict:
    """Return lossless owner/reference proposals for a concatenated owner.

    Required group fields: ``char_start``, ``char_end``, ``raw``, ``owner_raw``.
    Optional ``source_sha256`` is compared; unchanged local spans permit a
    proposal even when other parts of the source changed. ``id`` and ``source``
    are propagated.
    ``existing_reference_entry`` indexes the entry whose complete numeric list
    contains the original reference. ``parent_owner_span`` retains inherited
    context; ambiguous clauses are never silently promoted to standalone names.
    """
    result = {"version": VERSION, "group_id": group.get("id"),
              "source": group.get("source"),
              "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
              "status": "abstained", "entries": [], "ambiguities": [],
              "leftovers": [], "existing_reference_entry": None}
    a, b = group.get("char_start"), group.get("char_end")
    if (not isinstance(a, int) or not isinstance(b, int) or
            not 0 <= a < b <= len(raw) or raw[a:b] != group.get("raw")):
        result["ambiguities"].append("original_reference_span_mismatch")
        return result
    if group.get("source_sha256") not in (None, result["source_sha256"]):
        result["ambiguities"].append("source_changed_local_spans_match")
    owner = group.get("owner_raw", "")
    start = raw.rfind(owner, 0, a) if owner else -1
    if start < 0 or not re.fullmatch(r"[\s,;:.–—-]*", raw[start + len(owner):a]):
        result["ambiguities"].append("owner_not_contiguous_before_reference")
        return result
    # The original generic parser can have stopped halfway through a list.
    extension = re.match(r"(?:\s*[,;–—-]\s*\d+)*", raw[b:])
    end = b + extension.end()
    source_blocks = blocks(raw)
    if source_blocks and not any(block["body_start"] <= start and end <= block["body_end"]
                                 for block in source_blocks):
        result["ambiguities"].append("owner_reference_crosses_ocr_blocks")
        return result
    if re.search(r"</?[A-Za-z][^>]*>", raw[start:end]):
        result["ambiguities"].append("markup_inside_owner_reference_scope")
        return result
    result["source_span"] = [start, end]
    result["original_reference_span"] = [a, b]
    if re.match(r"\s*[.]\s*\d", raw[end:]):
        result["ambiguities"].append("numeric_continuation_after_period_requires_ocr_review")
    segments, result["leftovers"] = _segments(raw, start, end)
    for segment_start, segment_end in segments:
        segment = raw[segment_start:segment_end]
        if HEADING.fullmatch(segment.strip()):
            continue
        references = list(NUMBER.finditer(segment))
        if not references:
            result["leftovers"].append({"span": [segment_start, segment_end],
                                         "kind": "unparsed_text"})
            continue
        parent_span = None
        cursor = segment_start
        for reference in references:
            rstart = segment_start + reference.start()
            rend = segment_start + reference.end()
            ostart, oend = _trim(raw, cursor, rstart)
            while ostart < oend and raw[ostart] in ".,;":
                ostart, oend = _trim(raw, ostart + 1, oend)
            local_owner = raw[ostart:oend]
            flags = ["candidate_segmentation_not_certified"]
            if segment_start == start:
                flags.append("scope_begins_at_original_owner_context_not_extended")
            if not any(c.isalpha() for c in local_owner):
                result["ambiguities"].append("reference_without_explicit_clause_owner")
                result["leftovers"].append({"span": [cursor, rend],
                                             "kind": "ownerless_reference"})
                cursor = rend
                continue
            if parent_span is None:
                parent_span = [ostart, oend]
            else:
                flags.append("inherits_parent_context")
                result["ambiguities"].append("subclause_context_requires_preservation")
            # A hard-wrapped line with a different initial may also be a new
            # entry; retain it but never claim that the hierarchy is settled.
            if "\n" in local_owner and re.search(r"\?\s*\n", local_owner):
                flags.append("possible_unrecognized_entry_boundary")
                result["ambiguities"].append("possible_unrecognized_entry_boundary")
            entry = {"owner_raw": local_owner, "owner_span": [ostart, oend],
                     "reference_raw": raw[rstart:rend], "reference_span": [rstart, rend],
                     "entry_span": [ostart, rend], "parent_owner_span": parent_span,
                     "parent_owner_raw": raw[parent_span[0]:parent_span[1]],
                     "logical_entry_span": [segment_start, segment_end],
                     "contains_existing_reference": rstart <= a and rend >= b,
                     "flags": flags}
            if entry["contains_existing_reference"]:
                result["existing_reference_entry"] = len(result["entries"])
            result["entries"].append(entry)
            cursor = rend
        tail_start, tail_end = _trim(raw, cursor, segment_end)
        if tail_start < tail_end:
            result["leftovers"].append({"span": [tail_start, tail_end],
                                         "kind": "punctuation" if not any(
                                             c.isalnum() for c in raw[tail_start:tail_end])
                                         else "unparsed_text"})
    result["ambiguities"] = sorted(set(result["ambiguities"]))
    result["coverage"] = _coverage(raw, start, end, result["entries"], result["leftovers"])
    result["accounted_characters"] = sum(row["span"][1] - row["span"][0]
                                         for row in result["coverage"])
    if result["existing_reference_entry"] is not None:
        result["status"] = "candidate_repair"
    else:
        result["ambiguities"].append("original_reference_not_contained_in_one_list")
    return result
