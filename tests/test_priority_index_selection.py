import json
import sqlite3

from scripts.locate_priority_index_sections import select


def test_pending_snapshot_keeps_original_occurrences_and_readonly_source(tmp_path):
    source = tmp_path/'catalog.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE items(id TEXT PRIMARY KEY,group_id,volume,label,ref_raw,source,state,metadata_json)')
        db.execute('CREATE TABLE candidate_storage(item_id,file,navigable)')
        for ident, physical in [('pending', 810), ('linked', 810), ('outside', 850)]:
            metadata = dict(volume='PL003', source=f'/repo/teste/PL003/text/scan-{physical}.txt',
                            ref_raw='I, 387', source_span=[1, 7])
            db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                (ident, ident, 'PL003', 'label', 'I, 387', metadata['source'], 'source_only', json.dumps(metadata)))
        db.execute("INSERT INTO candidate_storage VALUES('linked','destination.txt',1)")
    before = source.read_bytes()
    selected = tmp_path/'selected.sqlite'
    counts = select(source, selected, ['PL003'])
    assert counts == {'PL003': 1, 'outside_reviewed_sections': 1}
    with sqlite3.connect(selected) as db, sqlite3.connect(source) as original:
        assert db.execute('SELECT * FROM items').fetchall() == original.execute("SELECT * FROM items WHERE id='pending'").fetchall()
    assert source.read_bytes() == before
    counts = select(source, tmp_path/'all.sqlite', ['PL003'], include_linked=True)
    assert counts == {'PL003': 2, 'outside_reviewed_sections': 1}
