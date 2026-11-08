import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing import residual_recovery_campaign as campaign
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.index_conservation_audit import coverage


def example(text=None):
    text = text or 'Alpha explicatur, I, 123 A.\n\nBeta laudatur, II, 234 B.\n\nGamma dicitur, I, 345 C.'
    raw = '<bloco tipo="texto_principal">'+text+'</bloco>'
    scopes = [{'span':[0,len(raw)], 'section_id':'section', 'kind':'thematic_index',
               'opening_status':'unknown_first_observed'}]
    return raw, scopes


def task(tmp_path, text=None):
    raw, scopes = example(text)
    source = tmp_path/'page.txt'; source.write_text(raw)
    meta = {'source':str(source), 'source_sha256':digest(raw.encode()), 'volume':'PL999','physical':1}
    baseline = {**meta, 'scopes':scopes, **coverage(raw,scopes,[])}
    return meta, baseline, None


def test_features_keep_distinct_namespaces_and_ambiguity():
    assert 'explicit_verse_locator' in campaign.features('Versus, vers. 1450.')
    assert 'roman_only_locator' in campaign.features('Alpha, LX. Beta, LXXX.')
    assert campaign.features('Anno 1450, cap. 3.') == []
    assert 'explicit_column_locator' in campaign.features('Ἀγάπη, col. 23 A.')
    assert 'line_hyphenation' in campaign.features('Pulchri-\ntudo')


def test_residual_runs_are_split_at_xml_blocks():
    raw='<bloco tipo="texto_principal">Alpha</bloco><bloco tipo="texto_principal">Beta</bloco>'
    pieces=list(campaign.residual_pieces(raw,[{'category':'residual','span':[0,len(raw)]}]))
    assert [p['text'] for p in pieces]==['Alpha','Beta']
    assert sum(p['nonspace_characters'] for p in pieces)==9


def test_additive_recovery_preserves_every_source_span(tmp_path):
    meta, baseline, parsed=task(tmp_path)
    original=Path(meta['source']).read_bytes()
    result=campaign.evaluate_page((meta,baseline,parsed))
    assert result['status']=='evaluated'
    assert len(result['additions'])==3
    assert result['after']['residual']==0
    # The audit's lexical numeral regex also matches subdivision C as Roman.
    assert result['new_reference_numeric_tokens']==7
    for entry in result['additions']:
        assert original.decode()[entry['char_start']:entry['char_end']]==entry['entry_raw']
        assert entry['quality']['edition_mapping']=='not_inferred'
    assert Path(meta['source']).read_bytes()==original


@pytest.mark.parametrize('text',[
    'Alpha anno 1500, I, 123 A.\n\nBeta, II, 234 B.\n\nGamma, I, 345 C.',
    'Alpha, vers. 1500, I, 123 A.\n\nBeta, II, 234 B.\n\nGamma, I, 345 C.',
    'alpha continua, I, 123 A.\n\nBeta, II, 234 B.\n\nGamma, I, 345 C.',
    'Alpha, I, 123 A. explicatur\n\nBeta, II, 234 B.\n\nGamma, I, 345 C.',
    'Alpha, I, 123 A. cap., 3.\n\nBeta, II, 234 B.\n\nGamma, I, 345 C.',
])
def test_uncertain_paragraph_is_not_claimed(tmp_path,text):
    result=campaign.evaluate_page(task(tmp_path,text))
    assert len(result['additions'])==2
    assert all(e['entry_raw'].startswith(('Beta','Gamma')) for e in result['additions'])
    assert result['after']['residual']>0


def test_no_partial_paragraph_or_context_region_recovery():
    raw, scopes=example()
    residual=[{'category':'residual','span':[raw.index('explicatur'),len(raw)]}]
    proposals,_=campaign.propose_rows(raw,scopes,residual)
    assert len(proposals)==2
    scopes[0]['kind']='work_catalog'
    assert not campaign.propose_rows(raw,scopes,residual)[0]


def test_existing_entry_and_numbers_are_preserved(tmp_path):
    meta, baseline, _=task(tmp_path)
    raw=Path(meta['source']).read_text()
    proposed,_=campaign.propose_rows(raw,baseline['scopes'],baseline['runs'])
    existing={**proposed[0],'id':'old'}
    baseline.update(coverage(raw,baseline['scopes'],[existing]))
    parsed={'sha256':meta['source_sha256'],'profile':'test','entries':[existing]}
    original=json.dumps(parsed,sort_keys=True)
    result=campaign.evaluate_page((meta,baseline,parsed))
    assert len(result['additions'])==2
    assert result['new_reference_numeric_tokens']==5
    assert json.dumps(parsed,sort_keys=True)==original


@pytest.mark.parametrize('text,reason',[
    ('Bardas capit, II, 388. A Phoca devictus, II, 430. Liberatus, II, 438.',
     'ambiguous_subdivision_or_next_clause_initial'),
    ('Bardas capit, II, 388. Cappadocia a Barda occupata II, 430. Liberatus, II, 438.',
     'possible_namespace_left_in_owner'),
])
def test_generalization_does_not_eat_preposition_or_leave_namespace_in_owner(tmp_path,text,reason):
    result=campaign.evaluate_page(task(tmp_path,text))
    assert not result['additions']
    assert result['rejected'][reason]==1


def test_repeated_ocr_passages_are_flagged_without_deleting_text(tmp_path):
    passage=' '.join(f'verbum{n}' for n in range(24))
    raw=f'<bloco tipo="texto_principal">{passage}</bloco><bloco tipo="texto_principal">{passage}</bloco>'
    repeated=campaign.repeated_passages(raw)
    assert len(repeated)==1
    assert repeated[0]['left_text']==repeated[0]['right_text']==passage
    assert repeated[0]['automatically_removed'] is False
    assert campaign.repeated_passages(f'<bloco tipo="texto_principal">{passage}</bloco>')==[]


def test_changed_source_cannot_reuse_baseline(tmp_path):
    t=task(tmp_path)
    Path(t[0]['source']).write_text('changed')
    assert campaign.evaluate_page(t)['status']=='source_changed'


def test_known_source_defect_survives_successful_extraction(tmp_path):
    meta,baseline,parsed=task(tmp_path)
    meta['source_quality']={'source_issue_status':'known_defects','source_issue_ids':['omission'],
                            'source_completeness':'known_omission'}
    result=campaign.evaluate_page((meta,baseline,parsed))
    assert result['status']=='evaluated' and len(result['additions'])==3
    assert result['source_quality']['source_completeness']=='known_omission'
    assert all(e['quality']['registered_source_issues']['source_issue_ids']==['omission']
               for e in result['additions'])


def test_baseline_mismatch_abstains(tmp_path):
    meta,baseline,parsed=task(tmp_path)
    baseline['characters']['residual']+=1
    assert campaign.evaluate_page((meta,baseline,parsed))['status']=='baseline_accounting_mismatch'


def test_anomalies_are_grouped_without_declaring_errors():
    findings=[{'kind':'numeric_tokens_before_reference_group','arabic_or_subscript_count':0}]
    assert campaign.anomaly_pattern(findings).endswith('roman_only_in_owner')


def test_cli_pipeline_writes_separate_relational_database(tmp_path):
    meta,baseline,_=task(tmp_path)
    audit=tmp_path/'audit';audit.mkdir()
    adb=tmp_path/'audit.sqlite';pdb=tmp_path/'parser.sqlite'
    with sqlite3.connect(adb) as con:
        con.execute('CREATE TABLE conservation_pages(run_id,source,result_json)')
        con.execute('INSERT INTO conservation_pages VALUES(?,?,?)',('run',meta['source'],json.dumps(baseline)))
    with sqlite3.connect(pdb) as con:
        con.execute('CREATE TABLE tasks(fingerprint,result_json)')
        con.execute('CREATE TABLE run_pages(fingerprint,run_id,source)')
    manifest={'experiment_db':str(adb),'parser_db':str(pdb),'run_id':'run','parser_run':'parser'}
    (audit/'manifest.json').write_text(json.dumps(manifest))
    (audit/'page_summary.json').write_text(json.dumps([meta]))
    (audit/'entry_quality.jsonl').write_text('')
    hashes={p:digest(p.read_bytes()) for p in (adb,pdb,Path(meta['source']))}
    result=campaign.run(audit,tmp_path/'out',workers=2)
    assert result['proposed_units']==3 and result['production_updates']==0
    assert result['mechanical_regressions']==0
    with sqlite3.connect(tmp_path/'out/recovery.sqlite') as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert con.execute('SELECT count(*) FROM units').fetchone()[0]==3
        assert con.execute('SELECT count(*) FROM clauses').fetchone()[0]==3
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    assert hashes=={p:digest(p.read_bytes()) for p in hashes}
    with pytest.raises(ValueError,match='new output'):
        campaign.run(audit,tmp_path/'out')
