"""Reconcile bounded recoveries and apply scoped maps in a fresh experiment only."""
from collections import Counter
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from tools.indexing.entry_parser_experiment import digest, ROOT
from tools.indexing.global_target_survey import SIMPLE_LIST
from tools.indexing.pl_section_map_experiment import bounded_text, REVIEWED_BASES
from tools.indexing.recovered_index_consolidation import merge_page
from tools.indexing.structured_index_experiment import (
    SELECTION_VERSION, check_span, consolidate, encoded, key, readonly, resolve,
)

VERSION = 'recovered-section-links-v1'


def json_file(path):
    return json.loads(Path(path).read_text())


def selected_baseline(source, config):
    selected, history = None, []
    with closing(readonly(config['experiment_db'])) as db:
        db.execute('BEGIN')
        for run in config['parser_runs']:
            state = db.execute('SELECT status FROM runs WHERE run_id=?', (run,)).fetchone()
            if not state or state[0] != 'completed':
                raise ValueError('Baseline parser run missing or incomplete')
            rows = db.execute('''SELECT t.result_json FROM run_pages p JOIN tasks t USING(fingerprint)
                WHERE p.run_id=? AND p.source=?''', (run, source)).fetchall()
            if len(rows) > 1:
                raise ValueError('Ambiguous baseline page')
            if rows:
                result = json.loads(rows[0][0])
                if not result.get('page'):
                    raise ValueError('Baseline task error requires explicit review')
                selected = result['page']
                history.append({'run': run, 'result_sha256': digest(rows[0][0].encode())})
    with closing(readonly(config['structured_db'])) as db:
        db.execute('BEGIN')
        row = db.execute('SELECT page_json FROM sources WHERE path=?', (source,)).fetchone()
        if row:
            selected = json.loads(row[0])
            history.append({'selection': 'consolidated', 'result_sha256': digest(row[0].encode())})
    return selected, history


def reconcile(page, baseline):
    source = page['source']
    raw = Path(source).read_text()
    if digest(raw.encode()) != page['sha256'] or (baseline and baseline['sha256'] != page['sha256']):
        raise ValueError('Stale reconciliation source')
    # The shared merger refuses even exact overlaps; no silent version election.
    merged, counts = merge_page(raw, source, page['physical_page'], baseline, page['entries'])
    merged['recovery_evidence'] = {k: page[k] for k in ('review', 'scopes', 'accounting')}
    merged['events'] = (baseline or {}).get('events', []) + page.get('events', [])
    merged['residuals'] += page.get('residuals', [])
    return merged, counts


def checked_map(directory, root):
    config = json_file(directory/'config.json')
    profile = config['profile']
    sources = dict(json_file(directory/'sources.json'))
    for path, checksum in sources.items():
        if digest(Path(path).read_bytes()) != checksum:
            raise ValueError('Stale target map: rebuild after OCR correction')
    for proof in json_file(directory/'proofs.json'):
        raw = Path(proof['path']).read_text()
        if digest(raw.encode()) != proof['sha256']:
            raise ValueError('Stale map editorial proof')
        check_span(raw, proof, proof['quote'])
    image_proofs = profile['anchors'] + profile['proofs']
    terminal = profile['target'].get('section_end')
    if terminal:
        image_proofs += [terminal]
    image_proofs += profile['target'].get('reading_order', [])
    for proof in image_proofs:
        path = root/'teste'/profile['volume']/'images'/proof['image_name']
        if digest(path.read_bytes()) != proof['image_sha256']:
            raise ValueError('Stale map image review')
    records = json_file(directory/'map.json')
    if len({r['number'] for r in records}) != len(records):
        raise ValueError('Duplicate map numbers')
    for record in records:
        for file in record['files']:
            if sources.get(file['file']) != file['sha256']:
                raise ValueError('Mapped file outside checked target sources')
    return {'directory': str(directory), 'profile': profile,
            'records': {r['number']: r for r in records},
            'map_sha256': digest((directory/'map.json').read_bytes())}


def source_findings(recovery, pages):
    findings = json_file(recovery/'visual_review.json')['source_findings']
    entries = {e['id']: (p, e) for p in pages for e in p['entries']}
    blocked = set()
    for finding in findings:
        if finding['candidate_id'] not in entries:
            raise ValueError('Review refers to an absent recovery candidate')
        page, entry = entries[finding['candidate_id']]
        if finding['source'] != page['source'] or finding['source_sha256'] != page['sha256']:
            raise ValueError('Stale source-quality review')
        raw = Path(page['source']).read_text()
        check_span(raw, finding, finding['ocr_raw'], (entry['char_start'], entry['char_end']))
        if digest(Path(finding['image']).read_bytes()) != finding['image_sha256']:
            raise ValueError('Stale source-quality image')
        blocked.add(entry['id'])
        entry.setdefault('quality', {})['source_findings'] = [
            f for f in findings if f['candidate_id'] == entry['id']]
    return blocked


def route_groups(page, routes, maps, blocked):
    raw = Path(page['source']).read_text()
    records = []
    sid = key('source', page['source'], page['sha256'])
    for entry in page['entries']:
        section = entry.get('section_candidate')
        route = routes.get(section)
        for group in entry['reference_groups']:
            a, b = check_span(raw, group, group['raw'], (entry['char_start'], entry['char_end']))
            status = ('source_reading_conflict' if entry['id'] in blocked else
                      'reference_owner_requires_review' if 'reference_owner_requires_review' in entry['flags'] else
                      'unmapped_section' if not route else
                      'unsupported_literal_or_inheritance' if not SIMPLE_LIST.fullmatch(group['raw']) else
                      'eligible_numeric_list')
            record = {'reference_id': key('reference', sid, a, b), 'candidate_id': entry['id'],
                      'source': page['source'], 'source_sha256': page['sha256'],
                      'section': section, 'group': group, 'status': status, 'occurrences': [],
                      'verified': False, 'import_ready': False}
            if status == 'eligible_numeric_list':
                if route['volume'] != page['volume']:
                    raise ValueError('Section route volume mismatch')
                for match in re.finditer(r'\d+', group['raw']):
                    number = int(match[0])
                    branches = []
                    for mapping in maps[section]:
                        profile = mapping['profile']
                        if profile['volume'] != page['volume']:
                            raise ValueError('Map volume mismatch')
                        source = profile['source']
                        if not source['physical_start'] <= page['physical_page'] <= source['physical_end']:
                            raise ValueError('Recovered occurrence outside map source scope')
                        _, lo, hi = bounded_text(raw, source.get('bounds', {}).get(str(page['physical_page']), {}))
                        if not lo <= a < b <= hi:
                            raise ValueError('Recovered occurrence crosses map section boundary')
                        found = mapping['records'].get(number)
                        if found:
                            branches.append({'profile': profile['id'], 'map_sha256': mapping['map_sha256'], 'mapping': found})
                    reviewed, diagnostic = {}, {}
                    for branch in branches:
                        m = branch['mapping']
                        if m['status'] != 'bounded_window_candidate':
                            continue
                        dest = reviewed if m['basis'] in REVIEWED_BASES else diagnostic
                        for f in m['files']:
                            dest[f['file']] = f
                    state = ('duplicated_number_alternatives_pending' if number in route['duplicated_numbers'] else
                             'reviewed_window_candidate' if reviewed else
                             'sequence_probe_not_verified' if diagnostic else
                             'outside_bounded_map' if not branches else 'missing_or_ambiguous_boundary')
                    record['occurrences'].append({'id': key(record['reference_id'], a+match.start(), a+match.end()),
                        'number': number, 'raw': match[0], 'char_start': a+match.start(), 'char_end': a+match.end(),
                        'status': state, 'candidate_files': list(reviewed.values()),
                        'diagnostic_files': list(diagnostic.values()), 'branches': branches,
                        'verified': False, 'import_ready': False})
            records.append(record)
    return records


def run(recovery, baseline_config, route_path, out, root=ROOT):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Use a fresh output directory')
    pages = deepcopy(json_file(recovery/'pages.json'))
    routes = json_file(route_path)
    if routes['version'] != 'recovered-section-routes-v1':
        raise ValueError('Unsupported routes')
    routes = routes['sections']
    config = json_file(baseline_config)
    maps = {section: [checked_map(resolve(p, root), root) for p in route['maps']]
            for section, route in routes.items()}
    blocked = source_findings(recovery, pages)
    reconciled, comparisons, groups = [], [], []
    for page in pages:
        baseline, history = selected_baseline(page['source'], config)
        merged, counts = reconcile(page, baseline)
        reconciled.append(merged)
        comparisons.append({'source': page['source'], 'physical': page['physical_page'],
                            'baseline_selection_history': history, **counts})
        groups.extend(route_groups(page, routes, maps, blocked))
    out.mkdir(parents=True)
    (out/'pages').mkdir()
    selection = {'version': SELECTION_VERSION, 'sources': [], 'scope': 'supplemental_recovery_pages_only'}
    for page in reconciled:
        path = out/'pages'/f"{key(page['source'])[:24]}.json"
        path.write_text(encoded(page)+'\n')
        selection['sources'].append({'source': page['source'], 'physical': page['physical_page'],
            'sha256': page['sha256'], 'artifact': str(path), 'units': len(page['entries'])})
    selection_path = out/'selection.json'
    selection_path.write_text(encoded(selection)+'\n')
    summary = consolidate(selection_path, out/'structured', root=root, sample_size=12)
    db = sqlite3.connect(out/'structured/index.sqlite')
    try:
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            db.execute('CREATE TABLE recovered_reference_routes(reference_id TEXT PRIMARY KEY REFERENCES reference_groups, status TEXT, evidence_json TEXT)')
            db.execute('CREATE TABLE recovered_numeric_candidates(occurrence_id TEXT PRIMARY KEY, reference_id TEXT REFERENCES recovered_reference_routes, number INTEGER, status TEXT, evidence_json TEXT)')
            for group in groups:
                db.execute('INSERT INTO recovered_reference_routes VALUES(?,?,?)', (group['reference_id'], group['status'], encoded(group)))
                for occurrence in group['occurrences']:
                    db.execute('INSERT INTO recovered_numeric_candidates VALUES(?,?,?,?,?)',
                               (occurrence['id'], group['reference_id'], occurrence['number'], occurrence['status'], encoded(occurrence)))
    finally:
        db.close()
    occurrences = [o for g in groups for o in g['occurrences']]
    summary.update(linking_version=VERSION, scope='supplemental_recovery_pages_only',
        group_statuses=dict(Counter(g['status'] for g in groups)),
        numeric_occurrences=len(occurrences), occurrence_statuses=dict(Counter(o['status'] for o in occurrences)),
        with_reviewed_candidate_files=sum(bool(o['candidate_files']) for o in occurrences),
        with_diagnostic_files=sum(bool(o['diagnostic_files']) for o in occurrences),
        source_conflict_candidates=len(blocked), reconciliation=comparisons)
    for name, value in [('summary', summary), ('reconciliation', comparisons)]:
        (out/f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    (out/'reference_routes.jsonl').write_text(''.join(encoded(g)+'\n' for g in groups))
    provenance = [recovery/'pages.json', recovery/'visual_review.json', baseline_config, route_path]
    provenance += [Path(m['directory'])/name for ms in maps.values() for m in ms
                   for name in ('map.json', 'sources.json', 'proofs.json', 'config.json')]
    (out/'manifest.json').write_text(encoded({'version': VERSION, 'inputs': {str(p.resolve()): digest(p.read_bytes()) for p in provenance},
        'code_sha256': digest(Path(__file__).read_bytes()),
        'dependencies_sha256': {name: digest(Path(__file__).with_name(name).read_bytes()) for name in
            ('structured_index_experiment.py', 'recovered_index_consolidation.py',
             'global_target_survey.py', 'pl_section_map_experiment.py')},
        'selection_policy': 'same as baseline; merge only nonoverlapping recovery spans; no global replacement'})+'\n')
    report = ['# Recuperação conciliada e mapas por seção', '',
              'Seleção suplementar das fontes recuperadas; não substitui a seleção global.',
              'Bancos anteriores e OCR somente leitura. Todos os resultados exigem revisão.', '',
              '| Fonte física | Unidades anteriores | Acrescentadas |', '|---|---:|---:|']
    report += [f"| {r['physical']} | {r['baseline_units']} | {r['added_units']} |" for r in comparisons]
    report += ['', '| Estado dos números elegíveis | Ocorrências |', '|---|---:|']
    report += [f'| {status} | {count} |' for status, count in summary['occurrence_statuses'].items()]
    report += ['', 'Grupos sem interpretação numérica: '+encoded(summary['group_statuses']),
               'Janelas conferidas não certificam leitura do índice ou atribuição ao proprietário.',
               'Duplicações editoriais conservam alternativas, mesmo quando só uma foi delimitada.',
               'Referências não suportadas e conflitos de fonte permanecem no SQLite, sem números expandidos.',
               '`structured/index.sqlite`: unidades, cláusulas, referências, busca FTS e as tabelas',
               '`recovered_reference_routes` / `recovered_numeric_candidates` com evidências completas.',
               '`reference_routes.jsonl`: grupos e candidatos separados por seção.',
               '`selection.json`: seleção explícita apenas das páginas suplementares.',
               'Nenhum vínculo foi aplicado ao banco original ou publicado.']
    (out/'report.md').write_text('\n'.join(report)+'\n')
    return summary
