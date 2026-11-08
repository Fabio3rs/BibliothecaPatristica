"""PL003's displaced Tertullian index refers to the two Tertullian tomes.

The notice and the target title leaves bind I/II to PL001/PL002. Headers
locate columns, with short, independently bounded gaps. No global offset,
prose numeral, inferred author identity or A–D passage certification is used.
"""
from collections import defaultdict
import hashlib
from pathlib import Path
import re

from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.reviewed_po_page_targets import header_markers, page_candidates
from tools.indexing.tome_candidates import parse_tome_candidates

VERSION = 'pl003-tertullian-targets-v1'
SECTION = 'PL003:displaced-tertullian-index'
NOTICE = ('Prior nota arithmetica, charactere Romano descripta, indicat tomum; '
          'posterior, charactere Arabico, columnam; littera, columnæ partem quæ eidem litteræ subjacet.')


def eligible(entry):
    try:
        physical = int(Path(entry['source']).stem.rsplit('-', 1)[1])
    except (KeyError, ValueError):
        return False
    return entry.get('volume') == 'PL003' and 807 <= physical <= 849


def current_reference(entry, raw):
    """Legacy rows lack owner spans. Validate the reference without inventing one.

    Repeated numerals cannot be reanchored alone. Exact offsets suffice even
    when another part of the OCR changed; old whole-file hashes are provenance.
    """
    literal = entry.get('reference_raw', entry.get('ref_raw'))
    span = entry.get('reference_span', entry.get('source_span'))
    if not isinstance(literal, str) or not literal or not isinstance(span, list) or len(span) != 2:
        return None, 'missing_literal_evidence'
    a, b = span
    if not all(type(x) is int for x in span) or not 0 <= a < b <= len(raw) or raw[a:b] != literal:
        return None, 'reference_changed_requires_reextraction'
    return dict(entry, reference_raw=literal, reference_span=list(span), section_id=SECTION), 'reference_span_current_owner_unverified'


def proof(path, quote):
    raw = path.read_text()
    if raw.count(quote) != 1:
        raise ValueError(f'Expected unique editorial evidence in {path}: {quote}')
    start = raw.index(quote)
    return dict(source=str(path.resolve()), raw=quote, span=[start, start+len(quote)],
                sha256=hashlib.sha256(raw.encode()).hexdigest())


def column_series(grouped):
    pages, anchors = {}, defaultdict(list)
    for physical, paths in sorted(grouped.items()):
        if len(paths) != 1:
            raise ValueError(f'Ambiguous physical page: {paths}')
        path = paths[0].resolve()
        raw = path.read_text()
        page = dict(file=str(path), physical=physical,
                    has_body=any(b['kind'] == 'texto_principal' and b['body'].strip() for b in blocks(raw)),
                    source_sha256=hashlib.sha256(raw.encode()).hexdigest())
        pages[str(physical)] = page
        for marker in header_markers(raw, 'plain'):
            if 1 <= marker['number'] <= 1800:
                # Odd/even columns share a physical leaf. Keep both literal
                # header witnesses; do not claim which part of the leaf holds it.
                unit = (marker['number'] + 1) // 2
                anchors[str(unit)].append(dict(page, **marker, source=str(path), column=marker['number']))
    supported = {}
    for unit, items in anchors.items():
        supported[unit] = []
        for item in items:
            neighbors = [a for delta in range(-4, 5) if delta
                         for a in anchors.get(str(int(unit)+delta), [])
                         if a['physical']-item['physical'] == delta]
            if neighbors:
                supported[unit].append(dict(item, support=min(neighbors,
                    key=lambda a: (abs(a['physical']-item['physical']), a['physical'], a['column']))))
    return dict(spec=dict(min=1, max=900, namespace='paired_columns'), pages=pages,
                anchors=dict(anchors), supported=supported)


def build_map(root):
    root = Path(root).resolve()
    sources = page_files(root/'teste/PL003/text', suffixes=['.txt'])
    opening = sources[807][0]
    closing = sources[849][0]
    # Select the actual section heading, not the running header on the mixed page.
    end = proof(closing, 'SYLLABUS RERUM\n\nQUÆ IN HOC VOLUMINE INCLUDUNTUR.')
    mapping = dict(version=VERSION, notice=proof(opening, NOTICE),
        opening=proof(opening, 'INDEX GENERALIS.\n    IN OPERA TERTULLIANI.'),
        displacement=proof(opening, 'at secundi nempe tomi calcem'), closing=end,
        tomes={}, titles={})
    for tome, volume, title_page, ordinal in [('I', 'PL001', 8, 'PRIMUS'), ('II', 'PL002', 7, 'SECUNDUS')]:
        grouped = page_files(root/'teste'/volume/'text', suffixes=['.txt'])
        path = grouped[title_page][0]
        mapping['titles'][tome] = [proof(path, 'TERTULLIANI'), proof(path, 'TOMUS '+ordinal+'.')]
        mapping['tomes'][tome] = column_series(grouped)
    return mapping


def _parsed_reference(entry):
    raw = entry.get('reference_raw', entry.get('ref_raw', ''))
    base = entry.get('reference_span', entry.get('source_span', [0]))[0]
    parsed = parse_tome_candidates(dict(entry_raw=raw, char_start=base), unit='column')
    return raw, base, parsed


def locators(entry):
    _, _, parsed = _parsed_reference(entry)
    return parsed['interpreted_references'] if parsed else []


def _candidates(atom, mapping):
    series = mapping['tomes'].get(atom['tome'])
    number, end = atom['number'], atom.get('range_end')
    if not series or not number or end is not None and (end < number or end-number > 40):
        return []
    result = []
    for col in range(number, (end or number)+1):
        for candidate in page_candidates(series, (col+1)//2):
            result.append(dict(candidate, requested_column=col))
    return result


def within_body(entry, mapping):
    if not eligible(entry):
        return False
    span = entry.get('reference_span', entry.get('source_span', [0, 0]))
    source = Path(entry['source']).resolve()
    if source == Path(mapping['closing']['source']) and span[0] >= mapping['closing']['span'][0]:
        return False
    notice = mapping.get('notice')
    return not (notice and source == Path(notice['source']) and span[0] < notice['span'][1])


def locate(entry, mapping):
    if not within_body(entry, mapping):
        return []
    span = entry.get('reference_span', entry.get('source_span', [0, 0]))
    result = []
    for atom in locators(entry):
        for candidate in _candidates(atom, mapping):
            candidate['method'] = VERSION
            candidate['namespace'] = 'tertullian_tome_column'
            candidate['uncertainties'] += ['owner_segmentation_not_certified', 'column_subdivision_not_localized']
            if atom.get('qualifiers'):
                candidate['uncertainties'].append('following_extent_not_expanded')
            candidate['evidence'] = [dict(kind='displaced_tome_column', locator=atom,
                source=entry['source'], span=span, raw=entry.get('reference_raw', entry.get('ref_raw')),
                notice=mapping['notice'], target_titles=mapping['titles'][atom['tome']],
                header_anchors=candidate.pop('anchors'))]
            result.append(candidate)
    return result


def diagnose(entry, mapping):
    """Count every explicit column, plus unparsed numeric/ibid occurrences.

    A range with one retrieved page is not fully localized. Residual text from
    the list parser remains visible rather than turning a successful prefix
    into a completeness claim. This does not repair the reference silently.
    """
    raw, base, parsed = _parsed_reference(entry)
    atoms = parsed['interpreted_references'] if parsed else []
    residuals = parsed['non_locator_spans'] if parsed else [
        dict(raw=raw, source_span=[base, base+len(raw)])]
    unknown = []
    for residual in residuals:
        for match in re.finditer(r'(?<!\w)(?:[0-9]+|ibid\.?)(?!\w)', residual['raw'], re.I):
            start = residual['source_span'][0]+match.start()
            unknown.append(dict(raw=match.group(), span=[start, start+len(match.group())],
                                reason='unparsed_locator_material'))
    span = entry.get('reference_span', entry.get('source_span', [0, 0]))
    in_scope = within_body(entry, mapping)
    count, found, unmatched = 0, 0, []
    for atom in atoms:
        number, end = atom['number'], atom.get('range_end')
        if end is not None and (not number or end < number or end-number > 40):
            count += 1
            unmatched.append(dict(atom, reason='unsupported_range'))
            continue
        for column in range(number or 0, (end or number or 0)+1):
            single = dict(atom, number=column, range_end=None)
            count += 1
            if in_scope and _candidates(single, mapping):
                found += 1
            else:
                unmatched.append(single)
        for qualifier in atom.get('qualifiers', []):
            # An apparatus note is not an open-ended range. Only following
            # extents need an additional unresolved coverage unit.
            if re.search(r'\b(?:seq|seqq|sqq|sq)\.', qualifier['raw'], re.I):
                unknown.append(dict(raw=qualifier['raw'], span=qualifier['source_span'],
                                    reason='following_extent_not_expanded'))
    if not atoms and not unknown:
        unknown.append(dict(raw=raw, span=[base, base+len(raw)],
                            reason='reference_not_mechanically_decoded'))
    return dict(locator_count=count+len(unknown), located_locator_count=found,
                reason=('outside_reviewed_section' if not in_scope else
                        'explicit_tome_columns' if atoms else 'tome_context_or_ibid_not_resolved'),
                unmatched=unmatched, unparsed=unknown,
                raw=raw, owner_verified=False)
