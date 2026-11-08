"""Conservative interpretations of full literal entries, scoped by explicit profiles."""
import re

from tools.indexing.entry_parser_experiment import display

TOME_ITEM = re.compile(r'(?:(?P<tome>II|I),\s*)?(?P<page>[0-9]{1,4})(?:\s*[-–]\s*(?P<end>[0-9]{1,4}))?')


def explicit_tome_list(entry):
    """An explicit initial tome scopes only this uninterrupted numeric suffix.

    No inheritance from another entry/line of prose, and no ibid/seq expansion.
    Every printed number or closed range remains one reference occurrence.
    """
    raw = entry['entry_raw']
    match = re.fullmatch(r'(?P<head>[^0-9]+?)\s+(?P<refs>(?:II|I),\s*.+)\.', raw, re.S)
    if not match:
        return None
    head = match['head'].rstrip(' ,.')
    if not head or re.search(r'[.;:]', head):
        return None
    suffix, refs, pos, tome, tome_span = match['refs'], [], 0, None, None
    while pos < len(suffix):
        token = TOME_ITEM.match(suffix, pos)
        if token is None:
            return None
        base = entry['char_start'] + match.start('refs')
        if token['tome']:
            tome = token['tome']
            tome_span = [base+token.start('tome'), base+token.end('tome')]
        if tome is None:
            return None
        page = int(token['page'])
        end = int(token['end']) if token['end'] else None
        if page < 1 or (end is not None and end < page):
            return None
        refs.append({'number': page, 'range_end': end, 'series_key': tome, 'tome': tome,
                     'reference_kind': 'editorial_range' if end is not None else 'editorial_page',
                     'ref_raw': token[0], 'source_span': [base+token.start(),base+token.end()],
                     'tome_basis': 'explicit' if token['tome'] else 'same_entry_numeric_list',
                     'tome_source_span': tome_span})
        pos = token.end()
        if pos == len(suffix):
            break
        separator = re.match(r'\s*[,;]\s*', suffix[pos:])
        if not separator:
            return None
        pos += separator.end()
        if pos == len(suffix):
            return None
    return {'query_text': display(head), 'interpreted_references': refs,
            'segmentation_status': 'experimental_literal_not_certified'}


def interpret(entry, notation):
    raw = entry['entry_raw']
    flags = set(entry.get('flags', []))
    if entry.get('entry_type') != 'material':
        return None
    if notation == 'po_page_parts':
        allowed = {'experimental_po_profile', 'subscript_semantics_unverified', 'open_subscript_range'}
        parts = entry.get('locator_parts') or []
        if flags - allowed or not parts:
            return None
        refs = []
        for part in parts:
            page = part.get('page_raw') or ''
            if (part['kind'] not in {'page', 'page_note', 'page_subscript'}
                    or not re.fullmatch(r'[0-9]{1,4}', page) or int(page) == 0):
                return None
            start, end = part['char_start'] - entry['char_start'], part['char_end'] - entry['char_start']
            if not 0 <= start < end <= len(raw) or raw[start:end] != part['raw']:
                raise ValueError('PO locator part span differs from literal entry')
            refs.append({'number': int(page), 'series_key': 'bracketed', 'ref_raw': part['raw'],
                         'source_span': [part['char_start'], part['char_end']],
                         'subscript_raw': part.get('subscript_raw'), 'note_raw': part.get('note_raw'),
                         'qualifier_raw': part.get('qualifier_raw'), 'subscript_interpretation': 'unverified'})
        return {'query_text': entry['text_display'], 'interpreted_references': refs}
    allowed = {'unvalidated_profile', 'numeric_text_before_reference_group', 'unsupported_editorial_namespace'}
    if notation == 'explicit_tome_list':
        # Only a full successful parse can accept a closed range. Other
        # qualifiers (ibid, seq, etc.) still fail the suffix grammar.
        allowed.add('qualified_reference_preserved')
    if flags - allowed:
        return None
    if notation == 'explicit_tome_list':
        return explicit_tome_list(entry)
    if notation == 'explicit_tome_page':
        match = re.fullmatch(r'(?P<head>[^0-9]+?)\s+(?P<ref>(?P<tome>II|I),\s*(?P<page>[0-9]{1,4}))\s*\.', raw)
        if not match:
            return None
        head = match['head'].rstrip(' ,.')
        if not head or re.search(r'[.;:]', head):
            return None
        refs = [{'number': int(match['page']), 'series_key': match['tome'], 'tome': match['tome'],
                 'ref_raw': match['ref'], 'source_span': [entry['char_start']+match.start('ref'),
                                                       entry['char_start']+match.end('ref')]}]
    elif notation == 'page_line_leo_simple':
        pair = r'(?:Leo\s+)?[0-9]{1,4},\s*[0-9]{1,3}'
        match = re.fullmatch(r'(?P<head>[^0-9.;]+?)\s+(?P<refs>'+pair+r'(?:,\s*Leo\s+[0-9]{1,4},\s*[0-9]{1,3})?)\.', raw)
        if not match:
            return None
        head = match['head'].rstrip(' ,')
        if re.search(r'\bLeo\b', head):
            return None
        refs = []
        for loc in re.finditer(r'(?P<leo>Leo\s+)?(?P<page>[0-9]+),\s*(?P<line>[0-9]+)', match['refs']):
            a = entry['char_start'] + match.start('refs') + loc.start()
            refs.append({'number': int(loc['page']), 'line_raw': loc['line'],
                         'series_key': 'leo' if loc['leo'] else 'petropolitana',
                         'ref_raw': loc[0], 'source_span': [a, a+len(loc[0])],
                         'line_location_status': 'not_located'})
    else:
        raise ValueError(f'Unsupported notation: {notation}')
    if not refs or any(r['number'] == 0 for r in refs):
        return None
    return {'query_text': display(head), 'interpreted_references': refs,
            'segmentation_status': 'experimental_literal_not_certified'}
