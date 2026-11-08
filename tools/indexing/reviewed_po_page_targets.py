"""Read-only, section-scoped page candidates for the reviewed missing PO indices.

Numbers in titles, biblical owners, shelfmarks and apparatus are never header
anchors. A missing header can be bridged only between two agreeing local page
anchors. The returned candidates retain the exact current OCR evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from collections import defaultdict

from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'reviewed-po-page-targets-v2'
PROFILE = Path('docs/fixtures/index_rebuild/reviewed_po_target_scopes.json')
BRACKET = re.compile(r'\[\s*([0-9]{1,4})\s*\]')
NUMBER = re.compile(r'[0-9]{1,4}')
SUBSCRIPT = r'[₀₁₂₃₄₅₆₇₈₉₋₊–-]*'


def header_markers(raw, namespace):
    """Bracket pagination vs unbracketed edge numbers, separately evidenced."""
    for block in blocks(raw):
        if block['kind'] != 'cabecalho':
            continue
        body = block['body']
        if namespace == 'bracket':
            matches = BRACKET.finditer(body)
        else:
            masked = BRACKET.sub(lambda m: ' ' * len(m[0]), body)
            # Keep line boundaries: an OCR header may put its folio on one line.
            matches = re.finditer(r'(?m)^\s*([0-9]{1,4})(?=\s|$)|(?<![0-9\[\]])([0-9]{1,4})\s*$', masked)
        for match in matches:
            group = 1 if match[1] is not None else 2
            number = int(match[group])
            if number:
                a, b = block['body_start'] + match.start(group), block['body_start'] + match.end(group)
                yield {'number': number, 'raw': raw[a:b], 'span': [a, b]}


def _proof(root, volume, physical, quote, grouped):
    paths = grouped.get(physical, [])
    if len(paths) != 1:
        raise ValueError(f'{volume}/{physical}: title evidence is missing or duplicated')
    path = paths[0].resolve()
    raw = path.read_text()
    if not quote or raw.count(quote) != 1:
        raise ValueError(f'{volume}/{physical}: title evidence changed: {quote!r}')
    a = raw.index(quote)
    return {'source': str(path), 'physical': physical, 'raw': quote, 'span': [a, a + len(quote)]}


def build_series(spec, grouped):
    """Build a bounded current-OCR map; isolated markers remain weak candidates."""
    pages, nodes, weak = {}, defaultdict(list), []
    for physical in range(spec['first'], spec['last'] + 1):
        paths = grouped.get(physical, [])
        if len(paths) != 1:
            continue
        path = paths[0].resolve()
        data = path.read_bytes()
        raw = data.decode('utf-8')
        page = {'file': str(path), 'physical': physical,
                'source_sha256': hashlib.sha256(data).hexdigest(),
                'has_body': any(b['kind'] in {'texto_principal', 'tabela'} and b['body'].strip() for b in blocks(raw))}
        pages[str(physical)] = page
        for marker in header_markers(raw, spec['namespace']):
            if not spec['min'] <= marker['number'] <= spec['max']:
                continue
            anchor = {**page, **marker, 'source': str(path)}
            nodes[str(marker['number'])].append(anchor)
        if spec['namespace'] == 'bracket':
            for marker in header_markers(raw, 'plain'):
                if spec['min'] <= marker['number'] <= spec['max']:
                    weak.append({**page, **marker, 'source': str(path), 'brackets_lost_in_ocr': True})
    for anchor in weak:
        n = anchor['number']
        if any(other['physical'] - anchor['physical'] == delta
               for delta in range(-4, 5) if delta
               for other in nodes.get(str(n + delta), [])
               if not other.get('brackets_lost_in_ocr')):
            if not any(p['physical'] == anchor['physical'] for p in nodes.get(str(n), [])):
                nodes[str(n)].append(anchor)
    # Requiring same slope on a nearby page rejects years or chapter numbers
    # that happen to be printed on the edge of an OCR header.
    supported = defaultdict(list)
    for number, anchors in nodes.items():
        n = int(number)
        for anchor in anchors:
            neighbors = [other for delta in range(-4, 5) if delta
                         for other in nodes.get(str(n + delta), [])
                         if other['physical'] - anchor['physical'] == delta]
            if neighbors:
                supported[number].append({**anchor, 'support': min(neighbors, key=lambda p: (abs(p['physical']-anchor['physical']), p['physical']))})
    return {'spec': spec, 'pages': pages, 'anchors': dict(nodes), 'supported': dict(supported)}


def build_map(root: Path) -> dict:
    root = Path(root).resolve()
    profile = json.loads((root / PROFILE).read_text())
    maps, grouped_by_volume = {}, {}
    for spec in profile['scopes']:
        volume = spec['volume']
        grouped = grouped_by_volume.setdefault(volume, page_files(root/'teste'/volume/'text', suffixes=['.txt']))
        series = build_series(spec, grouped)
        series['title_evidence'] = [_proof(root, volume, p['physical'], p['quote'], grouped) for p in spec['evidence']]
        maps[spec['id']] = series
    return {'version': VERSION, 'profile': str((root / PROFILE).resolve()),
            'profile_sha256': hashlib.sha256((root / PROFILE).read_bytes()).hexdigest(), 'series': maps}


def routes(entry):
    sid = entry.get('section_id', '')
    if sid == 'PO002:martyrs-greek-names': return ['po002-martyrs']
    if sid.startswith('PO007:martyrs-'): return ['po007-martyrs']
    if sid.startswith('PO011:luc-'): return ['po011-luc']
    if sid.startswith('PO011:isaac-'): return ['po011-isaac']
    if sid == 'PO011:ammonas-syriac-words': return ['po011-ammonas', 'po010-ammonii']
    if sid.startswith('PO011:ammonas-'): return ['po011-ammonas']
    if sid.startswith('PO013:sargis-'): return ['po003-sargis', 'po013-sargis']
    if sid.startswith('PO013:church-documents-'): return ['po013-documents']
    if sid.startswith('po008-plerophories-'): return ['po008-plerophories']
    return []


def _page_list(raw, base, *, max_range=400):
    """Only a page-list grammar, preserving page/line subscripts as qualifiers."""
    # A separated n./note suffix is apparatus, not another page.
    raw = re.split(r'(?i)\b(?:n\.|note\b)', raw)[0]
    token = re.compile(r'\[?(?P<a>[0-9]{1,4})\]?(?:\s*(?:à|[-–])\s*\[?(?P<b>[0-9]{1,4})\]?)?(?P<line>[₀₁₂₃₄₅₆₇₈₉₋]+)?')
    requests, previous = [], 0
    for m in token.finditer(raw):
        gap = raw[previous:m.start()]
        if gap.strip(' \n\t.,;:') not in ('', 'et'):
            break
        first, last = int(m['a']), int(m['b'] or m['a'])
        if m['b'] and len(m['b']) < len(m['a']):
            scale = 10 ** len(m['b'])
            last += first // scale * scale
            if last < first: last += scale
        if not 0 < first <= last or last - first > max_range:
            break
        for n in range(first, last + 1):
            requests.append({'number': n, 'raw': m[0], 'span': [base+m.start(), base+m.end()],
                             'line_raw': m['line'], 'interpretation': 'printed_page_range' if last != first else 'printed_page'})
        previous = m.end()
    return requests


def _reference_requests(entry):
    """Interpret only the extracted reference, never owner/chapter/verse numbers."""
    ns = entry.get('reference_namespace')
    raw = entry.get('reference_raw') or ''
    base = (entry.get('reference_span') or [0])[0]
    sid = entry.get('section_id', '')
    if ns not in {'page', 'bracketed_page', 'page_line', 'page_line_or_page_list'}:
        return []
    # These PO008 families lost subscript typography in OCR. Only the first
    # number of each page,line group is recoverable without choosing a reading.
    if ns == 'page_line_or_page_list' or sid == 'po008-plerophories-subjects':
        result = []
        # Recognize a compound page,line interval before trying either reading
        # of ambiguous commas. Otherwise '167, 8 à 167, 27' creates the false
        # page range 8–167. Mask the complete literal span in both fallbacks.
        paired_range = re.compile(r'(?<![0-9])(?P<p1>[0-9]{1,4})\s*,\s*(?P<l1>[0-9]{1,3})\s*(?:à|[-–])\s*(?P<p2>[0-9]{1,4})\s*,\s*(?P<l2>[0-9]{1,3})(?![0-9])')
        remainder = list(raw)
        for interval in paired_range.finditer(raw):
            a, b = int(interval['p1']), int(interval['p2'])
            if 0 < a <= b and b-a <= 400:
                for number in range(a,b+1):
                    result.append({'number':number, 'raw':interval[0],
                        'span':[base+interval.start(),base+interval.end()],
                        'line_start_raw':interval['l1'],'line_end_raw':interval['l2'],
                        'interpretation':'explicit_page_line_interval'})
            remainder[interval.start():interval.end()] = ' ' * len(interval[0])
        remainder = ''.join(remainder)
        pattern = re.compile(r'(?<![0-9])([0-9]{1,4})(?:\s*,\s*(?:n\.\s*)?[0-9]+(?:\s*[-–]\s*[0-9]+)?)?')
        for part in re.finditer(r'[^;\n]+', remainder):
            text = part[0]
            for m in pattern.finditer(text):
                gap = text[:m.start()]
                # Any descriptive prose breaks interpretation of this clause.
                if re.search(r'[A-Za-zΑ-ω]', re.sub(r'n\.', '', gap)):
                    break
                result.append({'number': int(m[1]), 'raw': m[0],
                    'span': [base+part.start()+m.start(), base+part.start()+m.end()],
                    'interpretation': 'page_before_possible_line', 'uncertainty': 'page_line_or_list_not_fully_decoded'})
        # OCR sometimes flattened a genuine page list instead of subscripts.
        # Keep that reading as a lower-ranked alternative, explicitly not a
        # declaration that a possible line number is a page.
        primary_numbers = {r['number'] for r in result}
        for alternate in _page_list(remainder, base):
            if alternate['number'] not in primary_numbers:
                alternate.update({'interpretation':'alternative_page_list_reading',
                    'uncertainty':'comma_may_separate_line_or_page', 'score_cap': .56})
                result.append(alternate)
        return result
    if ns == 'page_line':
        result = []
        occupied = []
        for token in entry.get('page_line_tokens', []):
            relative_start = token.get('span', [base])[0] - base
            if re.search(r'(?:n\.|note)\s*$', raw[:relative_start], re.I):
                continue
            if not str(token.get('page_raw','')).isdigit():
                continue
            result.append({'number': int(token['page_raw']), 'raw': token['raw'],
                'span': token['span'], 'line_raw': token.get('line_raw'),
                'target_volume_candidate': token.get('target_volume_candidate'),
                'interpretation': 'explicit_page_line'})
            occupied.append([token['span'][0]-base, token['span'][1]-base])
        # Recover introductory plain page mentions alongside subscripted text
        # pages, but never extend beyond the external-edition gloss in PO002.
        end = raw.find('—') if sid == 'PO002:martyrs-greek-names' else len(raw)
        if end < 0: end = len(raw)
        cursor = 0
        for a, b in sorted(occupied) + [[end, end]]:
            if a > end: break
            residual = raw[cursor:a]
            if re.fullmatch(r'[\s.,;:0-9\[\]–-]*', residual):
                if re.search(r'[0-9]\s*,\s*[0-9]', residual):
                    residual = residual.split(',', 1)[0]
                result.extend(_page_list(residual, base+cursor))
            cursor = max(cursor,b)
        # Page + note is not a page,line pair. The original OCR parser may
        # accidentally read '1, 409' across '393 n. 1, 409 n. 1'. Preserve its
        # source data, but recover the explicit page/note groups independently.
        for match in re.finditer(r'(?<![0-9])([0-9]{1,4})\s*(?:n\.|note\b)', raw, re.I):
            result.append({'number':int(match[1]),'raw':match[0],
                'span':[base+match.start(),base+match.end()], 'interpretation':'page_with_note'})
        # A leading page range or plain page is safe when it is not followed by
        # a comma/line pair. Biblical owners and following prose stay excluded.
        prefix = re.match(r'\s*[0-9]{1,4}(?=\s*(?:[-–à.;]|et\b|$))', raw)
        if prefix:
            result.extend(_page_list(raw,base))
        # Explicit 'p.' overrides preceding biographical dates in the fragment.
        explicit = list(re.finditer(r'\bp\.\s*([0-9]{1,4})',raw))
        if explicit:
            return [{'number':int(m[1]),'raw':m[0], 'span':[base+m.start(),base+m.end()],
                     'interpretation':'explicit_page_prefix'} for m in explicit]
        return result
    return _page_list(raw, base)


def reference_requests(entry):
    requests = _reference_requests(entry)
    unique = {}
    for request in requests:
        key = (request['number'], tuple(request['span']))
        unique.setdefault(key, request)
    return list(unique.values())


def page_candidates(series, number):
    spec = series['spec']
    if not spec['min'] <= number <= spec['max']:
        return []
    direct = series['supported'].get(str(number), [])
    if direct:
        return [{'file': a['file'], 'physical': a['physical'], 'score': .94,
                 'method': 'section_header_sequence', 'anchors': [a], 'uncertainties': ['brackets_lost_in_ocr'] if a.get('brackets_lost_in_ocr') else []} for a in direct]
    # Only interpolate gaps with two independently read boundaries and no
    # discontinuity. Do not turn a global physical offset into an inferred map.
    candidates = {}
    for left_n in range(max(spec['min'], number-5), number):
        for left in series['supported'].get(str(left_n), []):
            physical = left['physical'] + number - left_n
            if str(physical) not in series['pages']: continue
            for right_n in range(number+1, min(spec['max'],number+5)+1):
                for right in series['supported'].get(str(right_n), []):
                    if right['physical'] - left['physical'] != right_n-left_n:
                        continue
                    page = series['pages'][str(physical)]
                    candidates[page['file']] = {'file': page['file'], 'physical': physical, 'score': .82,
                        'method': 'interpolated_between_agreeing_headers', 'anchors': [left,right],
                        'uncertainties': ['target_header_not_read_directly']}
    if candidates: return sorted(candidates.values(),key=lambda c:c['physical'])
    # At a body opening/closing a short continuation of two agreeing anchors is
    # useful for retrieval. Require actual body text, never a blank/title leaf.
    for delta in (-3, -2, -1, 1, 2, 3):
        for anchor in series['supported'].get(str(number + delta), []):
            physical = anchor['physical'] - delta
            page = series['pages'].get(str(physical))
            support = anchor['support']
            if not page or not page['has_body'] or abs(support['physical']-physical) > 6:
                continue
            candidates[page['file']] = {'file':page['file'],'physical':physical,'score':.7,
                'method':'short_continuation_of_agreeing_headers','anchors':[anchor,support],
                'uncertainties':['target_header_not_read_directly','short_sequence_extrapolation']}
    if candidates: return sorted(candidates.values(),key=lambda c:c['physical'])
    return [{'file': a['file'], 'physical': a['physical'], 'score': .58,
             'method': 'isolated_section_header', 'anchors': [a],
             'uncertainties': ['header_without_sequence_support']} for a in series['anchors'].get(str(number), [])]


def locate(entry: dict, mapping: dict) -> list:
    result = {}
    for request in reference_requests(entry):
        for key in routes(entry):
            series = mapping['series'][key]
            target_volume = request.get('target_volume_candidate')
            if target_volume and target_volume != series['spec']['volume']:
                continue
            for candidate in page_candidates(series, request['number']):
                file = candidate['file']
                evidence = {'scope_id': key, 'namespace': series['spec']['namespace'],
                    'target_volume': series['spec']['volume'], 'method': candidate['method'],
                    'reference': {**request, 'source': entry['source']},
                    'target_header_evidence': candidate['anchors'],
                    'scope_evidence': series['title_evidence'],
                    'notice_evidence': entry.get('reviewed_source_section',{}).get('notice_evidence',[])}
                uncertainty = candidate['uncertainties'] + ([request['uncertainty']] if request.get('uncertainty') else [])
                if file not in result:
                    result[file] = {'file':file,'physical': candidate['physical'], 'score':min(candidate['score'],request.get('score_cap',1)),
                        'evidence':[], 'uncertainties':[], 'verified':False, 'locator_version':VERSION}
                result[file]['score'] = max(result[file]['score'],min(candidate['score'],request.get('score_cap',1)))
                result[file]['evidence'].append(evidence)
                result[file]['uncertainties'] = sorted(set(result[file]['uncertainties'] + uncertainty))
    return sorted(result.values(),key=lambda c:(-c['score'],c['file']))


def diagnose(entry, mapping):
    if not routes(entry): return 'outside_reviewed_page_scopes'
    if not reference_requests(entry): return 'reference_not_mechanically_decoded'
    return 'candidate_available' if locate(entry,mapping) else 'number_absent_in_section_maps'
