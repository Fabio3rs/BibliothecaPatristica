"""Location is not an assertion of faithful quotation or semantic entailment."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tools.knowledge_graph.playground.common import KIT, digest, read_json
from tools.knowledge_graph.playground.evidence_locator_v5 import editorial_notes, locate_quote, locate_on_page
from tools.knowledge_graph.playground.evidence_locator import note_markers
from tools.knowledge_graph.playground.validation_current import evaluate_response
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_run
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store

FIXTURE = read_json(Path(__file__).parent / 'fixtures/kg_locator_page_v5.json')


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: f"{c['run_id']}-{c['evidence_id']}")
def test_real_pg026_72_failures_are_locatable_without_rewriting(case):
    blocks = deepcopy(FIXTURE['blocks'])
    before = digest(blocks)
    result = locate_on_page(case['quote'], case['locator'], blocks, definitions=editorial_notes(blocks))
    assert result['accepted'] and result['position_resolved']
    assert result['semantic_support'] == 'not_evaluated'
    source = next(b['text'] for b in blocks if b['id'] == result['resolved_locator'])
    assert source[result['start']:result['end']] == result['matched_quote']
    for op in result['edit_operations']:
        assert source[op['source_start']:op['source_end']] == op['source_text']
        assert case['quote'][op['quote_start']:op['quote_end']] == op['quote_text']
    assert digest(blocks) == before


def test_apparatus_inline_definitions_preserve_offsets_and_numeric_context():
    blocks = [{'id': 'n', 'type': 'aparato_critico', 'text': '⁹⁶ Psal. 7. ⁹⁷ Hebr. 10. ⁹⁸ ibid. 5.'}]
    definitions = editorial_notes(blocks)
    assert set(definitions) == {'96', '97', '98'}
    for entries in definitions.values():
        for marker in entries:
            assert blocks[0]['text'][marker['start']:marker['end']] == marker['text']
    source = 'scriptura dicit⁹⁷ verbum aeternum esse'
    result = locate_quote('scriptura dicit verbum aeternum esse', source, definitions=definitions)
    assert result['accepted'] and result['method'] == 'note_marker'
    assert result['ignored_note_markers'][0]['text'] == '⁹⁷'
    for text in ('annos ⁹⁷ vixit', 'numerus x⁹⁷ est', 'caput ⁹⁷ tractatur'):
        assert not note_markers(text, definitions)[0]['safe_editorial']
    assert not editorial_notes([dict(blocks[0], type='texto_principal')])


@pytest.mark.parametrize('quote,source', [
    ('Filius creatus est ante omnia saecula', 'Filius non creatus est ante omnia saecula'),
    ('annos habuit et dies 100 in terra', 'annos habuit et dies 101 in terra'),
    ('mensura a > b in hac demonstratione', 'mensura a < b in hac demonstratione'),
    ('Athanasio dixit verbum esse aeternum', 'Athanasii dixit verbum esse aeternum'),
    ('ὁ υἱὸς ὁμοούσιος τῷ πατρί ἐστιν', 'ὁ υἱὸς ὁμοιούσιος τῷ πατρί ἐστιν'),
])
def test_similar_passages_with_meaning_changes_are_located_but_flagged(quote, source):
    from tools.knowledge_graph.playground.evidence_locator_v4 import locate_quote as historical
    assert not historical(quote, source, names=['Athanasio'])['accepted']
    result = locate_quote(quote, source, names=['Athanasio'])
    assert result['accepted'] and result['located']
    assert result['quote_fidelity'] == 'requires_review'
    assert result['diagnostics'] and result['edit_operations']
    assert result['semantic_support'] == 'not_evaluated'


@pytest.mark.parametrize('quote,source', [
    ('A completely unrelated proposition', 'ὁ λόγος ἀγαθός ἐστιν'),
    ('breshit bra alaha lashmaya wlar a', 'ܒܪܝܫܝܬ ܒܪܐ ܐܠܗܐ ܠܫܡܝܐ ܘܠܐܪܥܐ'),
    ('datur salus omnibus da-', 'datur salus omnibus datur'),
    ('', 'Latin source'),
])
def test_no_weak_or_word_fragment_matches(quote, source):
    assert not locate_quote(quote, source)['accepted']


def test_repeated_passages_have_no_fabricated_position():
    quote = 'Filius aeternus est'
    result = locate_quote(quote, quote + '.\n' + quote + '.')
    assert result['accepted'] and result['located'] and not result['position_resolved']
    assert result['start'] is None and result['matched_quote'] is None
    assert result['candidate_count'] == 2


def test_fallback_is_current_page_only_and_keeps_original_locator():
    blocks = [{'id': 'b1', 'text': 'Other material.'}, {'id': 'b2', 'text': 'Filius aeternus est.'}]
    result = locate_on_page('Filius aeternus est', 'b1', blocks)
    assert result['accepted'] and result['resolved_locator'] == 'b2'
    assert result['locator'] == 'b1' and 'locator_mismatch' in result['diagnostics']
    assert result['search_scope'] == 'current_page'
    blocks.append({'id': 'b3', 'text': blocks[1]['text']})
    result = locate_on_page('Filius aeternus est', 'missing', blocks)
    assert result['accepted'] and result['resolved_locator'] is None
    assert result['candidate_count'] == 2
    assert {c['locator'] for c in result['candidates']} == {'b2', 'b3'}


def stored_fixture(tmp_path):
    store = Store(tmp_path / 'lab.db')
    snapshot = demo_snapshot()
    case = store.case(snapshot)
    exp = plan_experiment(store, [case], [{'provider': 'fixture', 'model': 'demo'}])
    run = store.get_run(store.list_runs(exp)[0]['id'])
    extraction = read_json(KIT / 'page-output.demo.json')
    return store, case, run, extraction


def test_policy_default_and_historical_dispatch(tmp_path):
    store, case, run, extraction = stored_fixture(tmp_path)
    result = {'raw_response': json.dumps(extraction)}
    prepared = run['prepared']
    assert prepared['validation_policy'] == 'local-quote-v5'
    assert evaluate_response(case['snapshot'], result, prepared)['matching_policy'] == 'local-quote-v5'
    old = deepcopy(prepared)
    old.pop('validation_policy')
    assert evaluate_response(case['snapshot'], result, old)['matching_policy'] == 'local-quote-v2'
    for version in ('local-quote-v3', 'local-quote-v4', 'local-quote-v5'):
        old['validation_policy'] = version
        assert evaluate_response(case['snapshot'], result, old)['matching_policy'] == version
    assert prepared['validation_policy'] == 'local-quote-v5'


def test_cached_output_revalidated_with_frozen_policy_without_inference(tmp_path):
    store, case, run, extraction = stored_fixture(tmp_path)
    first = execute_run(store, run['id'], caller=lambda *a: pytest.fail('No provider calls'))
    exp = plan_experiment(store, [case], [{'provider': 'fixture', 'model': 'demo'}])
    rid = store.list_runs(exp)[0]['id']
    cached = execute_run(store, rid, caller=lambda *a: pytest.fail('No provider calls'))
    assert cached['status'] == 'cached' and cached['cached_from'] == first['id']
    assert cached['validation']['matching_policy'] == 'local-quote-v5'
    assert not cached['result']['inference_performed']
    assert store.get_run(first['id']) == first


def test_structure_truncation_and_context_only_are_not_relaxed(tmp_path):
    store, case, run, extraction = stored_fixture(tmp_path)
    before = digest(case)
    result = {'raw_response': json.dumps(extraction), 'finish_reason': 'length'}
    checked = evaluate_response(case['snapshot'], result, run['prepared'])
    assert not checked['valid'] and checked['completion']['output_limit_reached']
    assert not evaluate_response(case['snapshot'], {'raw_response': '{'}, run['prepared'])['valid']
    extraction['concepts'][0]['id'] = extraction['entities'][0]['id']
    assert not evaluate_response(case['snapshot'], {'raw_response': json.dumps(extraction)}, run['prepared'])['structural_valid']
    assert digest(case) == before
    # Available context from other pages must never rescue an OCR quotation.
    assert not locate_on_page('Previous page only content', 'missing', case['snapshot']['ocr']['blocks'])['accepted']


def test_cli_dry_revalidation_is_readonly_and_explicit_append_preserves_history(tmp_path, monkeypatch):
    from scripts.playgrounds.kg_extractor_playground import main
    store, case, run, extraction = stored_fixture(tmp_path)
    result = {'raw_response': json.dumps(extraction)}
    store.start(run['id'])
    store.finish(run['id'], 'invalid', result, {'version': 'original', 'valid': False})
    before = store.get_run(run['id'])
    args = ['validate', '--db', str(store.path), '--run', str(run['id']),
            '--matching-policy', 'local-quote-v5', '--output', str(tmp_path / 'validation.json')]
    with monkeypatch.context() as m:
        m.setattr('tools.knowledge_graph.playground.storage.Store.__init__', lambda *a, **kw: pytest.fail('Dry-run opened writable Store'))
        assert main(args + ['--dry-run']) == 0
    assert store.get_run(run['id']) == before
    assert main(args) == 0
    after = store.get_run(run['id'])
    assert after['status'] == before['status'] and after['result'] == before['result']
    assert after['prepared'] == before['prepared']
    assert after['validation_history'][:-1] == before['validation_history']
    assert after['validation']['matching_policy'] == 'local-quote-v5'
