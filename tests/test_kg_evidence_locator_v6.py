"""Omissions are separate ordered intervals, not repaired or continuous quotes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.knowledge_graph.playground import evidence_locator_v5 as old
from tools.knowledge_graph.playground.evidence_locator_v6 import locate_quote, locate_on_page
from tools.knowledge_graph.playground.common import ROOT, read_json
from tools.knowledge_graph.playground.validation_current import evaluate_response
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.prompt_registry import render
from tools.knowledge_graph.playground.discourse_contract import locate_anchor

REAL = read_json(Path(__file__).parent / 'fixtures/kg_discontinuous_quotes.json')
LEFT = 'Filius aeternus creator est'
RIGHT = 'opera vero peribunt in tempore'
SOURCE = f'{LEFT}; non omnia verba citantur; {RIGHT}.'


@pytest.mark.parametrize('marker', ['[...]', '[ … ]', '...', '…', '(...)'])
def test_ordered_original_unicode_spans(marker):
    quote = f'{LEFT} {marker} {RIGHT}'
    m = locate_quote(quote, SOURCE)
    assert m['accepted'] and m['position_resolved'] and m['method'] == 'segmented'
    assert m['start'] is None and m['end'] is None and m['matched_quote'] is None
    assert m['semantic_support'] == 'not_evaluated' and m['quote_fidelity'] == 'requires_review'
    assert m['omitted_spans'][0]['text'] == '; non omnia verba citantur; '
    for segment in m['segments']:
        assert SOURCE[segment['start']:segment['end']] == segment['matched_quote']
        assert quote[segment['quote_start']:segment['quote_end']] == segment['quote']


@pytest.mark.parametrize('case', REAL['cases'][:3], ids=lambda c: f"{c['run_id']}-{c['id']}")
def test_real_gemma_quotes_all_fragments_found(case):
    before = deepcopy(REAL)
    source = REAL['blocks'][0]['text']
    assert not old.locate_quote(case['quote'], source)['accepted']
    found = locate_on_page(case['quote'], case['locator'], REAL['blocks'])
    assert found['accepted'] and found['method'] == 'segmented'
    assert found['resolved_locator'] == 'b2'
    for s in found['segments']:
        assert s['method'] == 'exact' and s['quote'] == source[s['start']:s['end']]
    assert REAL == before


def test_real_gpt_oss_extra_fragment_not_accepted():
    case = REAL['cases'][3]
    assert not locate_quote(case['quote'], REAL['blocks'][0]['text'])['accepted']


@pytest.mark.parametrize('quote', [
    f'{RIGHT} [...] {LEFT}',
    f'{LEFT} [...] An invented sentence absent from the entire source',
    f'{LEFT} [...] non [...] {RIGHT}',
    'Filius aeternus creator [...] aeternus creator est',
])
def test_missing_reordered_short_and_overlapping_fragments(quote):
    assert not locate_quote(quote, SOURCE)['accepted']


def test_no_implicit_omission_and_literal_source_ellipsis():
    assert not locate_quote(f'{LEFT} {RIGHT}', SOURCE)['accepted']
    quoted = f'{LEFT} [...] {RIGHT}'
    assert locate_quote(quoted, quoted)['method'] == 'exact'


def test_repeated_locations_remain_ambiguous():
    m = locate_quote(f'{LEFT} [...] {RIGHT}', SOURCE + '\n' + SOURCE)
    assert m['accepted'] and not m['position_resolved']
    assert m['method'] == 'ambiguous' and len(m['candidates']) == 3
    assert not m['segments'] and m['start'] is None
    for chain in m['candidates']:
        assert chain['segments'][0]['end'] <= chain['segments'][1]['start']


def test_combination_and_fragment_limits_fail_closed():
    assert not locate_quote(f'{LEFT} [...] {RIGHT}', (SOURCE + '\n') * 21)['accepted']
    assert not locate_quote(' [...] '.join([LEFT] * 13), (LEFT + '; ') * 13)['accepted']


def test_normalization_edit_diagnostics_and_combining_offsets():
    source = '😀 ἄγγελος οὐκ αἰώνιος; praesens omissio; præstantior est Filius in natura.'
    quote = 'ἄγγελος οὐκ αἰώνιος [...] praestantior est Filius in natura'
    m = locate_quote(quote, source)
    assert m['accepted'] and m['method'] == 'segmented'
    for s in m['segments']:
        assert source[s['start']:s['end']] == s['matched_quote']
    assert any(s.get('edit_operations') for s in m['segments'])


def test_omitted_negation_is_visible_not_semantically_approved():
    m = locate_quote(f'{LEFT} [...] {RIGHT}', f'{LEFT}; haec conclusio non sequitur; {RIGHT}')
    assert m['accepted']
    assert 'non sequitur' in m['omitted_spans'][0]['text']
    assert m['semantic_support'] == 'not_evaluated'
    assert 'explicit_omissions_require_semantic_review' in m['diagnostics']


def test_numeric_and_negation_differences_are_not_erased():
    assert not locate_quote('anno 218 [...] tempus finitum', 'anno 211; et postea tempus finitum')['accepted']
    assert not locate_quote('Filius non [...] tempus finitum', 'Filius est; et postea tempus finitum')['accepted']


def test_page_fallback_and_ambiguous_blocks_no_cross_block_stitching():
    quote = f'{LEFT} [...] {RIGHT}'
    blocks = [{'id':'b1','text':'Alius locus'}, {'id':'b2','text':SOURCE}]
    m = locate_on_page(quote, 'b1', blocks)
    assert m['accepted'] and m['resolved_locator'] == 'b2'
    assert all(s['locator'] == 'b2' for s in m['segments'])
    blocks.append({'id':'b3','text':SOURCE})
    m = locate_on_page(quote, 'b1', blocks)
    assert m['accepted'] and not m['position_resolved'] and len(m['candidates']) == 2
    assert not locate_on_page(quote,'b1',[{'id':'b1','text':LEFT},{'id':'b2','text':RIGHT}])['accepted']


@pytest.mark.parametrize('source_type', ['ocr_block', 'summary_segment'])
def test_versioned_navigation_replay_preserves_sources_and_response(source_type):
    source = demo_snapshot()
    data = read_json(ROOT / 'tools/knowledge_graph/playground/fixtures/page-output.navigation.demo.json')
    locator = 'b1' if source_type == 'ocr_block' else 's1'
    if source_type == 'ocr_block': source['ocr']['blocks'][0]['text'] = SOURCE
    else: source['v2']['segments'][0]['summary'] = SOURCE
    data['evidence'][0].update(source=source_type, locator=locator, quote=f'{LEFT} [...] {RIGHT}')
    p = dict(render(source, 'navigation-v1-topics-arguments', 'concepts_relations'), validation_policy='local-quote-v5')
    response = {'raw_response':json.dumps(data)}
    before = deepcopy((source, p, response))
    frozen = evaluate_response(source,response,p)
    revised = evaluate_response(source,response,p,matching_policy='local-quote-v6')
    assert not frozen['valid'] and revised['valid']
    assert revised['evidence_counts']['segmented'] == 1
    assert revised['parsed'] == data and revised['semantic_status'] == 'pending_review'
    assert (source,p,response) == before
    assert evaluate_response(source,response,p) == frozen


def test_discourse_anchors_do_not_search_omitted_text():
    source = demo_snapshot();source['ocr']['blocks'][0]['text'] = SOURCE
    evidence = dict(source='ocr_block',locator='b1')
    match = locate_on_page(f'{LEFT} [...] {RIGHT}','b1',source['ocr']['blocks'])
    good = locate_anchor({'evidence_id':'ev1','text':LEFT},evidence,match,source)
    bad = locate_anchor({'evidence_id':'ev1','text':'non omnia verba citantur'},evidence,match,source)
    assert good['accepted'] and not bad['accepted']


def test_cli_dry_replay_and_explicit_append_do_not_rewrite_history(tmp_path, monkeypatch):
    from scripts.playgrounds.kg_extractor_playground import main
    from tools.knowledge_graph.playground.storage import Store
    from tools.knowledge_graph.playground.experiment_runner import plan_experiment
    source = demo_snapshot(); source['ocr']['blocks'][0]['text'] = SOURCE
    store = Store(tmp_path / 'isolated.db'); case = store.case(source)
    exp = plan_experiment(store, [case], [{'provider':'fixture','model':'demo'}],
                          variants=['navigation-v1-topics-arguments'])
    run_id = store.list_runs(exp)[0]['id']
    data = read_json(ROOT / 'tools/knowledge_graph/playground/fixtures/page-output.navigation.demo.json')
    data['evidence'][0]['quote'] = f'{LEFT} [...] {RIGHT}'
    response = {'raw_response':json.dumps(data)}
    prepared = store.get_run(run_id)['prepared']
    old_validation = evaluate_response(source, response, prepared, matching_policy='local-quote-v5')
    store.start(run_id); store.finish(run_id, 'invalid', response, old_validation)
    before = store.get_run(run_id)
    args = ['validate', '--db',str(store.path), '--run',str(run_id),
            '--matching-policy','local-quote-v6', '--output',str(tmp_path/'validation.json')]
    with monkeypatch.context() as m:
        m.setattr(Store, '__init__', lambda *a,**kw: pytest.fail('Dry-run must not open writable store'))
        assert main(args + ['--dry-run']) == 0
    assert store.get_run(run_id) == before
    assert read_json(tmp_path/'validation.json')['valid']
    assert main(args) == 0
    after = store.get_run(run_id)
    assert after['result'] == before['result'] and after['status'] == 'invalid'
    assert after['prepared'] == before['prepared']
    assert after['validation_history'][:-1] == before['validation_history']
    assert after['validation']['valid'] and after['validation']['matching_policy'] == 'local-quote-v6'
