import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.editorial_pattern_discovery import (
    discover, explain, group_context, interpretations, page_evidence, signature,
)


@pytest.mark.parametrize('a,b', [('10–12','10,12'), ('I, 10','1, 10'), ('12₃','123'), ('10 a','10')])
def test_signatures_preserve_relevant_distinctions(a,b):
    assert signature(a)['template'] != signature(b)['template']


def test_slots_reconstruct_literal_offsets():
    raw='II, 370 A; 402–405; ibid.'
    result=signature(raw)
    assert result['template']=='R , N LETTER ; N – N ; ibid .'
    for slot in result['slots']:
        a,b=slot['span']
        assert raw[a:b]==slot['raw']


def test_numeric_pair_does_not_become_page_line_by_magnitude():
    assert 'page_and_line' in interpretations('79,10')
    assert 'two_editorial_pages' in interpretations('79,10')
    assert len(interpretations('79,10'))==3


def test_explicit_latin_notice_and_negative_prose():
    result=explain('Prior nota arithmetica, charactere Romano descripta, indicat tomum; '
                   'posterior, charactere Arabico, columnam; littera, columnæ partem.')
    assert {'marker':'roman','unit':'tome'} in result['slot_mappings']
    assert {'marker':'arabic','unit':'column'} in result['slot_mappings']
    assert explain('Numeri XII, 10. Epistola Pauli ad Romanos.') is None
    assert explain('Romani indicant victoriam, Graeci fugam.') is None
    french=explain('Les chiffres indiquent les pages; les lettres désignent les colonnes.')
    assert 'page' in french['units']
    internal=explain('Revocatur lector ad numeros grandiores textui Latino intermistos.')
    assert internal['existing_namespace_hint']=='internal_emphasized_numbers_edition_unknown'


def test_markup_attributes_not_notice_and_offsets_are_raw(tmp_path):
    source=tmp_path/'x-1.txt'
    raw='<bloco tipo="texto_principal" title="Numeri indicant paginas">\nNumeri indicant columnas.\n</bloco>'
    source.write_text(raw)
    result=page_evidence(source)
    assert len(result['notices'])==1
    notice=result['notices'][0]
    assert notice['units']==['column']
    a,b=notice['span']
    assert raw[a:b]==notice['quote']
    source.write_text('<bloco tipo="texto_principal" title="Numeri indicant paginas">Verbum.</bloco>')
    assert page_evidence(source)['notices']==[]


def test_group_recovers_context_without_reassigning_roman():
    item={'source':'x','ref_raw':'104'}
    group={'source':'x','raw':'104, 108','owner_raw':'Verbum II,'}
    raw,roman,flags=group_context(item,group)
    assert raw=='104, 108' and roman=='II'
    assert 'roman_suffix_in_owner_candidate' in flags
    assert group_context(item,None)[0]=='104'


def fixture(tmp_path):
    source=tmp_path/'teste/PL001/text/a-10.txt';source.parent.mkdir(parents=True)
    raw='<bloco tipo="texto_principal">Verbum I, 10; 11.</bloco>'
    source.write_text(raw)
    neighbor=source.with_name('a-9.txt')
    neighbor.write_text('<bloco tipo="texto_principal">Numeri indicant paginas editionis Frobenianae.</bloco>')
    digest=hashlib.sha256(raw.encode()).hexdigest()
    group={'id':'g','source':str(source),'raw':'I, 10; 11.', 'owner_raw':'Verbum',
           'char_start':raw.index('I, 10'),'char_end':raw.index('</bloco>'),'source_sha256':digest}
    survey=tmp_path/'survey.sqlite'
    with sqlite3.connect(survey) as db:
        db.execute('CREATE TABLE groups(id TEXT,evidence_json TEXT)')
        db.execute('INSERT INTO groups VALUES(?,?)',('g',json.dumps(group)))
    item={'id':'a','group_id':'g','volume':'PL001','source':str(source),'ref_raw':'10',
          'label':'Verbum','cause':'no_literal_header_marker_in_survey'}
    pending=tmp_path/'pending.jsonl'
    pending.write_text(json.dumps(item)+'\n'+json.dumps({**item,'id':'b','ref_raw':'11'})+'\n')
    return source,survey,pending


def test_end_to_end_counts_groups_not_interpretations_and_preserves_sources(tmp_path):
    source,survey,pending=fixture(tmp_path)
    before={p:p.read_bytes() for p in (source,survey,pending)}
    out=tmp_path/'out'
    summary=discover(pending,survey,tmp_path/'teste',out,neighbors=1)
    assert summary['items']==2 and summary['distinct_groups']==1
    assert summary['patterns']==1 and summary['repeated_patterns']==0
    assert summary['counts']['richer_group_than_item']==2
    assert summary['counts']['current_span_matches']==2
    assert summary['notice_candidates']==1
    assert summary['new_destination_links']==0
    with sqlite3.connect(out/'patterns.sqlite') as db:
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        p=json.loads(db.execute('SELECT evidence_json FROM patterns').fetchone()[0])
        assert p['scope']=='source_page_and_literal_template_not_confirmed_section'
    assert all(p.read_bytes()==value for p,value in before.items())
    source.write_text('changed OCR')
    result=discover(pending,survey,tmp_path/'teste',tmp_path/'out2',neighbors=0)
    assert result['counts']['source_changed']==2
    assert result['items']==2
    with pytest.raises(ValueError,match='Duplicate'):
        pending.write_text(pending.read_text()*2)
        discover(pending,survey,tmp_path/'teste',tmp_path/'bad')


def test_history_requires_unique_flanks_and_records_numeric_disagreement(tmp_path):
    from tools.indexing.editorial_pattern_history import anchored_reference
    from tools.indexing.ocr_variant_audit import tokens
    current='alpha beta gamma delta I, 10. epsilon zeta eta theta'
    alternative='alpha beta gamma delta I, 100. epsilon zeta eta theta'
    a,b=current.index('I,'),current.index(' epsilon')
    aligned=anchored_reference(tokens(current),tokens(alternative),[a,b])
    assert aligned and aligned['current_tokens']==['i','10']
    assert aligned['alternative_tokens']==['i','100']
    assert anchored_reference(tokens(current),tokens(alternative+' '+alternative),[a,b]) is None
    assert anchored_reference(tokens('I, 10'),tokens(alternative),[0,5]) is None


def test_history_audit_readonly_preserves_models_and_hash_variants(tmp_path):
    from tools.indexing.editorial_pattern_history import audit_history
    from tools.indexing.editorial_pattern_discovery import sha
    current='alpha beta gamma delta I, 10. epsilon zeta eta theta'
    alternative=current.replace('10','100')
    source=tmp_path/'teste/PG001/text/a-1.txt';source.parent.mkdir(parents=True);source.write_text(current)
    versions=tmp_path/'versions.sqlite'
    with sqlite3.connect(versions) as db:
        db.executescript('CREATE TABLE ocr_engine_versions(id,engine,model); CREATE TABLE ocr_results(id,volume_id,page_num,engine_version_id,text_content,text_hash,image_hash,is_current);')
        db.execute('INSERT INTO ocr_engine_versions VALUES(1,?,?)',('tesseract','migne'))
        for i,raw in enumerate([current,alternative],1):
            db.execute('INSERT INTO ocr_results VALUES(?,?,?,?,?,?,?,?)',(i,'PG001',1,1,raw,sha(raw),'different-image-hash',i==1))
    before=versions.read_bytes()
    group={'source':str(source),'source_sha256':sha(current),'raw':'I, 10.', 'owner_raw':'alpha beta gamma delta',
           'char_start':current.index('I,'),'char_end':current.index(' epsilon')}
    pattern={'source':str(source),'volume':'PG001','physical':1,'group_ids':['g']}
    cache={str(source.resolve()):{'raw':current,'sha256':sha(current)}}
    out=tmp_path/'out';out.mkdir()
    result=audit_history([pattern],{'g':group},cache,versions,tmp_path/'teste',out)
    assert result['counts']['numeric_reading_disagreement']==1
    page=result['pages'][0]
    assert page['current_matching_versions']==[1]
    assert page['versions'][1]['model']=='migne'
    assert page['versions'][1]['page_identity']=='volume_page_registration'
    check=result['checks'][0];a,b=check['alternative_span']
    assert alternative[a:b]==check['alternative_quote']
    assert not check['automatic_repair']
    assert versions.read_bytes()==before


def test_extraction_diagnostics_distinguishes_stale_offsets_from_context(tmp_path):
    from tools.indexing.editorial_pattern_history import extraction_diagnostics
    from tools.indexing.editorial_pattern_discovery import sha
    raw='Verbum II, 10.'
    group={'source_sha256':sha(raw),'char_start':11,'char_end':14,'raw':'10.','owner_raw':'Verbum II,'}
    assert extraction_diagnostics(group,raw)==['roman_context_in_owner_not_reference']
    assert extraction_diagnostics(group,raw+' ')==['source_changed_realign_before_attribution']
    group['char_start']=10
    assert extraction_diagnostics(group,raw)==['stored_reference_span_mismatch']


def test_real_pilot_notice_regressions():
    pl101=explain('In hoc Indice numeri Romani I. II. significant primum vel secundum tomum '
                  '(Juxta divisionem editionis Frobenii), numeri Arabici paginam.')
    assert {'marker':'roman','unit':'tome'} in pl101['slot_mappings']
    assert {'marker':'arabic','unit':'page'} in pl101['slot_mappings']
    pl217=explain('PRIOR NUMERUS LIBRUM, POSTERIOR EPISTOLAM INDICAT.')
    assert {'marker':'first','unit':'book'} in pl217['slot_mappings']
    assert {'marker':'second','unit':'epistle'} in pl217['slot_mappings']
    pg110=explain('Primus numerus signat paginas editionis Petropolitanae quarum seriem in textu '
                  'grandioribus typis expressimus ; secundus lineas ejusdem editionis.')
    assert {'marker':'first','unit':'page'} in pg110['slot_mappings']
    assert {'marker':'second','unit':'line'} in pg110['slot_mappings']
    assert explain('Numerorum divisio et varietas, I, 408. Typica interpretatio, 87. '
        'Magna excellentia in S. Scriptura, 344. — Numerorum ex Veteri et Novo Testamento comparatio, I, '
        '211. — Numerorum Liber unde dictus, II, 431. Nummulariorum quod munus, I, 487. '
        'Nuptiae legitimae non damnandae, II, 516. Iis tamen continentia virginum praeferenda, '
        'ibid. Primae et secundae bonae dicendae sunt, 518. — Nuptiae in Cana Galilaeae quid significant, II, 465.') is None


def test_notice_does_not_join_predicate_from_another_index_entry():
    assert explain('Primus obolos sua effigie signat, I, 801. Numerianus imperator, I, 464. '
                   'Numerorum libri summa, I, 140.') is None
    assert explain('Pythagora significat, I, 300. Quadragesimus numerus, poenitentiae typus, I, 87. '
                   'Quadringenti anni servitutis Israel unde computandi, I, 519.') is None
    notice=explain('Numeri ergo qui annum 2015 Abrahami exsuperant, ad annos Christi sunt accommodandi.')
    assert notice['chronological_era_conversion_mentioned']
