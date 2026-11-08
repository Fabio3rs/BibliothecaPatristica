import json
import sqlite3
from argparse import Namespace

import pytest

from tools.indexing import hybrid_retrieval_experiment as hybrid


def candidates(count=40):
    rows = [{"path": str(i), "physical_page": i, "score": 1 - i / 100}
            for i in range(1, count + 1)]
    headers = {r["path"]: {**r, "numbers": [r["physical_page"] * 2]} for r in rows}
    return rows, headers


def test_editorial_quota_and_semantic_reserve_without_gold():
    rows, headers = candidates()
    refs = [{"ref_id": 1, "ref_raw": "70", "number": 70}]
    choices, evidence = hybrid.shortlist(rows, refs, headers, enabled=True)
    assert len(choices["hybrid"]) == 20
    assert choices["hybrid"][0] == "35"
    assert "1" in choices["hybrid"]
    assert "35" not in choices["semantic_only"]
    assert evidence["35"][0]["kind"] == "header_number_match"
    assert evidence["34"][0]["kind"] == "physical_neighbor_of_match"
    assert len(set(choices["hybrid"])) == 20


def test_unmapped_namespace_uses_only_semantic_fallback():
    rows, headers = candidates()
    refs = [{"ref_id": 1, "ref_raw": "70", "number": 70}]
    choices, evidence = hybrid.shortlist(rows, refs, headers, enabled=False)
    assert choices["hybrid"] == choices["semantic_only"]
    assert choices["editorial_only"] == [] and evidence == {}


def test_restarted_numbering_and_multiple_occurrences_not_collapsed():
    rows, headers = candidates()
    headers["37"]["numbers"] = [60]
    refs = [{"ref_id": 1, "ref_raw": "60", "number": 60},
            {"ref_id": 2, "ref_raw": "70", "number": 70}]
    choices, _ = hybrid.shortlist(rows, refs, headers, enabled=True, editorial_slots=4, radius=0)
    assert choices["hybrid"][:3] == ["30", "35", "37"]


def test_missing_header_uses_neighbors_without_deriving_editorial_number_from_filename():
    rows, headers = candidates()
    headers["35"]["numbers"] = []
    refs = [{"ref_id": 1, "ref_raw": "70", "number": 70}]
    choices, evidence = hybrid.shortlist(rows, refs, headers, enabled=True, radius=2)
    assert "34" in choices["editorial_only"] and "36" in choices["editorial_only"]
    assert "35" not in evidence  # No silent 2*physical+offset assignment.


def test_metrics_preserve_all_known_targets():
    m = hybrid.metrics(["a", "b"], {"a", "c"}, 100)
    assert m["recall"] == .5 and m["hit"] == 1 and m["all_gold"] == 0
    assert m["candidate_fraction"] == .02 and m["gold_ranks_in_shortlist"]["c"] is None


@pytest.fixture
def saved_run(tmp_path):
    root = tmp_path / 'corpus'
    folder = root / 'PG999/text'
    folder.mkdir(parents=True)
    run = tmp_path / 'run'
    run.mkdir()
    inventory, ranking = [], []
    for i in range(1, 41):
        path = folder / f'PG999-{i:03d}.txt'
        path.write_text(f'<pagina><bloco tipo="cabecalho">{i * 2} LIBER</bloco>'
                        '<bloco tipo="texto_principal">pax concordia</bloco></pagina>')
        inventory.append({"path": str(path), "volume": "PG999", "physical_page": i,
                          "ocr_sha256": hybrid.digest(path.read_bytes())})
        ranking.append({"path": str(path), "physical_page": i, "score": 1 - i / 100,
                        "snippet": "pax concordia", "is_gold": i == 35})
    query = {"query_id": "e1", "entry_key": "e1", "query": "Pax", "entry_raw": "Pax, 70, 72.",
             "section_key": "s1", "volume": "PG999", "label_origin": "previous_jev",
             "gold_files": {inventory[34]["path"]: inventory[34]["ocr_sha256"]},
             "references": [{"ref_id": 1, "ref_raw": "70"}]}
    for name, obj in (("sample", {"queries": [query]}), ("sources", inventory),
                      ("results", [{"status": "evaluated", "query_id": "e1", "query_mode": "instruct",
                                    "variant": "chunks", "ranking": ranking}])):
        (run / f'{name}.json').write_text(json.dumps(obj))
    db = tmp_path / 'main.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''
      CREATE TABLE alphabetical_sections(section_key TEXT,volume_id TEXT);
      CREATE TABLE alphabetical_entries(entry_key TEXT,lemma_raw TEXT,entry_raw TEXT,section_key TEXT);
      CREATE TABLE alphabetical_refs(ref_id INTEGER,entry_key TEXT,ref_order INTEGER,ref_raw TEXT,
                                    ref_kind TEXT,target_file TEXT,locator_status TEXT);
      INSERT INTO alphabetical_sections VALUES ('s1','PG999');
      INSERT INTO alphabetical_entries VALUES ('e1','Pax','Pax, 70, 72.','s1');
      INSERT INTO alphabetical_refs VALUES (1,'e1',1,'70','editorial_page','POISONED_TARGET','resolved');
      INSERT INTO alphabetical_refs VALUES (2,'e1',2,'72','editorial_page',NULL,'unresolved');
    ''')
    con.commit()
    con.close()
    return Namespace(run=run, db=db, root=root, out=tmp_path / 'out', header_section=['s1'],
                     top_k=20, editorial_slots=10, numeric_radius=2, variant='chunks', query_mode='instruct')


def test_offline_run_uses_unresolved_printed_occurrences_without_db_mutation(saved_run):
    before = saved_run.db.read_bytes()
    summary = hybrid.run(saved_run)
    assert saved_run.db.read_bytes() == before
    assert summary["inference_calls"] == summary["production_db_updates"] == 0
    rows = json.loads((saved_run.out / 'results.json').read_text())
    mixed = next(r for r in rows if r['variant'] == 'hybrid')
    assert mixed['metrics']['all_gold'] == 1
    assert mixed['selection'][0]['physical_page'] == 35
    assert mixed['selection'][1]['physical_page'] == 36
    citations = json.loads((saved_run.out / 'citations.json').read_text())['e1']
    assert len(citations) == 2
    assert all('target_file' not in c and 'locator_status' not in c for c in citations)


@pytest.mark.parametrize('raw', ['770, 9', 'I, 757', '12-18', '12 et seq.', 'Leo 70', 'III', '１２'])
def test_complex_literals_fall_back_instead_of_becoming_page_numbers(saved_run, raw):
    with sqlite3.connect(saved_run.db) as con:
        con.execute('UPDATE alphabetical_refs SET ref_raw=? WHERE ref_id=2', (raw,))
    queries = json.loads((saved_run.run / 'sample.json').read_text())['queries']
    refs = hybrid.load_citations(saved_run.db, queries)['e1']
    assert refs[0]['number'] == 70 and refs[1]['number'] is None


def test_changed_ocr_rejected(saved_run):
    (saved_run.root / 'PG999/text/PG999-035.txt').write_text('changed')
    with pytest.raises(ValueError, match='OCR changed'):
        hybrid.run(saved_run)


def test_known_internal_numbering_cannot_be_matched_to_headers(saved_run):
    saved_run.header_section = ['PG069:alpha:analytic_subject:001']
    with pytest.raises(ValueError, match='internal bold numbers'):
        hybrid.run(saved_run)
    assert not saved_run.out.exists()


def test_empty_sample_rejected(saved_run):
    (saved_run.run / 'sample.json').write_text('{"queries": []}')
    with pytest.raises(ValueError, match='Empty sample'):
        hybrid.run(saved_run)


def test_changed_printed_reference_rejected(saved_run):
    with sqlite3.connect(saved_run.db) as con:
        con.execute("UPDATE alphabetical_refs SET ref_raw='80' WHERE ref_id=1")
    with pytest.raises(ValueError, match='Printed reference differs'):
        hybrid.run(saved_run)


def test_gold_annotation_does_not_change_selection(saved_run):
    hybrid.run(saved_run)
    before = json.loads((saved_run.out / 'results.json').read_text())
    sample_path = saved_run.run / 'sample.json'
    sample = json.loads(sample_path.read_text())
    sources = json.loads((saved_run.run / 'sources.json').read_text())
    sample['queries'][0]['gold_files'] = {sources[1]['path']: sources[1]['ocr_sha256']}
    sample_path.write_text(json.dumps(sample))
    saved_run.out = saved_run.out.parent / 'new_labels'
    hybrid.run(saved_run)
    after = json.loads((saved_run.out / 'results.json').read_text())
    assert [[p['path'] for p in r['selection']] for r in before] == [[p['path'] for p in r['selection']] for r in after]
