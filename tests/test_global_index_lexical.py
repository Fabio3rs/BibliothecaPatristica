from scripts.audit_global_index_lexical import fast_matches, index_text, search, load_records, volume_worker
from scripts.audit_po015_names_lexical import page_matches, read_history_text
import pytest


def test_vocabulary_prefilter_has_same_matches_as_bounded_audit():
    raw = '<bloco tipo="texto_principal" script="grego">Ἀαρὼν καὶ Σαβελλίου καὶ Ζαχαρίας Ἀαρών</bloco>'
    for query in ['Ἀαρών', 'Σαβέλλιος', 'Ζαχρίας', 'Ὥρ', 'Σαβέλλιος καὶ Ζαχρίας']:
        slow = page_matches(read_history_text(raw), query, 'full_label', 0.8)
        fast = fast_matches(index_text(raw), query)
        assert [(h['method'], h['char_start'], h['char_end']) for h in fast] == [
            (h['method'], h['char_start'], h['char_end']) for h in slow]


def test_latin_body_is_not_assumed_to_be_parallel_translation():
    p = index_text('<bloco tipo="texto_principal" script="latino">Augustinus</bloco>')
    assert search(p, 'Augustinus')['matches'][0]['role'] == 'body'


def test_partial_query_match_is_not_full_label():
    p = index_text('Ptolemaeus Euergetes')
    result = search(p, 'Ptolemaeus Philadelphus')
    assert result['method'] == 'exact' and result['query_scope'] == 'first_token_only'


def test_catalog_override_does_not_add_old_source_groups(tmp_path):
    import json
    directory = tmp_path / 'survey/volumes/PO015'
    directory.mkdir(parents=True)
    (directory / 'summary.json').write_text('{}')
    (directory / 'groups.jsonl').write_text(json.dumps({'id': 'old', 'source': 'source', 'status': 'old'})+'\n')
    (directory / 'occurrences.jsonl').write_text(json.dumps({'group_id': 'old'})+'\n')
    catalog = tmp_path / 'catalog'
    catalog.mkdir()
    (catalog / 'entries.json').write_text(json.dumps([{'source': 'source', 'id': 'e', 'name_raw': 'name',
        'references': [{'id': 'r', 'source': 'source', 'source_sha256': 'hash', 'char_start': 0,
                        'char_end': 1, 'ref_raw': '1', 'status': 'missing', 'target_files': []}]}]))
    records, _, _ = load_records(tmp_path / 'survey', 'PO015', catalog)
    rows = list(records)
    assert len(rows) == 1 and rows[0]['id'] == 'po015:r'


def test_recovered_reference_uses_its_span_and_owner_not_legacy_group(tmp_path):
    import json
    directory = tmp_path/'volumes/PL010'; directory.mkdir(parents=True)
    (directory/'summary.json').write_text('{}')
    group = {'id':'g','source':'s','source_sha256':'h','char_start':50,'char_end':52,
             'raw':'20','owner_raw':'Alpha I, 10. Beta','status':'profile_interpreted'}
    (directory/'groups.jsonl').write_text(json.dumps(group)+'\n')
    ref = {'id':'r','group_id':'g','owner_raw':'Alpha','source_span':[10,15],'ref_raw':'I, 10',
           'tier':'documented_profile','status':'marker_file_candidate','candidate_role':'primary',
           'uncertainties':['owner_unverified'],'files':[]}
    (directory/'occurrences.jsonl').write_text(json.dumps(ref)+'\n')
    records, _, _ = load_records(tmp_path,'PL010',tmp_path/'unused')
    row, = records
    assert row['query'] == 'Alpha' and row['ref_raw'] == 'I, 10'
    assert row['source_span'] == [10,15] and row['group_source_span'] == [50,52]
    assert row['reference_interpretation']['uncertainties'] == ['owner_unverified']


@pytest.mark.parametrize('destination,physical,image_state,identity', [
    ('PL009', 2, 'same', 'same_image_bytes'),
    ('PL009', 2, 'different', 'volume_page_registration'),
    ('PL009', 2, 'missing', 'volume_page_registration'),
    ('PL124', 2, 'different', 'replacement_source_needs_review'),
    ('PL020', 611, 'different', 'volume_page_registration'),
    ('PL020', 612, 'same', 'excluded_removed_tome_xxi'),
])
def test_worker_history_uses_destination_volume_and_preserves_numeric_tier(
        tmp_path, destination, physical, image_state, identity):
    import hashlib
    import json
    import sqlite3
    from scripts.catalog_po015_names import sha
    root = tmp_path / 'teste'
    source = root / 'PG002/text/source-001.txt'
    target = root / destination / f'text/target-{physical:03}.txt'
    image = root / destination / f'images/{destination}-{physical:03}.png'
    for p in [source, target, image]:
        p.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('Aaron 33')
    target.write_text('unrelated')
    if image_state != 'missing':
        image.write_bytes(b'image')
    survey = tmp_path / 'survey'
    d = survey / 'volumes/PG002'
    d.mkdir(parents=True)
    (d / 'summary.json').write_text(json.dumps({'counts': {'selected_source_pages': 1}}))
    group = {'id': 'g', 'source': str(source), 'source_sha256': sha(source),
             'char_start': 6, 'char_end': 8, 'raw': '33', 'owner_raw': 'Aaron', 'status': 'numeric_screen_only'}
    (d / 'groups.jsonl').write_text(json.dumps(group)+'\n')
    occurrence = {'id': 'r', 'group_id': 'g', 'status': 'header_coincidence', 'tier': 'unmapped_namespace_screen',
                  'files': [{'path': str(target), 'sha256': sha(target), 'physical': physical}]}
    (d / 'occurrences.jsonl').write_text(json.dumps(occurrence)+'\n')
    history = tmp_path / 'history.sqlite'
    with sqlite3.connect(history) as con:
        con.executescript('''CREATE TABLE ocr_engine_versions(id,engine,model);
            INSERT INTO ocr_engine_versions VALUES(1,'tesseract','test');
            CREATE TABLE ocr_results(id,volume_id,page_num,image_hash,text_hash,text_content,
                is_current,created_at,engine_version_id);''')
        con.execute('INSERT INTO ocr_results VALUES(?,?,?,?,?,?,?,?,?)',
                    (1, destination, physical, sha(image) if image_state == 'same' else 'old-bytes',
                     hashlib.sha256(b'Aaron').hexdigest(), 'Aaron', 0, '', 1))
    before = sha(history), sha(source), sha(target)
    out = tmp_path / 'out'
    config = {'out': str(out), 'survey': str(survey), 'root': str(root),
              'catalog': str(tmp_path / 'unused'), 'versions_db': str(history)}
    result = volume_worker(config, 'PG002')
    excluded = identity.startswith('excluded_')
    assert result['counts'].get('historical_exact', 0) == (0 if excluded else 1)
    record = json.loads((out / 'volumes/PG002/evidence.jsonl').read_text())
    assert record['tier'] == 'unmapped_namespace_screen'
    assert not record['verified'] and record['line_status'] == 'not_inferred'
    alternatives = record['targets'][0]['alternatives']
    if excluded:
        assert not alternatives
        assert result['historical_versions'][identity] == 1
    else:
        assert alternatives[0]['version_id'] == 1
        assert alternatives[0]['page_identity'] == identity
    assert before == (sha(history), sha(source), sha(target))
