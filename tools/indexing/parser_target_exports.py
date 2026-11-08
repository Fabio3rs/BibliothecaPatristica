"""Evidence-preserving offline exports; no inference or resolution promotion."""
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_jsonl(path, records):
    with path.open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')


def editorial_maps(series_by_key, bindings):
    maps = {}
    for key, series in sorted(series_by_key.items()):
        markers = []
        for (number, physical), support in sorted(series['supported'].items()):
            page = series['pages'][physical]
            markers.append({'number': number, 'physical': physical, 'file': page['file'],
                'sha256': page['sha256'], 'sequence_support': support,
                'occurrences': page.get('occurrences', {}).get(number, []),
                'header': page['header']})
        record = {'version': 'editorial-candidate-map-v1', 'spec': series['spec'],
                  'method': series['method'], 'binding': bindings[key],
                  'sources': sorted(series['sources']), 'markers': markers,
                  'verified': False, 'application_supported': False}
        record['map_id'] = digest(record)
        maps[key] = record
    return maps


def validate_job_sources(job):
    """Consumers must recheck before inference; paths alone are not identities."""
    for source in [job['source'], *job['candidate_files']]:
        path = Path(source['file'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError(f'Stale semantic candidate source: {path}')
    source = job['source']
    raw = Path(source['file']).read_bytes().decode('utf-8')
    a, b = source['entry_span']
    if raw[a:b] != job['entry_raw']:
        raise ValueError('Semantic candidate entry span differs from OCR')
    span = job['reference'].get('source_span')
    if span and (not a <= span[0] < span[1] <= b or
                 raw[span[0]:span[1]] != job['reference']['ref_raw']):
        raise ValueError('Semantic candidate reference span differs from OCR')
    tome_span = job['reference'].get('tome_source_span')
    if tome_span and (not a <= tome_span[0] < tome_span[1] <= b or
                     raw[tome_span[0]:tome_span[1]] != job['reference']['tome']):
        raise ValueError('Semantic candidate tome span differs from OCR')


def review_queues(records, maps):
    jobs, pending = [], []
    for record in records:
        entry = record['entry']
        for reference in record['references']:
            mapping = maps.get(reference['series_key'])
            item = {'version': 'parser-semantic-candidate-v1',
                    'fingerprint': record['fingerprint'], 'candidate_id': entry['id'],
                    'query_text': record['query_text'], 'entry_raw': entry['entry_raw'],
                    'query_context_status': 'unverified',
                    'context_candidate_id': entry.get('context_candidate_id'),
                    'entry_flags': entry.get('flags', []),
                    'source': {'file': record['source'], 'sha256': record['source_sha256'],
                               'entry_span': [entry['char_start'], entry['char_end']],
                               'volume': record['volume'], 'physical': record['physical']},
                    'reference': reference, 'map_id': mapping['map_id'] if mapping else None,
                    'candidate_files': reference['files'], 'verified': False,
                    'application_supported': False, 'gold_label': None,
                    'purpose': 'semantic_review_of_mechanical_candidates'}
            item['job_id'] = digest(item)
            validate_job_sources(item)
            if reference['status'] in {'bounded_window_candidate', 'header_page_candidate'}:
                jobs.append(item)
            else:
                pending.append(item)
    return jobs, pending
