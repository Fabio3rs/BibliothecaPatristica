"""Recover literal tome locators; editorial profiles supply their meaning.

Offsets are Unicode source offsets. No tome is converted to a corpus volume.
Only adjacent numeric list members inherit the explicitly printed tome.
"""
import re

from tools.indexing.entry_parser_experiment import display


ROMAN = r"(?=[IVXLCDM]+\b)M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
EXPLICIT = re.compile(rf"(?<!\w)(?P<tome>{ROMAN})\s*,\s*(?=\d)")
NUMBER = re.compile(r"(?P<number>[0-9]{1,4})(?:\s*[-–]\s*(?P<end>[0-9]{1,4}))?(?![0-9])")
QUALIFIER = re.compile(r"\s*,?\s*(?P<qualifier>(?:et\s+)?(?:seq\.|seqq\.|sqq\.|sq\.)|not\.(?:\s*[0-9]+)?)(?!\w)")
LETTERS = re.compile(r"\s+(?P<letters>[A-Da-d](?:\s*[,–-]\s*[A-Da-d])*)(?=\s*(?:[.,;:]|$))")
SEPARATOR = re.compile(r"\s*[,;]\s*")
IBID = re.compile(r"ibid\.(?!\w)", re.I)


def parse_tome_candidates(entry, *, unit="page", namespace=None):
    """Find explicit tome groups even inside a concatenated analytical entry.

    ``entry`` supplies ``entry_raw`` and optional ``char_start``. The caller must
    select a section whose editorial convention actually defines tome locators.
    Ownership is the preceding literal clause, not a certified principal lemma.
    Prose between groups is never used to inherit a tome or page.
    """
    if unit not in {"page", "column"}:
        raise ValueError("unit must be page or column")
    raw = entry["entry_raw"]
    base = entry.get("char_start", 0)
    references, clauses, groups = [], [], []
    cursor = 0
    while match := EXPLICIT.search(raw, cursor):
        group_start = match.start()
        owner_start, owner_end = cursor, group_start
        while owner_start < owner_end and raw[owner_start] in " \n\t.,;:":
            owner_start += 1
        while owner_end > owner_start and raw[owner_end - 1] in " \n\t.,;:":
            owner_end -= 1
        # Only a clause candidate: punctuation can also abbreviate a personal
        # name. Keep the full preceding text below so this decision is reversible.
        context_start = owner_start
        boundaries = list(re.finditer(r"[.;:]\s+(?=[A-ZÀ-Þ])", raw[owner_start:owner_end]))
        if boundaries:
            owner_start += boundaries[-1].end()
        owner = raw[owner_start:owner_end]
        clause = {"owner_raw": owner, "owner_span": [base + owner_start, base + owner_end],
                  "preceding_context_raw": raw[context_start:owner_end],
                  "preceding_context_span": [base + context_start, base + owner_end],
                  "ownership_status": "preceding_text_candidate", "reference_indexes": []}
        tome, tome_span = match["tome"], [base + match.start("tome"), base + match.end("tome")]
        pos, explicit, last_number = match.end(), True, None
        start = match.start()
        while True:
            number = NUMBER.match(raw, pos)
            ibid = IBID.match(raw, pos) if number is None else None
            if number is None and ibid is None:
                break
            flags = ["candidate_not_certified", "owner_clause_not_principal_lemma"]
            if number:
                value = int(number["number"])
                end = int(number["end"]) if number["end"] else None
                pos = number.end()
                if value == 0 or (end is not None and end < value):
                    flags.append("invalid_numeric_range")
                last_number = value
            else:
                value, end, pos = last_number, None, ibid.end()
                flags.append("ibid_same_numeric_group_candidate")
            letters_raw, qualifiers = None, []
            letters = LETTERS.match(raw, pos) if unit == "column" else None
            if letters:
                letters_raw, pos = letters["letters"], letters.end()
            while qualifier := QUALIFIER.match(raw, pos):
                qualifiers.append({"raw": qualifier["qualifier"],
                                   "source_span": [base + qualifier.start("qualifier"), base + qualifier.end("qualifier")]})
                pos = qualifier.end()
                flags.append("qualified_locator_preserved")
            reference = {
                "number": value, "range_end": end, "series_key": tome, "tome": tome,
                "namespace": namespace, "reference_kind": f"editorial_{unit}",
                "ref_raw": raw[start:pos], "source_span": [base + start, base + pos],
                "tome_basis": "explicit" if explicit else "same_numeric_group",
                "tome_source_span": tome_span.copy(), "subdivision_raw": letters_raw,
                "qualifiers": qualifiers, "owner_raw": owner,
                "owner_span": clause["owner_span"].copy(), "flags": flags,
                "owner_context_raw": clause["preceding_context_raw"],
                "owner_context_span": clause["preceding_context_span"].copy(),
            }
            clause["reference_indexes"].append(len(references))
            references.append(reference)
            separator = SEPARATOR.match(raw, pos)
            if not separator:
                break
            next_pos = separator.end()
            next_tome = EXPLICIT.match(raw, next_pos)
            if next_tome:
                tome = next_tome["tome"]
                tome_span = [base + next_tome.start("tome"), base + next_tome.end("tome")]
                start, pos, explicit = next_pos, next_tome.end(), True
            elif NUMBER.match(raw, next_pos) or IBID.match(raw, next_pos):
                start, pos, explicit = next_pos, next_pos, False
            else:
                break
        if not clause["reference_indexes"]:
            cursor = match.end()
            continue
        clauses.append(clause)
        groups.append([base + group_start, references[-1]["source_span"][1]])
        cursor = references[-1]["source_span"][1] - base
    if not references:
        return None
    # Material between groups may contain unresolved inheritance or prose numbers.
    # Preserve it explicitly; do not silently assign it to the previous locator.
    residuals, consumed = [], 0
    for start, end in groups:
        start, end = start - base, end - base
        if consumed < start:
            residuals.append({"raw": raw[consumed:start], "source_span": [base + consumed, base + start]})
        consumed = end
    if consumed < len(raw):
        residuals.append({"raw": raw[consumed:], "source_span": [base + consumed, base + len(raw)]})
    return {"query_text": display(clauses[0]["owner_raw"]), "interpreted_references": references,
            "clauses": clauses, "locator_groups": groups, "non_locator_spans": residuals,
            "segmentation_status": "clause_candidates_not_certified", "parser": "tome-candidates-v1"}
