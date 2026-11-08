"""Offline tests. Loopback integration is separately opt-in under Native Lab."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from tools.indexing.multimodal.contract import SCHEMA, VERSION, digest
from tools.indexing.multimodal.reading import SCHEMA as READING_SCHEMA, VERSION as READING_VERSION
from tools.indexing.multimodal.sources import prepare_task, read_page, find_ocr, check_sources
from tools.indexing.multimodal.validation import validate
from tools.indexing.multimodal.staging import canonical, continuations, db_snapshot, diff
from tools.indexing.multimodal.http import Config, payload, call, reservation
from scripts.reextract_alphabetical_multimodal import main


def make_sources(tmp_path, number=1, text='INDEX NOMINUM\nAaron, 12.\nRoma, 770, 9.'):
    root = tmp_path / 'corpus'
    folder = root / 'PG113'
    (folder / 'text').mkdir(parents=True, exist_ok=True)
    (folder / 'images').mkdir(exist_ok=True)
    path = folder / 'text' / f'fixture-{number:03d}.txt'
    path.write_text('<pagina>\n<bloco tipo="texto_principal" script="latino">\n' + text + '\n</bloco>\n</pagina>\n')
    Image.new('RGB', (100, 160), 'white').save(folder / 'images' / f'fixture-{number:03d}.png')
    return root, path


def evidence(page, line, quote=None):
    return {'page_id': page['page_id'], 'block_id': line['block_id'], 'line_ids': [line['id']],
            'quote': line['text'] if quote is None else quote, 'visual_region_id': None,
            'visual_reading': None, 'corrected_fields': [], 'uncertain': False}


def dto_for(task):
    page = task['pages'][0]
    lines = page['lines']
    dto = {key: [] for key in SCHEMA['properties']}
    dto['schema_version'] = VERSION
    dto['segments'] = [{'id': 'seg', 'page_id': page['page_id'], 'line_ids': [l['id'] for l in lines],
        'role': 'owned', 'reason': 'Printed index', 'evidence': [evidence(page, lines[0])]}]
    dto['sections'] = [{'id': 'sec', 'segment_ids': ['seg'], 'heading_raw': lines[0]['text'],
        'kind': 'onomastic_mixed', 'taxonomy': {'pipeline_owner': 'alphabetical', 'alphabetical_role': 'owned_section',
        'material_reference_mode': 'remissive', 'scripture_mode': 'none'},
        'index_page_raw': None, 'scripture_profile': None, 'evidence': [evidence(page, lines[0])]}]
    dto['coverage'] = [{'page_id': page['page_id'], 'line_ids': [lines[0]['id']], 'disposition': 'heading', 'object_ids': ['sec'], 'reason': ''}]
    for n, line in enumerate(lines[1:], 1):
        lemma, rest = line['text'].split(', ', 1)
        ref_raw = rest.rstrip('.')
        numbers = ref_raw.split(', ')
        entry = {'id': f'e{n}', 'section_id': 'sec', 'parent_id': None, 'order': n, 'kind': 'lemma',
            'lemma_raw': lemma, 'lemma_display': lemma, 'entry_raw': line['text'], 'visual_entry_reading': None,
            'cross_reference_target': None, 'scripture_mode': None, 'evidence': [evidence(page, line)]}
        ref = {'id': f'r{n}', 'entry_id': entry['id'], 'order': 1, 'ref_raw': ref_raw, 'visual_ref_reading': None,
            'kind': 'editorial_page_line' if len(numbers) == 2 else 'editorial_page', 'page_start': numbers[0],
            'page_end': None, 'column': None, 'line_start': numbers[1] if len(numbers) == 2 else None,
            'line_end': None, 'edition': None, 'tome': None, 'series': None, 'rule_ids': [], 'scripture_id': None,
            'interpretation_evidence': 'printed locator', 'alternatives': [], 'evidence': [evidence(page, line, ref_raw)]}
        dto['entries'].append(entry)
        dto['references'].append(ref)
        dto['coverage'].append({'page_id': page['page_id'], 'line_ids': [line['id']], 'disposition': 'entry',
            'object_ids': [entry['id']], 'reason': ''})
    dto['visual_coverage'] = [{'region_id': r['id'], 'status': 'inspected', 'missing_ocr_content': '', 'reason': ''} for r in page['regions']]
    return dto


def reading_for(task):
    legacy = dto_for(task)
    section = legacy['sections'][0]
    entries = []
    for e in legacy['entries']:
        refs = [{k: v for k, v in r.items() if k in READING_SCHEMA['$defs']['entry']['properties']['references']['items']['properties']}
                | {'scripture_ref_number': None} for r in legacy['references'] if r['entry_id'] == e['id']]
        entries.append({'lemma': e['lemma_raw'], 'text': e['entry_raw'], 'kind': e['kind'],
            'cross_reference_target': None, 'references': refs, 'scripture_refs': [], 'children': [],
            'continuation': 'none', 'uncertainties': []})
    return {'schema_version': READING_VERSION, 'sections': [{'heading': section['heading_raw'],
        'kind': section['kind'], 'material_reference_mode': 'remissive', 'scripture_mode': 'none',
        'scripture_profile': None, 'index_page': None, 'conventions': [], 'entries': entries, 'edition_mappings': []}],
        'exclusions': [], 'uncertainties': []}


@pytest.fixture
def sample(tmp_path):
    root, path = make_sources(tmp_path)
    task = prepare_task(root, 'PG113', 1, tmp_path / 'out', 'ocr-image-crops', neighbors=0)
    return task, dto_for(task)


def database(path):
    # Use the existing schema, not a parallel approximation.
    from alphabetical_index_db import connect_db, init_schema
    con = connect_db(path)
    init_schema(con)
    con.close()
    return path


def test_pairing_offsets_native_crops(sample):
    task, dto = sample
    page = task['pages'][0]
    raw = Path(page['ocr_file']).read_text()
    assert len(page['lines']) == 3
    for line in page['lines']:
        assert raw[line['char_start']:line['char_end']] == line['text']
    assert [r['width'] for r in page['regions']] == [100, 55, 55]
    assert page['regions'][2]['crop_box'] == [45, 0, 100, 160]
    check_sources(task)


def test_missing_and_ambiguous_pairing(tmp_path):
    root, path = make_sources(tmp_path)
    image = root / 'PG113/images/fixture-001.png'
    image.unlink()
    with pytest.raises(ValueError, match='image_'):
        prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0)
    for name in ('a-001.png', 'b-001.png'):
        Image.new('RGB', (10, 10)).save(image.parent/name)
    with pytest.raises(ValueError, match='image_'):
        prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0)
    path.with_name('other-001.txt').write_text(path.read_text())
    with pytest.raises(ValueError, match='exactly one'):
        find_ocr(root, 'PG113', 1)


@pytest.mark.parametrize('bbox', ['0,0,1,1', '-999,99999,100000,200000', 'unreliable LLM estimate'])
def test_llm_bbox_never_controls_image_crops(tmp_path, bbox):
    root, path = make_sources(tmp_path)
    path.write_text(path.read_text().replace('script="latino"', f'script="latino" bbox="{bbox}"'))
    image_path = root / 'PG113/images/fixture-001.png'
    with Image.new('RGB', (100, 160)) as image:
        image.putdata([(x, y, (x + y) % 256) for y in range(160) for x in range(100)])
        image.save(image_path)
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image-crops', 0)
    page = task['pages'][0]
    assert page['blocks'][0]['bbox_raw'] == bbox
    assert page['blocks'][0]['bbox_space'] == 'unverified_ocr_coordinates'
    expected = [[0, 0, 100, 160], [0, 0, 55, 160], [45, 0, 100, 160]]
    assert [r['crop_box'] for r in page['regions']] == expected
    with Image.open(image_path) as original:
        for region, box in zip(page['regions'], expected):
            with Image.open(region['path']) as actual:
                assert actual.tobytes() == original.crop(box).tobytes()
    check_sources(task)


def test_xml_inline_and_soft_wrap_preserved(tmp_path):
    _, path = make_sources(tmp_path, text='INDEX\nALEXAN-\nDRINUS, <i>125</i>.')
    page = read_page(path)
    assert [l['text'] for l in page['lines']] == ['INDEX', 'ALEXAN-', 'DRINUS,', '125', '.']
    assert 'ALEXAN-\nDRINUS' in page['ocr_xml']


@pytest.mark.parametrize('mode', ['chat', 'responses'])
def test_payload_original_flex_strict_and_no_base64_logs(sample, mode):
    task, _ = sample
    config = Config(api_mode=mode)
    request = payload(task, config, 'instruction', materialize=True)
    assert request['service_tier'] == 'flex'
    assert 'data:image/png;base64,' in json.dumps(request)
    assert '"detail": "original"' in json.dumps(request)
    assert 'base64,' not in json.dumps(payload(task, config, 'instruction'))
    fmt = request['response_format']['json_schema'] if mode == 'chat' else request['text']['format']
    assert fmt['strict'] and fmt['schema'] == READING_SCHEMA
    def check(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    # Only the LLM schema must require every field. Internal staging also accepts
    # optional Python-computed offsets for backwards-compatible evidence replay.
    check(READING_SCHEMA)


def test_config_keys_and_tier(monkeypatch):
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-key')
    assert Config().key() == 'test-only-key'
    monkeypatch.setenv('LLM_API_KEY', 'preferred')
    assert Config().key() == 'preferred'
    with pytest.raises(ValueError, match='Flex only'):
        Config(service_tier=None).validate()
    with pytest.raises(ValueError, match='original'):
        Config(model='gpt-4.1').validate()
    with pytest.raises(ValueError):
        Config(timeout=float('nan')).validate()


def test_valid_dto_and_canonical_page_line(sample):
    task, dto = sample
    result = validate(dto, task)
    assert result['valid'], result
    candidate, report = canonical(dto, result, task)
    assert report['valid'], report
    assert not report['import_ready']
    ref = candidate['refs'][1]
    assert ref['cited_editorial_page_start_number'] == 770
    assert ref['cited_editorial_line_start_number'] == 9
    assert ref['target_file'] is None


@pytest.mark.parametrize('mutation,expected', [
    (lambda d: d.pop('segments'), 'required'),
    (lambda d: d['entries'][0]['evidence'][0].update(quote='Invented'), 'quote'),
    (lambda d: d['entries'][0].update(lemma_raw='Wrong'), 'lemma_raw'),
    (lambda d: d['entries'][0].update(lemma_display='Wrong'), 'lemma repair'),
    (lambda d: d['references'][0].update(entry_id='e2'), 'owning entry'),
    (lambda d: d['references'][1].update(kind='editorial_page'), 'line number'),
    (lambda d: d['references'][0].update(page_start='999'), 'printed reference'),
    (lambda d: d['references'][0].update(edition='Verona'), 'namespace'),
    (lambda d: d['coverage'].pop(), 'exactly once'),
    (lambda d: d['entries'][0]['evidence'][0].update(visual_region_id='invented', visual_reading='Aaron'), 'not sent'),
])
def test_invalid_dto_failures(sample, mutation, expected):
    task, dto = sample
    mutation(dto)
    result = validate(dto, task)
    assert not result['valid']
    assert expected in str(result['errors'])


def test_rule_scope_and_namespace(sample):
    task, dto = sample
    ref = dto['references'][1]
    ev = dto['entries'][1]['evidence']
    rule = {'id': 'rule', 'section_ids': ['sec'], 'segment_ids': ['seg'], 'kind': 'explicit_declaration',
        'literal': ev[0]['quote'], 'edition': 'Petropolitana', 'tome': None, 'series': 'Leo',
        'notation': 'page,line', 'evidence': ev}
    dto['editorial_rules'].append(rule)
    ref.update(edition='Petropolitana', series='Leo', rule_ids=['rule'])
    assert validate(dto, task)['valid']
    rule['section_ids'] = []
    assert 'outside its section' in str(validate(dto, task)['errors'])


def test_boundary_inside_page(sample):
    task, dto = sample
    path = Path(task['pages'][0]['ocr_file'])
    path.write_text(path.read_text().replace('Roma, 770, 9.', 'ORDO RERUM'))
    task['pages'][0] = read_page(path)
    task['primary_page_id'] = task['pages'][0]['page_id']
    task['pages'][0]['role'] = 'primary'
    # The mechanical classifier independently prevents ownership of ORDO.
    page = task['pages'][0]
    dto = dto_for({**task, 'pages': [{**page, 'lines': page['lines'][:2]}]})
    dto['segments'][0]['line_ids'].append(page['lines'][2]['id'])
    assert 'boundary heading' in str(validate(dto, task)['errors'])
    dto['segments'][0]['line_ids'].pop()
    dto['segments'].append({'id': 'boundary', 'page_id': page['page_id'], 'line_ids': [page['lines'][2]['id']],
        'role': 'boundary', 'reason': 'ORDO belongs to general pipeline', 'evidence': [evidence(page, page['lines'][2])]})
    dto['coverage'].append({'page_id': page['page_id'], 'line_ids': [page['lines'][2]['id']],
        'disposition': 'boundary', 'object_ids': ['boundary'], 'reason': 'general pipeline'})
    assert validate(dto, task)['valid']


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status_code = body, status
        self.headers, self.text = headers or {}, json.dumps(body)
    def json(self):
        return self.body


@pytest.mark.parametrize('kind,expected', [('valid', 'received'), ('invalid', 'invalid_response'),
    ('refusal', 'refused'), ('truncated', 'incomplete'), ('original', 'api_error'), ('schema', 'api_error'),
    ('tier', 'api_error'), ('timeout', 'api_error')])
def test_http_mock_outcomes(sample, monkeypatch, kind, expected):
    import requests
    task, dto = sample
    body = {'model': 'gpt-5.4-mini', 'service_tier': 'flex', 'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(dto)}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
    status = 200
    if kind == 'invalid': body['choices'][0]['message']['content'] = 'not json'
    if kind == 'refusal': body['choices'][0]['message']['refusal'] = 'refused'
    if kind == 'truncated': body['choices'][0]['finish_reason'] = 'length'
    if kind in ('original', 'schema'):
        body, status = {'error': {'message': kind + ' unsupported; secret-test'}}, 400
    if kind == 'tier': body['service_tier'] = 'default'
    def post(*args, **kwargs):
        assert kwargs['json']['service_tier'] == 'flex'
        assert kwargs['allow_redirects'] is False
        if kind == 'timeout': raise requests.Timeout()
        return Response(body, status)
    monkeypatch.setattr(requests, 'post', post)
    config = Config()
    config.validate()
    result = call(task, config, 'prompt', 'secret-test', 1)
    assert result['status'] == expected
    assert 'secret-test' not in json.dumps(result)
    if kind in ('original', 'schema'):
        assert 'explicit_configuration_required' in result['error']
    if kind == 'valid':
        assert result['reported_cost_usd'] is None
        assert result['estimated_cost_usd'] > 0


def test_responses_adapter(sample, monkeypatch):
    import requests
    task, dto = sample
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: Response({'status': 'completed', 'service_tier': 'flex',
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(dto)}]}]}))
    assert call(task, Config(api_mode='responses'), 'prompt', 'fake', 1)['dto'] == dto


def test_no_api_and_db_unchanged(tmp_path, monkeypatch):
    root, _ = make_sources(tmp_path)
    db = database(tmp_path/'db.sqlite')
    before = db.read_bytes()
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: pytest.fail('no-api made a request'))
    out = tmp_path/'prepared'
    assert main(['--volume', 'PG113', '--page', '1', '--root', str(root), '--db', str(db),
        '--no-api', '--limit', '0', '--out', str(out)]) == 0
    assert json.loads((out/'summary.json').read_text())['calls'] == 0
    assert before == db.read_bytes()


def test_checkpoint_invalidation(tmp_path):
    from tools.indexing.multimodal.contract import write_json
    from tools.indexing.multimodal.runner import checkpoint_matches
    write_json(tmp_path/'response.json', {'status': 'received', 'dto': {}})
    write_json(tmp_path/'checkpoint.json', {'input_fingerprint': 'abc', 'response_sha256': digest((tmp_path/'response.json').read_bytes())})
    assert checkpoint_matches(tmp_path, 'abc')
    assert checkpoint_matches(tmp_path, 'changed') is None
    (tmp_path/'response.json').write_text('{}')
    assert checkpoint_matches(tmp_path, 'abc') is None


def test_real_boundary_hints():
    from tools.indexing.alphabetical_mechanical_analysis import detect_heading
    for volume, number in [('PG113', 666), ('PG066', 906)]:
        path = find_ocr(ROOT/'teste', volume, number)
        assert any(l['heading_hint'] and l['heading_hint']['role'] == 'stop_boundary' for l in read_page(path)['lines'])


def test_schema_artifact_matches_code():
    assert json.loads((ROOT/'schemas/alphabetical_multimodal_extraction.schema.json').read_text()) == SCHEMA


def test_visual_repair_keeps_ocr_raw(sample):
    task, dto = sample
    e = dto['entries'][0]
    e['lemma_display'] = 'AARON'
    e['evidence'][0].update(visual_region_id=task['pages'][0]['regions'][0]['id'],
        visual_reading='AARON', corrected_fields=['lemma_display'])
    assert validate(dto, task)['valid']
    assert e['lemma_raw'] == 'Aaron'
    task['pages'][0]['regions'] = []
    assert not validate(dto, task)['valid']


def test_confirmed_continuity_and_pending(tmp_path):
    root, _ = make_sources(tmp_path, 1)
    make_sources(tmp_path, 2)
    tasks = [prepare_task(root, 'PG113', n, tmp_path/'out', 'ocr-only', 1) for n in (1, 2)]
    results = []
    for i, task in enumerate(tasks):
        dto = dto_for(task)
        dto['continuations'] = [{'id': 'cont', 'entry_id': 'e1', 'direction': 'trailing' if i == 0 else 'leading',
            'peer_page_id': tasks[1-i]['primary_page_id'], 'peer_entry_id': None, 'peer_quote': 'Aaron, 12.',
            'status': 'confirmed', 'reason': 'test reciprocal endpoint', 'evidence': dto['entries'][0]['evidence']}]
        validated = validate(dto, task)
        assert validated['valid'], validated
        results.append({'task': task, 'dto': dto, 'validation': validated})
    report = continuations(results)
    assert len(report['stitches']) == 1
    assert len(report['stitches'][0]['reference_fragments']) == 2
    assert len(report['fragment_requests']) == 2
    results[1]['dto']['continuations'][0]['status'] = 'open'
    assert continuations(results)['stitches'] == []


def test_column_continuity_in_same_page(sample):
    task, dto = sample
    dto['continuations'] = []
    for i, direction in [(0, 'trailing'), (1, 'leading')]:
        dto['continuations'].append({'id': 'c'+str(i), 'entry_id': dto['entries'][i]['id'],
            'direction': direction, 'peer_page_id': task['primary_page_id'], 'peer_entry_id': dto['entries'][1-i]['id'],
            'peer_quote': dto['entries'][1-i]['entry_raw'], 'status': 'confirmed', 'reason': 'column wrap',
            'evidence': dto['entries'][i]['evidence']})
    checked = validate(dto, task)
    assert checked['valid']
    assert len(continuations([{'task': task, 'dto': dto, 'validation': checked}])['stitches']) == 1


def test_benchmark_measures_assignment_not_counts(sample):
    from tools.indexing.multimodal.benchmark import annotated_metrics
    task, dto = sample
    page = task['pages'][0]
    annotation = {'ocr_sha256': page['ocr_sha256'], 'review': 'synthetic test', 'scopes': [{
        'line_ids': [l['id'] for l in page['lines'][1:]], 'entries': [
            {'lemma_raw': e['lemma_raw'], 'entry_raw': e['entry_raw'], 'references': [
                {k: r[k] for k in ('ref_raw', 'page_start', 'line_start', 'series')} for r in dto['references'] if r['entry_id'] == e['id']]}
            for e in dto['entries']]}]}
    metrics = annotated_metrics(dto, task, annotation)
    assert metrics['segmentation']['recall'] == 1
    assert metrics['reference_attribution_and_interpretation']['precision'] == 1
    dto['references'][0]['entry_id'] = 'e2'
    metrics = annotated_metrics(dto, task, annotation)
    assert metrics['reference_attribution_and_interpretation']['precision'] == .5


def test_revision_application_backup_preserves_originals(tmp_path, sample):
    from tools.indexing.multimodal.runner import stage
    from tools.indexing.multimodal.contract import write_json
    from scripts.apply_alphabetical_reextraction_revision import prepare, apply
    task, dto = sample
    db = database(tmp_path/'db.sqlite')
    from scripts.import_alphabetical_index_json import import_payload
    from alphabetical_index_db import connect_db
    existing, _ = canonical(dto, validate(dto, task), task)
    con = connect_db(db)
    import_payload(con, existing, replace=False)
    con.execute("UPDATE alphabetical_refs SET target_file='/fixture/body-012.txt', resolved_target_ocr_file='/fixture/body-012.txt', raw_json=json_set(raw_json, '$.jev_evidence', 'preserve-this-evidence')")
    con.commit()
    con.close()
    directory = tmp_path/'staging'
    write_json(directory/'task.json', task)
    stage({'task': task, 'directory': directory, 'fingerprint': 'test'}, {'status': 'received', 'dto': dto}, db)
    plan = tmp_path/'review.json'
    prepare(directory, plan)
    with pytest.raises(ValueError, match='approved'):
        apply(plan, db, tmp_path/'backup.sqlite')
    reviewed = json.loads(plan.read_text())
    reviewed.update(approved=True, reviewer='test fixture', reason='Verified synthetic extraction')
    write_json(plan, reviewed)
    report = apply(plan, db, tmp_path/'backup.sqlite')
    assert report['canonical_rows_modified'] == 0
    with sqlite3.connect(db) as con, sqlite3.connect(tmp_path/'backup.sqlite') as backup:
        assert con.execute('SELECT COUNT(*) FROM alphabetical_reextraction_revisions').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM alphabetical_refs').fetchone()[0] == 2
        for table in ('alphabetical_entries', 'alphabetical_refs', 'alphabetical_locator_evidence'):
            assert con.execute(f'SELECT * FROM {table}').fetchall() == backup.execute(f'SELECT * FROM {table}').fetchall()


def test_fragment_selection_does_not_resend_whole_ocr(sample, tmp_path):
    task, dto = sample
    page = task['pages'][0]
    root = Path(page['ocr_file']).parents[2]
    partial = prepare_task(root, 'PG113', 1, tmp_path/'partial', 'ocr-only', 0,
                           line_selection=[page['lines'][1]['id']])
    assert partial['scope'] == 'fragment'
    assert partial['pages'][0]['ocr_xml'] == 'Aaron, 12.'
