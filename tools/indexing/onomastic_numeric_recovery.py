"""Readonly, section-scoped recovery of missing onomastic page candidates.

Bracketed fascicle pages anchor the sequence. Missing brackets are accepted only
when two distinct anchors agree; suppressed page numbers remain inferred. Lines
following the page number never become additional page destinations.
"""
import hashlib
import json
import re
from pathlib import Path

from tools.indexing.editorial_ocr_blocks import blocks

VERSION = 'onomastic-numeric-recovery-v1'
BRACKET = re.compile(r'\[\s*(\d{1,4})\s*\]')
NUMBER = re.compile(r'(?<![\w\d])\d{1,4}(?![\w\d])')
PAGE_LINE = re.compile(r'^\s*(\d+)\s*[, .]\s*\d+(?:\s*[, .]\s*\d+)*(?:\s*s\.)?\s*\.?\s*$')


def page_line(raw):
    match = PAGE_LINE.fullmatch(raw)
    return int(match[1]) if match else None


def read_pages(root, profile):
    pages = {}
    lo, hi = profile['target_physical_scope']
    for path in sorted((Path(root)/profile['volume']/'text').glob('*.txt')):
        physical = int(path.stem.rsplit('-', 1)[-1])
        if not lo <= physical <= hi:
            continue
        if physical in pages:
            raise ValueError(f'Duplicate physical file: {physical}')
        text = path.read_text()
        parsed = blocks(text)
        evidence = []
        for block in parsed:
            if block['kind'] != 'cabecalho':
                continue
            for match in NUMBER.finditer(block['body']):
                start = block['body_start'] + match.start()
                evidence.append(dict(number=int(match[0]), file=str(path.resolve()),
                    span=[start, start+len(match[0])], raw=match[0]))
        anchored = []
        for block in parsed:
            if block['kind'] == 'cabecalho':
                anchored.extend(int(m[1]) for m in BRACKET.finditer(block['body']))
        pages[physical] = dict(file=str(path.resolve()), physical=physical,
            sha256=hashlib.sha256(text.encode()).hexdigest(), numbers=evidence,
            anchors=sorted(set(anchored)),
            body=any(b['kind']=='texto_principal' and sum(c.isalpha() for c in b['body'])>=20 for b in parsed))
    return pages


def locate(number, pages, max_bridge=8, edge=1):
    """Infer only from local slope +1; conflicting bracketed pages break bridges."""
    anchors = [(p, n) for p, page in sorted(pages.items()) for n in page['anchors']]
    found = {}
    for i, (left, a) in enumerate(anchors):
        for right, b in anchors[i+1:]:
            if not 0 < right-left <= max_bridge or b-a != right-left:
                continue
            target = left + number-a
            if target not in pages or not pages[target]['body']:
                continue
            inside = left <= target <= right
            if not inside and min(abs(target-left), abs(target-right)) > edge:
                continue
            start, end = min(target,left), max(target,right)
            if any(n != a+p-left for p,n in anchors if start<=p<=end):
                continue
            page = pages[target]
            literal = [e for e in page['numbers'] if e['number']==number]
            score = .91 if number in page['anchors'] else .86 if literal else .78 if inside else .65
            proofs = []
            for physical, value in ((left,a),(right,b)):
                proofs.append(dict(physical=physical, number=value, file=pages[physical]['file'],
                    sha256=pages[physical]['sha256'],
                    evidence=[e for e in pages[physical]['numbers'] if e['number']==value]))
            candidate = dict(file=page['file'], physical=target, score=score, navigable=True,
                method=VERSION, number=number, source_sha256=page['sha256'],
                evidence=dict(kind='literal_header_in_local_sequence' if literal else 'local_page_sequence',
                    literal=literal, anchors=proofs, inferred=not bool(literal),
                    extrapolated=not inside), verified=False,
                warnings=['page_candidate_not_semantic_certification', 'line_not_localized'])
            old = found.get(target)
            if old is None or (score, -(right-left)) > (old['score'], -old['_bridge']):
                candidate['_bridge'] = right-left
                found[target] = candidate
    result = sorted(found.values(), key=lambda c:(-c['score'],c['file']))
    for rank,candidate in enumerate(result,1):
        candidate.pop('_bridge')
        candidate['rank'] = rank
    return result


def recover(row, profile, pages):
    metadata = json.loads(row['metadata_json'])
    result = dict(item_id=row['id'], group_id=row['group_id'], volume=row['volume'],
                  label=row['label'], ref_raw=row['ref_raw'], candidates=[])
    if metadata.get('reference_interpretation', {}).get('notation_status') != 'interpreted_page_line':
        result['reason'] = 'reference_interpretation_unsupported'
        return result
    path = Path(row['source'])
    text = path.read_text()
    span = metadata.get('source_span')
    if not span or text[slice(*span)] != row['ref_raw']:
        result['reason'] = 'source_reference_changed_or_missing_span'
        return result
    number = page_line(row['ref_raw'])
    if number is None:
        result['reason'] = 'reference_interpretation_unsupported'
        return result
    result['source_evidence'] = dict(file=str(path), span=span, raw=row['ref_raw'],
        sha256=hashlib.sha256(text.encode()).hexdigest())
    result['number'] = number
    result['candidates'] = locate(number,pages,profile.get('max_bridge',8),profile.get('edge',1))
    result['reason'] = ('literal_header_not_in_previous_map' if result['candidates'][0]['evidence']['literal']
                        else 'page_marker_absent_or_unrecognized') if result['candidates'] else 'no_agreeing_local_sequence'
    return result
