"""Offline, transactional reassignment of uniquely evidenced legacy citations."""
import json
import sqlite3

import pytest

from scripts import apply_literal_index_repairs as repair
from scripts.alphabetical_index_db import connect_db, init_schema
from scripts.reviewed_index_evidence import validate_reviewed_editorial
from test_literal_reconcile import setup, entry
from tools.indexing.multimodal.contract import digest, write_json


def fixture(tmp_path, extra=''):
    body = 'Mundraga, 109.\nMurgule, 128.\nMyrina, 8, 18.' + extra
    task, dto = setup(tmp_path, body, [entry('Mundraga, 109.', 'Mundraga', '109.'),
                                     entry('Murgule, 128.', 'Murgule', '128.'),
                                     entry('Myrina, 8, 18.', 'Myrina', '8, 18.')])
    for name in ('run', 'corroborate'):
        folder = tmp_path/name/'tasks/page'
        write_json(folder/'task.json', task)
        write_json(folder/'response.json', {'status': 'received', 'dto': dto})
        write_json(folder/'checkpoint.json', {'input_fingerprint': 'test',
                   'response_sha256': digest((folder/'response.json').read_bytes())})
        write_json(tmp_path/name/'manifest.json', {'tasks': [
            {'directory': str(folder), 'input_fingerprint': 'test'}]})
    source = task['pages'][0]['ocr_file']
    db = tmp_path/'input.db'
    con = connect_db(db)
    init_schema(con)
    con.execute("""INSERT INTO alphabetical_volumes(volume_id,collection,source_root,created_at,updated_at)
        VALUES ('PG113','PG',?,'now','now')""", (str(tmp_path),))
    con.execute("""INSERT INTO alphabetical_sections(section_key,volume_id,section_order,section_kind,
        heading_raw,file_start,file_end,raw_json) VALUES ('s','PG113',1,'analytic_subject','INDEX',?,?,'{}')""",
                (source, source))
    con.execute("""INSERT INTO alphabetical_entries(entry_key,section_key,entry_order,entry_kind,
        lemma_raw,entry_raw,index_entry_source_ocr_file,index_section_start_ocr_file,
        heading_letter,inferred_printed_page,raw_json)
        VALUES ('e','s',1,'lemma','Mundraga',?,?,?,'M',109,'{}')""", (body, source, source))
    for order, number in enumerate((109, 128, 8, 18, 999), 1):
        con.execute("""INSERT INTO alphabetical_refs(entry_key,ref_order,ref_kind,ref_raw,page_ref_raw,
            page_ref_int,cited_editorial_page_start_number,locator_status,raw_json)
            VALUES ('e',?,'editorial_page',?,?,?,?,'unresolved',?)""",
                    (order, str(number), str(number), number, number, json.dumps({
                        'preserve': True, 'deterministic_repair': {'block_reason': 'source_anchor_not_unique'}})))
    con.execute("UPDATE alphabetical_refs SET locator_status='resolved',target_file=? WHERE ref_id=5", (source,))
    con.execute("""INSERT INTO alphabetical_locator_evidence(ref_id,evidence_order,evidence_kind,raw_json)
        VALUES (5,1,'jev_semantic','{"probability":0.99}')""")
    con.commit()
    con.close()
    return db, task


def plan(tmp_path, db):
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        result = repair.build_plan(con, tmp_path/'run', tmp_path/'corroborate', ['PG113:1'])
    repair.write_new(tmp_path/'plan.json', result)
    return result


def apply(tmp_path, db):
    return repair.apply_plan(db, tmp_path/'plan.json', tmp_path/'backup.db', tmp_path/'audit.json')


def snapshot(db):
    with sqlite3.connect(db) as con:
        return {table: con.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                for table in ['alphabetical_entries', 'alphabetical_refs', 'alphabetical_locator_evidence']}


def test_apply_preserves_ids_numbers_resolved_evidence_originals_and_backup(tmp_path):
    db, _ = fixture(tmp_path)
    before = snapshot(db)
    proposal = plan(tmp_path, db)
    assert proposal['counts'] == {'references_to_reassign': 4, 'entries_to_create': 3}
    assert snapshot(db) == before
    result = apply(tmp_path, db)
    assert result['references_reassigned'] == 4
    assert snapshot(tmp_path/'backup.db') == before
    after = snapshot(db)
    assert after['alphabetical_entries'][0] == before['alphabetical_entries'][0]
    assert after['alphabetical_refs'][-1] == before['alphabetical_refs'][-1]
    assert after['alphabetical_locator_evidence'] == before['alphabetical_locator_evidence']
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('''SELECT r.*,e.lemma_raw,e.entry_raw FROM alphabetical_refs r
            JOIN alphabetical_entries e USING(entry_key) WHERE ref_id<5 ORDER BY ref_id''').fetchall()
        assert [(r['ref_id'], r['lemma_raw'], r['ref_raw']) for r in rows] == [
            (1, 'Mundraga', '109'), (2, 'Murgule', '128'), (3, 'Myrina', '8'), (4, 'Myrina', '18')]
        for row in rows:
            validate_reviewed_editorial(dict(row))
            assert row['locator_status'] == 'unresolved'
            assert json.loads(row['raw_json'])['preserve'] is True
            assert json.loads(row['raw_json'])['reviewed_editorial']['block_reason'] is None
        assert con.execute('SELECT COUNT(*) FROM alphabetical_literal_patches').fetchone()[0] == 4
        assert con.execute('SELECT COUNT(*) FROM alphabetical_entry_source_spans').fetchone()[0] == 3
        assert not con.execute("SELECT 1 FROM alphabetical_entries WHERE entry_key!='e' AND (heading_letter IS NOT NULL OR index_editorial_page_estimate IS NOT NULL)").fetchone()
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    with pytest.raises(ValueError, match='already applied'):
        repair.apply_plan(db, tmp_path/'plan.json', tmp_path/'backup2.db', tmp_path/'audit2.json')
    assert snapshot(db) == after


def test_repeated_numbers_not_reassigned_and_namespace_not_cleared(tmp_path):
    db, _ = fixture(tmp_path, '\nAthenae, 128.')
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_refs SET raw_json=? WHERE ref_id=1", (json.dumps({
            'reviewed_editorial': {'block_reason': 'editorial_namespace_requires_mapping'}}),))
    p = plan(tmp_path, db)
    assert 2 not in [c['ref_id'] for c in p['changes']]
    assert p['changes'][0]['block_reason'] == 'editorial_namespace_requires_mapping'


@pytest.mark.parametrize('change', ['db', 'ocr', 'response'])
def test_stale_inputs_abort_without_any_db_mutation(tmp_path, change):
    db, task = fixture(tmp_path)
    plan(tmp_path, db)
    if change == 'db':
        with sqlite3.connect(db) as con:
            con.execute("UPDATE alphabetical_refs SET confidence=0.4 WHERE ref_id=2")
    elif change == 'ocr':
        from pathlib import Path
        Path(task['pages'][0]['ocr_file']).write_text('changed')
    else:
        (tmp_path/'corroborate/tasks/page/response.json').write_text('{}')
    before = snapshot(db)
    with pytest.raises(ValueError):
        apply(tmp_path, db)
    assert snapshot(db) == before
    assert not (tmp_path/'backup.db').exists()


def test_postwrite_failure_rolls_back_and_retains_backup(tmp_path, monkeypatch):
    db, _ = fixture(tmp_path)
    plan(tmp_path, db)
    before = snapshot(db)
    def fail(*args):
        raise RuntimeError('simulated failure after changes')
    monkeypatch.setattr(repair, 'refresh_volume_quality', fail)
    with pytest.raises(RuntimeError, match='simulated'):
        apply(tmp_path, db)
    assert snapshot(db) == snapshot(tmp_path/'backup.db') == before
    with sqlite3.connect(db) as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='alphabetical_literal_patches'").fetchone()
    assert not (tmp_path/'audit.json').exists()


@pytest.mark.parametrize('guard', ['evidence', 'page_line', 'disagreement', 'continuation'])
def test_candidates_with_unresolved_evidence_or_structure_are_skipped(tmp_path, guard):
    db, _ = fixture(tmp_path)
    if guard in ('evidence', 'page_line'):
        with sqlite3.connect(db) as con:
            if guard == 'evidence':
                con.execute("""INSERT INTO alphabetical_locator_evidence(ref_id,evidence_order,evidence_kind,raw_json)
                    VALUES (2,1,'candidate','{}')""")
            else:
                con.execute('UPDATE alphabetical_refs SET cited_editorial_line_end_number=4 WHERE ref_id=2')
    else:
        folder = tmp_path/('corroborate' if guard == 'disagreement' else 'run')/'tasks/page'
        response = repair.read(folder/'response.json')
        if guard == 'disagreement':
            response['dto']['elements'].pop(1)
        else:
            response['dto']['elements'][1]['continuation'] = 'leading'
        write_json(folder/'response.json', response)
        write_json(folder/'checkpoint.json', {'input_fingerprint': 'test',
            'response_sha256': digest((folder/'response.json').read_bytes())})
    assert 2 not in [c['ref_id'] for c in plan(tmp_path, db)['changes']]


@pytest.mark.parametrize('parent_kind,allowed', [('letter', True), ('entry', False), ('context', False)])
def test_letter_heading_is_grouping_but_inherited_context_is_not(tmp_path, parent_kind, allowed):
    db, _ = fixture(tmp_path)
    for run in ('run', 'corroborate'):
        folder = tmp_path/run/'tasks/page'
        response = repair.read(folder/'response.json')
        children = response['dto']['elements']
        if parent_kind == 'entry':
            parent = entry('Mundraga', 'Mundraga', None)
            parent['children'] = children
        else:
            parent = {'type': 'heading', 'text': 'M' if parent_kind == 'letter' else 'Tomus II',
                      'role': 'group' if run == 'run' else 'section', 'children': children}
        response['dto']['elements'] = [parent]
        write_json(folder/'response.json', response)
        write_json(folder/'checkpoint.json', {'input_fingerprint': 'test',
            'response_sha256': digest((folder/'response.json').read_bytes())})
    assert bool(plan(tmp_path, db)['changes']) is allowed
