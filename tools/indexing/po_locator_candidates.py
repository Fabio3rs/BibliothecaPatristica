"""Page-first PO locator candidates; no canonical changes or inferred file offsets.

Call only on the material-reference side of an already isolated entry. A scoped
editorial profile must select the notation; this is not a detector of index prose.
"""
from __future__ import annotations

import re

from tools.indexing.entry_parser_po import TOKEN
from tools.indexing.parser_target_experiment import locate_header

VERSION = 'po-locator-candidates-v2'
SUPERSCRIPT = 'ᵃᵇᶜᵈ⁰¹²³⁴⁵⁶⁷⁸⁹'
QUALIFIER = re.compile(r'cf\.\s*', re.I)
NOTE = re.compile(r'n(?:ote)?\.?\s*[0-9]+', re.I)


def _span(raw, start, end, base):
    return {'raw': raw[start:end], 'char_start': base + start, 'char_end': base + end}


def parse_po_locators(raw, notation, *, base=0):
    """Preserve occurrences and residuals; uncertain lines do not erase pages.

page_line: semicolons delimit pages, commas delimit line lists. page_subscript:
explicit subscript/underscore syntax separates page and detail. Input offsets
are Python character offsets in the unchanged source (``base`` is its origin).
    """
    if notation not in {'page_line', 'page_subscript'}:
        raise ValueError(f'Unsupported PO notation: {notation}')
    result = {'version': VERSION, 'notation': notation, 'locators': [], 'residuals': [],
              'verified': False}

    def residual(a, b, reason):
        if raw[a:b].strip(' \n\t,;.'):
            result['residuals'].append({**_span(raw, a, b, base), 'reason': reason})

    def emit(a, b, page_a, page_b, detail=None, warnings=None, qualifier=None, endpoint=None):
        page = int(raw[page_a:page_b])
        if page <= 0:
            residual(a, b, 'nonpositive_page')
            return
        result['locators'].append({**_span(raw, a, b, base), 'page': page,
            'page_span': _span(raw, page_a, page_b, base), 'detail_raw': detail,
            'detail_status': 'unverified' if detail else 'not_provided',
            'qualifier_raw': qualifier, 'range_endpoint': endpoint,
            'warnings': warnings or [], 'status': 'search_candidate', 'verified': False})

    # Parenthetical remissions can contain numbers which are not target pages.
    # Do not split semicolons inside parentheses into new locator groups.
    starts, depth, start = [], 0, 0
    for i, char in enumerate(raw):
        if char == '(':
            depth += 1
        elif char == ')':
            depth = max(0, depth - 1)
        elif char == ';' and depth == 0:
            starts.append((start, i))
            start = i + 1
    starts.append((start, len(raw)))

    for a, b in starts:
        pos = a
        while pos < b and raw[pos].isspace():
            pos += 1
        q = QUALIFIER.match(raw, pos, b)
        qualifier = q[0].strip() if q else None
        if q:
            pos = q.end()
        if notation == 'page_line':
            match = re.match(r'[0-9]+', raw[pos:b])
            if not match:
                residual(a, b, 'missing_page_prefix')
                continue
            end = pos + match.end()
            if end < b and raw[end] in SUPERSCRIPT:
                residual(a, b, 'superscript_remission_not_material_page')
                continue
            tail = raw[end:b].strip().rstrip('.')
            detail = tail.lstrip(',。. ').strip() or None
            warnings = []
            if tail and not tail.startswith((',', '。', '.')):
                warnings.append('missing_page_detail_separator')
            # Recognize syntax only. Ordering contradictions retain the base page.
            list_match = re.fullmatch(r'[0-9]+(?:\s*[-–]\s*[0-9]+)?(?:\s*,\s*[0-9]+(?:\s*[-–]\s*[0-9]+)?)*(?:\s*s{1,2}\.)?', detail or '')
            if detail and not list_match:
                warnings.append('detail_uninterpreted')
            elif detail:
                values = list(map(int, re.findall(r'[0-9]+', detail)))
                if any(v <= 0 for v in values) or values != sorted(set(values)):
                    warnings.append('ambiguous_line_list_or_missing_page_separator')
            emit(a, b, pos, end, detail, warnings, qualifier)
            continue

        while pos < b:
            if raw[pos].isspace() or raw[pos] in ',;.':
                pos += 1
                continue
            continuation = re.match(r'etc\.(?=\s|$)', raw[pos:b], re.I)
            if continuation:
                residual(pos, pos + continuation.end(), 'unspecified_continuation')
                pos += continuation.end()
                continue
            q = QUALIFIER.match(raw, pos, b)
            if q:
                qualifier = q[0].strip()
                pos = q.end()
                continue
            match = TOKEN.match(raw, pos, b)
            if not match:
                residual(pos, b, 'unparsed_locator_tail')
                break
            end = match.end()
            if end < b and raw[end] in SUPERSCRIPT:
                residual(pos, b, 'superscript_remission_not_material_page')
                break
            if match['cross']:
                endpoints = list(re.finditer(r'([0-9]+)_([0-9]+)', match[0]))
                pages = [int(m[1]) for m in endpoints]
                for order, ep in enumerate(endpoints):
                    warnings = ['range_interior_not_expanded']
                    if pages[1] < pages[0]:
                        warnings.append('reversed_page_range')
                    emit(pos, end, pos + ep.start(1), pos + ep.end(1), ep[2], warnings,
                         qualifier, 'start' if order == 0 else 'end')
            else:
                detail = match['sub']
                # OCR mixes ASCII dashes with Unicode subscripts. Consume the
                # line range without swallowing the next baseline page number.
                if detail:
                    tail = re.match(r'(?:[-–₋][₀₁₂₃₄₅₆₇₈₉]+)+', raw[end:b])
                    if tail:
                        detail += tail[0]
                        end += tail.end()
                if match['note']:
                    detail = (detail or '') + match['note']
                else:
                    # Unicode subscript followed by spaces and n. is covered by
                    # TOKEN, but retain other explicit note spelling for audit.
                    following = NOTE.match(raw, end, b)
                    if following:
                        detail = (detail or '') + following[0]
                        end = following.end()
                warnings = []
                if match['sub'] and match['sub'].endswith(('₋', '-')):
                    warnings.append('open_detail_range')
                # Comma encoding is uncertain in this family. Preserve a base
                # page and its remaining detail instead of fabricating page 12
                # from the damaged subscript in 61,12.
                remainder = raw[end:b]
                if not match['sub'] and not match['note'] and re.match(r'\s*,\s*[0-9]', remainder):
                    detail = remainder.strip()
                    end = b
                    warnings.append('ambiguous_comma_detail_or_page_list')
                emit(pos, end, match.start('page'), match.end('page'), detail,
                     warnings, qualifier)
            qualifier = None
            pos = end
    return result


def locate_po_candidates(series, number):
    """Return all literal markers in the existing fascicle scope, ranked by support.

A missing sequence neighbor lowers evidence strength; it does not suppress an
observed bracketed page number. No interpolation or physical filename arithmetic.
    """
    record = locate_header(series, number)
    known = {f['physical'] for f in record['files']}
    for f in record['files']:
        f['evidence_strength'] = 'sequence_supported_marker'
    for physical in sorted(series['nodes'].get(number, ())):
        if physical in known:
            continue
        record['files'].append({'physical': physical, **series['pages'][physical],
            'sequence_support': None, 'evidence_strength': 'literal_marker_without_sequence'})
    record['status'] = ('missing_literal_header' if not record['files'] else
                        'ambiguous_header_candidates' if len(record['files']) > 1 else
                        'header_page_candidate')
    record['verified'] = False
    record['candidate_policy'] = VERSION
    return record
