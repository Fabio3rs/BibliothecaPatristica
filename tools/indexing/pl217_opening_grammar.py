"""Read the printed PL217 index contract and reuse its literal row grammar.

All offsets address the unchanged OCR string. The opening identifies a namespace,
not physical destinations; a book is never converted to a Patrologia volume.
"""
from __future__ import annotations

import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.entry_parser_literal_rows import parse_page
from tools.indexing.epistle_index_locators import parse_epistle_locators

VERSION = "pl217-opening-grammar-v1"
TITLE = re.compile(r"\bINDEX\s+EPISTOLARUM\b", re.I)
ORDER = re.compile(r"SECUNDUM\s+LITTERAM\s+INITIALEM\s+ORDINATUS", re.I)
NOTICE = re.compile(
    r"PRIOR\s+NUMERUS\s+LIBRUM\s*,\s*POSTERIOR\s+EPISTOLAM\s+INDICAT\.?", re.I)
CLOSURE = re.compile(r"\bORDO\s+RERUM\b", re.I)


def analyze_title_page(raw: str, source: str = "") -> dict:
    """Read the explicit four-tome plan, independently of index locators.

    TOMUS QUARTUS is a bibliographic tome, while XV in an incipit is a book.
    This small grammar intentionally reports no mapping to PL214/215/216: such
    correspondences require those volumes' own catalogues and text openings.
    """
    rules = {
        "declared_four_tomes": r"TOMIS\.?\s+QUATUOR\.?\s+DISTR[IU]B[UV]TA",
        "first_three_registers": r"QUORUM\s+PRIORES\s+TRES\s+REGESTORUM\s+BALUZIANAM\s+RECENSIONEM\s+COMPLECTUNTUR",
        "fourth_extra_register_letters": r"QUARTO\s+VOLUMINI\s+INSUNT\s+EPISTOL[ÆA]E?\s+EXTRA\s+REGESTUM\s+VAGANTES",
        "current_fourth_tome": r"TOMUS\s+QUARTUS\.?",
    }
    evidence = []
    for block in blocks(raw):
        if block["kind"] not in {"texto_principal", "cabecalho"}:
            continue
        for rule, pattern in rules.items():
            if match := re.search(pattern, block["body"], re.I):
                a, b = block["body_start"] + match.start(), block["body_start"] + match.end()
                evidence.append({"rule": rule, "raw": raw[a:b], **source_span(raw, a, b)})
    found = {e["rule"] for e in evidence}
    return {"source": source, "source_sha256": digest(raw.encode()), "version": VERSION,
            "declared_tomes": 4 if "declared_four_tomes" in found else None,
            "current_tome": 4 if "current_fourth_tome" in found else None,
            "register_tomes": [1, 2, 3] if "first_three_registers" in found else [],
            "fourth_tome_extra_register_letters": "fourth_extra_register_letters" in found,
            "evidence": evidence, "physical_volume_mapping": None}


def analyze_opening(raw: str, source: str = "") -> dict:
    """Require the explicit title and number legend in one editorial block.

    An isolated INDEX or a running header does not declare how numbers work.
    Missing/damaged legends remain unmatched; they are not inferred from volume ID.
    """
    for block in blocks(raw):
        if block["kind"] not in {"cabecalho", "texto_principal"}:
            continue
        title, notice = TITLE.search(block["body"]), NOTICE.search(block["body"])
        if not (title and notice):
            continue
        evidence = []
        for role, match in (("title", title), ("number_legend", notice),
                            ("ordering", ORDER.search(block["body"]))):
            if match:
                a, b = block["body_start"] + match.start(), block["body_start"] + match.end()
                evidence.append({"role": role, "raw": raw[a:b], **source_span(raw, a, b)})
        return {"version": VERSION, "matched": True, "source": source,
                "source_sha256": digest(raw.encode()), "namespace": "book_epistle",
                "entry_kind": "epistle_incipit", "first_number": "book",
                "second_number": "epistle", "numbers_are_pages": False,
                "ordering": "initial_letter" if ORDER.search(block["body"]) else "unknown",
                "evidence": evidence, "status": "ocr_explicit_legend"}
    return {"version": VERSION, "matched": False, "source": source,
            "source_sha256": digest(raw.encode()), "evidence": [],
            "status": "missing_explicit_legend"}


def parse_index_page(raw: str, source: str, physical: int, opening: dict | None = None) -> dict:
    """Extract incipits under an explicit opening contract, retaining residuals.

    The caller supplies contiguous section pages. Internal ORDO RERUM closes the
    index, but the same words in the top running header do not: PL217/600 is mixed.
    Neither bboxes nor an old database is needed to regenerate these boundaries.
    """
    opening = opening or analyze_opening(raw, source)
    if not opening.get("matched") or opening.get("namespace") != "book_epistle":
        raise ValueError("An explicit book/epistle opening contract is required")
    scopes, closing = [], None
    section_id = f"{opening['source']}:book_epistle"
    for block in blocks(raw):
        # Require preceding body material to distinguish a mixed-page title from
        # a running header. Restrict the title to the first line of its block.
        title = CLOSURE.search(block["body"])
        if title and not block["body"][:title.start()].strip() and scopes:
            a = block["body_start"] + title.start()
            b = block["body_start"] + title.end()
            closing = {"kind": "internal_editorial_closure", "raw": raw[a:b],
                       **source_span(raw, a, b)}
            break
        if block["kind"] == "texto_principal":
            scopes.append({"span": [block["body_start"], block["body_end"]],
                           "section_id": section_id})
    result = parse_page(raw, source=source, physical=physical, volume="PL217", scopes=scopes)
    result.update(opening=opening, namespace="book_epistle", scopes=scopes,
                  closing=closing, grammar_version=VERSION)
    for entry in result["entries"]:
        entry["namespace"] = "book_epistle"
        entry["entry_kind"] = "epistle_incipit"
        entry["editorial_locators"] = []
        entry["locator_residuals"] = []
        for group in entry["reference_groups"]:
            parsed = parse_epistle_locators(group["raw"], offset=group["char_start"])
            group["namespace_status"] = "book_epistle_from_printed_legend"
            group["editorial_locators"] = parsed["locators"]
            group["locator_residuals"] = parsed["residuals"]
            entry["editorial_locators"].extend(parsed["locators"])
            entry["locator_residuals"].extend(parsed["residuals"])
        if entry["locator_residuals"]:
            entry["flags"].append("unparsed_editorial_locator_preserved")
    return result
