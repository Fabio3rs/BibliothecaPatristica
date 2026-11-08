"""Recover a literal Roman locator prefix accidentally included in its owner.

This is a pure, occurrence-local parser. It returns hypotheses with character
offsets into the supplied OCR, never edition/volume identities or destinations.
"""
import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'roman-owner-context-v1'
SUFFIX = re.compile(r'\b(?P<roman>[IVXLCDM]+)\s*[,;.]?\s*$')
ROMAN = re.compile(r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\Z')


def _whitespace_pattern(text):
    return r'\s+'.join(re.escape(token) for token in text.split())


def repair_roman_owner(raw, group):
    """Return a source-backed proposal, or an explicit reason for abstention.

    ``restored_candidate`` only asserts literal context recovery. Numeric owner
    residue, personal-name numerals and missing separators retain both original
    and expanded hypotheses as ``ambiguous_roman_owner``. All spans are absolute
    Unicode character offsets; None means no source-backed proposal was possible.
    """
    owner = group.get('owner_raw', '')
    reference = group.get('raw', '')
    result = {
        'version': VERSION, 'group_id': group.get('id'),
        'source': group.get('source'), 'status': 'not_applicable',
        'original_owner_raw': owner, 'original_reference_raw': reference,
        'corrected_owner_raw': None, 'expanded_reference_raw': None,
        'roman_raw': None, 'original_reference_span': None,
        'owner_span': None, 'corrected_owner_span': None,
        'expanded_reference_span': None, 'warnings': [],
        'namespace_status': 'uninterpreted', 'hypotheses': [],
    }
    suffix = SUFFIX.search(owner)
    if not suffix or not ROMAN.fullmatch(suffix['roman']):
        result['warnings'].append('no_valid_roman_owner_suffix')
        return result
    result['roman_raw'] = suffix['roman']
    a, b = group.get('char_start'), group.get('char_end')
    if not (isinstance(a, int) and isinstance(b, int) and
            0 <= a < b <= len(raw) and raw[a:b] == reference):
        result.update(status='evidence_mismatch')
        result['warnings'].append('reference_span_does_not_match_current_ocr')
        return result
    result['original_reference_span'] = [a, b]
    if not re.match(r'\d', reference):
        result['warnings'].append('reference_does_not_begin_with_arabic_number')
        return result
    regions = blocks(raw)
    if not regions and '<' not in raw and '[bloco' not in raw:
        regions = [{'body_start': 0, 'body_end': len(raw)}]
    scope = next((r for r in regions if r['body_start'] <= a < b <= r['body_end']), None)
    if scope is None:
        result['status'] = 'evidence_mismatch'
        result['warnings'].append('reference_not_inside_known_ocr_block')
        return result
    # Match the complete owner immediately before this occurrence. A repeated
    # owner elsewhere on the page must never supply this reference's context.
    prefix = owner[:suffix.start()].rstrip()
    if not prefix:
        result['warnings'].append('owner_without_lexical_text')
        return result
    owner_gap = r'\s+' if owner[suffix.start()-1].isspace() else ''
    tail = owner[suffix.end('roman'):].strip()
    tail_pattern = r'\s*' + re.escape(tail) if tail else ''
    pattern = (r'(?<!\w)(?P<owner>' + _whitespace_pattern(prefix) + owner_gap
               + r'(?P<roman>' + re.escape(suffix['roman']) + r')' + tail_pattern
               + r')(?P<separator>\s*' + ('' if tail else r'[,;.]?') + r'\s*)\Z')
    preceding = raw[scope['body_start']:a]
    match = re.search(pattern, preceding)
    if not match:
        result['status'] = 'evidence_mismatch'
        result['warnings'].append('owner_not_contiguous_in_same_ocr_block')
        return result
    start = scope['body_start'] + match.start()
    roman_start = scope['body_start'] + match.start('roman')
    owner_end = scope['body_start'] + match.end('owner')
    corrected_end = roman_start
    while corrected_end > start and raw[corrected_end-1] in ' \t\r\n,;':
        corrected_end -= 1
    corrected = raw[start:corrected_end]
    if not any(c.isalpha() for c in corrected):
        result['warnings'].append('owner_without_lexical_text')
        return result
    result.update(
        status='restored_candidate', corrected_owner_raw=corrected,
        expanded_reference_raw=raw[roman_start:b], owner_span=[start, owner_end],
        corrected_owner_span=[start, corrected_end],
        expanded_reference_span=[roman_start, b],
        source_sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
    )
    warnings = result['warnings']
    if not prefix.endswith((',', ';')):
        warnings.append('roman_may_belong_to_owner_missing_left_separator')
    if not re.search(r'[,;.]', tail + match['separator']):
        warnings.append('roman_to_number_separator_missing')
    if re.search(r'\d', corrected):
        warnings.append('owner_contains_previous_numeric_references')
    if re.search(r'\n\s*\n', corrected):
        warnings.append('owner_crosses_paragraph_boundary')
    if warnings:
        result['status'] = 'ambiguous_roman_owner'
    if group.get('source_sha256') and group['source_sha256'] != result['source_sha256']:
        warnings.append('source_changed_but_local_spans_match')
    result['hypotheses'] = [
        {'kind': 'original', 'owner_raw': owner, 'reference_raw': reference,
         'reference_span': [a, b]},
        {'kind': 'roman_prefixed_locator', 'owner_raw': corrected,
         'reference_raw': raw[roman_start:b], 'reference_span': [roman_start, b],
         'namespace_status': 'uninterpreted'},
    ]
    return result
