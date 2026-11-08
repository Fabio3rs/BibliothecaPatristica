"""Bounded indices of rules and narrative sections; preserve literal fragments."""
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.epistle_index_locators import roman_to_int
from tools.indexing.internal_index_formats import greek_number, language, trim

VERSION = 'ordinal-index-formats-v2'
PRIME = "'΄ʹʹ’᾽᾿′"
RULE = re.compile(rf"(?m)^[ \t]*(?:(?P<arabic>\d{{1,3}})[.]?[ \t]+|(?P<greek>[Α-Ωα-ωϚϛϟ]{{1,4}})[{PRIME}][.]?[ \t]+)")
SECTION = re.compile(r'(?m)^[ \t]*(?:[-–—][ \t]+)?(?P<roman>[IVXLCDM]+)[.][ \t\n]+')
SECTION_INDEX = re.compile(r'(?m)^[ \t]*(?:[-–—][ \t]+)?(?P<roman>[IVXLCDM]+(?:[.][ \t]+[IVXLCDM]+)*)[.][ \t\n]+')
SUMMARY_START = re.compile(r'^(?:De\b|Quod\b|Quis\b|Quomodo\b|Qua\b|Cujusmodi\b|Περὶ\b|Ὅτι\b|Τί\b|Πῶς\b|Ποταπο)')


def parse_ordinal_page(raw, *, family, source, volume, physical, scopes, section_context=None):
    pattern = RULE if family == 'rule_number' else SECTION_INDEX
    entries, residuals = [], []
    for scope in scopes:
        lo, hi = scope['span']
        previous = {}
        for block in blocks(raw):
            a, b = max(lo, block['body_start']), min(hi, block['body_end'])
            if a >= b or block['kind'] not in {'texto_principal', 'cabecalho'}:
                continue
            text = raw[a:b]
            lang = language(raw, block)
            matches = list(pattern.finditer(text))
            if block['kind'] == 'cabecalho':
                matches = [m for m in matches if SUMMARY_START.match(text[m.end():].lstrip())]

            def emit(oa, ob, ra=None, rb=None, numbers=None):
                oa, ob = trim(raw, oa, ob)
                if oa >= ob:
                    return
                entry = {'source': source, 'volume': volume, 'physical': physical,
                         'section_id': scope['section_id'], 'entry_kind': 'section_summary',
                         'entry_span': [ra if ra is not None else oa, ob],
                         'owner_span': [oa, ob], 'owner_raw': raw[oa:ob],
                         'owner_fragments': [{'source': source, 'span': [oa, ob], 'raw': raw[oa:ob]}],
                         'reference_span': [ra, rb] if ra is not None else None,
                         'reference_raw': raw[ra:rb] if ra is not None else None,
                         'reference_namespace': family, 'grammar': family, 'language': lang,
                         'internal_binding': {'division': family, 'numbers': numbers or []},
                         'flags': ['literal_candidate_not_certified'], 'parser': VERSION}
                if not numbers:
                    entry['flags'].append('ordinal_absent_in_ocr' if ra is None else 'ordinal_unparseable_in_ocr')
                if numbers and len(numbers) > 1:
                    entry['flags'].append('shared_summary_multiple_ordinals')
                if entry['owner_raw'].endswith('-'):
                    entry['flags'].append('possible_continuation')
                entries.append(entry)
                previous[lang] = entry

            leading_end = a + matches[0].start() if matches else b
            pa, pb = trim(raw, a, leading_end)
            prev = previous.get(lang)
            joined = False
            adjacent = prev and not re.sub(r'<[^>]+>', '', raw[prev['owner_fragments'][-1]['span'][1]:pa]).strip()
            if pa < pb and adjacent and prev['owner_fragments'][-1]['raw'].endswith('-') and raw[pa].islower():
                fragment = {'source': source, 'span': [pa, pb], 'raw': raw[pa:pb]}
                prev['owner_fragments'].append(fragment)
                prev['entry_span'][1] = pb
                prev['search_label'] = '-\n'.join(f['raw'].rstrip('-') for f in prev['owner_fragments'])
                prev['search_label'] = re.sub(r'-\s*\n\s*(?=\w)', '', prev['search_label'])
                prev['flags'] = [f for f in prev['flags'] if f != 'possible_continuation']
                prev['flags'].append('mechanical_soft_wrap_between_blocks')
                joined = True
            if not matches and not joined and family == 'rule_number' and block['kind'] == 'texto_principal':
                lines = list(re.finditer(r'[^\n]+', text))
                nonempty = [m for m in lines if m[0].strip()]
                if len(nonempty) >= 3 and all(SUMMARY_START.match(m[0].strip()) for m in nonempty):
                    for m in nonempty:
                        emit(a + m.start(), a + m.end())
                    continue
            if pa < pb and not joined:
                residuals.append({'span': [pa, pb], 'raw': raw[pa:pb],
                                  'reason': 'unparsed_leading_or_continuation', 'section_id': scope['section_id']})
            for i, match in enumerate(matches):
                token = next(value for value in match.groupdict().values() if value is not None)
                if family == 'section_number':
                    parsed = [roman_to_int(t) for t in re.findall(r'[IVXLCDM]+', token)]
                    numbers = parsed if all(parsed) else []
                else:
                    number = int(token) if token.isdecimal() else greek_number(token)
                    # An elided Greek word such as δι' is not a malformed ordinal.
                    if not number:
                        continue
                    numbers = [number] if number else []
                start = a + match.end()
                end = a + (matches[i+1].start() if i+1 < len(matches) else len(text))
                group = next(key for key, value in match.groupdict().items() if value is not None)
                emit(start, end, a + match.start(group), a + match.end(group), numbers)
    return {'version': VERSION, 'entries': entries, 'residuals': residuals, 'context': section_context or {}}


def reconcile_bounded_ordinals(entries):
    """Propose missing ordinals only when both bounds and row count agree.

    Printed evidence remains unchanged. No interpolation across section/language
    boundaries, and no invented reference_raw. Returns audit events.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    audit = []
    for entry in entries:
        if entry.get('grammar') in {'rule_number', 'section_number'}:
            groups[(entry['section_id'],entry['language'])].append(entry)
    for group in groups.values():
        group.sort(key=lambda e:(e['physical'],e['entry_span'][0]))
        i = 0
        while i < len(group):
            if group[i]['internal_binding']['numbers']:
                i += 1
                continue
            start = i
            while i < len(group) and not group[i]['internal_binding']['numbers']:
                i += 1
            if start == 0 or i == len(group):
                continue
            left,right = group[start-1],group[i]
            ln,rn = left['internal_binding']['numbers'],right['internal_binding']['numbers']
            if len(ln)!=1 or len(rn)!=1 or rn[0]-ln[0]!=i-start+1:
                continue
            evidence = [{'source':e['source'],'span':e['reference_span'],'raw':e['reference_raw']} for e in (left,right)]
            for offset,entry in enumerate(group[start:i],1):
                entry['internal_binding'].update(numbers=[ln[0]+offset],inferred=True,
                    inference='bounded_row_count',evidence=evidence)
                entry['flags'].append('ordinal_inferred_from_bounded_sequence')
                audit.append({'source':entry['source'],'span':entry['owner_span'],
                              'number':ln[0]+offset,'reason':'bounded_row_count','evidence':evidence})
        for before,entry,after in zip(group,group[1:],group[2:]):
            a,b,c = (e['internal_binding']['numbers'] for e in (before,entry,after))
            if len(a)==len(b)==len(c)==1 and c[0]==a[0]+2 and b[0]!=a[0]+1:
                entry['internal_binding']['alternative_numbers']=[a[0]+1]
                entry['internal_binding']['alternative_evidence']=[
                    {'source':e['source'],'span':e['reference_span'],'raw':e['reference_raw']} for e in (before,after)]
                entry['flags'].append('ordinal_conflicts_with_neighbors')
                audit.append({'source':entry['source'],'span':entry['reference_span'],
                              'literal_number':b[0],'alternative':a[0]+1,'reason':'ordinal_conflicts_with_neighbors'})
    return audit
