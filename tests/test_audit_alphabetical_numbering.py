import hashlib
import json
from pathlib import Path
import pytest

from scripts.alphabetical_index_db import connect_db, init_schema
from scripts.audit_alphabetical_numbering import (
    audit, build_series, queries_for, reference_tome, supported_sequence_nodes,
)


def test_sequences_allow_missing_markers_without_interpolating_numbers():
    nodes = {n: {i} for i, n in enumerate([10, 11, 13, 14, 16, 17, 19], 1)}
    nodes[850] = {4}
    supported = supported_sequence_nodes(nodes)
    assert (14, 4) in supported
    assert (850, 4) not in supported
    assert all(n != 12 for n, _ in supported)
    assert supported == supported_sequence_nodes(dict(reversed(list(nodes.items()))))


def test_numbers_in_one_file_or_reverse_order_do_not_establish_pagination():
    assert supported_sequence_nodes({n: {1} for n in range(1, 20)}) == {}
    assert supported_sequence_nodes({n: {20 - n} for n in range(1, 20)}) == {}


def test_tome_is_explicit_and_not_inherited_from_arbitrary_legacy_context():
    row = {'ref_raw': '79', 'context_raw': 'I, 519 ... II, 79', 'raw_json': '{}'}
    assert reference_tome(row) is None
    assert reference_tome(dict(row, ref_raw='II, 79')) == 'II'
    repair = {'deterministic_repair': {'candidate': {'citation': 'I, 79, II, 80'}}}
    assert reference_tome(dict(row, raw_json=json.dumps(repair))) is None


def test_greek_cases_do_not_conflate_place_and_person():
    row = {'lemma_raw': 'Ἀθανάσιος', 'section_kind': 'onomastic_person', 'raw_json': '{}'}
    assert 'αθανασιου' not in queries_for(row)
    assert 'αθανασιου' in queries_for(row, True)
    variants = queries_for(dict(row, lemma_raw='Μακεδονία'), True)
    assert 'μακεδονιοσ' not in variants
    assert 'μακεδονιου' not in variants


def test_reviewed_anchor_rejects_late_note_counters_and_detects_stale_ocr(tmp_path):
    root = tmp_path / 'PG001' / 'text'
    root.mkdir(parents=True)
    for i in range(1, 8):
        suffix = ' 2 another note' if i == 7 else ''
        (root / f'page-{i:03}.txt').write_text(
            f'<pagina><bloco tipo="texto_principal">{i} Text.{suffix}</bloco></pagina>')
    anchor_file = root / 'page-004.txt'
    anchor = {'number': 4, 'physical_sequence': 4,
              'source_sha256': hashlib.sha256(anchor_file.read_bytes()).hexdigest()}
    spec = {'id': 'test', 'volume': 'PG001', 'physical_start': 1, 'physical_end': 7,
            'reviewed_anchors': [anchor]}
    result = build_series(spec, tmp_path)
    assert result['nodes'][2] == {2}
    assert result['anchor_rejected_nodes'] == 1
    assert result['supported'][4, 4]['basis'] == 'reviewed_anchor'
    anchor_file.write_text(anchor_file.read_text().replace('Text', 'Other'))
    with pytest.raises(ValueError, match='anchor'):
        build_series(spec, tmp_path)


def test_audit_is_readonly_and_candidates_cannot_be_applied(tmp_path):
    root = tmp_path / 'corpus' / 'PG001' / 'text'
    root.mkdir(parents=True)
    for i in range(1, 8):
        name = 'Athanasius' if i == 4 else 'Alius'
        (root / f'page-{i:03}.txt').write_text(
            f'<pagina><bloco tipo="cabecalho">999</bloco>'
            f'<bloco tipo="texto_principal" script="latino">{i} {name} disputat.</bloco>'
            '<bloco tipo="rodape">850 Athanasius</bloco></pagina>')
    index = root / 'page-010.txt'
    index.write_text('<pagina><bloco tipo="texto_principal">Athanasius 4</bloco></pagina>')
    db = tmp_path / 'input.sqlite'
    con = connect_db(db)
    init_schema(con)
    con.execute("""INSERT INTO alphabetical_volumes(volume_id,collection,source_root,created_at,updated_at)
                   VALUES ('PG001','PG',?,'now','now')""", (str(root),))
    con.execute("""INSERT INTO alphabetical_sections(section_key,volume_id,section_kind,heading_raw,file_start,file_end,raw_json)
                   VALUES ('s','PG001','onomastic_person','INDEX',?,?,'{}')""", (str(index), str(index)))
    con.execute("""INSERT INTO alphabetical_entries(entry_key,section_key,entry_order,entry_kind,lemma_raw,entry_raw,raw_json)
                   VALUES ('e','s',1,'lemma','Athanasius','Athanasius 4','{}')""")
    con.execute("""INSERT INTO alphabetical_refs(entry_key,ref_order,ref_kind,ref_raw,page_ref_raw,page_ref_int,locator_status,raw_json)
                   VALUES ('e',1,'editorial_page','4','4',4,'unresolved','{}')""")
    con.commit()
    con.close()
    spec = {'id': 'test', 'volume': 'PG001', 'physical_start': 1, 'physical_end': 7}
    series = build_series(spec, tmp_path / 'corpus')
    assert 850 not in series['nodes']
    assert 999 not in series['nodes']
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    profile = {'index_volume': 'PG001', 'series': [spec]}
    result = audit(db, profile, tmp_path / 'report', tmp_path / 'corpus')
    assert result['counts'] == {'unique_file_candidate': 1}
    assert result['application_supported'] is False
    assert result['mode'] == 'diagnostic_only'
    assert Path(result['records'][0]['candidates'][0]['file']).name == 'page-004.txt'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    unknown = audit(db, dict(profile, requires_tome=True), tmp_path / 'unknown', tmp_path / 'corpus')
    assert unknown['counts'] == {'tome_unknown': 1}
