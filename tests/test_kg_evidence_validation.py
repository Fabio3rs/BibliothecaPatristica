from copy import deepcopy
import json

import pytest

from tools.knowledge_graph.playground.common import KIT, read_json
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.validation import evaluate


@pytest.fixture
def source():
    return demo_snapshot()


@pytest.fixture
def extraction():
    return read_json(KIT / 'page-output.demo.json')


def test_full_starter_contract(source, extraction):
    result = evaluate(source, extraction)
    assert result['structural_valid'] and result['source_valid']
    assert result['semantic_status'] == 'pending_review'
    assert result['gold_status'] == 'not_gold'
    assert result['diagnostics'][0]['code'] == 'stance_requires_semantic_review'


@pytest.mark.parametrize('value', [None, [], 'bad json', '{', {'concepts':None}, {'concepts':[1]}, 42, '{"concepts":[],"concepts":[]}'])
def test_malformed_outputs_never_crash(source, value):
    assert not evaluate(source, value)['valid']


@pytest.mark.parametrize('change', ['extra_metadata','enum','missing_field','bbox','duplicate_id','endpoint','missing_evidence','empty_id'])
def test_contract_and_reference_rejection(source, extraction, change):
    if change == 'extra_metadata': extraction['documento'] = 'PG026'
    elif change == 'enum': extraction['concepts'][0]['grounding'] = 'certain'
    elif change == 'missing_field': del extraction['concepts'][0]['meaning']
    elif change == 'bbox': extraction['evidence'][0]['bbox'] = [0,0,20,20]
    elif change == 'duplicate_id': extraction['concepts'][0]['id'] = 'ev1'
    elif change == 'endpoint': extraction['relations'][0]['target_id'] = 'ev1'
    elif change == 'missing_evidence': extraction['concepts'][0]['evidence_ids'] = ['invented']
    elif change == 'empty_id': extraction['concepts'][0]['id'] = ''
    assert not evaluate(source, extraction)['structural_valid']


@pytest.mark.parametrize('change', ['quote','block','segment','previous_context','empty_quote'])
def test_exact_source_evidence_rejection(source, extraction, change):
    if change == 'quote': extraction['evidence'][-1]['quote'] += ' invented'
    elif change == 'block': extraction['evidence'][-1]['locator'] = 'b999'
    elif change == 'segment': extraction['evidence'][0]['locator'] = 's999'
    elif change == 'previous_context':
        source['v2']['context_only'] = {'cumulative_summary': 'A statement from a previous page'}
        extraction['evidence'][0]['quote'] = 'A statement from a previous page'
    else: extraction['evidence'][0]['quote'] = ''
    r = evaluate(source, extraction)
    assert r['structural_valid'] and not r['source_valid']
    assert r['parsed'] == extraction and r['validated_extraction'] is None


def test_normalized_quote_is_located_without_rewriting_it(source, extraction):
    source['ocr']['blocks'][0]['text'] = 'Atha-\nnasius refutat'
    extraction['evidence'][-1]['quote'] = 'Athanasius refutat'
    r = evaluate(source, extraction)
    assert r['source_valid'] and not r['source_exact'] and r['valid']
    assert any(d['code'] == 'approximate_quote_match' for d in r['diagnostics'])
    match = r['evidence_matches'][-1]
    assert match['method'] == 'normalized'
    assert match['matched_quote'] == 'Atha-\nnasius refutat'
    assert r['parsed']['evidence'][-1]['quote'] == 'Athanasius refutat'


def test_similar_summary_evidence_and_matching_counts(source, extraction):
    extraction['evidence'][0]['quote'] = 'Atanasio'
    checked = evaluate(source, extraction)
    assert checked['valid'] and not checked['source_exact']
    assert checked['evidence_counts']['normalized'] == 1
    assert checked['evidence_matches'][0]['matched_quote'] == 'Atanásio'
    assert checked['parsed'] == extraction
    assert checked['semantic_status'] == 'pending_review'


def test_similarity_cannot_find_evidence_in_a_different_block(source, extraction):
    source['ocr']['blocks'].append(dict(source['ocr']['blocks'][0], id='b2'))
    source['ocr']['blocks'][0]['text'] = 'Different passage.'
    assert not evaluate(source, extraction)['source_valid']


def test_empty_extraction_is_valid(source, extraction):
    for key in extraction:
        if key != 'schema_version': extraction[key] = []
    assert evaluate(source, extraction)['valid']


def test_refutation_not_automatic_semantic_gold(source, extraction):
    assert extraction['claims'][0]['mode'] == 'reported'
    assert extraction['relations'][0]['predicate'] == 'REFUTES'
    extraction['relations'][0]['predicate'] = 'DEFENDS'  # deliberately incorrect historical attribution
    r = evaluate(source, extraction)
    assert r['valid']  # quote membership alone cannot establish endorsement
    assert r['semantic_status'] == 'pending_review'
    assert any(d['object_id'] == 'r1' for d in r['diagnostics'])


def test_edit_alignment_is_a_validation_annotation_not_a_source_rewrite(source, extraction):
    source['ocr']['blocks'].append({'id': 'b_edit', 'text': 'De genitis autem illiis sic loquitur'})
    extraction['evidence'].append({'id': 'ev_edit', 'source': 'ocr_block', 'locator': 'b_edit',
                                   'quote': 'De genitis autem filiis sic loquitur'})
    before_source, before_extraction = deepcopy(source), deepcopy(extraction)
    checked = evaluate(source, extraction)
    assert checked['version'] == 'kg-validation-6'
    assert checked['matching_policy'] == 'local-quote-v2'
    assert checked['valid'] and not checked['source_exact']
    assert checked['semantic_status'] == 'pending_review' and checked['gold_status'] == 'not_gold'
    assert checked['evidence_matches'][-1]['edit_distance'] == 2
    assert source == before_source and extraction == before_extraction
    source['ocr']['blocks'][-1]['text'] = 'De genitis autem filiis (10) sic loquitur'
    checked = evaluate(source, extraction)
    assert not checked['valid']
    assert any(d['code'] == 'protected_quote_difference' for d in checked['diagnostics'])


def test_source_grounded_sensitive_fixtures():
    from pathlib import Path
    fixtures = read_json(Path(__file__).parent / 'fixtures/kg_playground/historical_synthetic.json')
    assert {f['capability'] for f in fixtures} == {'equivalent_spellings','same_label_different_meanings','editorial_attribution','concept_without_modern_name'}
    for f in fixtures:
        assert f['source']['source']['synthetic']
        checked = evaluate(f['source'], f['output'])
        assert checked['valid'], checked
        assert checked['semantic_status'] == 'pending_review'
    homonym = fixtures[1]
    assert len(homonym['output']['concepts']) == 2
    assert any('duplicate_concept_label_review' in w for w in evaluate(homonym['source'], homonym['output'])['warnings'])
    assert fixtures[2]['output']['relations'][0]['source_id'] == 'e_editor'
    assert fixtures[3]['output']['concepts'][0]['grounding'] == 'inferred'


def test_concepts_only_enforces_empty_optional_arrays(source, extraction):
    assert not evaluate(source,extraction,mode='concepts_only')['valid']


@pytest.mark.parametrize('opening,newline', [('```json','\n'), ('```JSON','\r\n'), ('```','\n')])
def test_single_complete_markdown_fence_is_a_recorded_wrapper(source, extraction, opening, newline):
    raw = '  ' + opening + newline + json.dumps(extraction, ensure_ascii=False) + newline + '```\n'
    checked = evaluate(source, raw)
    assert checked['valid'] and checked['graph_inspectable']
    assert checked['parsed'] == extraction
    assert checked['response_format'] == 'markdown_fence'
    assert checked['format_compliant'] is False
    assert 'markdown_fence_removed' in checked['warnings']


@pytest.mark.parametrize('template', [
    'Here is the result:\n```json\n%s\n```', '```json\n%s\n```\nExplanation',
    '```json\n%s', '```python\n%s\n```', '```json\n%s\n```\n```json\n{}\n```',
    '%s {}', '```json\n%s,\n```'])
def test_ambiguous_wrappers_and_invalid_json_are_not_repaired(source, extraction, template):
    checked = evaluate(source, template % json.dumps(extraction))
    assert not checked['valid'] and not checked['graph_inspectable']
    assert checked['parsed'] is None


@pytest.mark.parametrize('body', ['{"schema_version":"a","schema_version":"b"}', '{"concepts":NaN}'])
def test_fences_do_not_bypass_strict_json_checks(source, body):
    checked = evaluate(source, '```json\n' + body + '\n```')
    assert not checked['valid'] and checked['parsed'] is None


def test_fenced_output_does_not_change_quote_contents(source, extraction):
    source['ocr']['blocks'][0]['text'] = 'λόγος ```json\nquoted\n```'
    extraction['evidence'][-1]['quote'] = source['ocr']['blocks'][0]['text']
    raw = '```json\n' + json.dumps(extraction, ensure_ascii=False) + '\n```'
    assert evaluate(source, raw)['parsed'] == extraction
    assert evaluate(source, raw)['valid']


def test_cross_namespace_collision_inspectable_but_still_invalid(source, extraction):
    previous = extraction['evidence'][0]['id']
    extraction['evidence'][0]['id'] = 'e1'
    for group in ('concepts','entities','claims','events','relations'):
        for obj in extraction[group]:
            obj['evidence_ids'] = ['e1' if ref == previous else ref for ref in obj['evidence_ids']]
    raw = '```json\n' + json.dumps(extraction) + '\n```'
    checked = evaluate(source, raw)
    assert checked['schema_valid'] and checked['source_valid'] and checked['graph_inspectable']
    assert not checked['structural_valid'] and not checked['valid']
    assert checked['validated_extraction'] is None
    assert checked['id_conflicts'] == [{'id':'e1','groups':['entities','evidence']}]
    assert checked['parsed'] == extraction


@pytest.mark.parametrize('change', ['nodes', 'relations', 'evidence', 'endpoint', 'evidence_ref', 'schema'])
def test_ambiguous_graph_cannot_be_inspected(source, extraction, change):
    if change == 'nodes': extraction['claims'][0]['id'] = 'e1'
    elif change == 'relations': extraction['relations'][0]['id'] = 'e1'
    elif change == 'evidence': extraction['evidence'][0]['id'] = extraction['evidence'][1]['id']
    elif change == 'endpoint': extraction['relations'][0]['target_id'] = 'missing'
    elif change == 'evidence_ref': extraction['concepts'][0]['evidence_ids'] = ['missing']
    else: del extraction['concepts'][0]['meaning']
    checked = evaluate(source, extraction)
    assert not checked['valid'] and not checked['graph_inspectable']


def test_truncated_completion_reports_reasoning_and_cannot_be_repaired_by_revalidation(source):
    from tools.knowledge_graph.playground.prompt_registry import render
    from tools.knowledge_graph.playground.validation import evaluate_response
    prepared=render(source,'concepts-v1','concepts_relations')
    prepared['config']={'max_output_tokens':8192}
    result={'raw_response':'{"evidence":[{"quote":"unfinished','finish_reason':'length',
            'usage':{'output_tokens':8192,'reasoning_tokens':6824}}
    checked=evaluate_response(source,result,prepared)
    assert checked['completion']['visible_output_tokens']==1368
    assert checked['completion']['max_output_tokens']==8192
    assert checked['completion']['output_limit_reached']
    assert checked['parsed'] is None and not checked['valid']
    assert checked['warnings']==['provider_output_limit_reached']
    assert '6824' in checked['diagnostics'][0]['message']
    assert '1368' in checked['diagnostics'][0]['message']
    assert evaluate_response(source,result,prepared)==checked


def test_provider_length_finish_is_not_accepted_as_complete_even_with_valid_json(source, extraction):
    from tools.knowledge_graph.playground.prompt_registry import render
    from tools.knowledge_graph.playground.validation import evaluate_response
    prepared=render(source,'concepts-v1','concepts_relations')
    checked=evaluate_response(source,{'raw_response':json.dumps(extraction),'finish_reason':'length'},prepared)
    assert checked['structural_valid'] and checked['source_valid']
    assert not checked['valid'] and checked['validated_extraction'] is None
    checked=evaluate_response(source,{'raw_response':json.dumps(extraction),'finish_reason':'stop','usage':{'output_tokens':8192}},prepared)
    assert checked['valid'] and checked['completion']['output_limit_reached'] is False
    assert checked['completion']['reasoning_tokens'] is None
    assert checked['completion']['visible_output_tokens'] is None


@pytest.mark.parametrize('reasoning,total', [(10,5),(-1,10),('3',10),(True,10),(3,None),(3,'10')])
def test_inconsistent_or_absent_token_breakdown_is_not_fabricated(reasoning,total):
    from tools.knowledge_graph.playground.validation import completion_details
    details=completion_details({'usage':{'output_tokens':total,'reasoning_tokens':reasoning}}, {})
    assert details['visible_output_tokens'] is None
