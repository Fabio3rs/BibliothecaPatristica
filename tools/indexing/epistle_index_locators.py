"""Literal book/epistle locators documented by PL217's printed index notice.

Books are editorial units, never physical volumes. Parsing is local to one
reference group; no context survives between calls. Unknown text remains a
residual and interrupts inherited context.
"""

from __future__ import annotations

import re


VERSION = "epistle-index-locators-v1"
_ROMAN = r"[IVXLCDM]+"
_APPENDIX = r"Appen(?:d(?:ix)?\.?|-\s*dix\.?)"
_SUPPLEMENT = r"Suppl(?:em(?:ent(?:um)?)?)?\.?"
_IMPERIAL = r"Registr(?:um)?[.,]?\s+(?:(?:de|ad)\s+)?(?:neg(?:otiis)?\s*\.?\s*)?imp(?:erii)?\.?"
_NUMBER = r"(?P<epistle>[1-9]\d*)(?:\s+(?P<suffix>bis|ter|quater))?"
_EXPLICIT = re.compile(
    rf"(?:(?P<book>{_ROMAN})(?:\s*[,.]\s*|\s+)"
    rf"(?:(?P<appendix>{_APPENDIX})\s*)?|"
    rf"(?P<supplement>{_SUPPLEMENT})\s*,?\s*|"
    rf"(?P<imperial>{_IMPERIAL})\s*,?\s*){_NUMBER}(?!\w)",
    re.IGNORECASE,
)
_SHORT = re.compile(rf"(?:(?P<ibid>ibid(?:em)?\.?)\s*,?\s*)?{_NUMBER}(?!\w)", re.I)
_IBID = re.compile(r"ibid(?:em)?\.?(?!\w)", re.I)
_SEPARATOR = re.compile(r"(?:\s*[,.;]\s*|\s+et\s+|\s+)", re.I)
_VALID_ROMAN = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def roman_to_int(raw: str) -> int | None:
    """Reject malformed OCR numerals rather than silently normalizing them."""
    raw = raw.upper()
    if not raw or not _VALID_ROMAN.fullmatch(raw):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    return sum(-values[c] if i + 1 < len(raw) and values[c] < values[raw[i + 1]] else values[c]
               for i, c in enumerate(raw))


def locator_key(locator: dict) -> tuple:
    return (locator["series"], locator.get("book"), locator["epistle"], locator.get("suffix"))


def parse_epistle_locators(raw: str, *, offset: int = 0) -> dict:
    """Return locators and unparsed residuals with half-open source spans.

    Bare numbers inherit only after punctuation in the same supplied reference
    group. The caller must supply actual locator text, not an entire paragraph.
    ``ibid.`` repeats an antecedent; ``ibid., 5`` inherits its series/book.
    Suffixes such as *bis* and *ter* are part of identity. An interval preserves
    its first anchor and warns about the unparsed endpoint; it is not expanded.
    """
    locators, residuals = [], []
    pos = 0
    antecedent = None
    separated = False
    while pos < len(raw):
        sep = _SEPARATOR.match(raw, pos)
        if sep:
            separated = separated or bool(re.search(r"[,;]|\bet\b", sep.group(), re.I))
            pos = sep.end()
            if pos == len(raw):
                break
        start = pos
        match = _EXPLICIT.match(raw, pos)
        warnings = []
        inherited_from = None
        if match and match.group("book") and roman_to_int(match.group("book")) is None:
            match = None
        if match:
            series = "register"
            if match.group("appendix"):
                series = "appendix"
            elif match.group("supplement"):
                series = "supplement"
            elif match.group("imperial"):
                series = "imperial_register"
            book = roman_to_int(match.group("book")) if match.group("book") else None
            epistle = int(match.group("epistle"))
            suffix = match.group("suffix")
        else:
            short = _SHORT.match(raw, pos) if antecedent and separated else None
            ibid = _IBID.match(raw, pos) if antecedent and separated else None
            match = short or ibid
            if match:
                series, book, epistle, suffix = locator_key(antecedent)
                if short:
                    epistle = int(short.group("epistle"))
                    suffix = short.group("suffix")
                inherited_from = {"char_start": antecedent["char_start"], "char_end": antecedent["char_end"]}
                warnings.append("context_inherited_within_reference_group")
        if match:
            end = match.end()
            if re.match(r"\s*[-–—]\s*\d", raw[end:]):
                warnings.append("range_endpoint_unparsed")
            item = {"series": series, "book": book, "epistle": epistle,
                    "suffix": suffix.lower() if suffix else None,
                    "raw": raw[start:end], "char_start": offset + start,
                    "char_end": offset + end, "inherited_from": inherited_from,
                    "warnings": warnings}
            locators.append(item)
            antecedent = item
            pos = end
            separated = False
            continue
        # Preserve the complete unknown segment through the next semicolon;
        # do not recover arbitrary numbers out of prose or a damaged locator.
        next_separator = raw.find(";", pos)
        end = next_separator if next_separator >= 0 else len(raw)
        residuals.append({"raw": raw[start:end], "char_start": offset + start,
                          "char_end": offset + end, "reason": "unrecognized_epistle_locator"})
        pos = end
        antecedent = None
        separated = False
    return {"version": VERSION, "locators": locators, "residuals": residuals}
