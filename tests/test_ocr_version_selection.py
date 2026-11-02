"""Read-only ranking and reversible exports, using a small isolated history DB."""
import json
from pathlib import Path
import sqlite3
import stat
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ocr_versions_db as versions
from offline_ocr import selection

FIXTURES = Path(__file__).parent / "fixtures/ocr_diff_v2"


@pytest.fixture
def workspace(tmp_path):
    base = tmp_path / "teste"
    image = base / "PG001/images/PG001-638.png"
    image.parent.mkdir(parents=True)
    # Geometry-only fixture; tests inject the recorded TSV, never OCR this raster.
    Image.new("L", (2480, 3509), 255).save(image)
    image_hash = selection.digest(image.read_bytes())
    text_dir = base / "PG001/text"
    text_dir.mkdir()
    target = text_dir / "uuid-638.txt"
    current = (FIXTURES / "PG001_638_current.xml").read_text()
    previous = (FIXTURES / "PG001_638_previous.xml").read_text()
    target.write_text(current)
    target.chmod(0o640)
    db = tmp_path / "versions.db"
    con = versions.open_versions_db(db)
    for version_id, engine, model, text, is_current in (
        (357449, "ollama", "qwen3.5:cloud", previous, 0),
        (809912, "openai", "gpt-5-mini", current, 1),
        (853769, "tesseract", "migne", "not a candidate", 0),
    ):
        engine_id = versions.get_or_create_engine_version(con, engine=engine, model=model,
                                                         prompt_key="PROMPT", system_prompt="fixture")
        con.execute("""INSERT INTO ocr_results
          (id,volume_id,page_num,image_path,image_hash,engine_version_id,text_content,text_hash,is_current)
          VALUES (?,?,?,?,?,?,?,?,?)""", (version_id, "PG001", 638, str(image), image_hash,
                                         engine_id, text, versions.sha256_str(text), is_current))
    con.commit()
    con.close()
    tsv = (FIXTURES / "PG001_638_migne_raw.tsv").read_text()

    def reference_provider(path):
        return tsv, {"key": "fixture", "image_hash": selection.digest(path.read_bytes()),
                     "image_size": [2480, 3509], "tsv_sha256": selection.digest(tsv.encode())}

    return {"db_path": db, "base_dir": base, "run_dir": tmp_path / "run",
            "volumes": ["PG001"], "pages": [638], "reference_provider": reference_provider,
            "target": target, "image": image, "current": current, "previous": previous}


def scan(workspace):
    args = {k: v for k, v in workspace.items() if k not in {"target", "image", "current", "previous"}}
    return selection.scan(**args)


def proposal(workspace):
    return json.loads((workspace["run_dir"] / "proposals.jsonl").read_text())


def database_snapshot(workspace):
    with sqlite3.connect(workspace["db_path"]) as con:
        return con.execute("SELECT id,text_content,text_hash,is_current FROM ocr_results ORDER BY id").fetchall()


def test_scan_selects_better_historical_version_and_changes_no_sources(workspace):
    before = database_snapshot(workspace)
    manifest = scan(workspace)
    row = proposal(workspace)
    assert manifest["summary"] == {"recommended": 1}
    assert row["selected_id"] == 357449
    assert row["before_hash"] == versions.sha256_str(workspace["current"])
    assert row["improvements_vs_filesystem"]["missing_reference_tokens"] > 10
    assert row["excluded"] == [{"id": 853769, "reason": "reference_engine_not_export_candidate"}]
    assert workspace["target"].read_text() == workspace["current"]
    assert database_snapshot(workspace) == before
    assert "357449" in selection.report_run(workspace["run_dir"])
    assert (workspace["run_dir"] / "report.txt").exists()
    assert not (workspace["run_dir"] / "backups").exists()


def test_explicit_apply_backups_exact_bytes_and_rollback_preserves_database(workspace):
    before = database_snapshot(workspace)
    scan(workspace)
    run = workspace["run_dir"]
    assert selection.apply_run(run) == {"written": 1}
    assert workspace["target"].read_text() == workspace["previous"]
    assert stat.S_IMODE(workspace["target"].stat().st_mode) == 0o640
    backups = list((run / "backups").glob("*.txt"))
    assert len(backups) == 1 and backups[0].read_bytes() == workspace["current"].encode()
    assert selection.apply_run(run) == {"written": 0}
    assert selection.rollback_run(run) == {"restored": 1}
    assert workspace["target"].read_text() == workspace["current"]
    assert selection.rollback_run(run) == {"restored": 0}
    assert database_snapshot(workspace) == before
    events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    assert all(e.get("reasons") for e in events if e["event"] == "write_intent")
    assert {"page_evaluated", "write_intent", "written", "rolled_back"} <= {e["event"] for e in events}


@pytest.mark.parametrize("changed,expected", [("target", "filesystem_changed"), ("image", "image_changed"),
                                               ("db", "history_changed"), ("proposal", "proposal_integrity_error"),
                                               ("evidence", "evidence_changed")])
def test_stale_sources_and_tampered_artifacts_prevent_apply(workspace, changed, expected):
    scan(workspace)
    run = workspace["run_dir"]
    if changed == "db":
        with sqlite3.connect(workspace["db_path"]) as con:
            con.execute("UPDATE ocr_results SET text_hash='modified' WHERE id=357449")
    elif changed == "proposal":
        with (run / "proposals.jsonl").open("a") as stream:
            stream.write("\n")
    elif changed == "evidence":
        (run / proposal(workspace)["candidates"][0]["evidence"]).write_text("{}")
    else:
        workspace[changed].write_bytes(b"manual edit")
    with pytest.raises(ValueError, match=expected):
        selection.apply_run(run)
    assert workspace["target"].read_bytes() == (b"manual edit" if changed == "target" else workspace["current"].encode())
    assert not (run / "backups").exists()


def test_database_text_corruption_without_hash_change_prevents_apply(workspace):
    scan(workspace)
    with sqlite3.connect(workspace["db_path"]) as con:
        con.execute("UPDATE ocr_results SET text_content='bad' WHERE id=357449")
    with pytest.raises(ValueError, match="history_changed"):
        selection.apply_run(workspace["run_dir"])
    assert workspace["target"].read_text() == workspace["current"]


def test_unversioned_local_edit_requires_review(workspace):
    workspace["target"].write_text(workspace["current"].replace("salvel", "salvat"))
    scan(workspace)
    row = proposal(workspace)
    assert row["status"] == "review_required"
    assert "filesystem_text_not_in_eligible_history" in row["reasons"]
    assert selection.apply_run(workspace["run_dir"])["written"] == 0


def test_best_version_already_on_disk_is_kept(workspace):
    workspace["target"].write_text(workspace["previous"])
    scan(workspace)
    assert proposal(workspace)["status"] == "keep_current"
    assert selection.apply_run(workspace["run_dir"])["written"] == 0


def test_ambiguous_uuid_targets_require_review(workspace):
    other = workspace["target"].with_name("other-638.txt")
    other.write_text("independent text")
    scan(workspace)
    assert proposal(workspace)["reasons"] == ["ambiguous_files"]
    assert selection.apply_run(workspace["run_dir"])["written"] == 0


def test_create_missing_file_and_rollback_only_our_created_file(workspace):
    workspace["target"].unlink()
    scan(workspace)
    row = proposal(workspace)
    target = Path(row["target"])
    assert row["before_hash"] is None
    assert selection.apply_run(workspace["run_dir"])["written"] == 1
    assert target.read_text() == workspace["previous"]
    assert selection.rollback_run(workspace["run_dir"])["restored"] == 1
    assert not target.exists()


def test_rollback_refuses_to_discard_later_manual_edits(workspace):
    scan(workspace)
    selection.apply_run(workspace["run_dir"])
    workspace["target"].write_text("later manual edit")
    with pytest.raises(ValueError, match="filesystem_changed"):
        selection.rollback_run(workspace["run_dir"])
    assert workspace["target"].read_text() == "later manual edit"


def test_crash_after_replace_is_recoverable_from_write_intent(workspace, monkeypatch):
    scan(workspace)
    original = selection.journal

    def crash(run_dir, event, **fields):
        if event == "written":
            raise OSError("simulated crash after replace")
        return original(run_dir, event, **fields)

    monkeypatch.setattr(selection, "journal", crash)
    with pytest.raises(OSError):
        selection.apply_run(workspace["run_dir"])
    assert workspace["target"].read_text() == workspace["previous"]
    monkeypatch.setattr(selection, "journal", original)
    assert selection.rollback_run(workspace["run_dir"])["restored"] == 1
    assert workspace["target"].read_text() == workspace["current"]


def test_different_image_version_is_excluded(workspace):
    with sqlite3.connect(workspace["db_path"]) as con:
        con.execute("UPDATE ocr_results SET image_hash='another image' WHERE id=357449")
    scan(workspace)
    row = proposal(workspace)
    assert {"id": 357449, "reason": "different_image_hash"} in row["excluded"]
    assert row["selected_id"] == 809912
    assert row["status"] == "keep_current"


def test_ties_tradeoffs_and_low_reference_quality_abstain():
    def candidate(version_id, risk):
        return {"id": version_id, "text_hash": str(version_id), "content_profile": dict(zip(selection.POLICY["risk_dimensions"], risk)), "profile": {
            **dict(zip(selection.POLICY["risk_dimensions"], risk)),
            "supported_fraction": .99, "reliable_reference_tokens": 100}}

    for risk in ([5, 1, 4, 1], [2, 1, 8, 1]):
        result = selection.choose_version([candidate(1, [5, 1, 4, 1]), candidate(2, risk)], "1")
        assert result["status"] == "review_required"
        assert result["selected_id"] is None
    weak = candidate(1, [0, 0, 0, 0])
    weak["profile"]["reliable_reference_tokens"] = 4
    assert selection.choose_version([weak], None)["reasons"] == ["insufficient_reference_text"]


def test_duplicate_runs_are_not_overwritten(workspace):
    scan(workspace)
    with pytest.raises(FileExistsError):
        scan(workspace)


def test_page_cursor_respects_scope_and_does_not_repeat_pages(workspace):
    with sqlite3.connect(workspace["db_path"]) as con:
        con.execute("UPDATE ocr_results SET page_num=640 WHERE id=853769")
    con = selection.read_only(workspace["db_path"])
    try:
        assert [tuple(r) for r in selection._page_ids(con, ["PG001"], None, 1)] == [("PG001", 638)]
        assert [tuple(r) for r in selection._page_ids(con, ["PG001"], None, 1, "PG001:638")] == [("PG001", 640)]
        assert list(selection._page_ids(con, ["PG001"], [638], 1, "PG001:638")) == []
    finally:
        con.close()
    args = {k: v for k, v in workspace.items() if k not in {"target", "image", "current", "previous", "pages"}}
    manifest = selection.scan(**args, limit=1)
    assert manifest["more_available"] and manifest["next_after"] == "PG001:638"


def test_reference_cache_preserves_flags_and_detects_corruption(workspace, tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    raw = (FIXTURES / "PG001_638_migne_raw.tsv").read_text()
    def fake_tesseract(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=raw, stderr="fixture")
    monkeypatch.setattr(selection.subprocess, "run", fake_tesseract)
    config = {"tessdata": "/fixture", "lang": "migne", "psm": 3, "oem": 1, "no_rejects": False}
    cache = tmp_path / "cache"
    _, first = selection.reference_tsv(workspace["image"], cache, config)
    selection.reference_tsv(workspace["image"], cache, config)
    assert len(calls) == 1
    _, second = selection.reference_tsv(workspace["image"], cache, {**config, "no_rejects": True})
    assert second["key"] != first["key"]
    assert "tessedit_zero_kelvin_rejection=1" in calls[1] and "textord_no_rejects=1" in calls[1]
    cached = json.loads(Path(first["path"]).read_text())
    cached["tsv"] += "changed"
    Path(first["path"]).write_text(json.dumps(cached))
    with pytest.raises(ValueError, match="reference_cache_integrity_error"):
        selection.reference_tsv(workspace["image"], cache, config)


def test_read_only_connection_refuses_writes(workspace):
    con = selection.read_only(workspace["db_path"])
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM ocr_results")
    finally:
        con.close()


def add_version(workspace, version_id, text):
    with sqlite3.connect(workspace["db_path"]) as con:
        engine = con.execute("SELECT engine_version_id FROM ocr_results WHERE id=809912").fetchone()[0]
        con.execute("""INSERT INTO ocr_results
          (id,volume_id,page_num,image_path,image_hash,engine_version_id,text_content,text_hash,is_current)
          VALUES (?,?,?,?,?,?,?,?,?)""", (version_id, "PG001", 638, str(workspace["image"]),
          selection.digest(workspace["image"].read_bytes()), engine, text, versions.sha256_str(text), 0))


@pytest.mark.parametrize("text", ["<pagina estado='com_texto'><bloco/></pagina>",
    "<pagina estado='vazio'><bloco tipo='texto_principal' script='latino'>abc</bloco></pagina>",
    "<pagina estado='com_texto'><bloco tipo='outro' script='invalid'>abc</bloco></pagina>",
    "<pagina estado='com_texto'>lost<bloco tipo='outro' script='latino'>abc</bloco></pagina>"])
def test_empty_or_invalid_page_xml_is_ineligible(text):
    assert not selection._valid_xml(text)


def test_near_copy_from_real_ab_is_not_automatically_recommended(workspace):
    text = (Path(__file__).parent / "fixtures/ocr_ab_pg001_638/r001_micro_xml.xml").read_text()
    add_version(workspace, 900000, text)
    scan(workspace)
    row = proposal(workspace)
    assert row["status"] == "review_required"
    assert row["selected_id"] == 900000
    assert "near_verbatim_tesseract_agreement_requires_image_review" in row["reasons"]
    assert selection.apply_run(workspace["run_dir"]) == {"written": 0}
    assert workspace["target"].read_text() == workspace["current"]


def test_geometry_only_improvement_does_not_trigger_export():
    def candidate(i, spatial, content):
        return {"id": i, "text_hash": str(i),
                "profile": {**dict(zip(selection.POLICY["risk_dimensions"], spatial)),
                            "supported_fraction": .99, "reliable_reference_tokens": 100},
                "content_profile": dict(zip(selection.POLICY["risk_dimensions"], content))}
    result = selection.choose_version([candidate(1, [40, 30, 20, 10], [2, 1, 2, 1]),
                                       candidate(2, [4, 3, 2, 1], [2, 1, 2, 1])], "1")
    assert result["status"] == "review_required"
    assert result["reasons"] == ["content_improvement_below_policy_minimum"]


@pytest.mark.parametrize("field", ["content_evidence", "reference"])
def test_all_frozen_evidence_is_checked_before_apply(workspace, field):
    scan(workspace)
    row = proposal(workspace)
    path = row["reference"]["snapshot"] if field == "reference" else row["candidates"][0][field]
    (workspace["run_dir"] / path).write_text("tampered")
    with pytest.raises(ValueError, match="reference_changed|evidence_changed"):
        selection.apply_run(workspace["run_dir"])
    assert workspace["target"].read_text() == workspace["current"]


def test_modified_unselected_text_without_metadata_update_invalidates_plan(workspace):
    scan(workspace)
    with sqlite3.connect(workspace["db_path"]) as con:
        con.execute("UPDATE ocr_results SET text_content='corrupt' WHERE id=809912")
    with pytest.raises(ValueError, match="history_changed"):
        selection.apply_run(workspace["run_dir"])


def test_runs_share_corpus_lock_and_lock_is_released(workspace, tmp_path):
    scan(workspace)
    other = {**workspace, "run_dir": tmp_path / "other-run"}
    scan(other)
    with selection._operation(workspace["run_dir"]):
        with pytest.raises(BlockingIOError):
            selection.apply_run(other["run_dir"])
    assert selection.apply_run(other["run_dir"]) == {"written": 1}
    assert selection.rollback_run(other["run_dir"]) == {"restored": 1}


@pytest.mark.parametrize("tail", [b'{"event":"writ', b'{"event":"interrupted_valid_record"}'])
def test_rollback_recovers_torn_final_journal_line(workspace, tail):
    scan(workspace)
    selection.apply_run(workspace["run_dir"])
    with (workspace["run_dir"] / "events.jsonl").open("ab") as stream:
        stream.write(tail)
    assert selection.rollback_run(workspace["run_dir"]) == {"restored": 1}
    assert selection.rollback_run(workspace["run_dir"]) == {"restored": 0}
    assert workspace["target"].read_text() == workspace["current"]
    if tail == b'{"event":"writ':
        assert next(workspace["run_dir"].glob("journal-tail-*.bin")).read_bytes() == tail


def test_legacy_run_can_be_reported_and_rolled_back_but_not_applied(workspace):
    scan(workspace)
    selection.apply_run(workspace["run_dir"])
    path = workspace["run_dir"] / "manifest.json"
    data = json.loads(path.read_text())
    data["policy"]["version"] = selection.LEGACY_POLICY_VERSION
    path.write_text(json.dumps(data))
    assert "357449" in selection.report_run(workspace["run_dir"])
    with pytest.raises(ValueError, match="policy_changed_rescan_required"):
        selection.apply_run(workspace["run_dir"])
    assert selection.rollback_run(workspace["run_dir"]) == {"restored": 1}


def test_refine_writes_only_external_draft_and_preserves_sources(workspace, tmp_path):
    from offline_ocr.refinement import refine_run
    before = database_snapshot(workspace)
    scan(workspace)
    out = tmp_path / "draft"
    report = refine_run(workspace["run_dir"], "PG001:638", 809912, out,
                        repair_bboxes=True, merge_candidates=True)
    assert report["automatic_export"] is False
    assert report["bbox_review"]["text_changed"] is False
    assert (out / "draft.xml").exists() and (out / "review.json").exists()
    assert "adulteros" in (out / "review.txt").read_text()
    assert workspace["target"].read_text() == workspace["current"]
    assert database_snapshot(workspace) == before
    assert any("adulteros" in p["reference_text"] for p in report["merge_review"]["proposals"])
    with pytest.raises(ValueError, match="outside_corpus"):
        refine_run(workspace["run_dir"], "PG001:638", 809912, workspace["base_dir"] / "draft", repair_bboxes=True)
    with pytest.raises(FileExistsError):
        refine_run(workspace["run_dir"], "PG001:638", 809912, out, repair_bboxes=True)
