"""Literal analytic clauses with evidence-bearing Roman/ibid inheritance.

Scopes must come from a reviewed editorial section. The lexer recognizes the
same Roman/Arabic grammar as entry_parser_literal_rows, but accepts a namespace
without a preceding comma and prevents Latin 'A Phoca' becoming subdivision A.
No OCR repair, inferred destination, or page-number conversion is performed.
"""
from __future__ import annotations

import copy
import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'analytic-inherited-index-v1'
ROMAN = r'[IVXLCDM]+'
NUMBER = r'\d{1,4}(?!\d)'
IBID = r'(?:ibid(?:em)?\.?|ib\.)'
START = re.compile(rf'(?<!\w)(?:(?P<roman>{ROMAN})\s*,\s*(?=\d)|(?P<ibid>{IBID})(?!\w)|(?P<bare>{NUMBER})(?=\s*[,.;]|\s*$))')
# Subdivision A-D requires punctuation/end after it; A Phoca stays in the owner.
TOKEN = re.compile(rf'\s*(?:{ROMAN}\s*,\s*(?=\d)|{NUMBER}|{IBID}(?!\w)|[A-D](?=\s*(?:[,.;]|$))|(?:et\s+)?(?:seqq?|sqq|passim|fin)\.?\b|[,;–—-]|[.](?=\s*\d))')
DEPENDENT = re.compile(r'^(?:Ejus(?:dem)?|Eorum(?:dem)?|Item|Mors|Interitus|Quo|Quæ|Qui|Ubi|Quando|A quibus|Ab iis|Ibi|Post|Cur|Cum|Inde)\b')
HEADING = re.compile(r'^(?:INDEX\b|IN GEORGIUM\b|Revocatur\b|ORDO RERUM\b|[A-Z][.]?$)')


def _trim(raw, a, b, punctuation=False):
    chars = ' \t\r\n.,;—–' if punctuation else ' \t\r\n'
    while a < b and raw[a] in chars:
        a += 1
    while a < b and raw[b-1] in chars:
        b -= 1
    return a, b


def reference_groups(text):
    """Yield local reference spans, leaving sentence punctuation outside them."""
    cursor = 0
    for match in START.finditer(text):
        if match.start() < cursor:
            continue
        if match.group('bare') and not re.search(r'[,;]\s*$', text[:match.start()]):
            continue
        end = match.end()
        # Roman prefix matches only through its comma; require the first number.
        while token := TOKEN.match(text, end):
            if token.end() == end:
                break
            end = token.end()
        end = match.start() + len(text[match.start():end].rstrip(' \t\r\n,;—–-'))
        if end <= match.start():
            continue
        cursor = end
        yield match.start(), end


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    intervals = sorted((s['span'][0], s['span'][1], str(s.get('section_id', ''))) for s in scopes)
    for i, (a, b, _) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i-1][1]):
            raise ValueError('Invalid or overlapping source scopes')
    context = copy.deepcopy(section_context or {})
    out = dict(version=VERSION, source=source, volume=volume, physical=physical,
               source_sha256=hashlib.sha256(raw.encode()).hexdigest(), entries=[], residuals=[], context=context)
    source_blocks = blocks(raw)
    if not source_blocks and not re.search(r'</?\w+', raw):
        source_blocks = [dict(body_start=0, body_end=len(raw), kind='texto_principal')]

    def residual(a, b, reason, sid):
        a, b = _trim(raw, a, b)
        if a < b:
            out['residuals'].append(dict(span=[a, b], raw=raw[a:b], reason=reason, section_id=sid))

    for a, b, sid in intervals:
        ctx = context.setdefault(sid, {})
        signature = len(re.findall(rf'\b{ROMAN}\s*,\s*{NUMBER}', raw[a:b]))
        declared = ctx.get('family') == 'analytic_roman_inherited'
        notice = ' '.join(str(ctx.get(k, '')) for k in ('title', 'notice', 'heading')) + ' ' + raw[a:b]
        if not declared and not (signature >= 3 and re.search(r'INDEX\b', notice)):
            residual(a, b, 'missing_analytic_section_signature', sid)
            continue
        namespace = ctx.get('reference_namespace') or (
            'internal_text_number_with_roman_namespace' if re.search(r'numeros grandiores textui insertos', notice)
            else 'roman_namespace_arabic_locator')
        ctx['reference_namespace'] = namespace
        for block in source_blocks:
            start, end = max(a, block['body_start']), min(b, block['body_end'])
            if start >= end:
                continue
            text = raw[start:end]
            if block['kind'] != 'texto_principal' or HEADING.match(text.strip()):
                residual(start, end, 'heading_or_editorial_element', sid)
                continue
            cursor = start
            for x, y in reference_groups(text):
                ra, rb = start+x, start+y
                oa, ob = _trim(raw, cursor, ra, True)
                # A corrupt locator on a completed prior line must not become
                # part of the next name (e.g. 'II, a 593.\nAbisaca ...').
                broken = list(re.finditer(r'\d[.]\s*\n\s*(?=[A-ZÆŒ])', raw[oa:ob]))
                if broken:
                    cut = oa + broken[-1].end()
                    residual(oa, cut, 'unsupported_previous_numeric_clause', sid)
                    oa, ob = _trim(raw, cut, ob, True)
                    ctx.pop('previous_owner', None)
                # Preserve unreferenced cross-remissions rather than attaching
                # them to the next numeric entry in the same OCR column.
                crosses = list(re.finditer(r'(?:\bV\.|\bVide\b)\s+[^.\n]+[.]', raw[oa:ob]))
                if crosses:
                    cut = oa + crosses[-1].end()
                    residual(oa, cut, 'textual_cross_reference', sid)
                    oa, ob = _trim(raw, cut, ob, True)
                    ctx.pop('previous_owner', None)
                divider = re.match(r'(?:[A-Z][.]?\s*\n\s*)+', raw[oa:ob])
                if divider:
                    residual(oa, oa+divider.end(), 'alphabet_divider', sid)
                    oa, ob = _trim(raw, oa+divider.end(), ob, True)
                    ctx.pop('previous_owner', None)
                owner, ref = raw[oa:ob], raw[ra:rb]
                if not any(c.isalpha() for c in owner):
                    residual(cursor, rb, 'orphan_locator_continuation', sid)
                    cursor = rb
                    continue
                flags = ['literal_candidate_not_certified', 'clause_not_independent_headword']
                if re.search(r'\d', owner):
                    flags.append('numeric_owner_requires_review')
                if len(owner) > 600:
                    flags.append('long_owner_segmentation_requires_review')
                evidence = dict(source=source, span=[ra, rb], raw=ref)
                entry = dict(section_id=sid, source=source, volume=volume, physical=physical,
                    entry_span=[oa, rb], owner_span=[oa, ob], owner_raw=owner,
                    reference_span=[ra, rb], reference_raw=ref, reference_namespace=namespace,
                    grammar='analytic_roman_inherited', entry_kind='analytic_clause', flags=flags)
                romans = list(re.finditer(rf'\b(?P<roman>{ROMAN})\s*,\s*(?=\d)', ref))
                is_ibid = bool(re.match(IBID, ref))
                if is_ibid:
                    if ctx.get('previous_reference'):
                        entry['inherited_reference_evidence'] = copy.deepcopy(ctx['previous_reference'])
                        flags.append('ibid_previous_literal_reference')
                    else:
                        flags.append('ibid_without_prior_reference')
                    if re.search(r'\d', ref):
                        flags.append('ibid_with_additional_locator_interpretation_pending')
                elif not romans:
                    if ctx.get('previous_namespace'):
                        entry['inherited_namespace_evidence'] = copy.deepcopy(ctx['previous_namespace'])
                        flags.append('implicit_roman_namespace_candidate')
                    else:
                        flags.append('arabic_without_roman_context')
                previous_owner = ctx.get('previous_owner')
                if previous_owner and (DEPENDENT.match(owner) or owner[:1].islower()):
                    entry['inherited_owner_evidence'] = copy.deepcopy(previous_owner)
                    flags.append('subject_inheritance_candidate')
                else:
                    ctx['previous_owner'] = dict(source=source, span=[oa, ob], raw=owner)
                if romans:
                    m = romans[-1]
                    ctx['previous_namespace'] = dict(source=source, span=[ra+m.start('roman'), ra+m.end('roman')], raw=m.group('roman'))
                # Keep the fully evidenced chain rather than rewriting ibid.
                previous = dict(evidence)
                if entry.get('inherited_reference_evidence'):
                    previous['inherited_reference_evidence'] = entry['inherited_reference_evidence']
                if entry.get('inherited_namespace_evidence'):
                    previous['inherited_namespace_evidence'] = entry['inherited_namespace_evidence']
                ctx['previous_reference'] = previous
                out['entries'].append(entry)
                cursor = rb
            residual(cursor, end, 'unparsed_tail_or_cross_page_continuation', sid)
    if not source_blocks:
        for a, b, sid in intervals:
            residual(a, b, 'unsupported_markup', sid)
    return out
