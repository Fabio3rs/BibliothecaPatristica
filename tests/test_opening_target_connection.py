import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.opening_target_connection import (
    apply,
    build_syntagma,
    candidates_for,
    reconcile,
    syntagma_markers,
    tokens_for,
    section_descriptor,
    digest,
)
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.experiment_storage import pack_json


def block(text, kind="texto_principal", script="latino"):
    return f'<bloco tipo="{kind}" script="{script}">{text}</bloco>'


def test_internal_markers_exclude_canon_numbers_headers_and_greek():
    raw = block("973 974", "cabecalho") + block("478 text", script="grego")
    raw += block("49. 50. [27] 478 quatuor substratus; 479 4. Qui autem")
    raw += block("200 nota", "nota")
    markers = list(syntagma_markers(raw, 479))
    assert [m["number"] for m in markers] == [478, 479]
    assert all(raw[m["char_start"] : m["char_end"]] == m["raw"] for m in markers)


def test_syntagma_stops_within_mixed_page_and_preserves_reviewed_endpoints(tmp_path):
    folder = tmp_path / "PG104/text"
    folder.mkdir(parents=True)
    raw = block("SYNTAGMA 478 quatuor substratus. NOMOCANON 479 another work")
    (folder / "source-526.txt").write_text(raw)
    spec = {
        "volume": "PG104",
        "first": 526,
        "last": 526,
        "maximum": 479,
        "start_quote": "SYNTAGMA",
        "end_quote": "NOMOCANON",
        "sequence": {},
        "reviewed_markers": [{"physical": 526, "number": 478, "quote": "478 quatuor"}],
    }
    _, markers = build_syntagma(spec, tmp_path)
    assert [m["number"] for m in markers] == [478]
    assert markers[0]["reviewed_quote"]


def prior(ident, label, ref, a, b):
    return {
        "id": ident,
        "label": label,
        "ref_raw": ref,
        "metadata_json": json.dumps({"source_span": [a, b]}),
    }


def test_split_prior_occurrences_do_not_receive_sibling_targets():
    raw = "Name 12 a, 13 b."
    group = {"raw": raw[5:], "char_start": 5, "char_end": len(raw)}
    tokens = tokens_for("PG104:Bibliotheca:index", group)
    old = [prior("a", "Name", "12 a", 5, 9), prior("b", "Name", "13 b.", 11, 16)]
    # Use actual half-open spans, including final punctuation.
    old[1]["metadata_json"] = json.dumps({"source_span": [11, len(raw)]})
    matches, conflicts, _ = reconcile(group, "Name", tokens, old, raw)
    assert not conflicts
    assert [(i["id"], [t["number"] for t in ts]) for i, ts in matches] == [
        ("a", [12]),
        ("b", [13]),
    ]


def test_different_owner_and_changed_ocr_stay_in_reconciliation():
    group = {"raw": "12 a.", "char_start": 5, "char_end": 10}
    raw = "Name 12 a."
    old = [
        prior("wrong", "Other entry", "12 a.", 5, 10),
        prior("stale", "Name", "99 a.", 5, 10),
    ]
    matches, conflicts, _ = reconcile(
        group, "Name", tokens_for("Bibliotheca", group), old, raw
    )
    assert not matches
    assert conflicts == ["wrong", "stale"]


def test_inheritance_is_local_to_epistle_list():
    group = {"raw": "XVI, 55, 56.", "char_start": 100}
    tokens = tokens_for("PL217:epistle_incipits:index", group)
    assert [(t["book"], t["epistle"]) for t in tokens] == [(16, 55), (16, 56)]
    assert (
        tokens_for("PL217:epistle_incipits:index", {"raw": "56.", "char_start": 0})
        == []
    )


def test_ambiguous_internal_files_are_candidates(tmp_path):
    anchors = []
    for p in [300, 301]:
        path = tmp_path / f"{p}.txt"
        path.write_text("88 text")
        anchors.append(
            {"path": str(path), "physical": p, "number": 88, "excerpt": "88 text"}
        )
    candidates = candidates_for(
        "PG104:Syntagma:index",
        [{"number": 88}],
        "name",
        {"syntagma": {88: anchors}},
        tmp_path,
    )
    assert len(candidates) == 2
    assert all(
        "multiple_marker_files" in c["uncertainties"] and not c["certified"]
        for c in candidates
    )


def catalog_fixture(tmp_path):
    catalog, staged = tmp_path / "search.sqlite", tmp_path / "connections.sqlite"
    target = tmp_path / "target.txt"
    target.write_text("text")
    with sqlite3.connect(catalog) as db:
        db.executescript("""
            CREATE TABLE items(id TEXT PRIMARY KEY,group_id,volume,label,ref_raw,source,state,metadata_json);
            CREATE TABLE candidate_storage(item_id,file,rank,score,navigable,candidate_json BLOB,PRIMARY KEY(item_id,file));
            CREATE VIEW candidates AS SELECT item_id,file,rank,score,navigable,experiment_json(candidate_json) candidate_json FROM candidate_storage;
            CREATE TABLE candidate_origins(item_id,file,origin,PRIMARY KEY(item_id,file,origin));
            CREATE TABLE item_origins(item_id,origin,role,PRIMARY KEY(item_id,origin));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json);
            CREATE TABLE localization_queue(item_id PRIMARY KEY,reason);
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume);
        """)
        db.execute(
            "INSERT INTO items VALUES(?,?,?,?,?,?,?,?)",
            ("old", "old", "PG104", "Name", "12", "source", "with_destinations", "{}"),
        )
        db.execute("INSERT INTO items_fts VALUES(?,?,?)", ("Name", "12", "PG104"))
        db.execute("INSERT INTO metadata VALUES(?,?)", ("summary", "{}"))
        db.execute(
            "INSERT INTO candidate_storage VALUES(?,?,?,?,?,?)",
            ("old", str(target), 1, 90, 1, pack_json(json.dumps({"original": True}))),
        )
    with sqlite3.connect(staged) as db:
        db.executescript(
            """CREATE TABLE items(id TEXT PRIMARY KEY,group_id,volume,label,ref_raw,source,state,metadata_json);
            CREATE TABLE candidates(item_id,file,rank,score,navigable,candidate_json);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json);"""
        )
        db.execute(
            "INSERT INTO metadata VALUES(?,?)",
            ("summary", json.dumps({"catalog": str(catalog)})),
        )
        for ident in ["new", "old"]:
            db.execute(
                "INSERT INTO items VALUES(?,?,?,?,?,?,?,?)",
                (ident, ident, "PG104", "Name", "12", "source", "source_only", "{}"),
            )
            db.execute(
                "INSERT INTO candidates VALUES(?,?,?,?,?,?)",
                (ident, str(target), 1, 60, 1, json.dumps({"new_support": True})),
            )
    return catalog, staged


def test_atomic_idempotent_apply_preserves_prior_evidence_and_fts(tmp_path):
    catalog, staged = catalog_fixture(tmp_path)
    first = apply(catalog, staged)
    assert first["changes"] == {"new_items": 1, "new_links": 1}
    second = apply(catalog, staged)
    assert not second["changes"]
    assert second["after"] == first["after"]
    with readonly(catalog) as db:
        assert db.execute("SELECT count(*) FROM items_fts").fetchone()[0] == 2
        assert (
            db.execute("SELECT count(*) FROM opening_candidate_support").fetchone()[0]
            == 2
        )
        assert json.loads(
            db.execute(
                "SELECT candidate_json FROM candidates WHERE item_id='old'"
            ).fetchone()[0]
        ) == {"original": True}


def test_apply_rolls_back_new_entries_on_identity_conflict(tmp_path):
    catalog, staged = catalog_fixture(tmp_path)
    with sqlite3.connect(staged) as db:
        db.execute("UPDATE items SET label='wrong owner' WHERE id='old'")
    with pytest.raises(ValueError, match="changed since staging"):
        apply(catalog, staged)
    with readonly(catalog) as db:
        assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM items_fts").fetchone()[0] == 1


def test_printed_section_title_is_distinct_from_entry_and_material():
    title = "INDEX EPISTOLARUM\n\nINNOCENTII III"
    raw = title + "\n\nSECUNDUM LITTERAM"
    section = {
        "selection": {
            "volume": "PL217",
            "namespace": "book_epistle",
            "first": 585,
            "last": 600,
        },
        "opening": {
            "entry_kind": "epistle_incipit",
            "source": "source.txt",
            "source_sha256": digest(raw),
            "evidence": [
                {
                    "role": "title",
                    "raw": "INDEX EPISTOLARUM",
                    "char_start": 0,
                    "char_end": 17,
                },
                {
                    "role": "ordering",
                    "raw": "SECUNDUM LITTERAM",
                    "char_start": len(title) + 2,
                    "char_end": len(raw),
                },
            ],
        },
    }
    s = section_descriptor(section, raw)
    assert s["title_raw"] == title
    assert raw[s["title_span"]["char_start"] : s["title_span"]["char_end"]] == title
    assert s["index_type"] == "incipit_index"
    assert s["indexed_material"] == "letters"
    # A changed source cannot supply extra words to an older title assertion.
    assert (
        section_descriptor(section, raw.replace("INNOCENTII", "OTHER"))["title_raw"]
        == "INDEX EPISTOLARUM"
    )


def test_section_relationships_are_applied_and_auditable_without_rewriting_items(
    tmp_path,
):
    from scripts.audit_index_section_provenance import audit

    catalog, staged = catalog_fixture(tmp_path)
    with sqlite3.connect(staged) as db:
        db.executescript(
            """CREATE TABLE editorial_sections(section_id TEXT PRIMARY KEY,volume,index_type,indexed_material,title_raw,source,evidence_json);
            CREATE TABLE item_editorial_sections(item_id,section_id,assignment_kind);"""
        )
        db.execute(
            "INSERT INTO editorial_sections VALUES(?,?,?,?,?,?,?)",
            (
                "letters",
                "PG104",
                "incipit_index",
                "letters",
                "INDEX EPISTOLARUM",
                "source",
                "{}",
            ),
        )
        db.execute(
            "INSERT INTO item_editorial_sections VALUES(?,?,?)",
            ("new", "letters", "span"),
        )
    apply(catalog, staged)
    apply(catalog, staged)
    s = audit(catalog)
    assert s["total_items"] == 2
    assert s["items_with_printed_section_title_relation"] == 1
    assert s["items_with_specific_type_any_source"] == 1
    assert s["items_without_specific_type"] == 1
    with readonly(catalog) as db:
        assert db.execute("SELECT count(*) FROM editorial_sections").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM item_editorial_sections").fetchone()[0]
            == 1
        )
        assert (
            db.execute("SELECT metadata_json FROM items WHERE id='old'").fetchone()[0]
            == "{}"
        )
