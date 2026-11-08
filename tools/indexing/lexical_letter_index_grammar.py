"""Literal Greek lexical rows with Arabic locators and lowercase side letters.

Caller supplies a bounded editorial region; this is not a corpus-wide detector.
No normalization changes source offsets, and no locator becomes a PG column.
"""
from __future__ import annotations

import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'lexical-letter-index-v1'
GREEK = re.compile(r'[\u0370-\u03ff\u1f00-\u1fff]')
ATOM = re.compile(r'(?<!\w)(?P<number>\d{1,4})\s*(?P<side>[ab])(?!\w)')
TAIL = re.compile(r'(?:\s|[.,;:–—-]|\bet\b|\balibi\b|\b(?:saepius|sæpius|sepius|passim)\b)*')
NOTICE = 'Numeri respondent foliis libri Gudiani typis grandioribus expressis.'


def _trim(raw, a, b):
    while a < b and raw[a].isspace():
        a += 1
    while b > a and raw[b-1].isspace():
        b -= 1
    return a, b


def _reference_tail(text):
    """Accept only a complete locator list, keeping unknown OCR tokens residual."""
    matches = list(ATOM.finditer(text))
    if not matches:
        return None
    cursor = matches[0].start()
    for match in matches:
        if not TAIL.fullmatch(text[cursor:match.start()]):
            return None
        cursor = match.end()
    if not TAIL.fullmatch(text[cursor:]):
        return None
    return matches


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    """Return compatible entries/residuals and exact character provenance.

    A Greek lexical start opens a row. Numeric-only continuations remain attached
    until the next lemma; malformed numeric tails never migrate into its owner.
    Block boundaries are hard boundaries: cross-column ambiguity remains visible.
    """
    intervals = sorted((s['span'][0], s['span'][1], str(s.get('section_id', ''))) for s in scopes)
    for i, (a,b,_) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i-1][1]):
            raise ValueError('Invalid or overlapping source scopes')
    out = dict(version=VERSION, source=source, volume=volume, physical=physical,
               source_sha256=hashlib.sha256(raw.encode()).hexdigest(), entries=[], residuals=[])
    source_blocks = blocks(raw)
    if not source_blocks and not re.search(r'</?\w+', raw):
        source_blocks = [dict(body_start=0, body_end=len(raw), kind='texto_principal')]

    def residue(a,b,reason,sid):
        a,b = _trim(raw,a,b)
        if a < b:
            out['residuals'].append(dict(span=[a,b], raw=raw[a:b], reason=reason, section_id=sid))

    for a,b,sid in intervals:
        context = (section_context or {}).get(sid, section_context or {})
        notice_text = str(context.get('notice', ''))
        namespace = 'gudian_folio_side' if NOTICE in notice_text else 'number_lowercase_side_uninterpreted'

        def emit(start,end):
            start,end = _trim(raw,start,end)
            text = raw[start:end]
            matches = _reference_tail(text)
            if not matches:
                residue(start,end,'unparsed_lexical_row_or_locator',sid)
                return
            first = matches[0]
            oa,ob = _trim(raw,start,start+first.start())
            while ob > oa and raw[ob-1] in ',;:':
                ob -= 1
                oa,ob = _trim(raw,oa,ob)
            owner = raw[oa:ob]
            if not GREEK.search(owner) or any(c.isdigit() for c in owner):
                residue(start,end,'missing_or_ambiguous_lexical_owner',sid)
                return
            ra,rb = start+first.start(),end
            repeated_pages = next((list(group) for group in context.get('repeated_scan_groups', [])
                                   if physical in group), [])
            flags = ['literal_candidate_not_certified']
            if repeated_pages:
                flags.append('reviewed_repeated_printed_spread')
            out['entries'].append(dict(section_id=sid, source=source, volume=volume, physical=physical,
                entry_span=[start,end], owner_span=[oa,ob], owner_raw=owner,
                reference_span=[ra,rb], reference_raw=raw[ra:rb], reference_namespace=namespace,
                entry_kind='lexical_entry', grammar=VERSION, flags=flags,
                repeated_scan_physical_pages=repeated_pages,
                locator_atoms=[dict(number=int(m['number']), side=m['side'],
                    raw=m.group(), span=[start+m.start(),start+m.end()]) for m in matches]))

        if not source_blocks:
            residue(a,b,'unsupported_markup',sid)
        for block in source_blocks:
            lo,hi = max(a,block['body_start']),min(b,block['body_end'])
            if lo >= hi:
                continue
            if block['kind'] != 'texto_principal':
                residue(lo,hi,'editorial_nonbody',sid)
                continue
            pending = None
            previous_end = lo
            cursor = lo
            for line in raw[lo:hi].splitlines(keepends=True):
                la,lb = _trim(raw,cursor,cursor+len(line))
                cursor += len(line)
                if la == lb:
                    continue
                text = raw[la:lb]
                if re.fullmatch(r'[^\W\d_]\.?', text) and GREEK.search(text):
                    if pending is not None:
                        emit(pending,previous_end)
                        pending = None
                    residue(la,lb,'alphabetic_heading',sid)
                    continue
                starts_greek = bool(GREEK.match(text))
                continuation_start = bool(re.match(r'(?:\d|[ab]\b|et\b|sæpius\b|saepius\b|sepius\b|alibi\b)', text))
                if not starts_greek and not continuation_start:
                    if pending is not None:
                        emit(pending,previous_end)
                        pending = None
                    residue(la,lb,'orphan_continuation_or_nonlexical_text',sid)
                    continue
                if starts_greek and pending is not None:
                    current = raw[pending:previous_end].rstrip()
                    # A wrapped lemma has no completed locator yet. A following
                    # independently comma-delimited lemma must not be swallowed.
                    has_locator = bool(ATOM.search(current))
                    continuation = (not has_locator and
                                    (current.endswith(('-', ',', '(')) or
                                     current.count('(') > current.count(')')))
                    if not continuation:
                        emit(pending,previous_end)
                        pending = None
                if pending is None:
                    if starts_greek:
                        pending = la
                    else:
                        residue(la,lb,'orphan_continuation_or_nonlexical_text',sid)
                previous_end = lb
            if pending is not None:
                emit(pending,previous_end)
    return out
