"""Read-only, section-bounded probe of internal numbering; never resolves entries."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from scripts.audit_alphabetical_numbering import NUMBER, supported_sequence_nodes
from tools.corpus_utils import discover_preferred_pages, page_number
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.parser_target_experiment import locate_number, marker_options
from tools.ocr_xml_utils import parse_ocr_xml_page

VERSION = 'pl-section-map-v3'
REVIEWED_BASES = {'two_reviewed_markers', 'reviewed_marker_and_section_end'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unique_span(raw, quote):
    starts = [m.start() for m in re.finditer(re.escape(quote), raw)]
    if len(starts) != 1:
        raise ValueError(f'Expected unique literal evidence: {quote!r}; got {len(starts)}')
    return starts[0], starts[0] + len(quote)


def bounded_text(raw, bounds):
    start = unique_span(raw, bounds['start_quote'])[0] if bounds.get('start_quote') else 0
    end = unique_span(raw, bounds['end_quote'])[0] if bounds.get('end_quote') else len(raw)
    if end <= start:
        raise ValueError('Reversed section boundaries')
    return raw[start:end], start, end


def section_end_evidence(profile, series, root):
    """Reanchor a reviewed next-section title, never invent an N+1 marker."""
    spec = profile['target']
    proof = spec.get('section_end')
    if proof is None:
        return None
    order = series.get('page_order')
    last = order[-1] if order else spec['physical_end']
    if (proof['number'] != spec['number_end'] or proof['physical'] != last
            or proof.get('visual_review') is not True or not proof.get('quote', '').strip()):
        raise ValueError('Section end requires reviewed evidence at the final number and file')
    page = series['pages'][last]
    if spec.get('bounds', {}).get(str(last), {}).get('end_quote') != proof['quote']:
        raise ValueError('Section end must match the excluded regional end quote')
    image = root / profile['volume'] / 'images' / proof['image_name']
    if sha(image) != proof['image_sha256']:
        raise ValueError('Section-end image changed')
    raw = Path(page['file']).read_text()
    if sha(page['file']) != page['sha256']:
        raise ValueError('Section-end OCR changed during execution')
    start, end = unique_span(raw, proof['quote'])
    return {**proof, 'file': page['file'], 'sha256': page['sha256'],
            'char_start': start, 'char_end': end,
            'offset_space': 'raw_xml_unicode_characters',
            'ocr_changed': page['sha256'] != proof['ocr_sha256']}


def locate_section_end(series, proof, anchors, max_file_gap=3):
    number = proof['number']
    starts = marker_options(series, number)
    record = {'number': number, 'namespace': series['spec']['id'],
              'status': 'missing_reviewed_start', 'files': [], 'start_options': starts,
              'end_options': [], 'end_boundary_number': None, 'range_end': None,
              'section_end': proof, 'verified': False,
              'basis': 'reviewed_marker_and_section_end'}
    if len(starts) > 1:
        record['status'] = 'ambiguous_markers'
    if len(starts) != 1:
        return record
    start = starts[0]
    if not any(a['number'] == number and a['physical'] == start['physical']
               and a.get('visual_review') is True for a in anchors):
        return record
    if marker_options(series, number + 1):
        record['status'] = 'section_end_conflicts_with_next_marker'
        return record
    a, b = start['physical'], proof['physical']
    order = series.get('page_order')
    left, right = (order.index(a), order.index(b)) if order else (a, b)
    if right < left or (a == b and start['occurrence']['char_end'] > proof['char_start']):
        record['status'] = 'reversed_markers'
        return record
    if right - left > max_file_gap:
        record['status'] = 'window_exceeds_cap'
        return record
    window = order[left:right+1] if order else range(a, b+1)
    if any(n not in series['pages'] for n in window):
        record['status'] = 'missing_intermediate_file'
        return record
    record['file_order_basis'] = 'explicit_editorial_order' if order else 'physical_order'
    record['status'] = 'bounded_window_candidate'
    record['files'] = [{'physical': n, **{k: series['pages'][n][k]
                       for k in ('file', 'sha256', 'header')}} for n in window]
    return record


def build_map(profile, root):
    spec = profile['target']
    pages, sources, nodes = {}, [], defaultdict(set)
    for path in discover_preferred_pages(root / profile['volume'] / 'text', volume_id=profile['volume']):
        physical = page_number(path)
        if not spec['physical_start'] <= physical <= spec['physical_end']:
            continue
        data = path.read_bytes()
        raw = data.decode('utf-8')
        checksum = hashlib.sha256(data).hexdigest()
        bounds = spec.get('bounds', {}).get(str(physical), {})
        # Parse the intact XML first; retain only content inside reviewed literal boundaries.
        _, a, b = bounded_text(raw, bounds)
        texts, positions = [], []
        for block in blocks(raw):
            if block['kind'] not in spec.get('block_types', {}).get(str(physical), ['texto_principal']):
                continue
            # The parser exposes the original content span, not synthetic bbox geometry.
            start, end = block['body_start'], block['body_end']
            left, right = max(a, start), min(b, end)
            if left < right:
                if texts:
                    positions.append(None)
                texts.append(raw[left:right])
                positions.extend(range(left, right))
        body = '\n'.join(texts)
        occurrences = defaultdict(list)
        for match in NUMBER.finditer(body):
            number = int(match[0])
            if spec['number_start'] <= number <= spec['number_end'] + 1:
                nodes[number].add(physical)
                occurrences[number].append({'body_offset': match.start(),
                    'char_start': positions[match.start()], 'char_end': positions[match.end()-1]+1,
                    'raw': match[0], 'offset_space': 'raw_xml_unicode_characters', 'excerpt': body[max(0, match.start()-80):match.end()+100]})
        pages[physical] = {'file': str(path.resolve()), 'sha256': checksum,
                           'header': parse_ocr_xml_page(raw).header_text, 'body': body,
                           'occurrences': dict(occurrences), 'source_region': [a, b]}
        sources.append([str(path.resolve()), checksum])
    order = spec.get('reading_order')
    page_order = None
    if order is not None:
        page_order = [item['physical'] for item in order]
        if len(page_order) != len(set(page_order)) or set(page_order) != set(pages):
            raise ValueError('Reading order must cover each target file exactly once')
        previous_end = None
        for item in order:
            page = pages[item['physical']]
            header = ' '.join(page['header'].split())
            quote = item['header_quote']
            if not quote or header.count(quote) != 1:
                raise ValueError('Reading-order header evidence changed')
            first, last = item['editorial_start'], item['editorial_end']
            if (first <= 0 or last < first or
                    (previous_end is not None and first != previous_end + 1)):
                raise ValueError('Reading-order editorial intervals must be consecutive')
            for number in (first, last):
                if not re.search(r'(?<!\d)' + str(number) + r'(?!\d)', quote):
                    raise ValueError('Editorial interval absent from header evidence')
            image = root / profile['volume'] / 'images' / item['image_name']
            if sha(image) != item['image_sha256']:
                raise ValueError('Reading-order image evidence changed')
            previous_end = last
    positions = ({physical: rank for rank, physical in enumerate(page_order)}
                 if page_order is not None else {physical: physical for physical in pages})
    anchors, rejected = [], []
    for anchor in profile['anchors']:
        page = pages[anchor['physical']]
        image = root / profile['volume'] / 'images' / anchor['image_name']
        if sha(image) != anchor['image_sha256']:
            raise ValueError('Reviewed image changed: ' + str(image))
        if not anchor.get('quote'):
            rejected.append({**anchor, 'status': 'visual_marker_missing_in_ocr'})
            continue
        try:
            a, _ = unique_span(page['body'], anchor['quote'])
            hits = list(re.finditer(r'(?<!\d)' + str(anchor['number']) + r'(?!\d)', anchor['quote']))
            if len(hits) != 1:
                raise ValueError('Numeric anchor quote must contain exactly one marker')
            offset = a + hits[0].start()
            options = [x for x in page['occurrences'].get(anchor['number'], []) if x['body_offset'] == offset]
            if len(options) != 1:
                raise ValueError('Anchor is not in parsed target region')
        except ValueError as exc:
            rejected.append({**anchor, 'status': 'anchor_needs_review', 'error': str(exc)})
            continue
        anchors.append({**anchor, 'body_offset': offset,
                        'ocr_changed': page['sha256'] != anchor['ocr_sha256']})
        # Select the reviewed printed occurrence, not another equal integer in this file.
        page['occurrences'][anchor['number']] = options
    for number in nodes:
        nodes[number] = {seq for seq in nodes[number] if all(
            positions[seq] <= positions[x['physical']] if number < x['number'] else
            positions[seq] >= positions[x['physical']] if number > x['number'] else seq == x['physical'] for x in anchors)}
    if page_order is None:
        support = supported_sequence_nodes(nodes)
    else:
        ranked = {number: {positions[p] for p in files} for number, files in nodes.items()}
        support = {}
        for (number, rank), proof in supported_sequence_nodes(ranked).items():
            proof = {**proof, 'basis': 'sequence_in_explicit_editorial_order',
                     'witness': [[n, page_order[r]] for n, r in proof['witness']]}
            support[number, page_order[rank]] = proof
    for x in anchors:
        support[x['number'], x['physical']] = {'basis': 'visual_anchor_exact_quote', 'witness': [[x['number'], x['physical']]], 'chain_length': 0}
    series = {'spec': {'id': profile['id']}, 'pages': pages, 'sources': sources,
              'nodes': nodes, 'supported': support}
    if page_order is not None:
        series['page_order'] = page_order
    section_end = section_end_evidence(profile, series, root)
    records = []
    visual_numbers = {x['number'] for x in anchors}
    missing_visual = {x['number'] for x in rejected}
    for number in range(spec['number_start'], spec['number_end'] + 1):
        terminal = section_end is not None and number == section_end['number']
        record = (locate_section_end(series, section_end, anchors) if terminal
                  else locate_number(series, number))
        # A known missing marker must never be replaced by another occurrence of its digits.
        if number in missing_visual or (not terminal and number+1 in missing_visual):
            record['files'] = []
            record['status'] = 'reviewed_boundary_missing_from_ocr'
        if not terminal:
            record['basis'] = ('two_reviewed_markers' if number in visual_numbers and number+1 in visual_numbers
                               else 'sequence_probe_not_verified')
        record['import_ready'] = False
        records.append(record)
    return records, series, anchors, rejected


def run(profile_path, survey, root, out):
    if out.exists():
        raise ValueError('Use a new output directory')
    profile = json.loads(profile_path.read_text())
    proofs = []
    for proof in profile['proofs']:
        path = root / profile['volume'] / 'text' / proof['ocr_name']
        data = path.read_bytes()
        raw = data.decode('utf-8')
        checksum = hashlib.sha256(data).hexdigest()
        a,b = unique_span(raw, proof['quote'])
        image = root / profile['volume'] / 'images' / proof['image_name']
        if sha(image) != proof['image_sha256']:
            raise ValueError('Proof image changed')
        proofs.append({**proof, 'path': str(path.resolve()), 'sha256': checksum, 'char_start': a, 'char_end': b})
    records, series, anchors, rejected = build_map(profile, root)
    maps = {x['number']: x for x in records}
    groups = {x['id']: x for x in (json.loads(s) for s in (survey/'groups.jsonl').open())}
    links, counts, source_cache = [], Counter(), {}
    for line in (survey/'occurrences.jsonl').open():
        row = json.loads(line)
        group = groups[row['group_id']]
        if not profile['source']['physical_start'] <= group['physical'] <= profile['source']['physical_end']:
            continue
        path = Path(row['source'])
        if path not in source_cache:
            data = path.read_bytes()
            source_cache[path] = (data.decode('utf-8'), hashlib.sha256(data).hexdigest())
        raw, checksum = source_cache[path]
        bounded, a, b = bounded_text(raw, profile['source'].get('bounds', {}).get(str(group['physical']), {}))
        if not a <= row['char_start'] < row['char_end'] <= b:
            continue
        valid = checksum == row['source_sha256'] and raw[row['char_start']:row['char_end']] == row['raw']
        mapping = maps.get(row['number']) if valid else None
        status = ('stale_index_extraction' if not valid else
                  mapping['status'] if mapping else 'outside_pilot_number_range')
        counts[status] += 1
        links.append({'occurrence': row, 'owner_raw': group['owner_raw'], 'status': status,
                      'map': mapping, 'verified': False, 'import_ready': False})
    for proof in proofs:
        if sha(proof['path']) != proof['sha256']:
            raise ValueError('Proof OCR changed during execution')
    for path, digest in series['sources']:
        if sha(path) != digest:
            raise ValueError('OCR changed during execution')
    for path, (_, digest) in source_cache.items():
        if sha(path) != digest:
            raise ValueError('Index OCR changed during execution')
    summary = {'version': VERSION, 'profile': profile['id'], 'target_files': len(series['pages']),
               'number_denominator': len(records), 'map_statuses': dict(Counter(x['status'] for x in records)),
               'reviewed_anchors_reanchored': len(anchors), 'anchor_pending': len(rejected),
               'candidate_distinct_files': len({f['file'] for x in records for f in x['files']}),
               'reviewed_window_distinct_files': len({f['file'] for x in records if x['basis'] in REVIEWED_BASES for f in x['files']}),
               'reviewed_bounded_numbers': sum(x['basis'] in REVIEWED_BASES and bool(x['files']) for x in records),
               'section_end_bounded_numbers': sum(x['basis']=='reviewed_marker_and_section_end' and bool(x['files']) for x in records),
               'source_occurrences': len(links),
               'source_groups': len({x['occurrence']['group_id'] for x in links}),
               'source_pages_with_occurrences': sorted({groups[x['occurrence']['group_id']]['physical'] for x in links}),
               'source_pages_without_occurrences': sorted(set(range(profile['source']['physical_start'], profile['source']['physical_end']+1)) - {groups[x['occurrence']['group_id']]['physical'] for x in links}),
               'occurrence_statuses': dict(counts),
               'reviewed_window_occurrences': sum(bool(x['map']) and x['map']['basis'] in REVIEWED_BASES and bool(x['map']['files']) for x in links),
               'import_ready': False, 'db_updates': 0}
    out.mkdir(parents=True)
    artifacts = {'summary': summary, 'map': records, 'links': links, 'proofs': proofs,
                 'anchors': {'accepted': anchors, 'pending': rejected}, 'sources': series['sources'],
                 'config': {'profile': profile, 'profile_sha256':sha(profile_path), 'code_sha256':sha(__file__), 'survey':str(survey.resolve())}}
    for name, data in artifacts.items():
        (out/(name+'.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    return summary
