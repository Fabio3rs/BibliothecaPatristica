"""Literal clause grammar for bracket-numbered Allatian index families.

Scopes and section conventions are supplied by the caller. No volume identifier
selects a rule, and numbers are never silently converted to physical pages.
"""
from __future__ import annotations

import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = "allatian-bracket-indices-v1"
NUMBER = r"\d{1,4}(?!\d)"
ATOM = rf"(?:{NUMBER}|ibid(?:em)?\.?|ib\.)"
REF = re.compile(
    rf"(?<![\w])(?P<refs>{ATOM}(?:\s*(?:,|;|\bet\b)\s*{ATOM})*"
    rf"(?:\s*,?\s*(?:et\s+)?(?:seq(?:q)?|etc)\.?)?)"
    r"(?=\s*(?:[.]|$|\n\s*[A-ZÆŒ]))", re.I)
DEPENDENT = re.compile(r"^(?:In\b|De\b|Ejus\b|Ex\b|Quando\b|Est\b|Ipse\b|His\b|Unus\b|Male\b|Alia\b|Quæ\b|In\b|Sancti\b|Sanctæ\b)")
HEAD = re.compile(r"^(?:CERTI AUCTORES|Allatiano primo|Numeri ad eos|VITÆ SANCTORUM|Priore Allatiano|RERUM PRÆCIPUARUM|In hac Leonis|INDICULUS\.)")


def trim(raw, a, b):
    while a < b and raw[a].isspace():
        a += 1
    while a < b and raw[b - 1].isspace():
        b -= 1
    return a, b


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    """Extract referenced clauses with exact OCR spans and explicit ambiguity.

    A clause ends at a terminal numeric group or ibid. Subject inheritance is
    retained as evidence, never substituted for the literal owner. The saints
    index has secondary locators whose typography is lost in OCR: retain the
    entire group instead of declaring every comma-separated number a page.
    """
    intervals = sorted((s['span'][0], s['span'][1], str(s.get('section_id', ''))) for s in scopes)
    for i, (a, b, _) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i - 1][1]):
            raise ValueError('Invalid or overlapping source scopes')
    context = {k: dict(v) for k, v in (section_context or {}).items() if isinstance(v, dict)}
    result = dict(version=VERSION, source=source, volume=volume, physical=physical,
                  source_sha256=hashlib.sha256(raw.encode()).hexdigest(), entries=[], residuals=[], context=context)
    source_blocks = blocks(raw)
    if not source_blocks and not re.search(r'</?\w+', raw):
        source_blocks = [dict(body_start=0, body_end=len(raw), kind='texto_principal')]

    def residue(a, b, reason, sid):
        a, b = trim(raw, a, b)
        if a < b:
            result['residuals'].append(dict(span=[a, b], raw=raw[a:b], reason=reason, section_id=sid))

    for a, b, sid in intervals:
        ctx = context.setdefault(sid, {})
        family = ctx.get('family', 'allatian_authors')
        namespace = ('bracketed_text_number_with_secondary_locator' if family == 'allatian_saints'
                     else 'bracketed_text_number')
        for block in source_blocks:
            start, end = max(a, block['body_start']), min(b, block['body_end'])
            if start >= end:
                continue
            text = raw[start:end]
            if block['kind'] != 'texto_principal' or HEAD.match(text.strip()) or re.fullmatch(r'\s*[A-Z]\.?\s*', text):
                residue(start, end, 'heading_or_editorial_element', sid)
                continue
            cursor = start
            for match in REF.finditer(text):
                ra, rb = start + match.start('refs'), start + match.end('refs')
                if (re.fullmatch(r'\d', raw[ra:rb])
                        and not raw[cursor:ra].rstrip().endswith(',')
                        and re.match(r'[.]\s*\w', raw[rb:end])):
                    continue
                # Reference notation must follow punctuation, whitespace, or an
                # orphan continuation. A prose ordinal such as "1 Jejuniorum"
                # has no terminal delimiter and does not match REF.
                oa, ob = trim(raw, cursor, ra)
                while oa < ob and raw[oa] in '.,;':
                    oa += 1
                    oa, ob = trim(raw, oa, ob)
                while oa < ob and raw[ob - 1] in ',;':
                    ob -= 1
                    oa, ob = trim(raw, oa, ob)
                breaks = list(re.finditer(r'\n\s*\n|(?<=[.])[^\S\n]*\n\s*(?=[A-ZÆŒ])', raw[oa:ob]))
                if breaks:
                    cut = oa + breaks[-1].end()
                    reason = 'textual_cross_reference' if re.search(r'\bVide\b', raw[oa:cut]) else 'unreferenced_prior_paragraph'
                    residue(oa, cut, reason, sid)
                    oa, ob = trim(raw, cut, ob)
                # Alphabet dividers are sometimes inside a principal XML block.
                # Remove only isolated leading letters; retain their raw spans.
                divider = re.match(r'(?:[A-Z]\.?\s*\n\s*)+', raw[oa:ob])
                if divider:
                    residue(oa, oa + divider.end(), 'alphabet_divider', sid)
                    oa += divider.end()
                    oa, ob = trim(raw, oa, ob)
                    ctx.pop('previous_owner', None)
                crossrefs = list(re.finditer(r'\bVide\s+[^.]+[.]', raw[oa:ob]))
                if crossrefs:
                    cut = oa + crossrefs[-1].end()
                    residue(oa, cut, 'textual_cross_reference', sid)
                    oa, ob = trim(raw, cut, ob)
                if not any(c.isalpha() for c in raw[oa:ob]):
                    residue(cursor, rb, 'orphan_numeric_continuation', sid)
                    cursor = rb
                    continue
                owner = raw[oa:ob]
                # A preceding completed but unreferenced sentence is preserved
                # in this owner: no invented attribution to a guessed person.
                flags = ['literal_candidate_not_certified']
                if re.search(r'\n\s*\n', owner):
                    flags.append('owner_contains_unreferenced_paragraph')
                if cursor == start and owner[:1].islower():
                    flags.append('possible_previous_page_continuation')
                if family == 'allatian_saints':
                    flags.append('primary_secondary_number_typography_not_encoded')
                entry = dict(section_id=sid, source=source, volume=volume, physical=physical,
                             entry_span=[oa, rb], owner_span=[oa, ob], owner_raw=owner,
                             reference_span=[ra, rb], reference_raw=raw[ra:rb],
                             reference_namespace=namespace, grammar=family,
                             entry_kind='onomastic_entry' if family != 'allatian_subjects' else 'thematic_entry',
                             flags=flags)
                previous_owner = ctx.get('previous_owner')
                if previous_owner and (DEPENDENT.match(owner) or 'possible_previous_page_continuation' in flags):
                    entry['inherited_owner_evidence'] = previous_owner
                    flags.append('subject_inheritance_candidate')
                else:
                    ctx['previous_owner'] = dict(source=source, span=[oa, ob], raw=owner)
                if re.match(r'ib(?:id(?:em)?)?(?:\.|(?=$|[\s,;]))', raw[ra:rb], re.I):
                    if ctx.get('previous_reference'):
                        entry['inherited_reference_evidence'] = ctx['previous_reference']
                        flags.append('ibid_previous_literal_reference')
                    else:
                        flags.append('ibid_without_prior_reference')
                else:
                    ctx['previous_reference'] = dict(source=source, span=[ra, rb], raw=raw[ra:rb])
                result['entries'].append(entry)
                cursor = rb
            residue(cursor, end, 'unparsed_tail_or_cross_reference', sid)
    if not source_blocks:
        for a, b, sid in intervals:
            residue(a, b, 'unsupported_markup', sid)
    return result
