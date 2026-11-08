"""Recover explicitly reviewed OCR regions; no production import or destination lookup."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_experiment import digest, display, source_span, compare_rows
from tools.indexing.entry_parser_literal_rows import attributed_reference_groups
from tools.indexing.entry_parser_bulk import encoded, fingerprint, open_experiment, save
from tools.indexing.pl_section_map_experiment import unique_span
from tools.corpus_utils import page_number

VERSION = 'bounded-index-recovery-v1'
REMISSION = re.compile(r'\b(?:Vid\.|Vide|Confer|Cf\.)\s+[^.\n]+\.', re.I)
NUMERIC_LINE = re.compile(r'\d{1,4}(?:\s*,\s*\d{1,4})*\.')
QUESTION_TAIL = re.compile(r'\?\s*(?P<refs>\d{1,4}(?:\s*,\s*\d{1,4})*\.)$')


def reference_groups(raw, a, b):
    groups = attributed_reference_groups(raw, a, b, 'analytic-tome-rows-v1')
    match = QUESTION_TAIL.search(raw[a:b])
    if match and any(c.isalpha() for c in raw[a:a+match.start()]):
        start = a+match.start('refs')
        groups = [g for g in groups if g['char_end'] <= a+match.start()]
        owner_start = groups[-1]['char_end'] if groups else a
        while raw[owner_start].isspace():
            owner_start += 1
        owner_end = a+match.start()+1
        groups.append({'raw': raw[start:b], **source_span(raw, start, b),
                       'owner_text_raw': raw[owner_start:owner_end],
                       'owner_span': source_span(raw, owner_start, owner_end),
                       'namespace_status': 'literal_uninterpreted', 'inheritance_status': 'not_inferred',
                       'basis': 'question_followed_by_terminal_numeric_list'})
    return groups


def regions(raw, spec):
    result = []
    for item in spec['regions']:
        a = unique_span(raw, item['start_quote'])[0]
        b = unique_span(raw, item['end_quote'])[1]
        if a >= b or not item['block_types']:
            raise ValueError('Invalid reviewed region')
        result.append({'span': [a, b], 'block_types': item['block_types']})
    result.sort(key=lambda r: r['span'])
    if any(a['span'][1] > b['span'][0] for a, b in zip(result, result[1:])):
        raise ValueError('Overlapping reviewed regions')
    return result


def parse(raw, spec, source):
    scopes = regions(raw, spec)
    entries, residuals, events, selected = [], [], [], []
    checksum = digest(raw.encode())

    def emit(a, b, block, joined=False):
        text = raw[a:b]
        if '<' in text or '&' in text:
            residuals.append({'raw': text, 'reason': 'inline_markup_requires_alignment',
                              **source_span(raw, a, b)})
            return
        cross = [{'raw': m[0], **source_span(raw, a+m.start(), a+m.end())}
                 for m in REMISSION.finditer(text)]
        groups = reference_groups(raw, a, b)
        groups = [g for g in groups if not any(
            g['char_start'] < c['char_end'] and c['char_start'] < g['char_end'] for c in cross)]
        if not groups and not cross:
            residuals.append({'raw': text, 'reason': 'unrecognized_row_or_fragment',
                              **source_span(raw, a, b)})
            return
        flags = ['unvalidated_profile', 'ocr_row_not_certified_printed_lemma']
        if len(groups) > 1:
            flags.append('internal_clauses_preserved_not_independent_lemmas')
        if joined:
            flags.append('numeric_list_wrap')
        if any(re.search(r'\d', g['owner_text_raw']) or not g['owner_text_raw'] for g in groups):
            flags.append('reference_owner_requires_review')
        if any(re.search(r'\bibid\b', g['raw'], re.I) for g in groups):
            flags.append('inheritance_not_resolved')
        last = max([g['char_end'] for g in groups] + [c['char_end'] for c in cross])
        tail = raw[last:b].strip()
        if tail:
            flags.append('unparsed_trailing_text')
        if not text.endswith('.'):
            flags.append('open_fragment_at_region_end')
        owner = groups[0]['owner_text_raw'] if groups else text[:cross[0]['char_start']-a].strip()
        entries.append({'id': digest(f'{VERSION}:{source}:{checksum}:{a}:{b}'.encode())[:24],
            'source': source, 'source_sha256': checksum, 'volume': spec['volume'],
            'physical_page': spec['physical'], 'block': block,
            'section_candidate': spec['section_id'], 'observed_letter': None,
            'entry_type': 'mixed' if groups and cross else 'material' if groups else 'cross_reference',
            'entry_raw': text, 'text_raw': owner, 'text_display': display(owner),
            'reference_groups': groups, 'cross_references': cross,
            'references_raw': groups[0]['raw'] if len(groups) == 1 else None,
            'reference_span': ({k: groups[0][k] for k in source_span(raw, a, b)} if len(groups) == 1 else None),
            'flags': flags, 'status': 'candidate_only', 'unit_kind': 'ocr_row_candidate',
            'trailing_fragment': ({'raw': raw[last:b], **source_span(raw, last, b),
                                   'status': 'uninterpreted_not_attached_to_next_row'} if tail else None),
            'quality': {'region': 'reviewed_literal_scope', 'segmentation': 'candidate',
                        'reference_reading': 'not_verified', 'hierarchy': 'not_inferred',
                        'destination': 'not_attempted'},
            'verified': False, 'import_ready': False, **source_span(raw, a, b)})

    for i, block in enumerate(blocks(raw)):
        for scope in scopes:
            if block['kind'] not in scope['block_types']:
                continue
            a, b = max(block['body_start'], scope['span'][0]), min(block['body_end'], scope['span'][1])
            if a >= b:
                continue
            selected.append([a, b])
            lines = [(a+m.start()+len(m[0])-len(m[0].lstrip()), a+m.end()-len(m[0])+len(m[0].rstrip()))
                     for m in re.finditer(r'[^\n]+', raw[a:b]) if m[0].strip()]
            j = 0
            while j < len(lines):
                x, y = lines[j]
                if re.fullmatch('[A-ZÆŒ]', raw[x:y]):
                    events.append({'kind': 'observed_letter', 'raw': raw[x:y], **source_span(raw, x, y)})
                    j += 1
                    continue
                joined = False
                while j+1 < len(lines) and raw[x:y].endswith(','):
                    u, v = lines[j+1]
                    if (not NUMERIC_LINE.fullmatch(raw[u:v]) or re.search(r'\n\s*\n', raw[y:u])
                            or not attributed_reference_groups(raw, x, y, 'analytic-tome-rows-v1')):
                        break
                    y, j, joined = v, j+1, True
                emit(x, y, i, joined)
                j += 1

    if not selected:
        raise ValueError('No known OCR block selected by reviewed regions')

    # An independent character ledger does not assume that cabecalho is editorial.
    counts = Counter()
    claims = [('entry', e) for e in entries] + [('residual', r) for r in residuals] + [('editorial', e) for e in events]
    for scope in scopes:
        for block in blocks(raw):
            a, b = max(block['body_start'], scope['span'][0]), min(block['body_end'], scope['span'][1])
            for pos in range(a, b):
                if raw[pos].isspace():
                    continue
                labels = [kind for kind, c in claims if c['char_start'] <= pos < c['char_end']]
                label = ('overlap' if len(labels) > 1 else labels[0] if labels else
                         'excluded_block' if block['kind'] not in scope['block_types'] else 'unaccounted')
                counts[label] += 1
    if counts['overlap'] or counts['unaccounted']:
        raise ValueError('Regional conservation failed')
    return {'source': source, 'volume': spec['volume'], 'physical_page': spec['physical'], 'sha256': checksum,
            'profile': VERSION, 'scopes': scopes, 'selected_body_spans': selected,
            'entries': entries, 'residuals': residuals, 'events': events,
            'accounting': dict(counts), 'import_ready': False}


def run(profile_path, root, survey, out):
    if out.exists():
        raise ValueError('Use a new output directory')
    spec = json.loads(profile_path.read_text())
    if spec['version'] != VERSION:
        raise ValueError('Unsupported recovery profile')
    groups_path = survey/'groups.jsonl'
    groups = [json.loads(line) for line in groups_path.read_text().splitlines() if line.strip()]
    pages, checks, fingerprints = [], [], set()
    for page_spec in spec['pages']:
        source = (root/page_spec['volume']/'text'/page_spec['ocr_name']).resolve()
        if source.parent != (root/page_spec['volume']/'text').resolve() or source in fingerprints:
            raise ValueError('Invalid or repeated source')
        if page_number(source) != page_spec['physical']:
            raise ValueError('Physical source identity mismatch')
        fingerprints.add(source)
        image = root/page_spec['volume']/'images'/page_spec['image_name']
        if page_spec.get('visual_review') is not True or digest(image.read_bytes()) != page_spec['image_sha256']:
            raise ValueError('Reviewed image changed or review absent')
        raw = source.read_bytes().decode('utf-8')
        page = parse(raw, page_spec, str(source))
        page['review'] = {**page_spec, 'ocr_changed': page['sha256'] != page_spec['reviewed_ocr_sha256']}
        pages.append(page)
        prior = [g for g in groups if g['source'] == str(source)]
        checks.append({'physical': page_spec['physical'], 'source': str(source),
                       'prior_survey_groups': len(prior),
                       'prior_stale_groups': sum(g['source_sha256'] != page['sha256'] for g in prior),
                       'entries': len(page['entries']), 'reference_groups': sum(len(e['reference_groups']) for e in page['entries']),
                       'cross_references': sum(len(e['cross_references']) for e in page['entries']),
                       'open_fragments': sum('open_fragment_at_region_end' in e['flags'] for e in page['entries']),
                       'trailing_fragments': sum(e['trailing_fragment'] is not None for e in page['entries']),
                       'residual_rows': len(page['residuals']), 'accounting': page['accounting']})
    for page in pages:
        if digest(Path(page['source']).read_bytes()) != page['sha256']:
            raise ValueError('OCR changed during recovery')
    out.mkdir(parents=True)
    run_id = uuid.uuid4().hex
    config = {'version': VERSION, 'run_id': run_id, 'profile': spec,
              'profile_sha256': digest(profile_path.read_bytes()),
              'code_sha256': digest(Path(__file__).read_bytes()),
              'survey_groups_sha256': digest(groups_path.read_bytes())}
    config['dependencies_sha256'] = {name: digest(Path(__file__).with_name(name).read_bytes())
        for name in ('entry_parser_literal_rows.py', 'entry_parser_experiment.py',
                     'editorial_ocr_blocks.py', 'entry_parser_bulk.py', 'pl_section_map_experiment.py')}
    db = open_experiment(out/'experiment.sqlite', Path('data/alphabetical_indices.db'))
    try:
        with db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?)',
                       (run_id, datetime.now(timezone.utc).isoformat(), encoded(config), 'running'))
        for page in pages:
            task = {'source': page['source'], 'volume': page['volume'],
                    'physical': page['physical_page'], 'profile': VERSION,
                    'fingerprint': fingerprint({'config': config, 'source': page['source'], 'sha256': page['sha256']})}
            save(db, run_id, task, {'status': 'parsed', 'page': page,
                 'diffs': compare_rows(page, []), 'old_rows': [], 'error': None})
        with db:
            db.execute('UPDATE runs SET status=? WHERE run_id=?', ('completed', run_id))
    finally:
        db.close()
    summary = {'run_id': run_id, 'version': VERSION, 'pages': checks,
               'entries': sum(len(p['entries']) for p in pages),
               'api_calls': 0, 'db_updates': 0, 'import_ready': False,
               'coverage_basis': 'known OCR block bodies inside reviewed regions; not printed recall'}
    for name, value in [('manifest', config), ('pages', pages), ('summary', summary)]:
        (out/f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    for name in ('entries', 'residuals'):
        (out/f'{name}.jsonl').write_text(''.join(encoded({'source': p['source'], **e})+'\n' for p in pages for e in p[name]))
    return summary
