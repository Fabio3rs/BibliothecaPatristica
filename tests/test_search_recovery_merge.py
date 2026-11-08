from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.experiment_storage import pack_json
from tools.indexing.search_recovery_merge import VERSION, merge, numeric_candidate


def fixture(tmp_path):
    catalog = tmp_path / "search.sqlite"
    estimator = tmp_path / "estimate.sqlite"
    relations = tmp_path / "relations.sqlite"
    source = tmp_path / "index.txt"
    target = tmp_path / "target.txt"
    alternative = tmp_path / "alternative.txt"
    for path in (source, target, alternative):
        path.write_text("historical text")
    with sqlite3.connect(catalog) as db:
        db.executescript(
            """CREATE TABLE items(id TEXT PRIMARY KEY,volume,label,ref_raw,source,state);
            CREATE TABLE candidate_storage(item_id,file,rank,score,navigable,candidate_json BLOB,PRIMARY KEY(item_id,file));
            CREATE VIEW candidates AS SELECT item_id,file,rank,score,navigable,experiment_json(candidate_json) AS candidate_json FROM candidate_storage;
            CREATE TABLE candidate_origins(item_id,file,origin,PRIMARY KEY(item_id,file,origin));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);"""
        )
        for ident in ("numeric", "ibid", "self", "old", "changed"):
            db.execute(
                "INSERT INTO items VALUES(?,?,?,?,?,?)",
                (
                    ident,
                    "PG001",
                    ident,
                    "10",
                    str(source),
                    "with_destinations" if ident == "old" else "source_only",
                ),
            )
        old = dict(
            file=str(target),
            rank=1,
            score=60,
            navigable=True,
            evidence="original lexical support",
        )
        db.execute(
            "INSERT INTO candidate_storage VALUES(?,?,?,?,?,?)",
            ("old", str(target), 1, 60, 1, pack_json(json.dumps(old))),
        )
    with sqlite3.connect(estimator) as db:
        db.executescript(
            "CREATE TABLE runs(config_json); CREATE TABLE results(item_id,payload_json); CREATE TABLE candidates(item_id,file,rank,evidence_json);"
        )
        db.execute(
            "INSERT INTO runs VALUES(?)", (json.dumps({"catalog": str(catalog)}),)
        )
        for ident in ("numeric", "ibid", "self", "old", "changed"):
            cs = []
            if ident != "ibid":
                paths = (
                    [source]
                    if ident == "self"
                    else [target, alternative] if ident == "numeric" else [target]
                )
                for rank, path in enumerate(paths, 1):
                    c = dict(
                        file=str(path),
                        rank=rank,
                        matches=[dict(score=18, warnings=["uncertain"])],
                    )
                    cs.append(c)
                    db.execute(
                        "INSERT INTO candidates VALUES(?,?,?,?)",
                        (ident, str(path), rank, json.dumps(c)),
                    )
            p = dict(
                id=ident,
                source=str(source),
                label="former label" if ident == "changed" else ident,
                ref_raw="10",
                route="pagination",
                numbers=[10],
                candidates=cs,
                source_evidence_warning="span_not_current",
            )
            db.execute("INSERT INTO results VALUES(?,?)", (ident, json.dumps(p)))
    with sqlite3.connect(relations) as db:
        db.executescript(
            "CREATE TABLE metadata(key,json); CREATE TABLE candidates(item_id,file,rank,score,navigable,candidate_json);"
        )
        db.execute(
            "INSERT INTO metadata VALUES(?,?)",
            ("summary", json.dumps({"catalog": str(catalog)})),
        )
        c = dict(
            file=str(target),
            rank=1,
            score=6,
            navigable=True,
            certified=False,
            uncertainties=["ibid_scope_unverified"],
            evidence={"antecedent_item_id": "numeric"},
        )
        db.execute(
            "INSERT INTO candidates VALUES(?,?,?,?,?,?)",
            ("ibid", str(target), 1, 6, 1, json.dumps(c)),
        )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            dict(
                version=VERSION,
                catalog=str(catalog),
                estimator=str(estimator),
                relations=str(relations),
            )
        )
    )
    return plan, catalog, estimator, relations


def test_atomic_additive_merge_is_replayable_and_preserves_alternatives(tmp_path):
    plan, catalog, estimator, relations = fixture(tmp_path)
    before = estimator.read_bytes(), relations.read_bytes()
    with closing(readonly(catalog)) as db:
        old = db.execute(
            "SELECT candidate_json FROM candidate_storage WHERE item_id='old'"
        ).fetchone()[0]
    summary = merge(plan, tmp_path / "report")
    assert summary["before"]["pending"] == 4
    assert summary["after"]["pending"] == 2
    assert summary["gains_by_layer"] == dict(estimator=1, relations=1)
    assert summary["counts"]["excluded_self_pairs"] == 1
    assert summary["counts"]["excluded_changed_item_pairs"] == 1
    with closing(readonly(catalog)) as db:
        assert (
            db.execute(
                "SELECT candidate_json FROM candidate_storage WHERE item_id='old'"
            ).fetchone()[0]
            == old
        )
        assert (
            db.execute(
                "SELECT count(*) FROM candidates WHERE item_id='numeric'"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute("SELECT count(*) FROM recovery_candidate_support").fetchone()[0]
            == 6
        )
        assert (
            db.execute(
                "SELECT count(*) FROM candidates WHERE item_id IN ('self','changed')"
            ).fetchone()[0]
            == 0
        )
        c = json.loads(
            db.execute(
                "SELECT candidate_json FROM candidates WHERE item_id='numeric' LIMIT 1"
            ).fetchone()[0]
        )
        assert not c["certified"] and "span_not_current" in c["uncertainties"]
    assert merge(plan, tmp_path / "repeat") == summary
    assert before == (estimator.read_bytes(), relations.read_bytes())
    report = json.loads((tmp_path / "report/summary.json").read_text())
    assert sum(report["pending_reasons"].values()) == 2


def test_failure_rolls_back_all_layers(tmp_path):
    plan, catalog, _, relations = fixture(tmp_path)
    with sqlite3.connect(relations) as db:
        db.execute("UPDATE candidates SET item_id='absent'")
    with pytest.raises(ValueError, match="missing catalog item"):
        merge(plan, tmp_path / "report")
    with closing(readonly(catalog)) as db:
        assert db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM items WHERE state='source_only'"
            ).fetchone()[0]
            == 4
        )
        assert db.execute("SELECT count(*) FROM metadata").fetchone()[0] == 0


def test_foreign_catalog_refused_before_writing(tmp_path):
    plan, catalog, estimator, _ = fixture(tmp_path)
    before = catalog.read_bytes()
    with sqlite3.connect(estimator) as db:
        db.execute(
            "UPDATE runs SET config_json=?",
            (json.dumps({"catalog": str(tmp_path / "another.sqlite")}),),
        )
    with pytest.raises(ValueError, match="different catalog"):
        merge(plan, tmp_path / "report")
    assert catalog.read_bytes() == before


def test_estimator_rank_is_preserved_without_using_unscaled_scores():
    first = numeric_candidate(
        dict(file="one", rank=1, evidence_json='{"matches":[{"score":3}]}')
    )
    second = numeric_candidate(
        dict(file="two", rank=2, evidence_json='{"matches":[{"score":400}]}')
    )
    assert 10 > first["score"] > second["score"]


def test_catalog_verifier_checks_new_support_without_requiring_self_links(tmp_path):
    from tools.indexing.search_catalog_consolidation import verify

    plan, catalog, _, _ = fixture(tmp_path)
    merge(plan, tmp_path / 'report')
    origin = tmp_path / 'original.sqlite'
    with sqlite3.connect(origin) as db:
        db.executescript('CREATE TABLE candidates(item_id,file); CREATE TABLE items(id);')
    with sqlite3.connect(catalog) as db:
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', json.dumps({
            'catalog': str(origin), 'relations': [], 'supplements': []})))
        db.execute('CREATE VIRTUAL TABLE items_fts USING fts5(label)')
        db.execute('INSERT INTO items_fts SELECT label FROM items')
    verify(catalog)
    result = json.loads(catalog.with_name('verification.json').read_text())
    assert result['status'] == 'ok' and result['recovery_support_pairs_checked'] == 4
    with sqlite3.connect(catalog) as db:
        db.execute("DELETE FROM candidate_storage WHERE item_id='ibid'")
    with pytest.raises(ValueError, match='conservation failed'):
        verify(catalog)
