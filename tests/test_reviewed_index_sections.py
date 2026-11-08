import json
from pathlib import Path
import pytest
from tools.indexing.reviewed_index_sections import build_sections
from tools.indexing.unrepresented_region_recovery import section_context, recover_volume


def fixture(tmp_path):
    folder=tmp_path/'teste'/'PO999'/'text';folder.mkdir(parents=True)
    path=folder/'test-001.txt'
    raw=('<bloco tipo="texto_principal">Earlier prose 888.</bloco>'
         '<bloco tipo="cabecalho">TABLE DES CITATIONS</bloco>'
         '<bloco tipo="texto_principal">GENÈSE\nI, 2 ........ 15\nII, 3 ....... 18</bloco>'
         '<bloco tipo="cabecalho">TABLE DES MATIÈRES</bloco>'
         '<bloco tipo="texto_principal">Following prose 999.</bloco>')
    path.write_text(raw)
    spec=dict(id='fixture-scripture',volume='PO999',kind='scripture_index',first=1,last=1,
              opening_quote='TABLE DES CITATIONS',end_quote='TABLE DES MATIÈRES',
              parser='po',family='scripture',namespace='page',
              notice_quotes=[dict(physical=1,quote='Earlier prose 888.')])
    plan=tmp_path/'plan.json';plan.write_text(json.dumps(dict(sections=[spec])))
    return path,raw,plan


def test_mixed_page_bounds_recomputed_after_unrelated_edit(tmp_path):
    path,raw,plan=fixture(tmp_path)
    before=build_sections([plan],tmp_path)
    path.write_text('<!-- changed OCR metadata -->'+raw)
    after=build_sections([plan],tmp_path)
    assert before[0]['spans'][0]['span'] != after[0]['spans'][0]['span']
    selected=' '.join(path.read_text()[slice(*s['span'])] for s in after[0]['spans'])
    assert 'Earlier prose' not in selected and 'Following prose' not in selected
    assert 'GENÈSE' in selected
    ctx=section_context(after[0],{str(path.resolve()):path.read_text()})
    assert ctx['family']=='scripture' and ctx['namespace']=='page'
    assert 'Earlier prose 888.' in ctx['text']
    assert build_sections([plan],tmp_path)==after


def test_repeated_boundary_does_not_silently_choose_first(tmp_path):
    path,raw,plan=fixture(tmp_path)
    path.write_text(raw+'<bloco tipo="cabecalho">TABLE DES CITATIONS</bloco>')
    with pytest.raises(ValueError,match='exactly once'):
        build_sections([plan],tmp_path)


def test_duplicate_section_id_rejected(tmp_path):
    path,raw,plan=fixture(tmp_path)
    with pytest.raises(ValueError,match='Duplicate'):
        build_sections([plan,plan],tmp_path)


@pytest.mark.parametrize('key,value,message', [
    ('kind', 'word_index', 'Unsupported index kind'),
    ('parser', 'misspelled_parser', 'Unsupported parser'),
])
def test_plan_cannot_silently_drop_sections_or_use_wrong_parser(tmp_path, key, value, message):
    path, raw, plan = fixture(tmp_path)
    config = json.loads(plan.read_text())
    config['sections'][0][key] = value
    plan.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=message):
        build_sections([plan], tmp_path)


def test_actual_extraction_is_bounded_and_literal(tmp_path):
    path,raw,plan=fixture(tmp_path)
    result=recover_volume('PO999',build_sections([plan],tmp_path))
    assert len(result['entries'])==2
    for entry in result['entries']:
        assert raw[slice(*entry['reference_span'])]==entry['reference_raw']
        assert entry['reference_raw'] in {'15','18'}
        provenance = entry['reviewed_source_section']
        assert provenance['opening_raw'] == 'TABLE DES CITATIONS'
        assert provenance['kind'] == 'scripture_index'
        assert provenance['notice_evidence'][0]['quote'] == 'Earlier prose 888.'
    assert path.read_text()==raw


def test_declared_explanatory_notice_is_retained_as_editorial_material(tmp_path):
    path, raw, plan = fixture(tmp_path)
    config = json.loads(plan.read_text())
    config['sections'][0]['notice_quotes'].append(dict(physical=1, quote='I, 2 ........ 15'))
    plan.write_text(json.dumps(config))
    result = recover_volume('PO999', build_sections([plan], tmp_path))
    first, second = result['entries']
    assert first['entry_kind'] == 'editorial_note'
    assert first['source_role'] == 'editorial_notation_notice'
    assert second.get('source_role') is None


def test_conservation_preserves_unconsumed_body_and_reports_overlaps(tmp_path):
    import sqlite3
    from tools.indexing.reviewed_index_sections import audit_material
    path,raw,plan=fixture(tmp_path)
    sections=build_sections([plan],tmp_path)
    extraction=tmp_path/'extraction';extraction.mkdir()
    db=sqlite3.connect(extraction/'search.sqlite');db.execute('CREATE TABLE items(id,metadata_json)')
    start=raw.index('I, 2');end=raw.index('15',start)+2
    for i in range(2):
        db.execute('INSERT INTO items VALUES(?,?)',(str(i),json.dumps(dict(source=str(path.resolve()),entry_span=[start,end]))))
    db.commit();db.close()
    result=audit_material(sections,extraction,tmp_path)
    c=result['counts']
    assert c['body_chars']==c['consumed_body_chars']+c['residual_body_chars']
    assert c['overlapping_entry_chars']==end-start
    residue=(tmp_path/'unconsumed_body.jsonl').read_text()
    assert 'GENÈSE' in residue and '18' in residue
    assert 'Earlier prose' not in residue and 'Following prose' not in residue
