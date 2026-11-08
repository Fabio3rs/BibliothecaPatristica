import hashlib
import json
import sqlite3

from tools.indexing.link_next_steps_review import (
    compare_runs, dotted_columns, rebuild_internal, review_internal, review_scripture,
)


def test_column_parser_preserves_multiple_pages_and_abstains_on_complex_notation():
    assert dotted_columns('CXVIII, 46 .... 79')['material_numbers'] == [79]
    assert dotted_columns('LXXIII, 13-14 ... 96, 99')['material_numbers'] == [96, 99]
    assert dotted_columns('VIII, 10 ... 10, 50')['material_numbers'] == [10, 50]
    assert dotted_columns('VIII, 10 ... 10 s.') is None
    assert dotted_columns('VIII, 10, 50') is None


def test_review_uses_dotted_columns_not_equal_verse_alone_and_is_readonly(tmp_path):
    path = tmp_path/'index.txt'
    path.write_text('<texto_principal>CXVIII, 46 ... 79\nVIII, 10 ... 10, 50</texto_principal>')
    old = tmp_path/'body.txt'
    old.write_text('<texto_principal>Other text</texto_principal>')
    db_path = tmp_path/'index.sqlite'
    with sqlite3.connect(db_path) as c:
        c.executescript('''CREATE TABLE alphabetical_sections(section_key TEXT,volume_id TEXT);
            CREATE TABLE alphabetical_entries(entry_key TEXT,section_key TEXT,entry_raw TEXT,index_entry_source_ocr_file TEXT);
            CREATE TABLE alphabetical_scripture_refs(scripture_ref_id INTEGER,entry_key TEXT,ref_order INTEGER,verse_start INTEGER,book_raw TEXT);
            CREATE TABLE alphabetical_refs(ref_id INTEGER,entry_key TEXT,scripture_ref_order INTEGER,ref_raw TEXT,page_ref_int INTEGER);''')
        c.execute('INSERT INTO alphabetical_sections VALUES(?,?)', ('s','PO001'))
        c.executemany('INSERT INTO alphabetical_entries VALUES(?,?,?,?)', [
            ('a','s','CXVIII, 46 ... 79',str(old)), ('b','s','VIII, 10 ... 10, 50',str(path))])
        c.executemany('INSERT INTO alphabetical_scripture_refs VALUES(?,?,?,?,?)', [(1,'a',1,46,'Ps'), (2,'b',1,10,'Gen')])
        c.executemany('INSERT INTO alphabetical_refs VALUES(?,?,?,?,?)', [
            (1,'a',1,'46',46),(2,'a',1,'79',79),(3,'b',1,'10',10),(4,'b',1,'50',50)])
    before = db_path.read_bytes()
    summary = review_scripture(db_path, 'PO001', [path], tmp_path)
    assert summary['single_line_match_with_material_conflict'] == 1
    assert summary['review_without_proven_conflict'] == 1
    assert summary['original_source_line_absent'] == 1
    records = json.loads((tmp_path/'scripture_material_review.json').read_text())
    assert records[0]['conflicting_material_ids'] == [1]
    assert records[1]['conflicting_material_ids'] == []
    line = records[0]['line_matches_in_explicit_review_pages'][0]
    assert path.read_text()[line['char_start']:line['char_end']] == line['span_raw']
    assert db_path.read_bytes() == before


def test_compare_counts_occurrences_and_distinct_number_file_pairs_separately(tmp_path):
    before = tmp_path/'before'/'PG001'; before.mkdir(parents=True)
    after = tmp_path/'after'/'PG001'; after.mkdir(parents=True)
    old = [{'occurrence_id': str(i), 'number': 5, 'proposed': [],
            'removed_evidence': [{'path':'page','reason':'sequence_support_lost'}]} for i in range(2)]
    new = [{**r, 'proposed':['page'], 'source_current': True} for r in old]
    for root, rows in [(before,old),(after,new)]:
        (root/'candidate_diff.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    result = compare_runs(before.parent, after.parent, tmp_path)
    assert result['restored_edges'] == 2
    assert result['old_sequence_loss_distinct_pairs'] == 1
    assert result['old_sequence_loss_pairs_restored'] == 1


def test_internal_probe_can_be_rebuilt_after_ocr_correction(tmp_path):
    folder = tmp_path/'corpus'/'PG001'/'text'; folder.mkdir(parents=True)
    for i in range(1,9):
        (folder/f'page-{i:03}.txt').write_text(f'<pagina><bloco tipo="texto_principal" script="latino">{i} Verbum.</bloco></pagina>')
    profile = tmp_path/'profile.json'
    profile.write_text(json.dumps({'id':'test','volume':'PG001','physical_start':1,'physical_end':8,'scripts':['latino']}))
    probe = rebuild_internal(profile,tmp_path/'corpus',tmp_path)
    assert review_internal(probe,1,8,1,8,tmp_path)['numbers_with_one_candidate'] == 8
    changed = folder/'page-004.txt'
    changed.write_text('<pagina><bloco tipo="texto_principal" script="latino">Verbum.</bloco></pagina>')
    assert review_internal(probe,1,8,1,8,tmp_path)['stale_candidates'] == 1
    probe = rebuild_internal(profile,tmp_path/'corpus',tmp_path)
    result = review_internal(probe,1,8,1,8,tmp_path)
    assert result['stale_candidates'] == 0
    assert result['numbers_without_candidate'] == 1
