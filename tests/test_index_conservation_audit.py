import argparse
import json
import sqlite3

import pytest

from tools.indexing import index_conservation_audit as audit
from tools.indexing.editorial_regions_experiment import sha
from tools.indexing.entry_parser_bulk import open_experiment


def block(text, kind='texto_principal'):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def scope(raw, a=0, b=None):
    return [{'section_id': 's', 'span': [a, len(raw) if b is None else b],
             'kind': 'onomastic_index', 'opening_status': 'unknown_first_observed'}]


def entry(raw, text, key='e'):
    a = raw.index(text)
    return {'id': key, 'char_start': a, 'char_end': a+len(text), 'text_sha256': sha(text.encode()),
            'entry_raw': text, 'text_raw': text.split(',')[0], 'reference_span': None, 'references_raw': None}


def test_audits_unparsed_page_and_counts_residue_independently():
    raw = block('INDEX', 'cabecalho')+block('Abbas, 12.\nAdam, 13.')
    result = audit.coverage(raw, scope(raw), [])
    assert result['characters']['editorial_candidate'] == 5
    assert result['characters']['residual'] == len('Abbas,12.Adam,13.')
    assert len(result['numeric_tokens']) == 2
    assert all(n['status']=='residual_or_partial' for n in result['numeric_tokens'])


def test_consumption_does_not_hide_omitted_entry_or_duplicate():
    raw = block('Abbas, 12.\nAdam, 13.')
    e = entry(raw, 'Abbas, 12.')
    r = audit.coverage(raw, scope(raw), [e, {**e, 'id':'duplicate'}])
    assert r['characters']['duplicate_consumption'] == len('Abbas,12.')
    assert r['characters']['residual'] == len('Adam,13.')
    assert sum(r['characters'].values()) == len('Abbas,12.Adam,13.')


def test_coverage_partitions_regions_and_marks_editorial_conflicts():
    raw = block('INDEX', 'cabecalho')+block('Abbas, 12.')
    e=entry(raw,'INDEX')
    r=audit.coverage(raw,scope(raw),[e])
    assert r['characters']['entry_editorial_overlap']==5
    r=audit.coverage(raw,scope(raw)+scope(raw),[])
    assert r['characters']['region_overlap']==len('INDEXAbbas,12.')


def test_invalid_hash_is_residual_and_reference_outside_entry_is_blocking():
    raw=block('Abbas, 12. Adam, 13.')
    e=entry(raw,'Abbas, 12.')
    e['reference_span']=entry(raw,'13')
    assert audit.anomalies(raw,e,{'e'})[0]['kind']=='reference_span_invalid_or_outside_entry'
    e['text_sha256']='invalid'
    r=audit.coverage(raw,scope(raw),[e])
    assert r['invalid_candidate_ids']==['e']
    assert r['characters']['consumed_by_candidate']==0


def test_roman_subscript_calendar_tokens_are_not_interpreted_as_pages():
    raw=block('Abbas, II, 47₁₂; 12 aug.')
    r=audit.coverage(raw,scope(raw),[])
    assert [n['literal'] for n in r['numeric_tokens']]==['II','47₁₂','12']
    assert all(n['interpretation']=='unassigned_numeric_token' for n in r['numeric_tokens'])


def test_numbers_swallowed_by_lemma_are_reported_without_repair():
    raw=block('Aaron 66, 350, 405.')
    e=entry(raw,'Aaron 66, 350, 405.')
    e['reference_span']=entry(raw,'350, 405')
    f=audit.anomalies(raw,e,{'e'})
    assert f[0]['kind']=='numeric_tokens_before_reference_group'
    assert f[0]['examples']==['66']


def test_long_and_concatenation_shapes_are_review_leads():
    text='Abbas, 12. Adam, 13. Aaron, 14. '+('Long discussion '*150)
    raw=block(text)
    f=audit.anomalies(raw,entry(raw,text),{'e'})
    assert {'long_entry','multiple_entry_start_shapes'} <= {x['kind'] for x in f}
    assert all(x['severity']=='review' for x in f)


def test_unknown_wrapper_material_not_silently_discarded():
    raw='<pagina><mystery>Adam, 12.</mystery></pagina>'
    r=audit.coverage(raw,scope(raw),[])
    assert r['outside_known_blocks_characters']==len('Adam,12.')
    assert not any(r['characters'].values())
    assert r['unclassified_markup'][0]['snippet']=='Adam, 12.'


def test_nonlocal_parent_needs_cross_page_lookup_not_false_invalidity():
    raw=block('Abbas, 12.')
    e=entry(raw,'Abbas, 12.');e['context_candidate_id']='absent'
    finding=audit.anomalies(raw,e,{'e'})[0]
    assert finding['kind']=='context_not_found_on_current_page'
    assert finding['requires_cross_page_lookup'] and finding['severity']=='review'


def test_full_run_preserves_source_dbs_and_sees_page_without_parser(tmp_path):
    source_db=tmp_path/'source.db'
    c=sqlite3.connect(source_db)
    c.executescript('''CREATE TABLE alphabetical_sections(section_key TEXT,volume_id TEXT);
      CREATE TABLE alphabetical_entries(entry_key TEXT,section_key TEXT,entry_raw TEXT,lemma_raw TEXT,
      index_entry_source_ocr_file TEXT,resolved_target_ocr_file TEXT,target_file_best TEXT);''')
    c.close()
    parser_db=tmp_path/'parser.db';c=open_experiment(parser_db,source_db)
    c.execute('INSERT INTO runs VALUES(?,?,?,?)',('old','today','{}','complete'));c.commit();c.close()
    recovery=tmp_path/'recovery';recovery.mkdir()
    raw=block('INDEX','cabecalho')+block('Abbas, 12.\nAdam, 13.')
    src=tmp_path/'PG001-1.txt';src.write_text(raw);a=raw.index('Abbas');b=raw.index('</bloco>',a)
    span={'source':str(src),'source_sha256':sha(raw.encode()),'physical':1,'span':[a,b]}
    section={'section_id':'s','volume':'PG001','spans':[span],'opening_status':'unknown_first_observed',
             'kind':'onomastic_index','physical_start':1,'physical_end':1}
    page={'source':str(src),'source_sha256':sha(raw.encode()),'volume':'PG001','physical':1,'signals':[],
          'pieces':[{'span':[0,len(raw)],'rows':[{'span':[a,b]}]}]}
    for filename,obj in [('sections.jsonl',section),('pages.jsonl',page),('manifest.json',{})]:
        (recovery/filename).write_text(json.dumps(obj)+'\n')
    (recovery/'associations.jsonl').write_text('')
    args=argparse.Namespace(recovery=recovery,parser_run='old',parser_db=parser_db,source_db=source_db,
        experiment_db=tmp_path/'audit.db',workers=2,seed=20261105,verbose=False,out=tmp_path/'out')
    hashes={p:sha(p.read_bytes()) for p in [source_db,parser_db]}
    result=audit.run(args)
    assert result['totals']['pages_without_parser_result']==1
    assert result['characters']['residual']==len('Abbas,12.Adam,13.')
    assert result['entry_recall'] is None and not result['quality_certified']
    assert all(sha(p.read_bytes())==h for p,h in hashes.items())
    with sqlite3.connect(args.experiment_db) as c:
        assert c.execute('pragma journal_mode').fetchone()[0]=='wal'


def test_reconciliation_does_not_match_by_lemma_on_other_page(tmp_path):
    result=audit.reconcile({'overlapping_source_entries':[]},[{'source':'other','volume':'PG001','snippet':'Abbas'}])
    assert result['summary']['old_entries_on_audited_sources']==0
    assert result['automatic_changes']==0
