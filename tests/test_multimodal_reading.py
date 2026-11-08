"""Lean model DTO, mechanically reconstructed provenance and bounded context."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from test_multimodal_reextraction import make_sources, reading_for, database
from tools.indexing.multimodal.sources import prepare_task, read_page
from tools.indexing.multimodal.reading import SCHEMA, PROMPT, convert, editorial_hint
from tools.indexing.multimodal.alignment import Aligner
from tools.indexing.multimodal.http import payload, Config
from tools.indexing.multimodal.validation import validate, evidence_spans
from tools.indexing.multimodal.runner import stage
from tools.indexing.multimodal.contract import write_json, digest


@pytest.fixture
def task(tmp_path):
    root, _ = make_sources(tmp_path)
    return prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image-crops', 0)


def test_reading_conversion_valid_and_complete(task):
    dto, alignment = convert(reading_for(task), task)
    assert alignment['complete'], alignment
    result = validate(dto, task)
    assert result['valid'], result
    assert dto['references'][1]['page_start'] == '770'
    assert dto['references'][1]['line_start'] == '9'
    assert len(dto['references']) == 2
    assert all(s['char_start'] < s['char_end'] for e in result['spans'].values() for s in e if 'char_start' in s)


@pytest.mark.parametrize('transport', ['chat', 'responses'])
def test_lean_payload_images_reasoning_and_no_output_cap(task, transport):
    request = payload(task, Config(api_mode=transport), PROMPT, True)
    parts = request.get('messages', [{}, {'content': []}])[1]['content'] if transport == 'chat' else request['input'][0]['content']
    text = '\n'.join(b['text'] for b in parts if 'text' in b)
    assert 'PRIMARY PAGE' in text and 'FULL PAGE' in text and 'LEFT CROP' in text
    assert ':physical:' not in text and 'ocr_sha256' not in text and 'line_ids' not in text
    assert text.count('<pagina>') == 1
    assert not {'max_tokens', 'max_completion_tokens', 'max_output_tokens'} & request.keys()
    assert (request.get('reasoning_effort') or request.get('reasoning', {}).get('effort')) == 'medium'
    for i, part in enumerate(parts):
        if part['type'] in ('image_url', 'input_image'):
            assert parts[i-1]['text'].startswith('IMAGE ')
    config = Config(api_mode=transport, reasoning_effort=None)
    request = payload(task, config, PROMPT)
    assert 'reasoning' not in request and 'reasoning_effort' not in request


def test_prompt_short_schema_and_volume_cautions():
    assert len(PROMPT) < 1600
    assert 'Petropolitana' in editorial_hint('PG110')
    assert 'Verona' in editorial_hint('PL010')
    assert 'Lupareana' in editorial_hint('PG113')
    assert 'Oxford' in editorial_hint('PG040')
    artifact = Path(__file__).resolve().parents[1]/'schemas/alphabetical_reading.schema.json'
    assert json.loads(artifact.read_text()) == SCHEMA
    assert 'line_ids' not in json.dumps(SCHEMA)


def test_missing_structured_refs_and_residual_characters(task):
    reading = reading_for(task)
    reading['sections'][0]['entries'][0]['references'] = []
    _, alignment = convert(reading, task)
    assert not alignment['complete']
    assert any(p['reason'] == 'numeric_entry_without_structured_references' for p in alignment['pending'])
    reading = reading_for(task)
    reading['sections'][0]['entries'].pop()
    _, alignment = convert(reading, task)
    assert any(p['reason'] == 'unconsumed_ocr_text' for p in alignment['pending'])


def test_no_reference_number_repair(task):
    reading = reading_for(task)
    reading['sections'][0]['entries'][0]['references'][0]['ref_raw'] = '188'
    dto, alignment = convert(reading, task)
    assert not alignment['complete']
    assert not any(r['ref_raw'] == '188' for r in dto['references'])


def test_partial_reference_list_is_not_complete(task):
    reading = reading_for(task)
    ref = reading['sections'][0]['entries'][1]['references'][0]
    ref.update(ref_raw='770', kind='editorial_page', line_start=None)
    _, alignment = convert(reading, task)
    assert not alignment['complete']
    assert any(p['reason'] == 'numeric_text_outside_structured_references' and p['text'] == '9' for p in alignment['pending'])


def test_exact_lookup_does_not_match_partial_number_or_name(tmp_path):
    _, path = make_sources(tmp_path, text='INDEX\nRomani, 125.')
    aligner = Aligner(read_page(path))
    assert aligner.match('12', fuzzy=False)['status'] == 'not_found'
    assert aligner.match('Roma', fuzzy=False)['status'] == 'not_found'


def test_two_entries_on_one_ocr_line_keep_distinct_reference_offsets(tmp_path):
    root, path = make_sources(tmp_path, text='INDEX\nPaulus, 58.\nDioscorus, 58.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'initial', 'ocr-only', 0)
    reading = reading_for(task)
    path.write_text(path.read_text().replace('58.\nDioscorus', '58. Dioscorus'))
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    dto, alignment = convert(reading, task)
    result = validate(dto, task)
    assert result['valid'], result
    assert alignment['complete'], alignment
    refs = dto['references']
    assert refs[0]['evidence'][0]['line_ids'] == refs[1]['evidence'][0]['line_ids']
    assert refs[0]['evidence'][0]['ocr_char_start'] != refs[1]['evidence'][0]['ocr_char_start']
    tampered = deepcopy(refs[0]['evidence'])
    tampered[0]['ocr_char_start'] += 1
    with pytest.raises(ValueError, match='quote not found uniquely'):
        evidence_spans(tampered, task)
    refs[1]['evidence'] = deepcopy(refs[0]['evidence'])
    assert not validate(dto, task)['valid']  # A valid offset from a different owner is still rejected.


def test_page_lists_preserve_repetitions_modifiers_and_original_reading(tmp_path):
    root, _ = make_sources(tmp_path, text='INDEX\nArrianus, 17, 21, 21 bis; 49 passim.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    reading = reading_for(task)
    ref = reading['sections'][0]['entries'][0]['references'][0]
    ref.update(kind='editorial_page', page_start=None, line_start=None)
    before = deepcopy(reading)
    dto, alignment = convert(reading, task)
    assert reading == before
    assert [r['ref_raw'] for r in dto['references']] == ['17', '21', '21 bis', '49 passim']
    assert [r['page_start'] for r in dto['references']] == ['17', '21', '21', '49']
    assert all(r['page_end'] is None for r in dto['references'])
    result = validate(dto, task)
    assert result['valid'], result
    from tools.indexing.multimodal.staging import canonical
    payload, report = canonical(dto, result, task)
    assert report['valid'], report
    assert len(payload['refs']) == 4
    assert alignment['mechanical_annotations'][0]['original_reference']['ref_raw'] == ref['ref_raw']


def test_page_list_repeated_identical_number_uses_separate_occurrences(tmp_path):
    root, _ = make_sources(tmp_path, text='INDEX\nRoma, 12, 15, 15, 21.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    reading = reading_for(task)
    reading['sections'][0]['entries'][0]['references'][0].update(kind='editorial_page', page_start=None, line_start=None)
    dto, _ = convert(reading, task)
    result = validate(dto, task)
    assert result['valid'], result
    refs = dto['references']
    assert len(refs) == 4
    assert refs[1]['ref_raw'] == refs[2]['ref_raw'] == '15'
    assert refs[1]['evidence'][0]['ocr_char_start'] != refs[2]['evidence'][0]['ocr_char_start']
    refs[2]['evidence'] = deepcopy(refs[1]['evidence'])
    assert 'duplicate printed reference' in validate(dto, task)['errors'][0]


@pytest.mark.parametrize('literal', ['12-15', 'ibid.', '770, 9 seqq.', 'I, 757', '12 n. 3', '0, 12'])
def test_literal_parser_does_not_guess_mixed_or_inherited_notation(task, literal):
    from tools.indexing.multimodal.mechanical import page_reference_parts
    ref = reading_for(task)['sections'][0]['entries'][0]['references'][0]
    ref.update(ref_raw=literal, page_start=None)
    assert page_reference_parts(ref, literal) is None


def test_local_page_line_rule_blocks_page_list_parsing(task):
    from tools.indexing.multimodal.mechanical import page_reference_parts
    ref = reading_for(task)['sections'][0]['entries'][0]['references'][0]
    ref.update(ref_raw='770, 9', page_start=None)
    assert page_reference_parts(ref, '770, 9', page_line_context=True) is None
    ref['kind'] = 'editorial_page_line'
    assert page_reference_parts(ref, '770, 9') is None


def test_terminal_error_preview_is_bounded_and_full_result_preserved(tmp_path, capsys):
    from tools.indexing.multimodal.runner import print_result
    message = '194 validation errors:\n' + '\n'.join(f'- refs[{n}] missing anchor' for n in range(194))
    result = {'task_id': 'fixture', 'status': 'canonical_validation_failed',
              'canonical_report': {'errors': [message]}}
    print_result({'attempt': 1, 'directory': tmp_path}, result, 0)
    shown = json.loads(capsys.readouterr().out)
    assert len(shown['canonical_errors']) == 3
    assert shown['error_preview_truncated']
    assert len(json.dumps(shown)) < 1500
    assert result['canonical_report']['errors'] == [message]


@pytest.mark.parametrize('literal,page,line,end', [('cf. 45₁₃','45','13','13'), ('58₆₋₁₀','58','6','10')])
def test_subscript_axes_validate_without_concatenating_numbers(tmp_path, literal, page, line, end):
    root, _ = make_sources(tmp_path, text='INDEX\nRoma, '+literal+'.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    reading = reading_for(task)
    ref = reading['sections'][0]['entries'][0]['references'][0]
    ref.update(ref_raw=literal, kind='editorial_page_line', page_start=page, line_start=line, line_end=end)
    dto, _ = convert(reading, task)
    result = validate(dto, task)
    assert result['valid'], result
    assert dto['references'][0]['ref_raw'] == literal
    dto['references'][0]['page_start'] = page+line
    assert not validate(dto, task)['valid']
    dto['references'][0]['page_start'] = page
    dto['references'][0]['line_start'] = '999'
    assert not validate(dto, task)['valid']


def test_subscript_support_does_not_infer_axes_or_accept_mixed_notation():
    from tools.indexing.multimodal.mechanical import subscript_axis_supported
    assert not subscript_axis_supported('page_start','45','45₁₃','unresolved')
    assert not subscript_axis_supported('page_end','13','45₁₃','editorial_page_line')
    assert not subscript_axis_supported('line_start','11','45₁₃, 47₁₁','editorial_page_line')
    assert not subscript_axis_supported('line_start','13','45₁₃₋','editorial_page_line')


def test_inherited_headword_uses_parent_context_without_false_visual_repair(task):
    reading = reading_for(task)
    parent = reading['sections'][0]['entries'][0]
    child = reading['sections'][0]['entries'].pop()
    child.update(kind='sublemma', lemma=parent['lemma'])
    parent['children'] = [child]
    dto, alignment = convert(reading, task)
    result = validate(dto, task)
    assert result['valid'], result
    assert dto['entries'][1]['parent_id'] == dto['entries'][0]['id']
    assert dto['entries'][1]['lemma_raw'] is dto['entries'][1]['lemma_display'] is None
    assert dto['entries'][1]['visual_entry_reading'] is None
    assert all('lemma_display' not in e['corrected_fields'] for e in dto['entries'][1]['evidence'])
    assert any(a['kind'] == 'parent_lemma_context' for a in alignment['mechanical_annotations'])
    child['lemma'] = 'Unrelated unprinted name'
    dto, _ = convert(reading, task)
    assert not validate(dto, task)['valid']


def test_context_exclusion_of_exact_editorial_rule_is_reconciled(tmp_path):
    root, path = make_sources(tmp_path)
    task = prepare_task(root, 'PG113', 1, tmp_path/'initial', 'ocr-only', 0)
    reading = reading_for(task)
    note = 'First number denotes the page; second denotes the line.'
    path.write_text(path.read_text().replace('INDEX NOMINUM\n', 'INDEX NOMINUM\n'+note+'\n'))
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    reading['sections'][0]['conventions'] = [{'kind':'explicit_declaration','literal':note,
        'edition':None,'tome':None,'series':None,'notation':'page/line','physical_source_page':1}]
    reading['exclusions'] = [{'text':note,'role':'context','reason':'Editorial note'}]
    dto, alignment = convert(reading, task)
    assert validate(dto, task)['valid']
    assert len(dto['editorial_rules']) == 1
    assert any(a['kind'] == 'editorial_rule_context_reconciled' for a in alignment['mechanical_annotations'])
    reading['exclusions'][0]['role'] = 'boundary'
    dto, _ = convert(reading, task)
    assert 'overlapping ownership' in validate(dto, task)['errors'][0]


@pytest.mark.parametrize('line,quote', [
    ('Myrina, 8, 18.', '8'), ('Nicopolis, 12, 23, 25, 121,', '12'),
    ('Paphlagonia, 11, 112, 157.', '11'), ('Parius, 9, 19.', '9'),
    ('Sebasteæ th., 13, 136, 157.', '13'), ('Roma, Romani.', 'Roma'),
])
def test_validator_and_aligner_agree_on_complete_tokens(tmp_path, line, quote):
    root, _ = make_sources(tmp_path, text='INDEX\n'+line)
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    aligner = Aligner(task['pages'][0])
    match = aligner.match(quote, fuzzy=False)
    assert match['status'] == 'matched'
    span = evidence_spans(aligner.evidence(match), task)[0]
    assert span['char_start'] == match['char_start']
    assert span['char_end'] == match['char_end']
    assert aligner.raw[span['char_start']:span['char_end']] == quote


@pytest.mark.parametrize('line,quote', [
    ('Myrina, 8, 8, 18.', '8'), ('Myrina, 18.', '8'), ('Romani, 125.', 'Roma'),
])
def test_validator_rejects_true_duplicates_and_partial_tokens(tmp_path, line, quote):
    from test_multimodal_reextraction import evidence
    root, _ = make_sources(tmp_path, text='INDEX\n'+line)
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    page = task['pages'][0]
    with pytest.raises(ValueError, match='not found uniquely'):
        evidence_spans([evidence(page, page['lines'][1], quote)], task)


def test_offline_replay_reading_schema_rebuilds_diagnostics(tmp_path, monkeypatch):
    import requests
    from tools.indexing.multimodal.audit import replay_run
    monkeypatch.setattr(requests, 'post', lambda *a, **k: pytest.fail('Offline replay called HTTP'))
    root, _ = make_sources(tmp_path, text='INDEX\nMyrina, 8, 18.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    reading = reading_for(task)
    entry = reading['sections'][0]['entries'][0]
    prototype = entry['references'][0]
    entry['references'] = [{**prototype, 'ref_raw': n, 'page_start': n,
                           'kind': 'editorial_page', 'line_start': None} for n in ('8', '18')]
    source, dest = tmp_path/'paid', tmp_path/'audit'
    write_json(source/'task.json', task)
    write_json(source/'response.json', {'status': 'received', 'dto': reading})
    write_json(source/'checkpoint.json', {'input_fingerprint': 'test', 'response_sha256': digest((source/'response.json').read_bytes())})
    write_json(source/'manifest.json', {'tasks': [{'directory': str(source), 'input_fingerprint': 'test'}]})
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    db = database(tmp_path/'fixture.sqlite')
    db_before = db.read_bytes()
    result = replay_run(source, dest, db)
    assert result['calls'] == result['db_updates'] == 0
    assert result['statuses'] == {'validated_staging': 1}, result
    row = result['tasks'][0]
    assert row['diagnostics']['counts']['entries'] == 1
    assert row['diagnostics']['counts']['references'] == 2
    assert row['diagnostics']['evidence_errors'] == []
    assert db.read_bytes() == db_before
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


def test_exact_normalized_fuzzy_and_duplicates(tmp_path):
    _, path = make_sources(tmp_path, text='INDEX\nATHA-\nNASIUS episcopus, 138.\nConstantinopolitani episcopi, 188.\nRoma, 12.\nRoma, 12.')
    aligner = Aligner(read_page(path))
    assert aligner.match('ATHANASIUS episcopus, 138.')['method'] == 'normalized'
    match = aligner.match('Constantinopolitani episcopl, 188.')
    assert match['status'] == 'matched' and match['method'] == 'fuzzy'
    assert aligner.match('Constantinopolitani episcopi, 138.')['status'] != 'matched'
    assert aligner.match('Roma, 12.')['status'] == 'ambiguous'
    assert aligner.match('12', fuzzy=False)['status'] == 'ambiguous'


def test_alignment_across_blocks_ignores_xml_numbers_but_keeps_printed_numbers(tmp_path):
    _, path = make_sources(tmp_path)
    path.write_text('<pagina><bloco tipo="texto_principal" bbox="1,2,3,4">'
                    'Constantinopolitani episcopi,</bloco>\n'
                    '<bloco tipo="texto_principal" bbox="50,60,70,80">188.</bloco></pagina>')
    aligner = Aligner(read_page(path))
    match = aligner.match('Constantinopolitani episcopi, 188.')
    assert match['status'] == 'matched' and match['method'] == 'normalized'
    assert [n for n, _, _ in aligner.numeric_spans(match['char_start'], match['char_end'])] == ['188']
    assert aligner.match('Constantinopolitani episcopl, 188.')['status'] == 'matched'
    assert aligner.match('Constantinopolitani episcopi, 138.')['status'] != 'matched'
    assert aligner.match('Constantinopolitani episcopl, 138.')['status'] != 'matched'


def test_identical_duplicate_requires_same_structured_reading_and_occurrence(task):
    reading = reading_for(task)
    reading['sections'][0]['entries'].append(deepcopy(reading['sections'][0]['entries'][0]))
    dto, alignment = convert(reading, task)
    assert len(dto['entries']) == len(dto['references']) == 2
    assert validate(dto, task)['valid']
    annotations = [a for a in alignment['mechanical_annotations'] if a['kind'] == 'identical_entry_duplicate']
    assert len(annotations) == 1 and annotations[0]['evidence']
    reading['sections'][0]['entries'][-1]['uncertainties'] = ['Alternative interpretation']
    dto, _ = convert(reading, task)
    assert len(dto['entries']) == 3
    assert 'duplicate printed reference' in validate(dto, task)['errors'][0]


def test_parent_child_competing_reference_is_not_silently_deduplicated(task):
    reading = reading_for(task)
    parent = reading['sections'][0]['entries'][0]
    child = deepcopy(parent)
    child['kind'] = 'sublemma'
    parent['children'] = [child]
    dto, alignment = convert(reading, task)
    assert len(dto['entries']) == 3
    assert not any(a['kind'] == 'identical_entry_duplicate' for a in alignment['mechanical_annotations'])
    assert 'duplicate printed reference' in validate(dto, task)['errors'][0]


def test_two_real_occurrences_remain_ambiguous(tmp_path):
    root, _ = make_sources(tmp_path, text='INDEX\nRoma, 12.\nRoma, 12.')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    dto, alignment = convert(reading_for(task), task)
    assert dto['entries'] == []
    assert any(p['reason'] == 'ocr_ambiguous' for p in alignment['pending'])
    assert not any(a['kind'] == 'identical_entry_duplicate' for a in alignment['mechanical_annotations'])


@pytest.mark.parametrize('has_entries', [False, True])
def test_unanchored_sections_deferred_only_when_empty(task, has_entries):
    reading = reading_for(task)
    section = deepcopy(reading['sections'][0])
    section['heading'] = 'Invented context material heading'
    if not has_entries:
        section['entries'] = []
    reading['sections'].append(section)
    dto, alignment = convert(reading, task)
    assert len(dto['sections']) == (2 if has_entries else 1)
    assert validate(dto, task)['valid'] is (not has_entries)
    assert not alignment['complete']


def test_shared_running_header_keeps_evidence_without_overlapping_ownership(tmp_path):
    root, path = make_sources(tmp_path)
    task = prepare_task(root, 'PG113', 1, tmp_path/'initial', 'ocr-only', 0)
    reading = reading_for(task)
    section = reading['sections'][0]
    other = deepcopy(section)
    other['entries'] = [section['entries'].pop()]
    reading['sections'].append(other)
    for section in reading['sections']:
        section.update(heading='INDEX NOMINUM', index_page='123')
    reading['exclusions'] = [{'text': '123 INDEX NOMINUM', 'role': 'boundary', 'reason': 'Running header'}]
    path.write_text('<pagina><bloco tipo="cabecalho">123 INDEX NOMINUM</bloco>\n'
                    '<bloco tipo="texto_principal">Aaron, 12.\nRoma, 770, 9.</bloco></pagina>')
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    dto, alignment = convert(reading, task)
    result = validate(dto, task)
    assert result['valid'], result
    assert all(s['index_page_raw'] == '123' and s['evidence'] for s in dto['sections'])
    assert any(a['kind'] == 'header_exclusion_reconciled' for a in alignment['mechanical_annotations'])
    reading['exclusions'].append({'text': 'Aaron, 12.', 'role': 'boundary', 'reason': 'Conflicting body exclusion'})
    dto, _ = convert(reading, task)
    assert 'overlapping ownership segments' in validate(dto, task)['errors'][0]


def test_partial_alignment_does_not_hide_canonical_failure(task, tmp_path, monkeypatch):
    from tools.indexing.multimodal import runner
    reading = reading_for(task)
    reading['uncertainties'] = ['Pending reading']
    monkeypatch.setattr(runner, 'canonical', lambda *a: (None, {'valid': False, 'errors': ['Canonical conflict']}))
    result = stage({'task': task, 'directory': tmp_path/'stage', 'fingerprint': 'test'},
                   {'status': 'received', 'dto': reading}, database(tmp_path/'fixture.sqlite'))
    assert result['status'] == 'canonical_validation_failed'
    assert result['canonical_report']['errors'] == ['Canonical conflict']


def test_continuation_retained_as_pending(task):
    reading = reading_for(task)
    reading['sections'][0]['entries'][1]['continuation'] = 'trailing'
    dto, alignment = convert(reading, task)
    assert not alignment['complete']
    assert dto['continuations'][0]['status'] == 'open'
    assert dto['continuations'][0]['peer_page_id'] is None
    assert validate(dto, task)['valid']


def test_auto_neighbors_and_image_labels(tmp_path):
    root, _ = make_sources(tmp_path, 1, 'INDEX\nAaron, 12.')
    make_sources(tmp_path, 2, 'continues, 13.\nRoma, 14-')
    make_sources(tmp_path, 3, '15.\nRussi, 16.')
    task = prepare_task(root, 'PG113', 2, tmp_path/'out', 'ocr-image', 1, 'auto')
    assert all(p['regions'] for p in task['pages'])
    assert all(p['context_image_decision']['reasons'] for p in task['pages'][1:])
    text = json.dumps(payload(task, Config(), PROMPT), ensure_ascii=False)
    assert 'PREVIOUS PAGE — CONTEXT ONLY' in text and 'NEXT PAGE — CONTEXT ONLY' in text
    task = prepare_task(root, 'PG113', 2, tmp_path/'out', 'ocr-image', 1, 'never')
    assert all(not p['regions'] for p in task['pages'][1:])


def test_no_unnecessary_neighbor_images_and_missing_context(tmp_path):
    root, _ = make_sources(tmp_path, 1)
    make_sources(tmp_path, 2)
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 1, 'auto')
    assert not task['pages'][1]['regions']
    (root/'PG113/images/fixture-002.png').unlink()
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-image', 1, 'always')
    assert task['pages'][1]['context_image_decision']['status'] == 'unavailable'
    assert task['pages'][0]['regions']


def test_stage_partial_is_not_success(task, tmp_path):
    db = database(tmp_path/'fixture.sqlite')
    before = db.read_bytes()
    reading = reading_for(task)
    reading['sections'][0]['entries'].pop()
    job = {'task': task, 'directory': tmp_path/'stage', 'fingerprint': 'test'}
    result = stage(job, {'status': 'received', 'dto': reading}, db)
    assert result['status'] == 'partial_extraction', result
    assert (tmp_path/'stage/alignment.json').exists()
    assert db.read_bytes() == before


def test_old_schema_rejected_for_new_requests(task, tmp_path):
    from test_multimodal_reextraction import dto_for
    from tools.indexing.multimodal.reading import VERSION
    job = {'task': task, 'directory': tmp_path/'stage', 'fingerprint': 'test', 'expected_schema_version': VERSION}
    result = stage(job, {'status': 'received', 'dto': dto_for(task)}, tmp_path/'unused.sqlite')
    assert result['status'] == 'validation_failed'


def test_real_corpus_scoped_alignment():
    root = Path(__file__).resolve().parents[1]
    gold = json.loads((root/'tests/fixtures/multimodal_verification_v1.json').read_text())
    count = 0
    for page in gold['pages']:
        aligner = Aligner(read_page(root/page['ocr_file']))
        for scope in page['scopes']:
            for entry in scope['entries']:
                match = aligner.match(entry['entry_raw'])
                assert match['status'] == 'matched'
                bounds = [(match['char_start'], match['char_end'])]
                for ref in entry['references']:
                    assert aligner.match(ref['ref_raw'], within=bounds, fuzzy=False)['status'] == 'matched'
                    count += 1
    assert count == 16


def test_boundaries_are_not_owned(task):
    reading = reading_for(task)
    reading['sections'][0]['heading'] = 'ORDO RERUM'
    dto, alignment = convert(reading, task)
    assert not alignment['complete']
    assert not validate(dto, task)['valid']


def test_orphan_ocr_text_is_not_silently_metadata(tmp_path):
    root, path = make_sources(tmp_path)
    path.write_text(path.read_text().replace('</pagina>', 'Rhamble, 80.\n<notas>OCR metadata only.</notas>\n</pagina>'))
    task = prepare_task(root, 'PG113', 1, tmp_path/'out', 'ocr-only', 0)
    base = deepcopy(task)
    base['pages'][0]['lines'] = base['pages'][0]['lines'][:3]
    _, alignment = convert(reading_for(base), task)
    assert any(p.get('text') == 'Rhamble, 80.' for p in alignment['pending'])
    assert not any(p.get('text') == 'OCR metadata only.' for p in alignment['pending'])
