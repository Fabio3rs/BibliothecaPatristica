import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.entry_parser_experiment import digest
from tools.indexing.historical_source_candidates import prepare, recover, run_versions, search, clauses


def wrapped(body):
    return f'<pagina><bloco tipo="texto_principal">{body}</bloco></pagina>'


def issue(anchor='Aethiopia', kind='missing_printed_entries'):
    return {'id': 'test', 'kind': kind, 'search_anchors': [anchor]}


def test_unknown_markup_and_model_notes_never_become_entries():
    raw = '<unknown bbox="766">Aethiopia, I, 766.</unknown><notas>Aethiopia</notas>'
    assert prepare(raw)[1] == []
    assert recover(wrapped('Aevum, I, 178.'), raw, [issue()])[0] == []
    raw = wrapped('Aevum, I, 178.') + '<notas>Aethiopia, I, 766.</notas>'
    assert recover(wrapped('Aevum, I, 178.'), raw, [issue()])[0] == []


def test_alternative_fragment_keeps_numbers_spans_and_context():
    raw = wrapped('Antecedens nomen, I, 12. Æthiopia quid significat, I, 766. Sequens, ibid.')
    found, checks, _ = recover(wrapped('Antecedens nomen, I, 12. Sequens, ibid.'), raw, [issue()])
    entry, = [r for r in found if 'missing_anchor' in r['origins']]
    assert entry['owner']['quote'] == 'Æthiopia quid significat'
    assert entry['reference']['quote'] == 'I, 766.'
    assert raw[slice(*entry['entry']['span'])] == entry['entry']['quote']
    assert entry['context']['following'][0]['reference']['quote'] == 'ibid.'
    assert entry['context']['status'] == 'adjacent_clauses_not_proven_same_entry'
    assert not entry['automatic_repair_allowed']
    assert checks[0]['current_count'] == 0


def test_bounded_gap_recovers_unlisted_neighbor_and_keeps_reference_variants():
    before = 'Ante hanc regionem nomen primum clarum, I, 100. '
    after = ' Post hanc regionem nomen ultimum clarum, I, 200.'
    current = wrapped(before + after)
    alt = wrapped(before + 'Dacibyza, I, 544. Dadamus filius, I, 726.' + after)
    found, _, alignment = recover(current, alt, [issue('Dacibyza')])
    assert any(c['bounded_by_text'] for c in alignment['changes'])
    assert any(p['owner']['quote'] == 'Dadamus filius' for p in found)
    changed, _, _ = recover(wrapped('Dacibyza, I, 544.'), wrapped('Dacibyza, I, 545.'), [issue('Dacibyza')])
    assert changed[0]['origins'] == ['alternative_reading']
    assert changed[0]['reference']['quote'] == 'I, 545.'


def test_unbounded_gap_does_not_expand_and_duplicates_are_only_evidence():
    found, _, _ = recover(wrapped('Unrelated text.'), wrapped('Dacibyza, I, 544. Dadamus, I, 726.'),
                           [issue('Dacibyza')])
    assert len(found) == 1
    old = wrapped('nec dare potuerunt, I, 423. nec dare potuerunt, I, 423.')
    new = wrapped('nec dare potuerunt, I, 423.')
    found, _, _ = recover(old, new, [issue('nec dare potuerunt', 'duplicated_transcription')])
    assert found[0]['kind'] == 'duplication_candidate'
    assert len(found[0]['current_occurrences']) == 2
    assert not found[0]['automatic_repair_allowed']


def test_preposition_is_not_column_subdivision():
    raw = wrapped('Daemones pulsi, I, 634. A magis abacti, I, 432.')
    prepared, regions = prepare(raw)
    first, second = clauses(raw, prepared, regions)
    assert first['reference']['quote'] == 'I, 634.'
    assert second['owner']['quote'] == 'A magis abacti'


@pytest.mark.parametrize('volume,physical,identity', [
    ('PL101', 728, 'volume_page_registration'),
    ('PG024', 5, 'replacement_source_needs_review'),
    ('PL020', 612, 'excluded_removed_tome_xxi'),
])
def test_readonly_replay_search_and_structural_exceptions(tmp_path, volume, physical, identity):
    source = tmp_path/'current.txt'
    current = wrapped('Aevum, I, 178.')
    source.write_text(current)
    image = tmp_path/'image.png'
    image.write_bytes(b'fixture')
    alternative = wrapped('Aethiopia quid significat, I, 766. Aevum, I, 178.')
    db_path = tmp_path/'versions.sqlite'
    con = sqlite3.connect(db_path)
    con.executescript('''CREATE TABLE ocr_engine_versions(id,engine,model);
        CREATE TABLE ocr_results(id,text_content,text_hash,image_hash,is_current,
                                engine_version_id,volume_id,page_num);''')
    con.execute('INSERT INTO ocr_engine_versions VALUES(1,"test","test")')
    con.execute('INSERT INTO ocr_results VALUES(1,?,?,?,0,1,?,?)',
                (alternative, digest(alternative.encode()), 'different-image', volume, physical))
    con.commit()
    con.close()
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps({'pages': [{'volume': volume, 'physical': physical,
        'source': str(source), 'image': str(image), 'source_sha256': 'older-source', 'issues': [issue()]}]}))
    before = db_path.read_bytes(), source.read_bytes()
    out = tmp_path/'out'
    summary = run_versions(registry, db_path, out, tmp_path)
    assert before == (db_path.read_bytes(), source.read_bytes())
    page = json.loads((out/'pages.jsonl').read_text())
    assert page['source_changed_since_issue_review']
    reading = json.loads((out/'readings.jsonl').read_text())
    assert reading['page_identity'] == identity
    assert Path(reading['snapshot']).read_text() == alternative
    assert summary['ocr_updates'] == summary['canonical_db_updates'] == summary['inference_calls'] == 0
    matches = search(out/'candidates.sqlite', 'Æthiopia')
    if volume == 'PL020':
        assert not matches
    else:
        assert matches[0]['owner']['quote'] == 'Aethiopia quid significat'
        assert matches[0]['reference']['quote'] == 'I, 766.'
        assert matches[0]['page_identity'] == identity
    with pytest.raises(FileExistsError):
        run_versions(registry, db_path, out, tmp_path)


def test_unchanged_entry_not_counted_as_recovery():
    current = wrapped('Æthiopia quid significat, I, 766.')
    alternate = wrapped('Aethiopia quid significat, I, 766.')
    assert recover(current, alternate, [issue()])[0] == []


def test_unreadable_current_is_not_reported_as_a_proven_missing_anchor():
    found, checks, _ = recover('<unknown>Æthiopia, I, 766.</unknown>',
                               wrapped('Æthiopia, I, 766.'), [issue()])
    assert found[0]['origins'] == ['current_not_searchable']
    assert not checks[0]['current_searchable']
