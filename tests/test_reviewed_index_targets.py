import json
from pathlib import Path
import sqlite3
import sys
import types

import pytest

from tools.indexing import reviewed_index_targets as target


def occurrence():
    return dict(owner_raw='Aaron', owner_span=[0, 5], reference_raw='12',
                reference_span=[6, 8], entry_span=[0, 8], source_sha256='old-hash')


def test_locator_coverage_does_not_conflate_partial_hit_with_complete_list():
    records = [dict(diagnostic=dict(locator_count=3, located_locator_count=1)),
               dict(diagnostic=dict(locator_count=2, located_locator_count=2)),
               dict(diagnostic=dict(locator_count=1, located_locator_count=0)),
               dict(diagnostic={}, candidates=[{'file': 'legacy'}])]
    result = target.locator_coverage(records)
    assert result['locators'] == 6
    assert result['locators_with_candidates'] == 3
    assert result['entries_all_locators_with_candidates'] == 1
    assert result['entries_some_locators_with_candidates'] == 1
    assert result['entries_without_locator_measurement'] == 1


def test_literal_identity_survives_unrelated_ocr_changes():
    entry = occurrence()
    current, status = target.current_occurrence(entry, 'Aaron 12 changed footer')
    assert current['reference_span'] == [6, 8]
    assert status == 'literal_spans_current'
    current, status = target.current_occurrence(entry, 'New title. Aaron 12')
    assert current['reference_span'] == [17, 19]
    assert status == 'unique_literal_reanchor'
    assert entry['reference_span'] == [6, 8]


def test_changed_reference_and_ambiguous_owner_are_not_reassigned():
    assert target.current_occurrence(occurrence(), 'Aaron 13')[0] is None
    assert target.current_occurrence(occurrence(), 'New Aaron 12 Aaron 12')[0] is None


def test_file_dedup_preserves_alternative_support_and_stable_rank(tmp_path):
    folder = tmp_path / 'teste' / 'PO011' / 'text'
    folder.mkdir(parents=True)
    a, b = folder / 'a.txt', folder / 'b.txt'
    a.write_text('Text'); b.write_text('Text')
    candidates = [dict(file=str(a), score=0.5, evidence={'marker': 12}),
                  dict(file=str(a), score=0.8, evidence={'bounds': [11, 13]}),
                  dict(file=str(b), score=0.4, evidence={'alternative': True})]
    results = target.combine_candidates(candidates, tmp_path)
    assert len(results) == 2
    assert results[0]['score'] == 0.8 and len(results[0]['evidence']) == 2
    assert [r['rank'] for r in results] == [1, 2]
    assert all(not r['passage_verified'] and r['navigable'] for r in results)
    assert target.combine_candidates(list(reversed(candidates)), tmp_path) == results
    with pytest.raises(ValueError, match='existing corpus OCR'):
        target.combine_candidates([dict(file=str(folder/'missing.txt'))], tmp_path)


def fixture(tmp_path, monkeypatch):
    folder = tmp_path / 'teste' / 'PO011' / 'text'
    folder.mkdir(parents=True)
    source = folder / 'index-001.txt'; source.write_text('Aaron 12')
    destination = folder / 'body-002.txt'; destination.write_text('12 body')
    database = tmp_path / 'extraction.sqlite'
    db = sqlite3.connect(database)
    db.execute('CREATE TABLE items(id,group_id,volume,label,ref_raw,source,state,metadata_json)')
    meta = dict(occurrence(), id='one', section_id='PO011:fixture', source=str(source), volume='PO011')
    db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
               ('one','group','PO011','Aaron','12',str(source),'source_only',json.dumps(meta)))
    db.commit(); db.close()
    module = types.ModuleType('fixture_target_module')
    module.__file__ = __file__
    module.locate = lambda entry, mapping: [dict(file=str(destination), score=0.7, evidence={'number': 12})]
    monkeypatch.setitem(sys.modules, 'fixture_target_module', module)
    monkeypatch.setitem(target.MODULES, 'po_pages', 'fixture_target_module')
    return database


def test_overlay_is_reproducible_and_does_not_modify_extraction(tmp_path, monkeypatch):
    database = fixture(tmp_path, monkeypatch)
    original = database.read_bytes()
    args = dict(extraction=database, catalog=None, root=tmp_path, supplied_maps={'po_pages': {}})
    first = target.run(**args, out=tmp_path/'one')
    second = target.run(**args, out=tmp_path/'two')
    assert database.read_bytes() == original
    assert first['logical_sha256'] == second['logical_sha256']
    assert first['counts']['with_candidates'] == 1
    db = sqlite3.connect(tmp_path/'one'/'relations.sqlite')
    assert db.execute('SELECT count(*) FROM candidates').fetchone()[0] == 1
    db.close()


def test_catalog_identity_conflict_prevents_overlay(tmp_path, monkeypatch):
    database = fixture(tmp_path, monkeypatch)
    catalog = tmp_path/'catalog.sqlite'; catalog.write_bytes(database.read_bytes())
    db = sqlite3.connect(catalog)
    db.execute("UPDATE items SET label='Moses'"); db.commit(); db.close()
    with pytest.raises(ValueError, match='Catalog occurrence differs'):
        target.load_entries(database, catalog)


def test_partial_locator_lists_stay_in_pending_export(tmp_path, monkeypatch):
    database = fixture(tmp_path, monkeypatch)
    module = sys.modules['fixture_target_module']
    module.diagnose = lambda entry, mapping: dict(locator_count=2, located_locator_count=1,
                                                 unmatched_locators=[{'number': 13}])
    summary = target.run(database, None, tmp_path, tmp_path/'partial', supplied_maps={'po_pages': {}})
    assert summary['counts']['with_candidates'] == 1
    assert summary['locator_coverage']['entries_some_locators_with_candidates'] == 1
    assert 'Aaron' in (tmp_path/'partial'/'pending.csv').read_text()


def test_editorial_notice_stays_searchable_without_fake_index_destination(tmp_path, monkeypatch):
    database = fixture(tmp_path, monkeypatch)
    db = sqlite3.connect(database)
    meta = json.loads(db.execute('SELECT metadata_json FROM items').fetchone()[0])
    meta['source_role'] = 'editorial_notation_notice'
    db.execute('UPDATE items SET metadata_json=?', (json.dumps(meta),))
    db.commit(); db.close()
    result = target.run(database, None, tmp_path, tmp_path/'notice', supplied_maps={'po_pages': {}})
    assert result['counts']['with_candidates'] == 0
    row = json.loads((tmp_path/'notice'/'results.jsonl').read_text())
    assert row['status'] == 'editorial_notice_not_index_reference'
    assert row['label'] == 'Aaron'
    assert 'Aaron' in (tmp_path/'notice'/'pending.csv').read_text()


def test_section_routing_does_not_use_incidental_numeric_content():
    assert target.route(dict(section_id='unknown', volume='PO011', reference_raw='12')) is None
    assert target.route(dict(section_id='po008-apostles-canons-subjects')) == 'po_named'
    assert target.route(dict(section_id='po008-plerophories-greek')) == 'po_pages'
    assert target.route(dict(section_id='PG114:allatian-saints')) == 'pg114'
    assert target.route(dict(section_id='PG091:ambigua-gudian-lexical')) == 'gudian_folio'
    assert target.route(dict(section_id='PL005:arnobius-subjects-book-chapter')) == 'arnobius_book'
    assert target.route(dict(section_id='PG122:index-in-georgium-cedrenum')) == 'cedrenum_internal'
    assert target.route(dict(section_id='PL005:other-work', reference_namespace='book_chapter')) is None
    assert target.route(dict(section_id='PG122:index-in-joannem-scylitzam', reference_raw='I, 42')) is None


def test_explicit_cross_reference_keeps_same_section_and_multi_target_proofs(tmp_path):
    folder = tmp_path/'teste'/'PO008'/'text'
    folder.mkdir(parents=True)
    file = folder/'body.txt'; file.write_text('body')
    candidate = target.combine_candidates([dict(file=str(file), score=.8, evidence={'page': 12})], tmp_path)[0]

    def record(id, section, label, ref, candidates=()):
        return dict(item_id=id, section_id=section, label=label, reference_raw=ref,
                    reference_namespace='cross_reference' if ref.startswith('Voir') else 'page',
                    occurrence_status='literal_spans_current', candidates=list(candidates),
                    source='index.txt', reference_span=[0,len(ref)], diagnostic={}, status='no_candidates')

    records = [record('a','s','Christ','Voir : Jésus-Christ.'),
               record('b','s','Jésus-Christ','12',[candidate]),
               record('c','other','Wrong','Voir : Jésus-Christ.'),
               record('d','s','Circle1','Voir : Circle2.'),
               record('e','s','Circle2','Voir : Circle1.'),
               record('f','s','Combined','Voir : Jésus-Christ, Absent.')]
    result = target.cross_reference_candidates(records, tmp_path)
    assert result[0]['candidates'][0]['file'] == str(file)
    assert result[0]['candidates'][0]['evidence'][-1]['target_item_id'] == 'b'
    assert not result[2]['candidates'] and not result[3]['candidates'] and not result[4]['candidates']
    assert result[5]['candidates']
    assert result[5]['diagnostic']['cross_reference']['unmatched_targets'] == ['Absent']
