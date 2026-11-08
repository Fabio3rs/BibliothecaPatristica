"""Propose literal recovery of numeric list context left in an index owner.

All offsets refer to the supplied OCR. A candidate is an alternative reading,
never certification of ownership, reference units, or a destination.
"""
import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'numeric-owner-context-v1'
SUFFIX = re.compile(r'(?<!\w)(?P<numbers>[0-9]+(?:\s*[,;]\s*[0-9]+)*)\s*[,;.]?\s*$')
# These are abstention cues, not a dictionary assigning reference namespaces.
PROSE_NUMBER = re.compile(
    r'\b(?:anno|annis|annum|annos|annorum|ann[ée]e|year|years|'
    r'ætat\w*|aetat\w*|die|dies|mens\w*|circiter|circa|ante|post|'
    r'cap\w*|chap\w*|vers\w*|psalm\w*|psaut\w*|lib\w*|livre\w*|'
    r'genesis|genèse|exod\w*|numeror\w*|deuter\w*|epist\w*|'
    r'titl\w*|tract\w*|serm\w*|homil\w*|oration\w*|tom\w*|vol\w*|'
    r'letter\w*|section\w*|part\w*|version\w*|edition\w*|ψαλμ\w*|κεφ\w*|'
    r'king|roi|rex|papa|pope|henry|louis|ludovic\w*|henric\w*)\b', re.I)


def _whitespace_pattern(text):
    return r'\s+'.join(re.escape(token) for token in text.split())


def repair_numeric_owner(raw, group):
    """Return exact original/expanded hypotheses and conservative abstentions.

    Recovery needs a literal contiguous owner and comma/semicolon continuation
    inside one OCR block. Existing numbers, title/prose cues, paragraph breaks,
    and small unseparated numbers retain an ambiguous proposal. No corpus reads
    or mutations occur here. Dotted leaders are located but never repaired.
    """
    owner = group.get('owner_raw', '')
    reference = group.get('raw', '')
    result = {
        'version': VERSION, 'group_id': group.get('id'),
        'source': group.get('source'), 'status': 'not_applicable',
        'original_owner_raw': owner, 'original_reference_raw': reference,
        'original_source_sha256': group.get('source_sha256'),
        'corrected_owner_raw': None, 'expanded_reference_raw': None,
        'prefix_reference_raw': None, 'original_reference_span': None,
        'owner_span': None, 'corrected_owner_span': None,
        'expanded_reference_span': None, 'prefix_reference_span': None,
        'warnings': [], 'namespace_status': 'uninterpreted', 'hypotheses': [],
    }
    suffix = SUFFIX.search(owner)
    if not suffix:
        result['warnings'].append('no_separate_numeric_owner_suffix')
        return result
    a, b = group.get('char_start'), group.get('char_end')
    if not (isinstance(a, int) and not isinstance(a, bool) and
            isinstance(b, int) and not isinstance(b, bool) and
            0 <= a < b <= len(raw) and raw[a:b] == reference):
        result['status'] = 'evidence_mismatch'
        result['warnings'].append('reference_span_does_not_match_current_ocr')
        return result
    result['original_reference_span'] = [a, b]
    if not re.match(r'[0-9]', reference):
        result['warnings'].append('reference_does_not_begin_with_arabic_number')
        return result
    prefix = owner[:suffix.start()].rstrip()
    if not any(c.isalpha() for c in prefix):
        result['warnings'].append('owner_without_lexical_text')
        return result
    regions = blocks(raw)
    if not regions and '<' not in raw and '[bloco' not in raw:
        regions = [{'body_start': 0, 'body_end': len(raw)}]
    scope = next((r for r in regions if r['body_start'] <= a < b <= r['body_end']), None)
    if scope is None:
        result['status'] = 'evidence_mismatch'
        result['warnings'].append('reference_not_inside_known_ocr_block')
        return result
    gap = r'\s+' if owner[suffix.start()-1].isspace() else ''
    tail = owner[suffix.end('numbers'):].strip()
    tail_pattern = r'\s*' + re.escape(tail) if tail else ''
    pattern = (r'(?<!\w)(?P<owner>' + _whitespace_pattern(prefix) + gap +
               r'(?P<numbers>' + _whitespace_pattern(suffix['numbers']) + r')' +
               tail_pattern +
               r')(?P<separator>[\s,;.]*?)\Z')
    match = re.search(pattern, raw[scope['body_start']:a])
    if not match:
        result['status'] = 'evidence_mismatch'
        result['warnings'].append('owner_not_contiguous_in_same_ocr_block')
        return result
    start = scope['body_start'] + match.start()
    number_start = scope['body_start'] + match.start('numbers')
    owner_end = scope['body_start'] + match.end('owner')
    corrected_end = number_start
    while corrected_end > start and raw[corrected_end-1] in ' \t\r\n,;':
        corrected_end -= 1
    corrected = raw[start:corrected_end]
    result.update(
        status='restored_candidate', corrected_owner_raw=corrected,
        expanded_reference_raw=raw[number_start:b],
        prefix_reference_raw=raw[number_start:a],
        owner_span=[start, owner_end], corrected_owner_span=[start, corrected_end],
        expanded_reference_span=[number_start, b], prefix_reference_span=[number_start, a],
        source_sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
    )
    warnings = result['warnings']
    separator = raw[scope['body_start'] + match.end('numbers'):a]
    if not re.fullmatch(r'\s*[,;]\s*', separator):
        warnings.append('numeric_list_continuation_separator_missing')
    if re.search(r'[0-9]', corrected):
        warnings.append('owner_contains_previous_numbers')
    if re.search(r'\n\s*\n', raw[start:b]):
        warnings.append('context_crosses_paragraph_boundary')
    if PROSE_NUMBER.search(corrected) or re.search(r'\b\w+\.\s*$', corrected):
        warnings.append('owner_has_prose_or_title_number_cue')
    if re.fullmatch(r'(?:p|pp|pag|ibid|id|et|v|vv)\.?', corrected, re.I):
        warnings.append('owner_is_only_reference_marker')
    if re.search(r'\[ilegivel\]|\[illegible\]', corrected, re.I):
        warnings.append('owner_contains_illegible_text')
    if re.search(r'(?:\b[IVXLCDM]+|[§:—–-])\s*[,;]?\s*$', corrected):
        warnings.append('owner_has_structural_number_cue')
    left_separator = prefix.endswith((',', ';'))
    if not left_separator and not re.search(r'[0-9]\s*[,;]\s*[0-9]', reference):
        warnings.append('unseparated_owner_number_without_long_list')
    if not left_separator and int(suffix['numbers'].split(',')[0].split(';')[0]) <= 32:
        warnings.append('small_unseparated_number_may_belong_to_owner')
    if warnings:
        result['status'] = 'ambiguous_numeric_owner'
    if group.get('source_sha256') and group['source_sha256'] != result['source_sha256']:
        warnings.append('source_changed_but_local_spans_match')
    result['hypotheses'] = [
        {'kind': 'original', 'owner_raw': owner, 'reference_raw': reference,
         'reference_span': [a, b]},
        {'kind': 'numeric_prefixed_locator', 'owner_raw': corrected,
         'reference_raw': raw[number_start:b], 'reference_span': [number_start, b],
         'prefix_reference_span': [number_start, a], 'namespace_status': 'uninterpreted'},
    ]
    return result
