import json
import sqlite3

from tools.indexing.missing_page_supplement import select


def database(path, rows):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
        for id, source in rows:
            meta = {'source': str(source), 'owner_raw': 'Aaron', 'reference_raw': '12'}
            db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                       (id, id, 'PL999', 'Aaron', '12', str(source), 'source_only', json.dumps(meta)))


def test_selects_missing_pages_not_missing_volumes_and_replays(tmp_path):
    old = tmp_path / 'old-001.txt'; old.write_text('Old page')
    new = tmp_path / 'new-002.txt'; new.write_text('New page')
    catalog = tmp_path / 'catalog.sqlite'; extraction = tmp_path / 'extraction.sqlite'
    database(catalog, [('old', old)])
    database(extraction, [('new_interpretation', old), ('new', new)])
    before = catalog.read_bytes()
    a = select(extraction, catalog, tmp_path / 'a')
    b = select(extraction, catalog, tmp_path / 'b')
    assert a == b
    assert a['counts'] == {'existing_source_deferred_for_reconciliation': 1, 'new_physical_source': 1}
    with sqlite3.connect(tmp_path / 'a/search.sqlite') as db:
        assert db.execute('SELECT id FROM items').fetchall() == [('new',)]
        assert db.execute('SELECT count(*) FROM items_fts').fetchone()[0] == 1
    assert catalog.read_bytes() == before


def test_renamed_physical_page_cannot_duplicate_existing_material(tmp_path):
    old = tmp_path / 'old-001.txt'; old.write_text('old')
    renamed = tmp_path / 'renamed-001.txt'; renamed.write_text('corrected')
    catalog = tmp_path / 'catalog.sqlite'; extraction = tmp_path / 'extraction.sqlite'
    database(catalog, [('old', old)]); database(extraction, [('newid', renamed)])
    result = select(extraction, catalog, tmp_path / 'out')
    assert result['counts'] == {'existing_physical_page_under_other_path_deferred': 1}
