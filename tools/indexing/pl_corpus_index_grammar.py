"""Literal PL corpus author rows: biography/date + tome/column references.

Only a caller-selected section with an explicit numbering notice enables this
parser. A date is never a column, a cross-reference is never a physical page,
and inherited locators keep their literal antecedent. No source/DB writes.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import defaultdict

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.roman_pair_index_grammar import roman_value

VERSION = 'pl-corpus-author-rows-v1'
ROMAN = r'[IVXLCDM]+'
PAIR = re.compile(rf'(?<![\w])(?P<tome>{ROMAN})(?:\s*[,.;]\s*|\s+)(?P<column>\d{{1,4}})(?!\d)')
RANGE = re.compile(rf'(?<!\w)(?P<first>{ROMAN})\s*[-–—]\s*(?P<last>{ROMAN})(?!\w)')
CONT = re.compile(r'\s*(?:[,;.]|\bet\b)\s*(?P<number>\d{1,4})(?!\d)')
VIDE = re.compile(r'\bvide\s+[^\n]+', re.I)
IBID = re.compile(r'\bibid\.(?:\s*,\s*(?P<column>\d{1,4}))?', re.I)
NAME_START = re.compile(r'^(?:[?]\s*)?[A-ZÀ-ÝÆŒ][a-zà-ÿæœ]+')
NOTICE = re.compile(r'^(?:III\.|INDEX\b|OMNIUM\b|SCRIPTAQUE\b|NOTA\.|MONITUM\.|Hoc in Indice\b)', re.I)
DATE = re.compile(r'(?:[,.;]\s*|\s+)(?P<date>(?:(?:circa|an\.?|anno)\s*)?\d{2,4}|(?:s[æae]+c\.?|sec\.?|ss\.)\s*[ivxlcdmIVXLCDM]+|an\.?\s+inc(?:ert)?\.?)\s*[,.;]?\s*$')


def trim(raw, a, b):
    while a < b and raw[a].isspace():
        a += 1
    while b > a and raw[b - 1].isspace():
        b -= 1
    return a, b


def references(text, *, offset, source):
    """Read explicit pairs/ranges and Arabic lists, preserving invalid numerals."""
    locators = []
    occupied = []
    for match in PAIR.finditer(text):
        value = roman_value(match['tome'])
        if value is None:
            continue
        # A capitalized century followed by a date is biographical material.
        if re.search(r'(?:s[æae]+c|sec|ss)\.?\s*$', text[max(0, match.start()-12):match.start()], re.I):
            continue
        atom = {'tome_raw': match['tome'], 'tome_value': value,
                'roman_raw': match['tome'], 'roman_value': value,
                'column_raw': match['column'], 'column_value': int(match['column']),
                'arabic_raw': match['column'], 'arabic_value': int(match['column']),
                'source': source, 'span': [offset+match.start(), offset+match.end()], 'raw': match[0]}
        prefix = text[:match.start()]
        fragmented = bool(re.search(r'(?:\b[IVXLCDM]+[.,]?\s*|[(]\s*)$', prefix))
        if re.search(r'(?:s[æae]+c|sec|ss)\.?\s*[IVXLCDM]+[,.;]?\s*$', prefix, re.I):
            fragmented = False
        preface = bool(re.search(r'\b(?:Præfat|pref)\.?\s*$', prefix, re.I))
        if fragmented or preface:
            atom['uncertainty'] = 'preceding_roman_fragment_or_preface_namespace'
        elif 1 <= value <= 217:
            atom['target_volume'] = f'PL{value:03d}'
        else:
            atom['uncertainty'] = 'tome_outside_printed_corpus'
        locators.append(atom)
        end = match.end()
        while continuation := CONT.match(text, end):
            child = {k: copy.deepcopy(v) for k, v in atom.items() if k not in ('raw', 'span', 'column_raw', 'column_value', 'arabic_raw', 'arabic_value')}
            child.update(raw=continuation['number'], span=[offset+continuation.start('number'), offset+continuation.end('number')],
                         column_raw=continuation['number'], column_value=int(continuation['number']),
                         arabic_raw=continuation['number'], arabic_value=int(continuation['number']),
                         inherited_tome_evidence={'source': source, 'span': atom['span'], 'raw': atom['raw']})
            locators.append(child)
            end = continuation.end()
        occupied.append((match.start(), end))
    for match in RANGE.finditer(text):
        lo, hi = roman_value(match['first']), roman_value(match['last'])
        if lo is None or hi is None:
            continue
        atom = {'raw': match[0], 'source': source, 'span': [offset+match.start(), offset+match.end()],
                'relation': 'tome_range_without_column', 'tome_start': lo, 'tome_end': hi}
        if not 1 <= lo <= hi <= 217:
            atom['uncertainty'] = 'invalid_corpus_tome_range'
        locators.append(atom)
    for match in IBID.finditer(text):
        atom = {'raw': match[0], 'source': source, 'span': [offset+match.start(), offset+match.end()], 'relation': 'ibid'}
        preceding = [a for a in locators if a['span'][1] <= offset+match.start() and a.get('tome_value')]
        if preceding:
            prior = max(preceding, key=lambda a: a['span'][1])
            for key in ('tome_value', 'roman_value', 'target_volume'):
                if key in prior:
                    atom[key] = prior[key]
            atom['column_value'] = int(match['column']) if match['column'] else prior['column_value']
            atom['arabic_value'] = atom['column_value']
            atom['inherited_reference_evidence'] = {k: prior[k] for k in ('source', 'span', 'raw')}
        else:
            atom['uncertainty'] = 'ibid_without_same_row_antecedent'
        locators.append(atom)
    return sorted(locators, key=lambda a: a['span'])


def parse_region_page(raw, *, source, volume, physical, scopes, section_context=None):
    provided = section_context or {}
    context = {k: copy.deepcopy(v) for k, v in provided.items() if isinstance(v, dict)}
    intervals = sorted((s['span'][0], s['span'][1], str(s.get('section_id', ''))) for s in scopes)
    if provided and not context:
        context = {sid: copy.deepcopy(provided) for _, _, sid in intervals}
    for i, (a, b, _) in enumerate(intervals):
        if not 0 <= a < b <= len(raw) or (i and a < intervals[i-1][1]):
            raise ValueError('Invalid or overlapping scopes')
    result = {'version': VERSION, 'source': source, 'volume': volume, 'physical': physical,
              'source_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'entries': [], 'residuals': [], 'context': context}
    source_blocks = blocks(raw)
    if not source_blocks and not re.search(r'</?\w+', raw):
        source_blocks = [{'body_start': 0, 'body_end': len(raw), 'kind': 'texto_principal'}]

    def residue(a, b, why, sid):
        a, b = trim(raw, a, b)
        if a < b:
            result['residuals'].append({'span': [a, b], 'raw': raw[a:b], 'reason': why, 'section_id': sid})

    def row(a, b, sid, kind):
        a, b = trim(raw, a, b)
        text = raw[a:b]
        locs = references(text, offset=a, source=source)
        cross = VIDE.search(text)
        if not locs and not cross:
            residue(a, b, 'unparsed_author_row_or_fragment', sid)
            return
        ra = min([x['span'][0] for x in locs] + ([a+cross.start()] if cross else []))
        oa, ob = trim(raw, a, ra)
        while ob > oa and raw[ob-1] in ',; .':
            ob -= 1
        date = DATE.search(raw[oa:ra])
        date_evidence = None
        if date:
            da, db = oa+date.start('date'), oa+date.end('date')
            date_evidence = {'raw': raw[da:db], 'span': [da, db], 'source': source}
            ob = oa+date.start()
            oa, ob = trim(raw, oa, ob)
        if not NAME_START.match(raw[oa:ob]):
            residue(a, b, 'orphan_reference_or_missing_name', sid)
            return
        flags = ['literal_candidate_not_certified']
        if kind != 'texto_principal':
            flags.append('index_row_in_misclassified_ocr_header')
        if any(x.get('uncertainty') for x in locs):
            flags.append('uncertain_printed_locator')
        if re.search(r'\d', raw[oa:ob]):
            flags.append('owner_contains_unparsed_biographical_numbers')
        if re.search(r'-\s*\n', text):
            flags.append('literal_soft_wrap_preserved')
        if any(x.get('relation') == 'tome_range_without_column' for x in locs):
            flags.append('tome_range_without_column')
        item = {'section_id': sid, 'source': source, 'volume': volume, 'physical': physical,
                'entry_span': [a, b], 'owner_span': [oa, ob], 'owner_raw': raw[oa:ob],
                'reference_span': [ra, b], 'reference_raw': raw[ra:b], 'reference_namespace': 'pl_corpus_tome_column',
                'entry_kind': 'author_entry', 'grammar': 'pl_corpus_author_date_tome_column',
                'locators': locs, 'flags': flags}
        if date_evidence:
            item['biographical_date_evidence'] = date_evidence
        if cross:
            item['textual_cross_reference'] = {'raw': cross[0], 'source': source, 'span': [a+cross.start(), a+cross.end()]}
            flags.append('textual_cross_reference_not_physical_locator')
        result['entries'].append(item)

    for a, b, sid in intervals:
        ctx = context.get(sid, {})
        if ctx.get('family') != 'pl_corpus_author_date_tome_column' or ctx.get('reference_namespace') != 'pl_corpus_tome_column':
            residue(a, b, 'requires_reviewed_author_index_notice', sid)
            continue
        for block in source_blocks:
            lo, hi = max(a, block['body_start']), min(b, block['body_end'])
            if lo >= hi:
                continue
            if block['kind'] not in ('texto_principal', 'cabecalho'):
                residue(lo, hi, 'editorial_nonbody', sid)
                continue
            text = raw[lo:hi]
            if NOTICE.match(text.strip()):
                residue(lo, hi, 'heading_or_notice', sid)
                continue
            start = None
            for line in re.finditer(r'[^\n]+', text):
                la, lb = trim(raw, lo+line.start(), lo+line.end())
                if la == lb:
                    continue
                content = raw[la:lb]
                if re.fullmatch(r'[A-ZÆŒ]|\d{1,4}', content):
                    if start is not None:
                        row(start, la, sid, block['kind']); start = None
                    residue(la, lb, 'alphabet_divider_or_running_number', sid)
                    continue
                new_name = NAME_START.match(content) and not re.match(r'^(?:Vide|Apud|Idem)\b', content)
                if start is not None and new_name:
                    previous = raw[start:la].rstrip()
                    complete = bool(references(previous, offset=start, source=source) or VIDE.search(previous))
                    damaged_end = previous.endswith('.') and bool(re.search(r'\d', previous))
                    if (complete or damaged_end) and not previous.endswith('-'):
                        row(start, la, sid, block['kind']); start = la
                if start is None:
                    start = la
            if start is not None:
                row(start, hi, sid, block['kind'])
    # Keep every source occurrence but expose duplicate OCR passages explicitly.
    signatures = defaultdict(list)
    for entry in result['entries']:
        signature = re.sub(r'\s+', ' ', entry['owner_raw']+' '+entry['reference_raw']).casefold()
        signatures[signature].append(entry)
    for entries in signatures.values():
        if len(entries) > 1:
            for entry in entries:
                entry['flags'].append('repeated_literal_ocr_row')
                entry['duplicate_occurrence_spans'] = [x['entry_span'] for x in entries if x is not entry]
    return result
