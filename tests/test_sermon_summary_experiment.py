import argparse
import json
import sqlite3

import pytest

from tools.indexing.sermon_summary_experiment import extract, digest, run, work


def xml(body, title='INDEX RERUM.'):
    return f'<bloco tipo="cabecalho">{title}</bloco><bloco tipo="texto_principal">{body}</bloco>'


def test_summary_items_are_not_pages_and_preserve_title_qualification():
    raw=xml('SERM. CCXLIX. De extremo judicio, 1. — 1. Sacerdos doceat. 2. Pœnitentia. 2205-2208')
    result=extract(raw,'test','PL039',470)
    record=result['records'][0]
    assert record['sermon_number_raw']=='CCXLIX'
    assert record['title']['raw']=='De extremo judicio, 1.'
    assert [r['number_raw'] for r in record['items']]==['1','2']
    assert record['trailing_locator_candidate']['raw']=='2205-2208'
    assert record['items'][-1]['text']['raw']=='Pœnitentia.'
    assert all(i['role']=='summary_item_not_page' for i in record['items'])
    assert record['target_files']==[] and not record['import_ready']
    for field in [record['entry'],record['title'],record['summary'],record['trailing_locator_candidate'],
                  *(i['text'] for i in record['items'])]:
        a,b=field['span']
        assert raw[a:b]==field['raw']


def test_incomplete_and_irregular_numbering_preserved_not_repaired():
    raw=xml('SERM. X. Title. — 2. First. 2. Repeated, continued')
    r=extract(raw,'test','PL039',1)['records'][0]
    assert 'irregular_item_numbering' in r['flags']
    assert 'possible_continuation_or_missing_locator' in r['flags']
    assert [i['number_raw'] for i in r['items']]==['2','2']
    assert r['trailing_locator_candidate'] is None


def test_unnumbered_summary_and_missing_separator_are_distinct():
    unnumbered=extract(xml('SERM. X. Title. — Pœnæ æternæ. 2210'),'test','PL039',1)['records'][0]
    assert unnumbered['summary']['raw']=='Pœnæ æternæ.'
    assert unnumbered['items']==[]
    missing=extract(xml('SERM. X. Title. 2. Further description. 2210'),'test','PL039',1)['records'][0]
    assert missing['summary'] is None
    assert 'title_summary_separator_missing' in missing['flags']
    assert missing['unassigned'][0]['raw']=='Title. 2. Further description.'


def test_requires_index_context_and_keeps_unattached_fragments():
    raw=xml('Fragment from previous sermon.\nSERM. I. Title. — 1. Body. 123')
    result=extract(raw,'test','PL039',1)
    assert result['residuals'][0]['raw']=='Fragment from previous sermon.'
    assert extract(xml('SERM. I. Actual sermon.','AUGUSTINUS'),'test','PL039',1)['records']==[]


def test_inline_sermon_catalog_is_not_one_numbered_summary():
    raw=xml('SERM. II. De pace. 1237. — SERM. III. De silentio. 1239\n'
            '—SERM IV. De prudentia. 1240.')
    records=extract(raw,'test','PL040',702)['records']
    assert [r['sermon_number_raw'] for r in records]==['II','III','IV']
    assert [r['trailing_locator_candidate']['raw'] for r in records]==['1237','1239','1240']
    assert all(r['material_kind']=='sermon_catalog_or_unseparated_summary' for r in records)
    assert all(r['summary'] is None and not r['items'] for r in records)


def test_manifest_hash_validation_and_separate_wal_database(tmp_path):
    source=tmp_path/'page-470.txt'
    source.write_text(xml('SERM. I. Title. — 1. Body. 123'))
    item=dict(source=str(source),sha256=digest(source.read_bytes()),volume='PL039',physical=470)
    manifest=tmp_path/'manifest.jsonl'
    manifest.write_text(json.dumps(item)+'\n')
    db=tmp_path/'source.sqlite'
    db.write_bytes(b'untouched')
    args=argparse.Namespace(source_manifest=manifest,volume=[],workers=2,source_db=db,
                            experiment_db=tmp_path/'experiment.sqlite',out=tmp_path/'out')
    result=run(args)
    assert result['records']==1 and result['numbered_items']==1 and result['errors']==0
    assert db.read_bytes()==b'untouched'
    con=sqlite3.connect(args.experiment_db)
    assert con.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    assert con.execute('select count(*) from structured_material_candidates').fetchone()[0]==1
    con.close()
    source.write_text('changed')
    assert 'changed' in work(item)['error']
    args.out=tmp_path/'other'
    args.experiment_db=db
    with pytest.raises(ValueError,match='differ'):
        run(args)
