"""Read PG104's printed index notices and regenerate literal index clauses.

The notice identifies a numbering namespace, not a physical page. Sources and
offsets always address unchanged OCR. In particular, 'a/b' is preserved without
asserting that the letters mean columns, pages or sections of a named edition.
"""
import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import source_span
from tools.indexing.literal_target_links import interpret_group
from tools.indexing.missing_latin_region_parser import parse_region_page
from tools.indexing.subdivision_clause_recovery import account_range, candidates

VERSION = 'pg104-opening-grammar-v1'
NOTICE = re.compile(
    r'Revocatur\s+lector\s+ad\s+numeros\s+'
    r'(?P<style>grandiores|crassiores)\s+textui\s+Latino\s+'
    r'(?P<placement>intermistos|insertos)\s*[.]', re.I)
BIBLIOTHECA = re.compile(
    r'INDEX\s+RERUM\s+ET\s+VERBORUM\s+MEMORABILIUM\s+'
    r'QU(?:Æ|AE)\s+IN\s+PHOTII\s+BIBLIOTHECA\s+REPERIUNTUR[.]?', re.I)
SYNTAGMA = re.compile(
    r'(?:INDEX\s+ANALYTICUS\s+)?RERUM\s+NOTABILIORUM\s+'
    r'QU(?:Æ|AE)\s+IN\s+SYNTAGMATE\s+INVENIUNTUR[.]?', re.I)
BOUNDARY = re.compile(r'\bORDO\s+RERUM\b', re.I)
NAMESPACES = {
    'photius_bibliotheca_ab': 'Photius:Bibliotheca:Latin-internal-number-ab',
    'syntagma_internal_numbers': 'PG104:Syntagma:Latin-internal-number',
}


def analyze_opening(raw, source=''):
    """Require a named title plus the actual editorial notice in header/note.

    A running 'INDICES ANALYTICI' header or a prose quotation of the notice does
    not establish a new family. No fuzzy digit repair or edition guess occurs.
    """
    result = {'status': 'not_recognized', 'family': None, 'namespace': None,
              'source': str(source), 'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
              'diagnostics': []}
    editorial = [b for b in blocks(raw) if b['kind'] in {'cabecalho', 'nota', 'outro'}]
    found = []
    notices = []
    for block in editorial:
        a, b = block['body_start'], block['body_end']
        boundary = BOUNDARY.search(raw, a, b)
        if boundary:
            return dict(result, status='boundary', boundary_raw=boundary[0],
                        boundary_span=source_span(raw, *boundary.span()))
        for family, pattern in [('photius_bibliotheca_ab', BIBLIOTHECA),
                                ('syntagma_internal_numbers', SYNTAGMA)]:
            title = pattern.search(raw, a, b)
            if title:
                found.append((family, title))
        notices.extend(NOTICE.finditer(raw, a, b))
    if len(found) != 1 or len(notices) != 1:
        if found or notices:
            result['diagnostics'].append('incomplete_or_ambiguous_opening')
        return result
    family, title = found[0]
    notice = notices[0]
    return dict(result, status='recognized', family=family, namespace=NAMESPACES[family],
                title_raw=title[0], title_span=source_span(raw, *title.span()),
                notice_raw=notice[0], notice_span=source_span(raw, *notice.span()),
                target_language='Latin', number_location='interspersed_in_text',
                edition=None, letter_semantics=None)


def _entry(raw, proposal, namespace, family, section_id):
    group = proposal['reference_groups'][0]
    owner = group['owner_text_raw']
    tokens = interpret_group(group['raw'], group['char_start']) if family == 'photius_bibliotheca_ab' else None
    flags = ['literal_candidate_not_certified', 'internal_number_not_pg_header',
             'owner_is_clause_not_certified_lemma']
    if tokens is None:
        flags.append('notation_interpretation_pending')
    elif any(token['number'] is None for token in tokens):
        flags.append('bare_subdivision_requires_context')
    return {**proposal, 'text_raw': owner, 'owner_raw': owner, 'owner_span': group['owner_span'],
            'references_raw': group['raw'],
            'reference_span': {'char_start': group['char_start'], 'char_end': group['char_end']},
            'entry_type': 'analytic_clause', 'section_id': section_id,
            'reference_namespace': namespace, 'grammar': family, 'flags': flags,
            'reference_tokens': tokens, 'targets': []}


def parse_index_page(raw, source, physical, opening=None):
    """Extract only within an explicitly supplied or locally observed section.

    The caller determines which continuation pages to inspect; this function
    never carries state to an entire volume. A new opening supersedes that
    state, and ORDO RERUM terminates it. Conservation covers OCR block bodies,
    not XML wrappers, image content or completeness of the printed index.
    """
    local = analyze_opening(raw, source)
    context = local if local['status'] in {'recognized', 'boundary'} else (opening or local)
    checksum = hashlib.sha256(raw.encode()).hexdigest()
    page = {'version': VERSION, 'source': str(source), 'volume': 'PG104', 'physical': physical,
            'sha256': checksum, 'source_sha256': checksum, 'opening': context,
            'profile': context.get('family'), 'entries': [], 'residuals': [], 'diagnostics': []}
    family = context.get('family') if context.get('status') == 'recognized' else None
    namespace = context.get('namespace')
    section_id = str(context.get('source', source)) + ':' + str(family)
    source_blocks = blocks(raw)
    if not source_blocks:
        page['residuals'] = [{'raw': raw, 'char_start': 0, 'char_end': len(raw), 'reason': 'unsupported_markup'}]
        page['accounting'] = {'body_characters': len(raw), 'claimed_characters': 0, 'residual_characters': len(raw)}
        return page
    total, claimed = 0, 0
    for block in source_blocks:
        a, b = block['body_start'], block['body_end']
        total += b-a
        added = []
        if block['kind'] == 'texto_principal' and family == 'photius_bibliotheca_ab':
            # An empty physical line is an explicit OCR paragraph boundary.
            # Reset ownership there, but not at single newlines: those often
            # wrap a clause. Otherwise a preceding codex reference can swallow
            # the next independent paragraph's perfectly valid a/b locator.
            proposals, rejected = [], []
            left = a
            for separator in re.finditer(r'\n[ \t]*\n', raw[a:b]):
                right = a + separator.start()
                found, pending = candidates(raw, left, right)
                proposals.extend(found)
                rejected.extend(pending)
                left = a + separator.end()
            found, pending = candidates(raw, left, b)
            proposals.extend(found)
            rejected.extend(pending)
            added = [_entry(raw, p, namespace, family, section_id) for p in proposals]
            # Rejections may overlap conserved residuals; they are diagnostics,
            # not additional character claims in the coverage denominator.
            page['diagnostics'].extend(rejected)
        elif block['kind'] == 'texto_principal' and family == 'syntagma_internal_numbers':
            parsed = parse_region_page(raw, source=str(source), volume='PG104', physical=physical,
                scopes=[{'span': [a, b], 'section_id': section_id}],
                section_context={'reference_namespace': namespace})
            for item in parsed['entries']:
                x, y = item['entry_span']
                ra, rb = item['reference_span']
                oa, ob = item['owner_span']
                proposal = {'entry_raw': raw[x:y], **source_span(raw, x, y), 'reference_groups': [{
                    'raw': raw[ra:rb], **source_span(raw, ra, rb), 'owner_text_raw': raw[oa:ob],
                    'owner_span': source_span(raw, oa, ob), 'namespace_status': namespace,
                    'inheritance_status': 'not_inferred'}]}
                entry = _entry(raw, proposal, namespace, family, section_id)
                entry['flags'].extend(item['flags'])
                added.append(entry)
            page['diagnostics'].extend(parsed['residuals'])
        account = account_range(raw, a, b, added)
        claimed += account['claimed_characters']
        page['entries'].extend(added)
        reason = 'unparsed_index_text' if block['kind'] == 'texto_principal' and family else 'editorial_or_outside_family'
        page['residuals'].extend(dict(item, reason=reason, block_kind=block['kind']) for item in account['remaining'])
    page['accounting'] = {'body_characters': total, 'claimed_characters': claimed,
                          'residual_characters': total-claimed}
    return page
