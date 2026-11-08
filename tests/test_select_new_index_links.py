import json
import sqlite3

import pytest

from scripts.select_new_index_links import select


def databases(tmp_path):
    catalog, overlay = tmp_path/'catalog.sqlite', tmp_path/'overlay.sqlite'
    schema = '''CREATE TABLE candidates(item_id,file,rank,score,navigable,candidate_json);
                CREATE TABLE relations(item_id,group_id,volume,status,evidence_json);
                CREATE TABLE metadata(key,json);'''
    for path in (catalog, overlay):
        with sqlite3.connect(path) as db:
            db.executescript(schema)
            db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', json.dumps({'catalog':str(catalog)})))
    with sqlite3.connect(catalog) as db:
        db.execute('CREATE TABLE items(id,source,ref_raw)')
        db.executemany('INSERT INTO items VALUES(?,?,?)', [('a','source','I, 9'),('b','source','II, 9')])
        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)', ('a','old',1,.7,1,'{"old":true}'))
    with sqlite3.connect(overlay) as db:
        db.executemany('INSERT INTO candidates VALUES(?,?,?,?,?,?)', [('a','old',1,.9,1,'{}'),('a','new',2,.8,1,'{}'),('b','other',1,.8,1,'{}')])
    return catalog, overlay


def test_select_preserves_old_and_counts_newly_located(tmp_path):
    catalog, overlay = databases(tmp_path)
    result = select(catalog, overlay, tmp_path/'new.sqlite')
    assert result['links_selected'] == 2
    assert result['newly_located_items'] == 1
    assert result['existing_links_preserved'] == 1
    with sqlite3.connect(catalog) as db:
        assert db.execute('SELECT candidate_json FROM candidates').fetchone()[0] == '{"old":true}'


def test_review_is_bound_to_source_and_reference(tmp_path):
    catalog, overlay = databases(tmp_path)
    path = tmp_path/'reviews.json'
    reviews = {'exclusions':[dict(item_id='b',file='other',source='source',reference_raw='II, 9',reason='wrong tome')]}
    path.write_text(json.dumps(reviews))
    result = select(catalog, overlay, tmp_path/'new.sqlite', path)
    assert result['newly_located_items'] == 0
    assert len(result['reviewed_exclusions']) == 1
    reviews['exclusions'][0]['reference_raw'] = 'III, 9'
    path.write_text(json.dumps(reviews))
    with pytest.raises(ValueError, match='no longer matches'):
        select(catalog, overlay, tmp_path/'invalid.sqlite', path)
    assert not (tmp_path/'invalid.sqlite').exists()


def test_unknown_item_and_wrong_catalog_rejected(tmp_path):
    catalog, overlay = databases(tmp_path)
    with sqlite3.connect(overlay) as db:
        db.execute("UPDATE candidates SET item_id='unknown' WHERE item_id='b'")
    with pytest.raises(ValueError, match='Unknown item'):
        select(catalog, overlay, tmp_path/'invalid.sqlite')
    with sqlite3.connect(overlay) as db:
        db.execute("UPDATE metadata SET json=?", (json.dumps({'catalog':'/wrong/catalog.sqlite'}),))
    with pytest.raises(ValueError, match='another catalog'):
        select(catalog, overlay, tmp_path/'invalid.sqlite')
