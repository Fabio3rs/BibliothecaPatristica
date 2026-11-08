"""Literal Roman/Arabic index grammar, selected by a documented section notice.

A Roman locator and its dependent Arabic list remain outside the owner. The
caller supplies the namespace (book/chapter, tome/column, etc.); typography alone
never turns these numbers into pages. No database or source writes occur here.
"""
from __future__ import annotations

import copy
import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'roman-pair-index-v1'
ROMAN = r'[IVXLCDM]+'
NUMBER = r'\d{1,4}(?!\d)'
EXPLICIT = rf'(?P<roman>{ROMAN})\s*,\s*(?P<number>{NUMBER})'
START = re.compile(rf'(?<!\w)(?:{ROMAN}\s*,\s*{NUMBER}|(?i:ib(?:id(?:em)?)?\.))(?!\w)')
PAIR = re.compile(EXPLICIT)
ARABIC = re.compile(NUMBER)
IBID = re.compile(r'ib(?:id(?:em)?)?\.', re.I)
CONNECTOR = re.compile(r'\s*(?:[,;]|\bet\b)\s*')
QUALIFIER = re.compile(r'\s*,?\s*(?:et\s+)?(?:seq(?:q)?\.?|sqq\.?|etc\.?|passim|alibi|sæpius)(?!\w)', re.I)
HEAD = re.compile(r'^(?:INDEX\b|NUMERUS\s+MAJOR\b|SCRIPTORUM\s+QUI\b|VOCUM\s+AC\b|RERUM\.?$|PRÆFATIO\b)', re.I)
ROMAN_VALUE = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
VALID_ROMAN = re.compile(r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$')


def roman_value(raw):
    """Reject malformed numerals; do not repair I/1 or V/Y without evidence."""
    if not raw or not VALID_ROMAN.fullmatch(raw):
        return None
    return sum(-ROMAN_VALUE[c] if i + 1 < len(raw) and ROMAN_VALUE[c] < ROMAN_VALUE[raw[i + 1]]
               else ROMAN_VALUE[c] for i, c in enumerate(raw))


def _trim(raw, a, b, punctuation=False):
    chars = '.,;:¹²³⁴⁵⁶⁷⁸⁹⁰' if punctuation else ''
    while a < b and (raw[a].isspace() or raw[a] in chars):
        a += 1
    while a < b and (raw[b - 1].isspace() or raw[b - 1] in chars):
        b -= 1
    return a, b


def read_reference_group(text, start, *, offset=0, source='', previous=None, maximum_roman=None):
    """Read explicit pairs and their comma-separated continuations with evidence.

    `II, 6, 17; IV, 38` has three locators; 17 inherits II from the same
    printed group. `ibid.` inherits only an explicitly preserved prior locator.
    No bare chapter starts a group, and inheritance never crosses a section.
    """
    pos, last, atoms, flags = start, copy.deepcopy(previous), [], []
    first = True
    while True:
        pair, same, number = PAIR.match(text, pos), IBID.match(text, pos), None
        if pair:
            rv = roman_value(pair['roman'])
            if rv is None or (maximum_roman is not None and rv > maximum_roman):
                if first:
                    return None
                break
            atom = {'roman_raw': pair['roman'], 'roman_value': rv,
                    'arabic_raw': pair['number'], 'arabic_value': int(pair['number']),
                    'source': source, 'span': [offset + pair.start(), offset + pair.end()],
                    'raw': pair[0], 'roman_span': [offset + pair.start('roman'), offset + pair.end('roman')],
                    'arabic_span': [offset + pair.start('number'), offset + pair.end('number')]}
            pos = pair.end()
        elif same:
            atom = {'source': source, 'span': [offset + same.start(), offset + same.end()], 'raw': same[0],
                    'relation': 'ibid'}
            if last and last.get('roman_value') is not None:
                atom.update(roman_raw=last['roman_raw'], roman_value=last['roman_value'],
                            arabic_raw=last['arabic_raw'], arabic_value=last['arabic_value'],
                            inherited_reference_evidence=copy.deepcopy(last))
                flags.append('ibid_previous_literal_reference')
            else:
                flags.append('ibid_without_prior_reference')
            pos = same.end()
        elif not first and last and (number := ARABIC.match(text, pos)):
            atom = {'source': source, 'span': [offset + number.start(), offset + number.end()], 'raw': number[0],
                    'arabic_raw': number[0], 'arabic_value': int(number[0]),
                    'roman_raw': last.get('roman_raw'), 'roman_value': last.get('roman_value'),
                    'inherited_roman_evidence': {'source': last['source'], 'span': last['span'], 'raw': last['raw']}}
            pos = number.end()
            flags.append('roman_inherited_within_reference_list')
        else:
            break
        atoms.append(atom)
        # Preserve a flat explicit anchor rather than recursively nesting all
        # earlier ibid chains. The immediate relation remains on this atom.
        if atom.get('roman_value') is not None:
            last = {k: copy.deepcopy(atom[k]) for k in ('source', 'span', 'raw', 'roman_raw', 'roman_value', 'arabic_raw', 'arabic_value')}
        qualifier = QUALIFIER.match(text, pos)
        if qualifier:
            atom['qualifier_raw'] = qualifier[0]
            atom['qualifier_span'] = [offset + qualifier.start(), offset + qualifier.end()]
            pos = qualifier.end()
        connector = CONNECTOR.match(text, pos)
        if not connector:
            break
        following = connector.end()
        if not (PAIR.match(text, following) or IBID.match(text, following) or ARABIC.match(text, following)):
            break
        # Commit the delimiter only after seeing another locator. Words after
        # a comma belong to the next clause, not to the preceding reference.
        pos = following
        first = False
    if not atoms:
        return None
    return {'end': pos, 'locators': atoms, 'last': last, 'flags': sorted(set(flags))}


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    """Parse bounded source regions; retain unparsed text and context explicitly."""
    intervals = sorted((s['span'][0], s['span'][1], str(s.get('section_id', ''))) for s in scopes)
    for i, (a, b, _) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i - 1][1]):
            raise ValueError('Invalid or overlapping source scopes')
    provided = section_context or {}
    context = {k: copy.deepcopy(v) for k, v in provided.items() if isinstance(v, dict)}
    if provided and not context:
        context = {sid: copy.deepcopy(provided) for _, _, sid in intervals}
    result = {'version': VERSION, 'source': source, 'volume': volume, 'physical': physical,
              'source_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'entries': [], 'residuals': [], 'context': context}
    source_blocks = blocks(raw)
    if not source_blocks and not re.search(r'</?\w+', raw):
        source_blocks = [{'body_start': 0, 'body_end': len(raw), 'kind': 'texto_principal'}]

    def residue(a, b, reason, sid):
        a, b = _trim(raw, a, b)
        if a < b:
            result['residuals'].append({'span': [a, b], 'raw': raw[a:b], 'reason': reason, 'section_id': sid})

    for a, b, sid in intervals:
        ctx = context.setdefault(sid, {})
        namespace = ctx.get('reference_namespace', 'roman_arabic_pair_unknown')
        for block in source_blocks:
            left, right = max(a, block['body_start']), min(b, block['body_end'])
            if left >= right:
                continue
            if block['kind'] != 'texto_principal':
                residue(left, right, 'editorial_nonbody', sid)
                continue
            text = raw[left:right]
            if HEAD.match(text.strip()):
                residue(left, right, 'heading_or_notice', sid)
                continue
            cursor, scan = left, 0
            while match := START.search(text, scan):
                ra = left + match.start()
                group = read_reference_group(text, match.start(), offset=left, source=source,
                                             previous=ctx.get('previous_reference'), maximum_roman=ctx.get('maximum_roman'))
                if group is None:
                    scan = match.end()
                    continue
                rb, scan = left + group['end'], group['end']
                oa, ob = _trim(raw, cursor, ra, True)
                # A standalone alphabet divider or a completed unreferenced
                # paragraph must not be silently appended to the next lemma.
                breaks = list(re.finditer(r'\n\s*\n|(?m:^[ \t]*[A-Z][.]?[ \t]*\n)', raw[oa:ob]))
                if breaks:
                    cut = oa + breaks[-1].end()
                    residue(cursor, cut, 'unreferenced_text_or_alphabet_divider', sid)
                    oa, ob = _trim(raw, cut, ob, True)
                    cursor = cut
                    ctx.pop('previous_owner', None)
                completed = list(re.finditer(r'[^\n]*\d[^\n]*[.]\s*\n', raw[oa:ob]))
                if completed:
                    cut = oa + completed[-1].end()
                    residue(cursor, cut, 'unsupported_previous_locator', sid)
                    cursor = cut
                    oa, ob = _trim(raw, cut, ob, True)
                if not any(c.isalpha() for c in raw[oa:ob]):
                    residue(cursor, rb, 'orphan_reference_continuation', sid)
                    cursor = rb
                    continue
                flags = ['literal_candidate_not_certified'] + group['flags']
                owner = raw[oa:ob]
                if re.search(r'\d', owner):
                    flags.append('owner_contains_unparsed_numeric_material')
                if re.search(r'\b[IVXLCDM]+\s*,\s*\d', owner):
                    flags.append('owner_contains_unsupported_pair')
                if re.search(r'-\s*\n', owner):
                    flags.append('literal_soft_wrap_preserved')
                entry = {'section_id': sid, 'source': source, 'volume': volume, 'physical': physical,
                         'entry_span': [oa, rb], 'owner_span': [oa, ob], 'owner_raw': owner,
                         'reference_span': [ra, rb], 'reference_raw': raw[ra:rb], 'reference_namespace': namespace,
                         'entry_kind': ctx.get('entry_kind', 'lexical_entry'), 'grammar': 'roman_arabic_pairs',
                         'locators': group['locators'], 'flags': flags}
                separator = raw[cursor:oa]
                clause_continuation = ('\n' not in separator or ',' in separator or ';' in separator)
                if cursor > left and clause_continuation and owner[:1].islower() and ctx.get('previous_owner'):
                    entry['inherited_owner_evidence'] = copy.deepcopy(ctx['previous_owner'])
                    flags.append('subject_inheritance_candidate')
                else:
                    ctx['previous_owner'] = {'source': source, 'span': [oa, ob], 'raw': owner}
                if 'ibid_previous_literal_reference' in flags and ctx.get('previous_reference'):
                    entry['inherited_reference_evidence'] = copy.deepcopy(ctx['previous_reference'])
                if group['last']:
                    ctx['previous_reference'] = group['last']
                result['entries'].append(entry)
                residue(cursor, oa, 'clause_separator', sid)
                cursor = rb
            residue(cursor, right, 'unparsed_tail_or_missing_roman', sid)
    if not source_blocks:
        for a, b, sid in intervals:
            residue(a, b, 'unsupported_markup', sid)
    return result
