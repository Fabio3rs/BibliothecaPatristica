import hashlib
import sqlite3

import pytest

from tools.indexing.index_version_evidence import (
    align_field, audit_local_readings, read_page_versions, resolve_quote,
)


def test_unique_quote_survives_unrelated_source_change():
    assert resolve_quote('new preface\nAaron 308.', 'Aaron 308.', [0, 10]) == [12, 22]
    assert resolve_quote('Aaron 308. Aaron 308.', 'Aaron 308.') is None
    assert resolve_quote('Aaron 308. Aaron 308.', 'Aaron 308.', [0, 10]) == [0, 10]


def test_numeric_and_lexical_readings_remain_proposals():
    current = 'primus secundus tertius quartus Aaron 308 quinta sexta septima octava'
    alternative = current.replace('Aaron 308', 'Aarou 303')
    a = current.index('Aaron')
    reading = align_field(current, alternative, [a, a+len('Aaron 308')])
    assert reading['status'] == 'numeric_reading_disagreement'
    assert reading['alternative_quote'].strip() == 'Aarou 303'
    assert reading['automatic_repair_allowed'] is False
    reading = align_field(current, current.replace('Aaron', 'Aarou'), [a, a+len('Aaron')])
    assert reading['status'] == 'lexical_reading_disagreement'


def test_repeated_flanks_and_naked_number_do_not_establish_alignment():
    line = 'primus secundus tertius quartus Aaron 308 quinta sexta septima octava'
    a = line.index('Aaron')
    assert align_field(line, line+' '+line, [a, a+5])['status'] == 'not_aligned'
    assert align_field('308', '308', [0, 3])['status'] == 'not_aligned'


def test_omitted_field_flag_is_not_proof_of_correct_reading():
    current = 'primus secundus tertius quartus Aaron quinta sexta septima octava'
    a = current.index('Aaron')
    result = align_field(current, current.replace('Aaron', ''), [a, a+5])
    assert result['possible_omission'] is True
    assert result['automatic_repair_allowed'] is False


def make_history(path, volume, current, alternatives):
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE ocr_engine_versions(id INTEGER,engine TEXT,model TEXT);
        INSERT INTO ocr_engine_versions VALUES(1,'tesseract','migne');
        CREATE TABLE ocr_results(id INTEGER,volume_id TEXT,page_num INTEGER,image_hash TEXT,
        image_path TEXT,text_hash TEXT,text_content TEXT,is_current INTEGER,created_at TEXT,
        engine_version_id INTEGER);''')
    for index, text in enumerate([current]+alternatives, 1):
        db.execute('INSERT INTO ocr_results VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (index, volume, 1, 'old-compressed-image', '/image.png',
                    hashlib.sha256(text.encode()).hexdigest(), text, int(index == 1), '2026', 1))
    db.commit()
    db.close()


def test_readonly_bounded_history_allows_compression_change(tmp_path):
    raw = 'primus secundus tertius quartus Aaron 308 quinta sexta septima octava'
    source = tmp_path/'current.txt'
    source.write_text(raw)
    image = tmp_path/'image.png'
    image.write_bytes(b'new-compressed-image')
    database = tmp_path/'history.sqlite'
    make_history(database, 'PL167', raw, [raw.replace('308', '303'), raw.replace('Aaron', 'Aarou')])
    before = database.read_bytes()
    result = audit_local_readings(source, 'PL167', 1, [{'id': 'entry', 'reference_quote': '308'}],
                                  database, max_versions=2, image_path=image)
    assert len(result['versions']) == 2
    assert result['versions_truncated'] is True
    assert {version['page_identity'] for version in result['versions']} == {'volume_page_registration'}
    assert database.read_bytes() == before
    assert result['automatic_repairs'] == 0


def test_known_replacement_generation_is_not_silently_mixed(tmp_path):
    raw = 'primus secundus tertius quartus Aaron 308 quinta sexta septima octava'
    source = tmp_path/'current.txt'
    source.write_text(raw)
    database = tmp_path/'history.sqlite'
    make_history(database, 'PL124', raw, [raw.replace('308', '303')])
    result = audit_local_readings(source, 'PL124', 1, [{'id': 'entry', 'reference_quote': '308'}], database)
    assert result['checks'] == []
    assert all(version['comparison_status'] == 'source_generation_review_required' for version in result['versions'])
    loaded = read_page_versions(source, 'PL124', 1, database)
    assert all('text_content' not in version for version in loaded['versions'])


def test_destination_history_exposes_admissible_text_and_provenance(tmp_path):
    source = tmp_path/'current.txt'
    source.write_text('Aaron 308')
    database = tmp_path/'history.sqlite'
    make_history(database, 'PL167', 'Aaron 308', ['Aarou 303'])
    result = read_page_versions(source, 'PL167', 1, database)
    assert [r['text_content'] for r in result['versions']] == ['Aaron 308', 'Aarou 303']
    assert all(r['engine'] == 'tesseract' and r['model'] == 'migne' for r in result['versions'])


def test_history_limit_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        audit_local_readings(tmp_path/'absent.txt', 'PL167', 1, [], tmp_path/'absent.db', max_versions=0)
