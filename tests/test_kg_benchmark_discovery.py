"""Small synthetic fixtures only; none of these texts enter the real benchmark."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from tools.knowledge_graph.benchmarks.common import (
    FIELDS, DuplicateIndex, digest, eligibility, excerpt, parse_generation,
    readonly, read_ocr, resolve_source, sha256_text, validate_schema,
)
from tools.knowledge_graph.benchmarks.discover_candidates import Reservoirs, make_record, profile_and_retrieve
from tools.knowledge_graph.benchmarks.enrich_selected import apply_inspection
from tools.knowledge_graph.benchmarks.prepare_review_batches import pack_examples, review_example
from tools.knowledge_graph.benchmarks.select_samples import resolution_pairs, select, work_families
from tools.knowledge_graph.benchmarks.validate_sources import validate_pairs, validate_record, validate_splits


@pytest.fixture
def source(tmp_path):
    corpus = tmp_path / "corpus"
    directory = corpus / "PG001/text"
    directory.mkdir(parents=True)
    path = directory / "PG001-001.txt"
    path.write_text('<pagina><bloco tipo="texto_principal">Non est idem nomen et res.</bloco></pagina>')
    segments = [{"order": 1, "kind": "body", "work_key": "PG001::test", "summary": "O autor distingue nome e coisa. A substância não se reduz ao nome."}]
    row = {k: "" for k in FIELDS}
    row.update(id=1, run_id=1, documento="PG001", pagina_num=1, pagina_file=path.name,
               previous_generation_id=None, is_current=0, tainted_by_page=None,
               ocr_result_id=None, source_hash="opaque-composite", status="valid",
               primary_page_kind="body", segments_json=json.dumps(segments, ensure_ascii=False),
               contributors_json="[]", static_analysis_json="{}", source_conflicts_json="[]",
               validation_issues_json="[]", page_kinds_json='["body"]',
               summary_display_pt=segments[0]["summary"], cumulative_summary="Context only.")
    row["output_hash"] = sha256_text(row["summary_display_pt"], row["cumulative_summary"], json.dumps(segments, ensure_ascii=False, sort_keys=True))
    out = tmp_path / "out"
    record, _ = make_record(parse_generation(row), corpus, out, None, None)
    record["benchmark_id"] = "KG-BENCH-000001"
    record["split"] = "representative"
    return corpus, path, out, record


def test_source_locator_prefers_generation_file_and_rejects_wrong_page(source):
    corpus, path, _, r = source
    uuid = path.with_name("another-001.txt")
    uuid.write_text("Different content")
    row = dict(r["source"], pagina_file=uuid.name)
    assert resolve_source(corpus, row) == (uuid, "stored_filename")
    row["pagina_num"] = 2
    assert resolve_source(corpus, row)[1] == "physical_number_mismatch"
    row["pagina_file"] = "../outside-002.txt"
    assert resolve_source(corpus, row)[1] == "unsafe_filename"


def test_ambiguous_numeric_locator_is_not_silently_chosen(source):
    corpus, path, _, r = source
    path.rename(path.with_name("a-001.txt"))
    path.with_name("b-001.txt").write_text("Second unrelated file")
    assert resolve_source(corpus, r["source"])[1] == "ambiguous_numeric_fallback"


def test_hash_and_exact_evidence_validation_detects_mutations(source):
    corpus, path, out, record = source
    assert validate_record(record, out, corpus)[0] == []
    changed = deepcopy(record)
    changed["evidence"]["summary_quote"] = "Invented quotation"
    assert "summary_quote_not_exact" in validate_record(changed, out, corpus)[0]
    changed = deepcopy(record)
    changed["evidence"]["ocr_start"] += 1
    assert "ocr_quote_not_exact_at_offsets" in validate_record(changed, out, corpus)[0]
    path.write_text("Changed after discovery")
    assert "current_ocr_hash_changed" in validate_record(record, out, corpus)[0]


def test_missing_malformed_and_non_utf8_ocr(source):
    corpus, path, out, record = source
    snapshot = json.loads((out / record["source"]["generation_snapshot"]).read_text())
    row = parse_generation(snapshot)
    path.unlink()
    missing, _ = make_record(row, corpus, out, None, None)
    assert missing["evidence"]["ocr_support"] == "unavailable"
    assert not missing["selection"]["eligible_for_selection"]
    path.write_text('<pagina><bloco tipo="texto_principal">A & B</bloco>')
    malformed, _ = make_record(row, corpus, out, None, None)
    assert "malformed_xml" in malformed["selection"]["risk_flags"]
    assert malformed["evidence"]["ocr_support"] == "uncertain"
    path.write_bytes(b"\xff\xfe")
    invalid, _ = make_record(row, corpus, out, None, None)
    assert invalid["evidence"]["ocr_support"] == "unavailable"


def test_historical_ocr_mismatch_is_excluded(source):
    corpus, _, out, record = source
    row = parse_generation(json.loads((out / record["source"]["generation_snapshot"]).read_text()))
    row["ocr_result_id"] = 5
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE ocr_results (id,volume_id,page_num,text_hash,text_content)")
    con.execute("INSERT INTO ocr_results VALUES (5,'PG001',1,'old','Older OCR')")
    result, _ = make_record(row, corpus, out, con, None)
    assert "ocr_changed_since_generation" in result["selection"]["risk_flags"]
    assert not result["selection"]["eligible_for_selection"]


def test_duplicate_detector_near_and_exact():
    d = DuplicateIndex()
    text = " ".join(f"token{i}" for i in range(100))
    assert d.check_add("first", text) is None
    assert d.check_add("copy", text.upper()) == ("first", 1.0)
    assert d.check_add("near", text + " small addition")[0] == "first"
    assert d.check_add("other", "unrelated different document") is None


def test_catalog_author_can_be_null(source):
    record = deepcopy(source[3])
    record["metadata"]["works"] = [{"author_raw": None, "title_raw": "Anonymous work"}]
    assert work_families(record) == {"anonymous work"}


def sample_records(record, count=120):
    records = []
    for i in range(count):
        r = deepcopy(record)
        r["candidate_id"] = f"candidate-{i}"
        doc = f"PG{i//3+1:03d}"
        key = f"{doc}::work"
        r["source"].update(generation_id=i+1, documento=doc, pagina_num=i%3+1,
                           ocr_sha256=digest(str(i)), work_key=key)
        summary = f"A substância significa algo no argumento sintético de número {i}."
        r["text"]["page_segments"][0].update(work_key=key, summary=summary)
        r["selection"]["risk_flags"] = ["metadata_pending"] if i%2 else []
        records.append(r)
    return records


def test_deterministic_selection_order_independence_and_holdout(source):
    records = sample_records(source[3])
    first, design = select(records, seed=123, sample_size=40)
    second, _ = select(list(reversed(records)), seed=123, sample_size=40)
    assert first == second
    assert len(first) == 40
    assert design["actual_quotas"] == {"calibration": 4, "difficult": 8, "representative": 20, "blind_holdout": 8}
    held = {r["source"]["documento"] for r in first if r["split"] == "blind_holdout"}
    dev = {r["source"]["documento"] for r in first if r["split"] != "blind_holdout"}
    assert not held & dev
    other, _ = select(records, seed=124, sample_size=40)
    assert first != other


def test_split_and_pair_leakage_is_detected(source):
    records, _ = select(sample_records(source[3]), sample_size=40)
    pairs = resolution_pairs(records, seed=123, target=4)
    assert len(pairs) == 4
    assert validate_pairs(pairs, records) == []
    pairs[0]["split"] = "wrong"
    assert any("pair_crosses_split" in e for e in validate_pairs(pairs, records))
    held = next(r for r in records if r["split"] == "blind_holdout")
    duplicate = deepcopy(held)
    duplicate["split"] = "calibration"
    errors = validate_splits([held, duplicate])
    assert any("duplicate_page" in e for e in errors)
    assert "holdout_leakage:volumes" in errors


def test_readonly_database_and_no_legacy_fallback(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE resumos (id)")
    con.commit()
    con.close()
    ro = readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("CREATE TABLE forbidden (id)")
    with pytest.raises(ValueError, match="no legacy fallback"):
        validate_schema(ro)
    assert "is_current=1" in eligibility("promoted")[0]
    with pytest.raises(ValueError, match="explicit"):
        eligibility("shadow")


def test_reservoir_is_bounded_and_order_independent():
    a, b = Reservoirs(17, 5), Reservoirs(17, 5)
    for i in range(100):
        a.add("category", {"id": i})
    for i in reversed(range(100)):
        b.add("category", {"id": i})
    assert sorted(r["id"] for r in a.rows()) == sorted(r["id"] for r in b.rows())
    assert len(list(a.rows())) == 5


def test_excerpt_and_packet_offsets_are_reproducible():
    raw = "x" * 3000 + "Platon gratia" + "y" * 3000
    quote, start, end = excerpt(raw, "Platon gratia", 100)
    assert quote == raw[start:end]
    assert (quote, start, end) == excerpt(raw, "Platon gratia", 100)
    batches = list(pack_examples([{"text": "a"*2000} for _ in range(8)], 3, 1500))
    assert sum(map(len, batches)) == 8
    assert max(map(len, batches)) == 2


def test_review_packet_keeps_full_evidence_and_drops_debug_metadata(source):
    _, path, out, record = source
    record["metadata"]["static_analysis"]["large_debug_payload"] = "x" * 100000
    example = review_example(record, out)
    assert example["full_current_page_ocr"] == read_ocr(path)
    assert json.loads(example["original_v2_summary"]["segments_json"]) == record["text"]["page_segments"]
    assert "large_debug_payload" not in json.dumps(example)
    assert example["benchmark_record"]["context"]["role"] == "context_only_never_current_page_evidence"


def test_crlf_offsets_and_holdout_curation_guard(source):
    _, path, out, record = source
    path.write_bytes(b"alpha\r\nbeta\r\n")
    assert read_ocr(path) == "alpha\r\nbeta\r\n"
    record["split"] = "blind_holdout"
    with pytest.raises(ValueError, match="blind holdout"):
        apply_inspection(record, {}, "")


def test_actual_v2_schema_eligibility_and_profile(source):
    from resumo_v2 import SCHEMA_SQL
    _, _, out, record = source
    row = json.loads((out / record["source"]["generation_snapshot"]).read_text())
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    row.update(facsimile_used=0, schema_version="summary-v2", prompt_version="fixture", model="fixture", provider="fixture")
    for gid, status, current, tainted in [(1,"valid",1,None), (2,"metadata_pending",1,None),
                                          (3,"valid",0,None), (4,"context_provisional",0,4)]:
        r = dict(row, id=gid, pagina_num=gid, status=status, is_current=current, tainted_by_page=tainted)
        con.execute(f"INSERT INTO resumo_generations ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)})", [r[k] for k in FIELDS])
    validate_schema(con)
    where, params = eligibility("promoted")
    profile, shortlist = profile_and_retrieve(con, where, params, 19, 20)
    assert profile["eligible_generations"] == 2
    assert profile["distributions"]["statuses"] == {"metadata_pending": 1, "valid": 1}
    assert {r["id"] for r in shortlist} == {1, 2}
    where, params = eligibility("shadow", [1])
    profile, shortlist = profile_and_retrieve(con, where, params, 19, 20)
    assert {r["id"] for r in shortlist} == {3}
