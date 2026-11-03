import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.alphabetical_index_db import connect_db, init_schema
from scripts import repair_alphabetical_material as repairer
from scripts.resolve_alphabetical_unresolved import decide, search_literal_files, scan


def make_db(tmp_path, body, lemma, refs, note="", legacy=None):
    root = tmp_path / "PG001" / "text"
    root.mkdir(parents=True)
    source = root / "page-010.txt"
    source.write_text('<pagina>\n<bloco tipo="nota">' + note + '</bloco>\n'
                      '<bloco tipo="texto_principal">\n' + body + '\n</bloco>\n</pagina>')
    db = tmp_path / "input.db"
    con = connect_db(db)
    init_schema(con)
    con.execute("""INSERT INTO alphabetical_volumes(volume_id,collection,source_root,created_at,updated_at)
        VALUES ('PG001','PG',?,'now','now')""", (str(root),))
    con.execute("""INSERT INTO alphabetical_sections(section_key,volume_id,section_order,section_kind,
        heading_raw,file_start,file_end,raw_json) VALUES ('s','PG001',1,'analytic_subject','INDEX',?,?,'{}')""",
                (str(source), str(source)))
    text = legacy or repairer.normalize_offsets(body)[0]
    con.execute("""INSERT INTO alphabetical_entries(entry_key,section_key,entry_order,entry_kind,
        lemma_raw,entry_raw,index_entry_source_ocr_file,index_section_start_ocr_file,raw_json)
        VALUES ('e','s',1,'lemma',?,?,?,?, '{}')""", (lemma, text, str(source), str(source)))
    for order, number in enumerate(refs, 1):
        con.execute("""INSERT INTO alphabetical_refs(entry_key,ref_order,ref_kind,ref_raw,page_ref_raw,
            page_ref_int,cited_editorial_page_start_number,locator_status,raw_json)
            VALUES ('e',?,'editorial_page',?,?,?,?,'unresolved','{"preserve":true}')""",
                    (order, str(number), str(number), number, number))
    con.commit()
    con.close()
    return db, source


def run_repair(tmp_path, db):
    output = tmp_path / "review.db"
    report = repairer.repair(db, ["PG001"], output, tmp_path / "report.json")
    return output, report


def test_split_preserves_input_ids_raw_and_source_spans(tmp_path):
    db, source = make_db(tmp_path, 'Anaxagoras. 533.\nAnchialus. 487.', 'Anaxagoras', [533, 487])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    output, report = run_repair(tmp_path, db)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert report['counts']['reassign'] == 2
    with sqlite3.connect(output) as con:
        assert con.execute("SELECT ref_id,lemma_raw,ref_raw FROM alphabetical_refs JOIN alphabetical_entries USING(entry_key) ORDER BY ref_id").fetchall() == [(1, 'Anaxagoras', '533'), (2, 'Anchialus', '487')]
        assert con.execute('SELECT line_start,line_end FROM alphabetical_source_spans ORDER BY line_start').fetchall() == [(4, 4), (5, 5)]
        original = json.loads(con.execute('SELECT original_json FROM alphabetical_deterministic_repairs').fetchone()[0])
        assert original['entry']['lemma_raw'] == 'Anaxagoras'
        assert len(original['refs']) == 2
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
        for lo, hi, checksum in con.execute('SELECT line_start,line_end,text_sha256 FROM alphabetical_source_spans'):
            text = ''.join(source.read_text().splitlines(keepends=True)[lo - 1:hi])
            assert hashlib.sha256(text.encode()).hexdigest() == checksum
    # Repeated repair of a review copy does not manufacture more entries or refs.
    second = repairer.repair(output, ['PG001'], tmp_path / 'second.db', tmp_path / 'second.json')
    assert second['counts'] == {}


def test_deduplicated_number_recovers_citation_without_guessing_old_association(tmp_path):
    db, _ = make_db(tmp_path, 'Anaxagoras. 533.\nAnchialus. 487.\nAndronicus agit, 487.', 'Anaxagoras', [533, 487])
    output, report = run_repair(tmp_path, db)
    assert report['counts']['recover'] == 1
    with sqlite3.connect(output) as con:
        original = json.loads(con.execute('SELECT raw_json FROM alphabetical_refs WHERE ref_id=2').fetchone()[0])
        assert original['deterministic_repair']['block_reason'] == 'grouped_entry_requires_segmentation'
        assert con.execute("SELECT ref_raw FROM alphabetical_refs JOIN alphabetical_entries USING(entry_key) WHERE lemma_raw='Anchialus'").fetchall() == [('487',)]


def test_chapters_are_not_pages_and_block_both_locator_paths(tmp_path):
    db, _ = make_db(tmp_path, 'Ὦμοι, humeri. Cœl. Hier. cap. 15, § 3.', 'Ὦμοι, humeri', [15, 3])
    output, report = run_repair(tmp_path, db)
    assert report['counts']['internal_reference'] == 2
    con = sqlite3.connect(output)
    con.row_factory = sqlite3.Row
    for record in con.execute('SELECT * FROM alphabetical_refs'):
        row = dict(record)
        assert row['ref_kind'] == 'target_locator'
        assert row['ref_raw'] in {'15', '3'}
        assert row['page_ref_int'] is None
        assert row['cited_editorial_page_start_number'] is None
        assert search_literal_files(row, {}, [])['blocked_reason'] == 'internal_work_locator'
        assert decide(row, {'candidates': [{'file': 'must-not-be-inspected'}]}, {}) == (None, 'internal_work_locator')
    con.close()


def test_namespace_blocks_reassigned_refs_and_preserves_tome(tmp_path):
    db, _ = make_db(tmp_path, 'AARON consecratio. I, 519\nABINADAB, fratris mei imperium. I, 79', 'AARON consecratio', [519, 79],
                    note='Numeri arabici paginas edit. Veron., Romani Operum Hilarii tomum indicant.')
    output, report = run_repair(tmp_path, db)
    assert report['counts']['reassign'] == 2
    with sqlite3.connect(output) as con:
        rows = con.execute('SELECT raw_json FROM alphabetical_refs').fetchall()
        for row in rows:
            metadata = json.loads(row[0])['deterministic_repair']
            assert metadata['namespace'] == 'verona_edition_and_tome'
            assert metadata['candidate']['citation'].startswith('I, ')
            assert metadata['block_reason'] == 'editorial_namespace_requires_mapping'
    assert scan(output, ['PG001'], tmp_path / 'locator', workers=1)['totals']['accepted'] == 0


def test_nonunique_or_changed_source_does_not_reassign(tmp_path):
    db, _ = make_db(tmp_path, 'Anaxagoras. 533.\nAnchialus. 487.', 'Anaxagoras', [533, 487],
                    legacy='Anaxagoras. 534. Anchialus. 488.')
    _, report = run_repair(tmp_path, db)
    assert report['counts'].get('reassign', 0) == 0
    assert all(d['block_reason'] == 'source_anchor_not_unique' for p in report['proposals'] for d in p['decisions'])


def test_mixed_internal_and_page_occurrences_are_not_reclassified(tmp_path):
    db, _ = make_db(tmp_path, 'Nomen. cap. 15, § 3. Vide pag. 15.', 'Nomen', [15])
    _, report = run_repair(tmp_path, db)
    assert report['counts'].get('internal_reference', 0) == 0


def test_resolved_refs_are_never_rewritten(tmp_path):
    db, source = make_db(tmp_path, 'Anaxagoras. 533.\nAnchialus. 487.', 'Anaxagoras', [533, 487])
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_refs SET locator_status='resolved',target_file=? WHERE ref_id=1", (str(source),))
    _, report = run_repair(tmp_path, db)
    assert report['counts'] == {}


def test_output_cannot_overwrite_existing_database(tmp_path):
    db, _ = make_db(tmp_path, 'AARON. 519', 'AARON', [519])
    with pytest.raises(ValueError, match='NEW path'):
        repairer.repair(db, ['PG001'], db, tmp_path / 'report.json')


def test_changed_ocr_aborts_without_publishing_copy(tmp_path, monkeypatch):
    db, source = make_db(tmp_path, 'Anaxagoras. 533.\nAnchialus. 487.', 'Anaxagoras', [533, 487])
    monkeypatch.setattr(repairer, 'refresh_volume_quality', lambda *args: source.write_text('changed'))
    with pytest.raises(ValueError, match='OCR changed'):
        run_repair(tmp_path, db)
    assert not (tmp_path / 'review.db').exists()


def test_partial_source_recovers_distinct_names_with_same_page(tmp_path):
    body = 'Rastotza, 96.\nRhamble, 80.\nRhegedora, 7.\nRhegepodandus, 7.'
    legacy = 'Rastoiza, 96. Rhamble, 80. Rhegedora, 7. Rhegepodandus, 7. Missingus, 999.'
    db, source = make_db(tmp_path, body, 'Rastoiza', [96, 80, 7, 999], legacy=legacy)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    output, report = run_repair(tmp_path, db)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert report['counts']['recover'] == 3
    assert report['counts']['hold'] == 4
    assert report['proposals'][0]['recovery_scope'] == 'exact_shared_fragments_only'
    with sqlite3.connect(output) as con:
        pairs = con.execute("SELECT e.lemma_raw,r.ref_raw FROM alphabetical_refs r JOIN alphabetical_entries e USING(entry_key) WHERE e.entry_key!='e' ORDER BY e.entry_order").fetchall()
        assert pairs == [('Rhamble', '80'), ('Rhegedora', '7'), ('Rhegepodandus', '7')]
        for (raw,) in con.execute("SELECT raw_json FROM alphabetical_refs WHERE entry_key='e'"):
            assert json.loads(raw)['deterministic_repair']['block_reason'] == 'source_anchor_not_unique'
        for lo, hi, checksum in con.execute('SELECT line_start,line_end,text_sha256 FROM alphabetical_source_spans'):
            text = ''.join(source.read_text().splitlines(keepends=True)[lo - 1:hi])
            assert hashlib.sha256(text.encode()).hexdigest() == checksum


def test_partial_inline_statements_keep_explicit_tome_and_unverified_hierarchy(tmp_path):
    body = 'Inimicorum non est quærenda ultio, sed conversio, I, 341. Iniquitatis operarii qui, I, 145.'
    legacy = body + ' Irenopolis, II, 651. Long legacy entry continues into another unavailable page.'
    db, _ = make_db(tmp_path, body, 'Inimicorum non est quærenda ultio, sed conversio', [341, 145, 651], legacy=legacy)
    output, report = run_repair(tmp_path, db)
    assert report['counts']['recover'] == 2
    with sqlite3.connect(output) as con:
        rows = con.execute("SELECT e.lemma_raw,e.entry_kind,r.ref_raw,r.raw_json FROM alphabetical_refs r JOIN alphabetical_entries e USING(entry_key) WHERE e.entry_key!='e' ORDER BY e.entry_order").fetchall()
        assert [(r[0], r[2]) for r in rows] == [('Inimicorum non est quærenda ultio, sed conversio', 'I, 341'), ('Iniquitatis operarii qui', 'I, 145')]
        for row in rows:
            assert row[1] == 'concordance_item'
            metadata = json.loads(row[3])['deterministic_repair']
            assert metadata['tome'] == 'I'
            assert metadata['candidate']['hierarchy_status'] == 'unverified'
            assert metadata['block_reason'] == 'editorial_namespace_requires_mapping'


def test_partial_recovery_does_not_choose_repeated_or_changed_source_fragments(tmp_path):
    body = 'Rastotza, 96.\nRhamble, 80.\nRhamble, 80.\nRhegedora, 8.'
    legacy = 'Rastoiza, 96. Rhamble, 80. Rhegedora, 7. Missingus, 999.'
    db, _ = make_db(tmp_path, body, 'Rastoiza', [96, 80, 7, 999], legacy=legacy)
    _, report = run_repair(tmp_path, db)
    assert report['counts'].get('recover', 0) == 0


def test_entry_filter_requires_existing_eligible_entry(tmp_path):
    db, _ = make_db(tmp_path, 'Anaxagoras. 533.\nAnchialus. 487.', 'Anaxagoras', [533, 487])
    output = tmp_path / 'review.db'
    with pytest.raises(ValueError, match='entry keys'):
        repairer.repair(db, ['PG001'], output, tmp_path / 'report.json', entry_keys=['missing'])
    assert not output.exists()
