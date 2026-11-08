import argparse
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.alphabetical_index_db import SCHEMA_SQL
from tools.indexing.index_coverage_experiment import (
    classify_reference, heading_signals, make_inventory, run, scan,
)


def ref(**updates):
    return dict(dict(pipeline_owner='alphabetical', alphabetical_role='owned_section',
        material_reference_mode='remissive', scripture_ref_order=None, ref_kind='editorial_page',
        locator_status='unresolved', target_file=None, resolved_target_ocr_file=None,
        raw_json='{}', ref_raw='123', page_ref_raw='123', page_ref_int=123), **updates)


def test_locator_input_is_not_namespace_or_target_validation():
    assert classify_reference(ref()) == 'numeric_locator_input_requires_namespace_check'
    assert classify_reference(ref(ref_raw='II, 123')) == 'namespace_requires_scoped_mapping'
    assert classify_reference(ref(ref_raw='Leo 123, 9')) == 'namespace_requires_scoped_mapping'
    assert classify_reference(ref(ref_kind='editorial_page_line')) == 'page_line_requires_scoped_mapping'
    assert classify_reference(ref(ref_kind='editorial_range')) == 'range_requires_full_extent'
    assert classify_reference(ref(locator_status='resolved')) == 'existing_resolution_not_revalidated'
    assert classify_reference(ref(locator_status='ambiguous')) == 'existing_candidate_or_ambiguity_requires_review'
    assert classify_reference(ref(raw_json='{"reviewed_editorial":{"block_reason":"wrong_edition"}}')) == 'editorial_block:wrong_edition'
    assert classify_reference(ref(raw_json='bad json')) == 'invalid_editorial_metadata'
    assert classify_reference(ref(scripture_ref_order=1)) == 'outside_material_scope'
    assert classify_reference(ref(page_ref_raw='123, 9', page_ref_int=None)) == 'notation_requires_interpretation'


def test_heading_and_internal_boundary_spans_are_literal():
    raw = '<bloco tipo="cabecalho">123 INDEX NOMINUM 124</bloco>' + \
          '<bloco tipo="texto_principal">Aaron 1.\nORDO RERUM\nOther prose.</bloco>'
    signals = heading_signals(raw)
    assert [s['kind'] for s in signals] == ['index_heading_hint','boundary_hint']
    assert signals[0]['families'] == ['onomastic']
    for s in signals:
        assert raw[s['span'][0]:s['span'][1]] == s['quote']
        assert not s['verified'] and s['ownership']=='uncertain'
    assert heading_signals('<bloco tipo="texto_principal">Ordinary prose.</bloco>') == []


def test_inventory_uses_available_tail_and_preserves_outside_origins(tmp_path):
    text=tmp_path/'PG086.01/text'
    text.mkdir(parents=True)
    for n in [1,2,3,10]:
        (text/f'p-{n}.txt').write_text('text')
    section=dict(volume_id='PG086.01',section_key='s',file_start='p-2.txt',file_end='p-3.txt',
                 pipeline_owner='alphabetical',alphabetical_role='owned_section')
    origins=[dict(volume_id='PG086.01',entry_key='e',source='p-1.txt')]
    tasks,issues,info=make_inventory(tmp_path,['PG086.01'],[section],origins,1)
    assert [t['physical'] for t in tasks] == [1,2,3,10]
    assert [t['physical'] for t in tasks if t['inside_registered_owned_range']] == [2,3]
    assert not issues
    (text/'duplicate-10.txt').write_text('other')
    tasks,issues,_=make_inventory(tmp_path,['PG086.01'],[],[],1)
    assert not tasks and issues[0]['issue']=='missing_or_ambiguous_source'


def fixture(tmp_path):
    root=tmp_path/'teste'
    for volume,text in [('PL010','INDEX NOMINUM'),('PO001','TABLE ALPHABÉTIQUE DES NOMS'),('PG086.01','plain prose')]:
        folder=root/volume/'text'
        folder.mkdir(parents=True)
        (folder/'p-1.txt').write_text(f'<bloco tipo="cabecalho">{text}</bloco>')
    db=tmp_path/'source.sqlite'
    con=sqlite3.connect(db)
    con.executescript(SCHEMA_SQL)
    con.execute("INSERT INTO alphabetical_volumes VALUES('PL010','PL','text',NULL,NULL,'now','now')")
    con.execute('''INSERT INTO alphabetical_sections(section_key,volume_id,section_order,section_kind,
        heading_raw,file_start,file_end,raw_json) VALUES('s','PL010',1,'onomastic_person','INDEX','p-1.txt','p-1.txt','{}')''')
    con.execute('''INSERT INTO alphabetical_entries(entry_key,section_key,entry_order,entry_kind,
        lemma_raw,entry_raw,index_entry_source_ocr_file,raw_json) VALUES('e','s',1,'lemma','Aaron','Aaron, 1.','p-1.txt','{}')''')
    con.execute('''INSERT INTO alphabetical_refs(entry_key,ref_order,ref_kind,ref_raw,page_ref_raw,
        locator_status,raw_json) VALUES('e',1,'editorial_page','1','1','unresolved','{}')''')
    con.commit()
    con.close()
    return argparse.Namespace(root=root,source_db=db,experiment_db=tmp_path/'experiment.sqlite',
        all_volumes=True,volume=None,tail_pages=2,workers=2,out=tmp_path/'out')


def test_end_to_end_no_source_mutation_no_false_absence(tmp_path):
    args=fixture(tmp_path)
    before=args.source_db.read_bytes()
    summary=run(args)
    assert args.source_db.read_bytes()==before
    assert summary['volumes']==3 and summary['pages_read']==3
    assert summary['index_hint_pages_outside_owned_ranges']==1
    volumes={v['volume']:v for v in json.loads((args.out/'volumes.json').read_text())}
    assert volumes['PG086.01']['discovery_status']=='no_index_confirmed_in_bounded_scan'
    assert not any(v['coverage_complete'] for v in volumes.values())
    proposals=[json.loads(line) for line in (args.out/'discovery_candidates.jsonl').read_text().splitlines()]
    assert proposals[0]['volume']=='PO001'
    con=sqlite3.connect(args.experiment_db)
    assert con.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    assert con.execute('SELECT count(*) FROM index_coverage_volumes').fetchone()[0]==3
    con.close()


def test_protects_real_database_and_existing_outputs(tmp_path):
    args=fixture(tmp_path)
    args.experiment_db=args.source_db
    before=args.source_db.read_bytes()
    with pytest.raises(ValueError,match='differ'):
        run(args)
    assert args.source_db.read_bytes()==before
    args.out.mkdir()
    with pytest.raises(ValueError,match='new'):
        run(args)


def test_read_failure_stays_explicit(tmp_path):
    result=scan(dict(source=str(tmp_path/'missing'),volume='PL010',physical=1))
    assert result['status']=='read_error' and result['signals']==[]


def test_discovery_handoff_reuses_parser_and_checks_provenance(tmp_path):
    from tools.indexing.entry_parser_bulk import discovery_inventory, work
    args=fixture(tmp_path)
    page=args.root/'PO001/text/p-1.txt'
    page.write_text('<bloco tipo="cabecalho">TABLE DES NOMS</bloco>'
                    '<bloco tipo="texto_principal">Aaron, 12.</bloco>')
    run(args)
    manifest=args.out/'discovery_candidates.jsonl'
    tasks=discovery_inventory(manifest,args.root,[])
    assert len(tasks)==1 and tasks[0]['volume']=='PO001'
    assert tasks[0]['profile']=='po-auto-v1'
    tasks[0]['raw']=page.read_text()
    result=work(tasks[0])
    assert result['page']['ownership']=='uncertain'
    assert result['page']['entries']
    assert all('discovery_ownership_unverified' in e['flags'] for e in result['page']['entries'])
    evidence=json.loads(manifest.read_text())
    evidence['signals'][0]['quote']='invented'
    manifest.write_text(json.dumps(evidence)+'\n')
    with pytest.raises(ValueError,match='span'):
        discovery_inventory(manifest,args.root,[])
    page.write_text(page.read_text()+'changed')
    with pytest.raises(ValueError,match='Stale'):
        discovery_inventory(manifest,args.root,[])


def test_discovery_handoff_rejects_source_in_other_volume(tmp_path):
    from tools.indexing.entry_parser_bulk import discovery_inventory
    args=fixture(tmp_path)
    run(args)
    manifest=args.out/'discovery_candidates.jsonl'
    evidence=json.loads(manifest.read_text())
    evidence['volume']='PL010'
    manifest.write_text(json.dumps(evidence)+'\n')
    with pytest.raises(ValueError,match='declared'):
        discovery_inventory(manifest,args.root,[])
