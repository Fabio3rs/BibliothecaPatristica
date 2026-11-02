import json
from pathlib import Path

import pytest

from tools.knowledge_graph.playground.evidence_locator import locate_quote, editorial_notes
from tools.knowledge_graph.playground.evidence_matching import locate_quote as old_locate
from tools.knowledge_graph.playground.prompt_registry import freeze_template, MODES
from tools.knowledge_graph.playground.common import digest, ROOT

CASES = json.loads((Path(__file__).parent/'fixtures/kg_locator_audit.json').read_text())


@pytest.mark.parametrize('case',CASES,ids=lambda c:f"run{c['run_id']}-{c['evidence_id']}")
def test_real_audit_cases(case):
    quote, source = case['quote'], case['source_text']
    match = locate_quote(quote,source,definitions=case['definitions'])
    assert match['accepted'] == case['expected_accepted']
    assert match['method'] == case['expected_method']
    assert match['semantic_support'] == 'not_evaluated'
    assert locate_quote(quote,source,policy='local-quote-v2') == old_locate(quote,source)
    candidates = match.get('candidates',[]) + ([match] if match['accepted'] else [])
    for item in candidates:
        assert source[item['start']:item['end']] == item['matched_quote']
        for marker in item['ignored_note_markers']:
            assert source[marker['start']:marker['end']] == marker['text']
        for op in item['edit_operations']:
            assert source[op['source_start']:op['source_end']] == op['source_text']
            assert quote[op['quote_start']:op['quote_end']] == op['quote_text']


@pytest.mark.parametrize('marker',['⁹⁷','(29)'])
def test_editorial_markers_require_definition(marker):
    source = f'Christus verbum {marker} aeternum et increatum est.'
    quote = 'Christus verbum aeternum et increatum est.'
    assert not locate_quote(quote,source)['accepted']
    notes = editorial_notes([{'id':'n1','type':'notas','text':marker+' lectio codicis.'}])
    match = locate_quote(quote,source,definitions=notes)
    assert match['accepted'] and match['method']=='note_marker'
    assert match['ignored_note_markers'][0]['text']==marker


@pytest.mark.parametrize('quote,source',[
    ('Filius creatus est ante omnia saecula','Filius non creatus est ante omnia saecula'),
    ('annos habuit et dies 100 in terra','annos habuit et dies 101 in terra'),
    ('in capite liber explicat verbum aeternum','in capite (29) liber explicat verbum aeternum'),
    ('numerus x est in hac demonstratione','numerus x² est in hac demonstratione'),
    ('mensura a > b in hac demonstratione','mensura a < b in hac demonstratione'),
    ('Athanasio dixit verbum esse aeternum','Athanasii dixit verbum esse aeternum'),
    ('verbum каὶ λόγος est aeternum','verbum καὶ λόγος est aeternum'),
    ('datur salus omnibus da-','datur salus omnibus datur'),
    ('καὶ οὐ πατήρ ἐστιν ἀγαθός','καὶ οὗ πατήρ ἐστιν ἀγαθός'),
    ('x verbum et ratio','x² verbum et ratio'),
])
def test_no_unsafe_acceptance(quote,source):
    definitions=editorial_notes([{'id':'n','type':'notas','text':'(29) explicatio.\n² nota.'}])
    result=locate_quote(quote,source,definitions=definitions,names=['Athanasio'])
    assert not result['accepted']


def test_combining_offsets_and_ligatures():
    source='πρόλογος· τῷ σῶ-\nματι καὶ æternum œconomia.'
    quote='τῷ σώματι καὶ aeternum oeconomia'
    result=locate_quote(quote,source)
    assert result['accepted']
    assert source[result['start']:result['end']]=='τῷ σῶ-\nματι καὶ æternum œconomia'


def test_repeated_short_exact_quotes_do_not_collapse():
    result=locate_quote('Deus','Deus Deus')
    assert result['method']=='ambiguous'
    assert result['located'] and not result['position_resolved']
    assert result['start'] is None
    assert locate_quote('Deo','Deo Deo')['method']=='ambiguous'


def test_complete_prompt_preserves_campaign_contract():
    old=freeze_template('concepts-v2.2-confidence','concepts_relations')
    new=freeze_template('concepts-v2.3-navigation-confidence','concepts_relations')
    assert old['system_hash']=='a0d9c13066957dfac30c9185c553cff7bf05b305bddaf6b538eab4db36afda89'
    assert new['schema_hash']==old['schema_hash']
    delta=(ROOT/'tools/knowledge_graph/prompts/navigation_v2.3_delta.txt').read_text()
    assert new['system_prompt'].replace(delta+'\n\n','',1)==old['system_prompt']
    for mode in MODES:
        text=freeze_template('concepts-v2.3-navigation-confidence',mode)['system_prompt']
        assert text.count('MODEL-REPORTED CONFIDENCE —')==1
        assert text.count('EXPERIMENT MODE\n')==1
        assert text.endswith(MODES[mode])
