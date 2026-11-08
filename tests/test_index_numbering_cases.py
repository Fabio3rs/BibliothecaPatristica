import json
from pathlib import Path
import sqlite3

from scripts.investigate_index_numbering_cases import find_evidence, investigate, sha


def test_evidence_excludes_coordinates_and_model_commentary():
    raw = '<pagina><bloco tipo="texto_principal" bbox="167,0,0,0">Cresconius 168.</bloco><notas>167</notas></pagina>'
    assert find_evidence(raw, r"\b167\b") == []
    hit, = find_evidence(raw, r"\b168\b")
    assert raw[slice(*hit["span"])] == hit["quote"] == "168"
    assert hit["kind"] == "texto_principal"


def test_inline_markup_and_numeric_boundaries():
    raw = '<pagina><bloco tipo="texto_principal">1167 <b bbox="502">167</b> Aquensis</bloco></pagina>'
    assert find_evidence(raw, r"\b502\b") == []
    hit, = find_evidence(raw, r"\b167\b")
    assert hit["quote"] == "167"
    assert raw[slice(*hit["span"])] == "167"


def test_unknown_xml_is_not_silently_searched_as_plain_text():
    assert find_evidence('<unknown bbox="167">167</unknown>', r"167") == []
    assert find_evidence('Cresconius 167', r"\b167\b")[0]["kind"] == "unstructured"


def test_replay_preserves_source_db_and_flags_changed_source(tmp_path):
    source = tmp_path / "teste/PL058/text/source-001.txt"
    source.parent.mkdir(parents=True)
    raw = '<pagina><bloco tipo="texto_principal">Aquensis 167</bloco></pagina>'
    source.write_text(raw)
    start = raw.index('167')
    audit = tmp_path / 'audit.jsonl'
    audit.write_text(json.dumps({'id': 'a', 'source': str(source), 'ref_raw': '167',
        'source_span': [start, start+3], 'source_sha256': 'older-version'})+'\n')
    db = tmp_path / 'versions.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''CREATE TABLE ocr_engine_versions(id, engine, model);
        CREATE TABLE ocr_results(id, text_content, text_hash, image_hash,
          is_current, engine_version_id, volume_id, page_num);''')
    con.execute('INSERT INTO ocr_engine_versions VALUES(1,?,?)', ('test', 'test'))
    con.execute('INSERT INTO ocr_results VALUES(1,?,?,?,?,1,?,1)', (raw, 'hash', 'image', 1, 'PL058'))
    con.commit()
    con.close()
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'cases': [{'case_id': 'test', 'item_ids': ['a'],
        'diagnosis': 'internal', 'reviewed_candidates': [], 'visual_observations': [],
        'probes': [{'volume': 'PL058', 'start': 1, 'end': 1, 'pattern': r'\b167\b'}],
        'history_pages': [{'volume': 'PL058', 'physical': 1, 'pattern': r'\b167\b'}]}]}))
    before = sha(db), sha(source)
    out = tmp_path / 'result'
    summary = investigate(tmp_path, profile, audit, db, out)
    assert (sha(db), sha(source)) == before
    assert summary['inference_calls'] == summary['canonical_db_updates'] == 0
    case, = json.loads((out / 'cases.json').read_text())
    assert case['items'][0]['current_source_state'] == 'changed'
    assert case['items'][0]['saved_span_still_matches']
    assert case['history'][0]['hits'][0]['quote'] == '167'
    assert case['probes'][0]['evidence'][0]['physical'] == 1
