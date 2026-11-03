from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.alphabetical_index_db import connect_db, init_schema
from scripts.resolve_alphabetical_unresolved import (
    apply_reports, cited_start, code_fingerprint, connect_readonly, decide,
    is_eligible, load_job, make_items, process_volume, scan,
    body_matches, name_head_queries,
    header_file_index, search_literal_files, source_snapshot,
)


@pytest.fixture
def isolated_caches(tmp_path, monkeypatch):
    from scripts import resolve_alphabetical_unresolved as resolver
    monkeypatch.setattr(resolver, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(resolver.editorial_page_estimator, "DEFAULT_ESTIMATOR_DB", tmp_path / "estimator.sqlite")


@pytest.fixture
def corpus(tmp_path, isolated_caches):
    db = tmp_path / "alphabetical.db"
    con = connect_db(db)
    init_schema(con)
    for volume in ("PG001", "PG002"):
        root = tmp_path / volume / "text"
        root.mkdir(parents=True)
        (root / "page-001.txt").write_text(
            '<pagina><bloco tipo="cabecalho">101 HISTORIA 102</bloco>\n'
            '<bloco tipo="texto_principal">Athanasius contra Arianos disputavit.</bloco></pagina>')
        (root / "page-002.txt").write_text(
            '<pagina><bloco tipo="cabecalho">103 HISTORIA 104</bloco>\n'
            '<bloco tipo="texto_principal">Alius episcopus respondet.</bloco></pagina>')
        index = root / "page-010.txt"
        index.write_text('<pagina><bloco tipo="cabecalho">INDEX 501 502</bloco>\n'
                         '<bloco tipo="texto_principal">Athanasius 101</bloco></pagina>')
        con.execute("""INSERT INTO alphabetical_volumes
            (volume_id,collection,source_root,created_at,updated_at) VALUES (?,?,?,?,?)""",
                    (volume, "PG", str(root), "now", "now"))
        con.execute("""INSERT INTO alphabetical_sections
            (section_key,volume_id,section_order,section_kind,heading_raw,file_start,file_end,raw_json)
            VALUES (?,?,1,'onomastic_person','INDEX NOMINUM',?,?,'{}')""",
                    (volume + ":s", volume, str(index), str(index)))
        con.execute("""INSERT INTO alphabetical_entries
            (entry_key,section_key,entry_order,entry_kind,lemma_raw,entry_raw,raw_json)
            VALUES (?,?,1,'lemma','Athanasius','Athanasius 101','{}')""",
                    (volume + ":e", volume + ":s"))
        con.execute("""INSERT INTO alphabetical_refs
            (entry_key,ref_order,ref_kind,ref_raw,page_ref_raw,page_ref_int,locator_status,raw_json)
            VALUES (?,1,'editorial_page','101','101',101,'unresolved','{"preserved":true}')""",
                    (volume + ":e",))
    con.commit()
    con.close()
    return db


def job_for(db, volume="PG001"):
    con = connect_readonly(db)
    try:
        return load_job(con, volume)
    finally:
        con.close()


def read_report(path, volume="PG001"):
    return json.loads((path / (volume + ".json")).read_text())


def test_pool_dry_run_is_readonly_and_matches_single_worker(corpus, tmp_path):
    before = hashlib.sha256(corpus.read_bytes()).hexdigest()
    a, b = tmp_path / "serial", tmp_path / "pool"
    first = scan(corpus, ["PG001", "PG002"], a, workers=1)
    second = scan(corpus, ["PG001", "PG002"], b, workers=2)
    assert first["totals"] == second["totals"]
    assert second["totals"]["accepted"] == 2
    assert hashlib.sha256(corpus.read_bytes()).hexdigest() == before
    for volume in ("PG001", "PG002"):
        assert read_report(a, volume)["records"] == read_report(b, volume)["records"]
    assert all(v["reused"] for v in scan(corpus, ["PG001", "PG002"], b)["volumes"])


def test_apply_updates_canonical_fields_preserves_raw_and_backs_up(corpus, tmp_path):
    report = tmp_path / "report"
    scan(corpus, ["PG001"], report, workers=1)
    result = apply_reports(corpus, report)
    assert result["applied"] == 1
    with sqlite3.connect(result["backup"]) as backup:
        assert backup.execute("SELECT count(*) FROM alphabetical_refs WHERE target_file IS NOT NULL").fetchone()[0] == 0
    with sqlite3.connect(corpus) as con:
        row = con.execute("SELECT target_file,resolved_target_ocr_file,locator_status,raw_json FROM alphabetical_refs WHERE entry_key='PG001:e'").fetchone()
        assert row[0] == row[1] == str(tmp_path / "PG001/text/page-001.txt")
        assert row[2] == "resolved"
        assert json.loads(row[3])["preserved"] is True
        assert con.execute("SELECT count(*) FROM alphabetical_locator_evidence").fetchone()[0] > 0
        assert con.execute("SELECT target_file_best,resolved_target_ocr_file FROM alphabetical_entries WHERE entry_key='PG001:e'").fetchone() == (row[0], row[0])
    with pytest.raises(ValueError, match="DB changed"):
        apply_reports(corpus, report)


def test_changed_ocr_rejects_apply(corpus, tmp_path):
    report = tmp_path / "report"
    scan(corpus, ["PG001"], report, workers=1)
    target = tmp_path / "PG001/text/page-001.txt"
    target.write_text(target.read_text().replace("101", "999"))
    with pytest.raises(ValueError, match="OCR changed"):
        apply_reports(corpus, report)
    with sqlite3.connect(corpus) as con:
        assert con.execute("SELECT count(*) FROM alphabetical_refs WHERE target_file IS NOT NULL").fetchone()[0] == 0


def test_stale_second_volume_rolls_back_first_volume(corpus, tmp_path):
    report = tmp_path / "report"
    scan(corpus, ["PG001", "PG002"], report, workers=2)
    with sqlite3.connect(corpus) as con:
        con.execute("UPDATE alphabetical_entries SET lemma_raw='Augustinus' WHERE entry_key='PG002:e'")
    with pytest.raises(ValueError, match="DB changed"):
        apply_reports(corpus, report)
    with sqlite3.connect(corpus) as con:
        assert con.execute("SELECT count(*) FROM alphabetical_refs WHERE target_file IS NOT NULL").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM alphabetical_locator_evidence").fetchone()[0] == 0


def test_ranges_keep_only_start_and_do_not_interpret_page_line_list(corpus):
    job = job_for(corpus)
    row = job["rows"][0]
    row.update(ref_kind="editorial_range", page_ref_int=None, range_start_raw="101", range_end_raw="110")
    assert make_items(job)[0]["cited_pages"] == [101]
    row.update(range_start_raw=None, page_ref_raw="28, 2; 34, 1")
    assert cited_start(row) is None


def test_eligibility_does_not_overwrite_targets_or_process_scripture(corpus):
    row = job_for(corpus)["rows"][0]
    assert is_eligible(row)
    assert not is_eligible(dict(row, target_file="candidate.txt"))
    assert not is_eligible(dict(row, resolved_target_ocr_file="candidate.txt"))
    assert not is_eligible(dict(row, scripture_ref_order=1))
    assert not is_eligible(dict(row, pipeline_owner="general"))
    assert not is_eligible(dict(row, locator_status="unverified"))
    assert is_eligible(dict(row, locator_status="unverified"), True)


def test_reject_number_only_header_only_and_other_index(corpus, tmp_path):
    job = job_for(corpus)
    row = job["rows"][0]
    candidate = {"file": str(tmp_path / "PG001/text/page-001.txt"),
                 "candidate_role": "target_candidate", "probability": 0.99,
                 "evidence": [{"kind": "raw_number_match", "raw": "101"}]}
    result = {"status": "resolved", "candidates": [candidate]}
    assert decide(row, result, job)[1] == "no_body_text_match"
    candidate["evidence"] = [{"kind": "header_name_match", "raw": "Athanasius"}]
    assert decide(row, result, job)[1] == "no_body_text_match"
    candidate["evidence"] = [{"kind": "body_name_match", "raw": "Athanasius"}]
    assert decide(row, result, job)[1] == "accepted"
    job["intervals"].append({"index_ocr_file_start": candidate["file"], "index_ocr_file_end": candidate["file"]})
    assert decide(row, result, job)[1] == "index_or_non_target"


def test_changed_source_invalidates_resume(corpus, tmp_path):
    job = job_for(corpus)
    reports = tmp_path / "reports"
    first = process_volume(job, reports, code_fingerprint())
    assert first["accepted"] == 1
    page = tmp_path / "PG001/text/page-001.txt"
    page.write_text(page.read_text().replace("Athanasius", "Augustinus"))
    second = process_volume(job, reports, code_fingerprint())
    assert not second.get("reused")
    assert second["accepted"] == 0


def test_fuzzy_place_person_confusion_stays_pending():
    evidence = [{"kind": "body_name_fuzzy", "raw":
                 "Μακεδονια => μακεδονιον (levenshtein_similarity=0.8000)"}]
    assert body_matches({"body_text": "Καὶ Μακεδόνιον προχειρίζεται"}, evidence) == []
    evidence = [{"kind": "body_name_fuzzy", "raw":
                 "EPISTOLAE DUAE AD VIRGINES => epist0lae duae ad virgines (levenshtein_similarity=0.9583)"}]
    assert body_matches({"body_text": "EPIST0LAE DUAE AD VIRGINES"}, evidence)


def test_name_head_variants_do_not_use_whole_editorial_description():
    row = {"lemma_raw": "Cœlestius hæreticus quis et unde", "section_kind": "onomastic_mixed"}
    assert name_head_queries(row) == ["Cœlestius", "Cœlestii", "Cœlestio", "Cœlestium"]
    assert name_head_queries(dict(row, lemma_raw="De morte Moysi")) == []
    assert name_head_queries(dict(row, section_kind="analytic_subject")) == []


def test_running_header_required_not_apparatus_number(corpus, tmp_path):
    job = job_for(corpus)
    target = tmp_path / "PG001/text/page-001.txt"
    target.write_text('<pagina><bloco tipo="cabecalho">999 HISTORIA</bloco>'
                      '<bloco tipo="texto_principal">Athanasius contra Arianos.</bloco>'
                      '<bloco tipo="rodape">Vide pag. 101.</bloco></pagina>')
    candidate = {"file": str(target), "candidate_role": "target_candidate", "probability": .99,
                 "evidence": [{"kind": "body_name_match", "raw": "Athanasius"}]}
    assert decide(job["rows"][0], {"status": "resolved", "candidates": [candidate]}, job)[1] == "no_literal_editorial_number_in_header"


def test_dry_run_can_choose_persistent_estimator_database(corpus, tmp_path, monkeypatch):
    import tools.indexing.editorial_page_estimator as estimator
    forbidden = tmp_path / "must-not-exist.sqlite"
    monkeypatch.setattr(estimator, "DEFAULT_ESTIMATOR_DB", forbidden)
    selected = tmp_path / "selected.sqlite"
    process_volume(job_for(corpus), tmp_path / "report", code_fingerprint(), estimator_db=selected)
    assert not forbidden.exists()
    assert selected.exists()


def test_explicit_literal_mode_skips_broad_locator(corpus, tmp_path, monkeypatch):
    from scripts import resolve_alphabetical_unresolved as resolver

    def forbidden(*args, **kwargs):
        pytest.fail("Literal scan must not run whole-volume ranking")

    monkeypatch.setattr(resolver, "resolve_index_targets", forbidden)
    summary = process_volume(job_for(corpus), tmp_path / "report", code_fingerprint(), search_mode="literal")
    assert summary["accepted"] == 1
    record = read_report(tmp_path / "report")["records"][0]
    assert record["proposal"]["ranking_score"] is None


def test_diagnostics_preserve_decisions_and_invalidate_resume(corpus, tmp_path):
    job = job_for(corpus)
    reports = tmp_path / "reports"
    implementation = code_fingerprint()
    process_volume(job, reports, implementation, search_mode="literal")
    fast = read_report(reports)["records"]
    summary = process_volume(job, reports, implementation, diagnostics=True)
    assert not summary.get("reused")
    slow = read_report(reports)["records"]
    for a, b in zip(fast, slow):
        assert a["reason"] == b["reason"]
        assert a["proposal"]["target_file"] == b["proposal"]["target_file"]
        assert a["proposal"]["evidence"] == b["proposal"]["evidence"]
        assert a["proposal"]["body_matches"] == b["proposal"]["body_matches"]
    result = scan(corpus, ["PG001"], reports, diagnostics=True)
    assert result["totals"]["accepted"] == 1
    assert apply_reports(corpus, reports)["applied"] == 1


def test_persistent_cache_reuses_ranking_across_reports(corpus, tmp_path, monkeypatch):
    from scripts import resolve_alphabetical_unresolved as resolver
    job = job_for(corpus)
    implementation = code_fingerprint()
    cold = process_volume(job, tmp_path / "cold", implementation)
    assert cold["cache_misses"] == 1

    def forbidden(*args, **kwargs):
        pytest.fail("Warm result cache must skip loading and ranking the volume")

    monkeypatch.setattr(resolver, "resolve_index_targets", forbidden)
    warm = process_volume(job, tmp_path / "warm", implementation)
    assert warm["cache_hits"] == 1
    assert warm["cache_misses"] == 0
    assert read_report(tmp_path / "cold")["records"] == read_report(tmp_path / "warm")["records"]


@pytest.mark.parametrize("change", ["ocr", "context", "override", "code", "membership"])
def test_result_cache_invalidates_dependencies(corpus, tmp_path, change):
    from tools.indexing.editorial_page_estimator import set_page_override
    job = job_for(corpus)
    implementation = code_fingerprint()
    reports = tmp_path / "reports"
    process_volume(job, reports, implementation)
    target = tmp_path / "PG001/text/page-001.txt"
    if change == "ocr":
        target.write_text(target.read_text().replace("Athanasius", "Alexandrus"))
    elif change == "context":
        job["rows"][0]["entry_raw"] = "Athanasius adversus Arianos 101"
    elif change == "override":
        set_page_override(db_path=tmp_path / "estimator.sqlite", file_path=target,
                          pages=[201, 202], volume_id="PG001", note="reviewed pagination")
    elif change == "code":
        implementation = "different-implementation"
    else:
        (target.parent / "page-003.txt").write_text(target.read_text())
    result = process_volume(job, reports, implementation)
    assert not result.get("reused")
    assert result["cache_misses"] == 1
    assert result["cache_hits"] == 0


def test_default_keeps_multi_range_candidate_without_literal_header(corpus, tmp_path):
    target = tmp_path / "PG001/text/page-001.txt"
    target.write_text(target.read_text().replace("101 HISTORIA 102", "HISTORIA")
                      .replace("Athanasius contra", "101 Athanasius contra"))
    summary = process_volume(job_for(corpus), tmp_path / "reports", code_fingerprint())
    record = read_report(tmp_path / "reports")["records"][0]
    assert summary["accepted"] == 0
    assert summary["ranked_pending"] == 1
    assert record["proposal"] is None
    assert record["ranked_proposal"]["file"] == str(target)
    assert record["locator_result"]["candidates"][0]["evidence"]


def test_estimator_failure_is_not_cached_as_success(corpus, tmp_path, monkeypatch):
    from tools.indexing import editorial_page_estimator as estimator

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(estimator, "estimate_editorial_pages", fail)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        process_volume(job_for(corpus), tmp_path / "reports", code_fingerprint())
    assert not (tmp_path / "reports/PG001.json").exists()
    for cache in (tmp_path / "cache").glob("*.sqlite"):
        with sqlite3.connect(cache) as con:
            assert con.execute("SELECT count(*) FROM results").fetchone()[0] == 0


def test_apply_rejects_changed_manual_pagination(corpus, tmp_path):
    from tools.indexing.editorial_page_estimator import set_page_override
    reports = tmp_path / "reports"
    scan(corpus, ["PG001"], reports, workers=1)
    set_page_override(db_path=tmp_path / "estimator.sqlite",
                      file_path=tmp_path / "PG001/text/page-001.txt", pages=[201, 202],
                      volume_id="PG001", note="corrected pagination")
    with pytest.raises(ValueError, match="Editorial overrides changed"):
        apply_reports(corpus, reports)


def prepare_neighbor_target(corpus, tmp_path):
    root = tmp_path / "PG001/text"
    for seq, left in enumerate((101, 103, None, 107, 109), 1):
        header = f"{left} HISTORIA {left + 1}" if left else "HISTORIA"
        body = "Athanasius contra Arianos disputavit." if seq == 3 else "Alius episcopus."
        (root / f"page-{seq:03}.txt").write_text(
            f'<pagina><bloco tipo="cabecalho">{header}</bloco>'
            f'<bloco tipo="texto_principal">{body}</bloco></pagina>')
    with sqlite3.connect(corpus) as con:
        con.execute("UPDATE alphabetical_refs SET ref_raw='105',page_ref_raw='105',page_ref_int=105 WHERE entry_key='PG001:e'")
        con.execute("UPDATE alphabetical_entries SET entry_raw='Athanasius 105' WHERE entry_key='PG001:e'")
    return root / "page-003.txt"


@pytest.mark.parametrize("header", ["HISTORIA", "405 HISTORIA 406"])
def test_neighbors_accept_missing_header_and_apply_with_provenance(corpus, tmp_path, header):
    target = prepare_neighbor_target(corpus, tmp_path)
    target.write_text(target.read_text().replace(
        '<bloco tipo="cabecalho">HISTORIA</bloco>', f'<bloco tipo="cabecalho">{header}</bloco>'))
    report = tmp_path / "report"
    literal = scan(corpus, ["PG001"], tmp_path / "literal", workers=1, search_mode="literal")
    inferred = scan(corpus, ["PG001"], report, workers=1)
    assert literal["totals"]["accepted"] == 0
    assert inferred["totals"]["accepted"] == inferred["totals"]["accepted_by_neighbors"] == 1
    record = read_report(report)["records"][0]
    assert record["proposal"]["target_file"] == str(target)
    proof = next(e for e in record["proposal"]["evidence"] if e["kind"] == "neighbor_validated_editorial_page")
    assert len(proof["sequence_validation"]["anchors"]) == 4
    assert proof["inferred_editorial_page"] == 105
    assert "editorial_page" not in proof
    warm = scan(corpus, ["PG001"], tmp_path / "warm", workers=1)
    assert warm["volumes"][0]["cache_hits"] == 1
    assert read_report(tmp_path / "warm")["records"] == read_report(report)["records"]
    assert apply_reports(corpus, report)["applied"] == 1
    with sqlite3.connect(corpus) as con:
        row = con.execute("SELECT evidence_kind,observed_editorial_page_number,raw_json FROM alphabetical_locator_evidence WHERE evidence_kind='neighbor_validated_editorial_page'").fetchone()
        assert row[1] is None
        assert json.loads(row[2])["sequence_validation"]["pages"] == [105, 106]


def test_neighbors_still_require_text_and_unique_file(corpus, tmp_path):
    target = prepare_neighbor_target(corpus, tmp_path)
    target.write_text(target.read_text().replace("Athanasius", "Basilides"))
    summary = process_volume(job_for(corpus), tmp_path / "missing", code_fingerprint())
    assert summary["accepted"] == 0
    target.write_text(target.read_text().replace("Basilides", "Athanasius"))
    competitor = target.parent / "page-006.txt"
    competitor.write_text('<pagina><bloco tipo="cabecalho">105 HISTORIA 106</bloco>'
                         '<bloco tipo="texto_principal">Athanasius contra Arianos.</bloco></pagina>')
    summary = process_volume(job_for(corpus), tmp_path / "ambiguous", code_fingerprint())
    assert summary["accepted"] == 0
    assert read_report(tmp_path / "ambiguous")["records"][0]["reason"] == "multiple_viable_ocr_files"


def test_apply_rejects_changed_neighbor_anchor(corpus, tmp_path):
    prepare_neighbor_target(corpus, tmp_path)
    report = tmp_path / "report"
    scan(corpus, ["PG001"], report, workers=1)
    anchor = tmp_path / "PG001/text/page-002.txt"
    anchor.write_text(anchor.read_text().replace("103 HISTORIA 104", "203 HISTORIA 204"))
    with pytest.raises(ValueError, match="OCR changed"):
        apply_reports(corpus, report)


def test_corrupt_literal_header_cannot_override_validated_sequence(corpus, tmp_path):
    target = prepare_neighbor_target(corpus, tmp_path)
    target.write_text(target.read_text().replace(
        '<bloco tipo="cabecalho">HISTORIA</bloco>',
        '<bloco tipo="cabecalho">405 HISTORIA 406</bloco>'))
    job = job_for(corpus)
    job["rows"][0].update(ref_raw="405", page_ref_raw="405", page_ref_int=405)
    summary = process_volume(job, tmp_path / "report", code_fingerprint())
    assert summary["accepted"] == 0
    assert read_report(tmp_path / "report")["records"][0]["reason"] == "literal_header_conflicts_with_validated_sequence"


def test_high_score_without_independent_anchors_does_not_validate_page(corpus, tmp_path):
    target = prepare_neighbor_target(corpus, tmp_path)
    job = job_for(corpus)
    candidate = {"file": str(target), "candidate_role": "target_candidate", "probability": 1.0,
                 "estimator_pages": [105, 106], "estimator_confidence": .999,
                 "evidence": [{"kind": "body_name_match", "raw": "Athanasius"},
                              {"kind": "adjacent_page_consensus", "raw": "neighbor_fit"}]}
    assert decide(job["rows"][0], {"candidates": [candidate]}, job)[0] is None


def test_multiple_matches_in_same_file_do_not_compete(corpus, tmp_path):
    job = job_for(corpus)
    candidate = {"file": str(tmp_path / "PG001/text/page-001.txt"),
                 "candidate_role": "target_candidate", "probability": .4,
                 "evidence": [{"kind": "body_name_match", "raw": "Athanasius"}]}
    result = {"status": "ambiguous", "candidates": [candidate, dict(candidate)]}
    proposal, reason = decide(job["rows"][0], result, job)
    assert reason == "accepted"
    assert proposal["resolution_level"] == "ocr_file"
    assert proposal["position_status"] == "not_resolved"


def test_competing_files_detected_even_outside_helper_top_k(corpus, tmp_path):
    job = job_for(corpus)
    other = tmp_path / "PG001/text/page-003.txt"
    other.write_text('<pagina><bloco tipo="cabecalho">101 ALIA OPERA 102</bloco>'
                     '<bloco tipo="texto_principal">Athanasius alteram rem dicit.</bloco></pagina>')
    headers = header_file_index(source_snapshot(job["source_root"]))
    search = search_literal_files(job["rows"][0], headers, [])
    assert len(search["candidates"]) == 2
    proposal, reason = decide(job["rows"][0], {"file_search": search}, job)
    assert proposal is None
    assert reason == "multiple_viable_ocr_files"
