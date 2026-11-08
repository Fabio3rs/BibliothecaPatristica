"""Named title/book summaries and manuscript homily catalogs, within supplied bounds."""
from collections import defaultdict
import hashlib
import re

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.epistle_index_locators import roman_to_int
from tools.indexing.internal_index_formats import greek_number, language, normalized, trim

VERSION = 'named-division-formats-v1'
TOKEN = r"[IVXLCDM]+|[Α-Ωα-ωϚϛϟ]+"
PREFIXES = {'title_number': r'Titulus|Τ[ίι]τλος', 'book_number': r'Liber|Λ[όο]γος'}
ORDINAL_WORDS = dict(zip(
    'primus secundus tertius quartus quintus sextus septimus πρωτοσ δευτεροσ τριτοσ τεταρτοσ πεμπτοσ εκτοσ εβδομοσ'.split(),
    list(range(1, 8)) * 2))
HOMILY = re.compile(r'(?m)^[ \t]*(?P<n>\d{1,3})[.]\s*')
FOLIO = re.compile(r'Fol[.]\s*(?P<n>\d+)(?:[.]?\s+(?P<side>verso|vers[.]))?', re.I)
INIT = re.compile(r'Init(?:ium\s*:|[.]\s*(?:sine alio titulo[.])?)\s*', re.I)
APPENDIX = re.compile(r'(?im)^[ \t]*(?:Appendix|Παράτιτλος)[.]\s*[—–-]\s*')


def pattern(family, opening=False):
    words = '|'.join(ORDINAL_WORDS) if opening else ''
    token = TOKEN + ('|' + words if words else '')
    # The target marker must occupy its own line, not a running page heading.
    suffix = r'[ \t]*(?=\n|$)' if opening else r'[ \t]*(?:[—–-][ \t]*)?'
    return re.compile(rf"(?im)^[ \t]*\[?(?:{PREFIXES[family]})\s+(?P<n>{token})['΄ʹʹ’᾽᾿′´]?[.]" + suffix)


def number(token):
    key = normalized(token)
    return ORDINAL_WORDS.get(key) or (roman_to_int(token.upper()) if re.fullmatch('[IVXLCDM]+', token, re.I) else greek_number(token.replace('ς', 'ϛ')))


def structural_view(text):
    """Decode only line-break entities / emphasis wrappers, keeping source offsets."""
    text = re.sub(r'&(?:amp;)?#(?:10|13);', lambda m: ' ' * (len(m[0])-1) + '\n', text)
    return re.sub(r'\[/?b\]', lambda m: ' ' * len(m[0]), text, flags=re.I)


def parse_named_page(raw, *, family, source, volume, physical, scopes, section_context=None):
    entries, residuals = [], []
    for scope in scopes:
        lo, hi = scope['span']
        for block in blocks(raw):
            a, b = max(lo, block['body_start']), min(hi, block['body_end'])
            if a >= b or block['kind'] != 'texto_principal':
                continue
            text = raw[a:b]
            matches = [(m, False) for m in (HOMILY if family == 'manuscript_homily' else pattern(family)).finditer(text)]
            if family == 'title_number':
                matches += [(m, True) for m in APPENDIX.finditer(text)]
            matches.sort(key=lambda pair: pair[0].start())
            lead = a + matches[0][0].start() if matches else b
            la, lb = trim(raw, a, lead)
            if la < lb:
                residuals.append({'section_id': scope['section_id'], 'span': [la, lb], 'raw': raw[la:lb],
                                  'reason': 'heading_notice_or_unparsed_text'})
            for i, (m, appendix) in enumerate(matches):
                end = a + (matches[i+1][0].start() if i+1 < len(matches) else len(text))
                oa, ob = trim(raw, a + m.end(), end)
                if oa >= ob:
                    continue
                n = None if appendix else int(m['n']) if family == 'manuscript_homily' else number(m['n'])
                if not appendix and n is None:
                    continue
                ra, rb = (None, None) if appendix else (a+m.start('n'), a+m.end('n'))
                namespace = family
                binding = {'division': family, 'numbers': [n] if n else []}
                extra = {}
                if family == 'manuscript_homily':
                    init = INIT.search(raw, oa, ob)
                    if not init:
                        residuals.append({'section_id': scope['section_id'], 'span': [a+m.start(), ob],
                                          'raw': raw[a+m.start():ob], 'reason': 'homily_without_incipit_marker'})
                        continue
                    folio = FOLIO.match(raw, oa, init.start())
                    namespace = 'manuscript_folio'
                    binding = {'division': namespace, 'numbers': [], 'sequence': n,
                               'folio': int(folio['n']) if folio else None,
                               'side': 'verso' if folio and folio['side'] else None}
                    ra, rb = (folio.start(), folio.end()) if folio else (None, None)
                    title_a = folio.end() if folio else oa
                    ta, tb = trim(raw, title_a, init.start())
                    ia, ib = trim(raw, init.end(), ob)
                    extra = {'title_raw': raw[ta:tb], 'title_span': [ta,tb],
                             'incipit_raw': raw[ia:ib], 'incipit_span': [ia,ib],
                             'sequence_span': [a+m.start('n'),a+m.end('n')]}
                    # Retain the complete printed item as the searchable owner, including the folio.
                entry = {'source': source, 'volume': volume, 'physical': physical,
                         'section_id': scope['section_id'], 'entry_span': [a+m.start(), ob],
                         'owner_span': [oa, ob], 'owner_raw': raw[oa:ob],
                         'reference_span': [ra,rb] if ra is not None else None,
                         'reference_raw': raw[ra:rb] if ra is not None else None,
                         'reference_namespace': namespace, 'internal_binding': binding,
                         'entry_kind': 'homily_catalog' if family == 'manuscript_homily' else 'section_summary',
                         'grammar': family, 'language': language(raw, block), 'parser': VERSION,
                         'flags': ['literal_candidate_not_certified'], **extra}
                if appendix:
                    entry['flags'].append('unnumbered_appendix')
                if family == 'manuscript_homily':
                    entry['flags'].append('external_folio_not_volume_page')
                entries.append(entry)
    return {'version': VERSION, 'entries': entries, 'residuals': residuals, 'context': section_context or {}}


def scan_named_targets(pages, family, diagnostics=None):
    targets = defaultdict(list)
    for page in pages:
        raw = page['raw']
        for block in blocks(raw):
            a, b = max(page.get('start',0), block['body_start']), min(page.get('end',len(raw)), block['body_end'])
            if a >= b or block['kind'] not in {'texto_principal', 'cabecalho'}:
                continue
            if family == 'manuscript_homily':
                if block['kind'] == 'texto_principal':
                    targets[('incipit',0)].append({'file':page['source'], 'physical':page['physical'],
                                                  'span':[a,b], 'raw':raw[a:b]})
                continue
            for m in pattern(family, opening=True).finditer(structural_view(raw[a:b])):
                n = number(m['n'])
                if not n:
                    continue
                targets[(family,n)].append({'file':page['source'], 'physical':page['physical'],
                    'marker':{'source':page['source'], 'span':[a+m.start(),a+m.end()], 'raw':raw[a+m.start():a+m.end()]},
                    'context':{'key':family, 'basis':'explicit_section_search_bounds'},
                    'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                    'language':language(raw,block), 'block_kind':block['kind'],
                    'snippet':raw[a+m.end():min(b,a+m.end()+600)]})
    by_page = defaultdict(lambda: defaultdict(set))
    for (_, n), anchors in targets.items():
        for anchor in anchors:
            if 'language' in anchor:
                by_page[anchor['file']][anchor['language']].add(n)
    for (_, n), anchors in targets.items():
        for anchor in anchors:
            parallel = by_page[anchor['file']]
            if len(parallel.get('lat', [])) == len(parallel.get('grc', [])) == 1 and parallel['lat'] != parallel['grc']:
                anchor['uncertainties'] = ['parallel_heading_ordinals_disagree']
                if diagnostics is not None:
                    diagnostics.append({'reason':'parallel_heading_ordinals_disagree',
                                        'number':n, **anchor['marker']})
    return targets


def lexical_text(text):
    text = re.sub(r'-\s*\n\s*', '', text)
    return ' '.join(re.findall(r'[^\W\d_]+', normalized(text)))


def locate_incipit(metadata, targets):
    """Long normalized prefix at a body block opening; never map a manuscript folio."""
    query = lexical_text(metadata.get('incipit_raw',''))
    words = query.split()[:12]
    if len(words) < 7 or len(' '.join(words)) < 40:
        return [], []
    prefix = ' '.join(words)
    found = {}
    for target in targets.get(('incipit',0),[]):
        text = lexical_text(target['raw'])
        if text != prefix and not text.startswith(prefix + ' '):
            continue
        candidate = found.setdefault(target['file'], {'file':target['file'], 'physical':target['physical'],
            'navigable':True, 'certified':False, 'score':40.0, 'tier':'normalized_incipit_candidate',
            'uncertainties':['shared_incipit_possible','external_folio_not_converted_to_local_pagination'],
            'evidence':{'query_span':metadata['incipit_span'], 'query_source':metadata['source'],
                        'normalized_prefix':prefix, 'anchors':[]}})
        candidate['evidence']['anchors'].append(target)
    result = sorted(found.values(), key=lambda c:c['file'])
    for rank, c in enumerate(result,1):
        c['rank'] = rank
    return result, []
