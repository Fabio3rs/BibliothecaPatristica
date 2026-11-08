"""Literal-only profile: transcribe first, preserve ambiguity, never mutate DB."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from test_multimodal_reextraction import make_sources, database, main
from scripts.reextract_alphabetical_multimodal import parser, selections
from tools.indexing.multimodal import literal
from tools.indexing.multimodal.audit import replay_run
from tools.indexing.multimodal.benchmark import compare
from tools.indexing.multimodal.contract import digest, write_json
from tools.indexing.multimodal.http import Config, payload
from tools.indexing.multimodal.runner import stage, checkpoint_matches
from tools.indexing.multimodal.sources import prepare_task


def entry(name='Aaron', text='Aaron, 12.', refs='12.'):
    return {'type': 'entry', 'name': name, 'text': text, 'references_raw': refs,
            'children': [], 'continuation': 'none'}


def reading():
    return {'elements': [{'type': 'heading', 'role': 'section', 'text': 'INDEX NOMINUM',
                         'children': [entry(), entry('Roma', 'Roma, 770, 9.', '770, 9.')]}],
            'uncertainties': []}


@pytest.fixture
def task(tmp_path):
    root, _ = make_sources(tmp_path)
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0, 'never')
    task['extraction_profile'] = 'literal-v1'
    return task


def test_schema_strict_and_versioned():
    Draft202012Validator.check_schema(literal.SCHEMA)
    assert json.loads(Path('schemas/alphabetical_literal.schema.json').read_text()) == literal.SCHEMA
    def check(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['properties']) == set(node['required'])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(literal.SCHEMA)


@pytest.mark.parametrize('mode', ['chat', 'responses'])
def test_payload_single_full_image_and_no_legacy_hints(task, mode):
    config = Config(api_mode=mode)
    result = payload(task, config, literal.PROMPT, materialize=True)
    blocks = result['messages'][1]['content'] if mode == 'chat' else result['input'][0]['content']
    images = [b for b in blocks if b['type'] in ('image_url', 'input_image')]
    assert len(images) == 1
    image = images[0]['image_url'] if mode == 'chat' else images[0]
    assert image['detail'] == 'original'
    url = image['url'] if mode == 'chat' else image['image_url']
    assert url.startswith('data:image/png;base64,')
    texts = '\n'.join(b.get('text', '') for b in blocks)
    assert task['pages'][0]['ocr_xml'] in texts
    assert texts.count('OCR XML:') == 1
    assert 'Editorial caution' not in texts and 'CONTEXT ONLY' not in texts
    assert result['service_tier'] == 'flex'
    assert (result.get('reasoning_effort') or result['reasoning']['effort']) == 'medium'
    assert not {'max_tokens', 'max_output_tokens', 'max_completion_tokens'} & result.keys()
    schema = result['response_format']['json_schema'] if mode == 'chat' else result['text']['format']
    assert schema['strict'] is True and schema['schema'] == literal.SCHEMA


def test_pilot_and_configuration_guards(tmp_path):
    args = parser().parse_args(['--pilot', 'literal-20', '--out', str(tmp_path/'out')])
    chosen = selections(args)
    assert len(chosen) == len(set(chosen)) == 20
    assert ('PG040', 427) in chosen and ('PG113', 653) in chosen
    with pytest.raises(SystemExit):
        main(['--extraction-profile', 'invalid', '--out', str(tmp_path/'bad')])
    assert main(['--pilot', 'literal-20', '--out', str(tmp_path/'out')]) == 2
    assert main(['--extraction-profile', 'literal-v1', '--volume', 'PG113', '--page', '1',
                 '--out', str(tmp_path/'out')]) == 2
    assert not (tmp_path/'out').exists()


def test_no_api_prepares_without_key_or_db(tmp_path, monkeypatch):
    root, _ = make_sources(tmp_path)
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: pytest.fail('Network called in no-api'))
    assert main(['--extraction-profile', 'literal-v1', '--root', str(root),
        '--volume', 'PG113', '--page', '1', '--mode', 'ocr-image', '--neighbors', '0',
        '--context-images', 'never', '--no-api', '--db', str(tmp_path/'absent.sqlite'),
        '--out', str(tmp_path/'run')]) == 0
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['tasks_prepared'] == summary['images'] == 1 and summary['calls'] == 0
    assert not (tmp_path/'absent.sqlite').exists()


def test_nullable_and_malformed_dto(task):
    dto = reading()
    dto['elements'][0]['children'][0]['name'] = None
    assert literal.analyze(dto, task)[0]['valid']
    del dto['elements'][0]['children'][0]['name']
    assert not literal.analyze(dto, task)[0]['valid']
    dto = reading()
    dto['elements'][0]['children'][0]['page'] = 12
    assert not literal.analyze(dto, task)[0]['valid']


def test_literal_staging_replay_and_database_unchanged(tmp_path, task):
    db = database(tmp_path/'db.sqlite')
    before = db.read_bytes()
    directory = tmp_path/'run/tasks/page'
    response = {'status': 'received', 'dto': reading()}
    job = {'task': task, 'directory': directory, 'fingerprint': 'literal-test'}
    result = stage(job, response, db)
    assert result['status'] == 'literal_staged'
    assert not result['import_ready'] and result['interpretation_status'] == 'not_performed'
    assert result['validation']['counts']['reference_groups'] == 2
    assert not (directory/'canonical_candidate.json').exists()
    write_json(directory/'task.json', task)
    write_json(directory/'response.json', response)
    write_json(directory/'checkpoint.json', {'input_fingerprint': 'literal-test',
        'response_sha256': digest((directory/'response.json').read_bytes())})
    write_json(tmp_path/'run/manifest.json', {'tasks': [{'directory': str(directory), 'input_fingerprint': 'literal-test'}]})
    assert checkpoint_matches(directory, 'different-profile') is None
    assert checkpoint_matches(directory, 'literal-test') == response
    audit = replay_run(tmp_path/'run', tmp_path/'audit', db)
    assert audit['calls'] == 0 and audit['statuses'] == {'literal_staged': 1}
    comparison = compare([tmp_path/'run'], {'pages': []})
    assert comparison['rows'][0]['verification']['status'] == 'literal_annotation_required'
    assert len(comparison['rows'][0]['literal_entries']) == 2
    assert db.read_bytes() == before


def test_duplicate_attribution_and_continuations_stay_pending(task):
    dto = reading()
    child = deepcopy(dto['elements'][0]['children'][0])
    child['continuation'] = 'trailing'
    dto['elements'][0]['children'].append(child)
    validation, alignment, _ = literal.analyze(dto, task)
    assert validation['valid']
    reasons = {p['reason'] for p in alignment['pending']}
    assert {'competing_source_occurrence', 'continuation_pending'} <= reasons
    assert not alignment['complete']


@pytest.mark.parametrize('text', ['Leo 770, 9', 'I, 757; II, 547', 'cf. 45₁₃', '69₁₃–70₁',
                                  '12 seqq.; ibid.; 17 not. a', 'vide Roma'])
def test_notation_is_not_interpreted(task, text):
    dto = {'elements': [entry(None, text, text)], 'uncertainties': []}
    before = deepcopy(dto)
    validation, alignment, _ = literal.analyze(dto, task)
    assert validation['valid'] and dto == before
    assert alignment['entries'][0]['references_raw'] == text
    assert 'page_start' not in alignment['entries'][0]


def test_source_only_biblical_table_and_boundary(task):
    dto = {'elements': [
        {'type': 'heading', 'role': 'group', 'text': 'EX PSALMO I.', 'children': [
            entry(None, 'Beatus vir', None)]},
        {'type': 'table', 'headers': ['Antverp.', 'Oxon.'], 'rows': [['65', '113'], ['66', None]]},
        {'type': 'fragment', 'text': 'continuatio', 'edge': 'leading'},
        {'type': 'note', 'text': 'Numeri arabici paginas edit. Veron.', 'marker': None},
        {'type': 'boundary', 'text': 'ORDO RERUM'}], 'uncertainties': []}
    validation, alignment, _ = literal.analyze(dto, task)
    assert validation['valid']
    assert validation['counts']['entries'] == 1
    assert validation['counts']['reference_groups'] == 0
    assert validation['counts']['table_cells'] == 4
    assert len(alignment['boundaries']) == 1
    assert not alignment['complete']


def test_empty_extraction_cannot_claim_coverage(task):
    validation, alignment, coverage = literal.analyze({'elements': [], 'uncertainties': []}, task)
    assert validation['valid'] and not alignment['complete']
    assert coverage['ratio'] == 0 and coverage['visual_completeness'] == 'not_verified'


def test_alignment_cannot_flatten_subscript_into_page_number(tmp_path):
    root, _ = make_sources(tmp_path, text='Aaron, 45₁₃.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 0, 'never')
    dto = {'elements': [entry(text='Aaron, 4513.', refs='4513.')], 'uncertainties': []}
    _, alignment, coverage = literal.analyze(dto, task)
    assert not alignment['complete']
    assert alignment['matches'][0]['status'] != 'matched'
    assert coverage['ratio'] == 0


def test_missing_db_does_not_discard_paid_transcription(tmp_path, task):
    result = stage({'task': task, 'directory': tmp_path/'stage', 'fingerprint': 'test'},
                   {'status': 'received', 'dto': reading()}, tmp_path/'missing.sqlite')
    assert result['status'] == 'literal_staged'
    assert (tmp_path/'stage/literal.json').exists()
    assert json.loads((tmp_path/'stage/comparison.json').read_text())['status'] == 'comparison_unavailable'
    assert not (tmp_path/'missing.sqlite').exists()
