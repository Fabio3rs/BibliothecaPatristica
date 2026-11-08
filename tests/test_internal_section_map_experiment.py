import hashlib
import json
import sqlite3

import pytest

from tools.indexing.internal_section_map_experiment import build_map, extract_markers, compare_occurrences, main, verify_notice


def fixture(tmp_path):
    root = tmp_path / 'teste'
    text = root / 'PG999' / 'text'
    text.mkdir(parents=True)
    for n in range(1, 6):
        (text / f'fixture-{n:03d}.txt').write_text(
            f'<pagina><bloco tipo="texto_principal" script="latino">'
            f'1. Caput\n\nVerba {n * 2 - 1} multa {n * 2} alia.</bloco></pagina>')
    profile = {'id': 'test-bounded', 'volume': 'PG999', 'physical_start': 1,
               'physical_end': 5, 'number_start': 1, 'number_end': 10,
               'index_physical_start': 100, 'index_physical_end': 100}
    return root, profile


def test_raw_spans_and_number_roles():
    raw = ('<pagina><bloco tipo="cabecalho">369</bloco>'
           '<bloco tipo="texto_principal" script="grego">[P. 227]</bloco>'
           '<bloco tipo="texto_principal" script="latino">22. Caput\n\n'
           'Verba &amp; 369 alia. [P. 228] et (12) nota, anno 6428; per 158 annos.'
           '</bloco></pagina>')
    markers = extract_markers(raw)
    assert [m['number'] for m in markers] == [22, 369, 228, 12, 6428, 158]
    assert [m['role'] for m in markers] == [
        'paragraph_number', 'inline_number_candidate', 'other_bracket_numbering',
        'parenthetical_number', 'explicit_non_marker_context', 'quantity_context']
    assert all(raw[m['char_start']:m['char_end']] == str(m['number']) for m in markers)


def test_same_number_chapter_does_not_pollute_window(tmp_path):
    root, profile = fixture(tmp_path)
    result = build_map(profile, root)
    one = result['numbers'][0]
    assert one['status'] == 'bounded_window_candidate'
    assert one['marker_files'] == [1]
    assert [f['physical'] for f in one['files']] == [1]
    two = result['numbers'][1]
    assert [f['physical'] for f in two['files']] == [1, 2]
    assert result['numbers'][-1]['status'] == 'missing_supported_boundary'


def test_missing_number_not_interpolated(tmp_path):
    root, profile = fixture(tmp_path)
    path = root / 'PG999/text/fixture-003.txt'
    path.write_text(path.read_text().replace('Verba 5', 'Verba'))
    result = build_map(profile, root)
    assert result['numbers'][4]['marker_files'] == []
    assert result['numbers'][3]['files'] == []
    assert result['numbers'][3]['marker_file_only']['physical'] == 2


def test_duplicate_marker_keeps_ambiguity(tmp_path):
    root, profile = fixture(tmp_path)
    path = root / 'PG999/text/fixture-003.txt'
    path.write_text(path.read_text().replace('Verba 5', 'Verba 5 etiam 5'))
    result = build_map(profile, root)
    assert result['numbers'][4]['status'] == 'ambiguous_markers'
    assert result['numbers'][4]['files'] == []


def test_anchor_context_and_image_hash_required(tmp_path):
    root, profile = fixture(tmp_path)
    images = root / 'PG999/images'
    images.mkdir()
    (images / 'PG999-1.png').write_bytes(b'test-image')
    profile['anchors'] = [{'number': 1, 'physical': 1, 'context': 'Verba 1 multa',
        'image_name': 'PG999-1.png', 'image_sha256': hashlib.sha256(b'test-image').hexdigest()}]
    assert build_map(profile, root)['anchors'][0]['number'] == 1
    path = root / 'PG999/text/fixture-001.txt'
    path.write_text(path.read_text().replace('Verba 1', 'Verba 11'))
    with pytest.raises(ValueError, match='renewed review'):
        build_map(profile, root)
    (images / 'PG999-1.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='image changed'):
        build_map(profile, root)


def test_correction_rebuilds_fresh_spans_and_refuses_old_index(tmp_path):
    root, profile = fixture(tmp_path)
    mapping = build_map(profile, root)
    source = root / 'PG999/text/fixture-100.txt'
    source.write_text('Lemma 1')
    db = tmp_path / 'audit.sqlite'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE audit(volume TEXT,evidence_json TEXT)')
    evidence = {'number': 1, 'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'reference_span': [6,7], 'occurrence_id': 'one', 'owner_raw': 'Lemma'}
    con.execute('INSERT INTO audit VALUES(?,?)', ('PG999', json.dumps(evidence)))
    con.commit()
    con.close()
    original_db = db.read_bytes()
    assert compare_occurrences(mapping, db)[0]['status'] == 'bounded_window_candidate'
    mapping['profile']['required_audit_rule'] = 'correct-section'
    assert compare_occurrences(mapping, db)[0]['status'] == 'source_requires_reextraction'
    del mapping['profile']['required_audit_rule']
    source.write_text('Corrected Lemma 1')
    assert compare_occurrences(mapping, db)[0]['status'] == 'source_requires_reextraction'
    assert db.read_bytes() == original_db
    page = root / 'PG999/text/fixture-002.txt'
    before = mapping['numbers'][2]['start_options'][0]['occurrence']['char_start']
    page.write_text(page.read_text().replace('Verba', 'Verba correcta'))
    refreshed = build_map(profile, root)
    assert refreshed['numbers'][2]['start_options'][0]['occurrence']['char_start'] > before


def test_cli_staging_wal_and_immutable_runs(tmp_path):
    root, profile = fixture(tmp_path)
    file = tmp_path / 'profile.json'
    file.write_text(json.dumps(profile))
    out = tmp_path / 'run'
    argv = ['--profile', str(file), '--root', str(root), '--out', str(out)]
    main(argv)
    con = sqlite3.connect(out / 'map.sqlite')
    assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert con.execute('SELECT COUNT(*) FROM numbers').fetchone()[0] == 10
    assert con.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 5
    con.close()
    with pytest.raises(ValueError, match='new output directory'):
        main(argv)


def test_notice_requires_unique_current_literal(tmp_path):
    root, profile = fixture(tmp_path)
    source = root / 'PG999/text/fixture-100.txt'
    source.write_text('<pagina><bloco tipo="texto_principal" script="latino">Ad numeros grandiores.</bloco></pagina>')
    profile['notice'] = {'physical': 100, 'quote': 'Ad numeros grandiores.'}
    assert verify_notice(profile, root)['status'] == 'unique_current_notice'
    source.write_text(source.read_text().replace('Ad numeros grandiores.', 'Ad paginas.'))
    with pytest.raises(ValueError, match='notice changed'):
        verify_notice(profile, root)


def test_real_pg148_continuation_includes_next_file():
    from tools.indexing.internal_section_map_experiment import ROOT
    profile = ROOT / 'data/index_entry_parser/section_maps_internal_001/pg148_profile.json'
    if not profile.exists():
        pytest.skip('Optional reviewed local corpus profile unavailable')
    result = build_map(json.loads(profile.read_text()), ROOT / 'teste')
    assert result['numbers'][0]['number'] == 142
    assert [f['physical'] for f in result['numbers'][0]['files']] == [148, 149]
def test_mixed_script_is_explicit_and_does_not_include_apparatus():
    from tools.indexing.internal_section_map_experiment import extract_markers
    raw = ('<bloco tipo="texto_principal" script="misto">text 214 here</bloco>'
           '<bloco tipo="aparato_critico" script="misto">text 215 here</bloco>')
    assert extract_markers(raw) == []
    assert [r['number'] for r in extract_markers(raw, ['latino', 'misto'])] == [214]
