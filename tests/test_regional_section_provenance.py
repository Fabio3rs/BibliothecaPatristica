from pathlib import Path
import json
import sqlite3

import pytest

from tools.indexing.regional_section_provenance import (
    VERSION,
    apply,
    build,
    classify_title,
    compare,
    locate_span,
    process_volume,
    select_pages,
    sha,
)


def block(text, kind="texto_principal"):
    return f'<bloco tipo="{kind}" script="latino">{text}</bloco>\n'


def item(path, raw, ident, ref):
    pos = raw.index(ref)
    return {
        "id": ident,
        "source": str(path.resolve()),
        "ref_raw": ref,
        "span": [pos, pos + len(ref)],
        "source_sha256": sha(raw),
    }


@pytest.mark.parametrize(
    "title,kind,material",
    [
        ("INDEX NOMINUM MARTYRUM", "onomastic_index", "martyrs"),
        ("TABLE DES NOMS DES SAINTS", "onomastic_index", "saints"),
        ("INDEX HOMILIARUM", "index_unspecified", "homilies"),
        ("CATALOGUS HOMILIARUM", "work_catalog", "homilies"),
        ("INDEX OPERUM S. FULGENTII", "index_unspecified", None),
        (
            "INDEX IN LIBROS MORALIUM ET HOMILIAS S. GREGORII",
            "index_unspecified",
            "homilies",
        ),
        ("INDEX RERUM IN HOMILIIS", "thematic_index", "homilies"),
        ("INDEX INITIORUM EPISTOLARUM", "incipit_index", "letters"),
        ("INDEX", "index_unspecified", None),
        ("INDEX RERUM ET VERBORUM", "subject_and_word_index", None),
        ("INDEX RERUM AC VERBORUM", "subject_and_word_index", None),
        ("INDEX RERUM ET NOMINUM", "subject_and_name_index", None),
        ("INDEX AUCTORUM ET OPERUM PERSONARUM RERUM", "mixed_index", None),
        ("ΠΙΝΑΞ ΤΩΝ ΣΥΓΓΡΑΦΕΩΝ", "author_index", None),
        ("INDICES ANALYTICI", "thematic_index", None),
        ("INDEX GEOGRAPHICUS", "geographic_index", None),
        ("TABLE DES MOTS SYRIAQUES ÉTRANGERS", "lexical_index", None),
        ("1637 [span]INDEX IN CYRILLUM.[/span] 1688", "index_unspecified", None),
    ],
)
def test_type_and_material_are_distinct(title, kind, material):
    assert classify_title(title) == (kind, material)


def test_mixed_page_membership_uses_current_reference_span(tmp_path):
    folder = tmp_path / "PG001/text"
    folder.mkdir(parents=True)
    raw = block("INDEX NOMINUM MARTYRUM") + block("Agnes 12.\nFelix 15.")
    raw += block("ORDO RERUM") + block("Homilia prima 200.\nHomilia secunda 220.")
    p = folder / "x-001.txt"
    p.write_text(raw)
    result = process_volume(
        (
            "PG001",
            [[1, 1]],
            str(tmp_path),
            [item(p, raw, "name", "12."), item(p, raw, "contents", "200.")],
        )
    )
    assignments = {
        a["item_id"]: result["sections"][a["section_id"]] for a in result["assignments"]
    }
    assert assignments["name"]["index_type"] == "onomastic_index"
    assert assignments["contents"]["index_type"] == "contents_catalog"
    for s in assignments.values():
        a, b = s["title_span"]
        assert raw[a:b] == s["title_raw"]


def test_running_heading_is_local_and_not_inherited_across_gap(tmp_path):
    folder = tmp_path / "PG001/text"
    folder.mkdir(parents=True)
    first = block("INDEX NOMINUM 300", "cabecalho") + block("Agnes 12.\nFelix 15.")
    later = block("Other text 72.")
    p = folder / "x-001.txt"
    p.write_text(first)
    q = folder / "x-003.txt"
    q.write_text(later)
    result = process_volume(
        (
            "PG001",
            [[1, 1], [3, 3]],
            str(tmp_path),
            [item(p, first, "a", "12."), item(q, later, "b", "72.")],
        )
    )
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["assignment_kind"] == "page_heading_candidate"
    assert result["outcomes"][1]["reason"] == "no_section_evidence"


def test_stale_hash_relocates_literal_and_does_not_block():
    i = {"span": [1, 3], "ref_raw": "123", "source_sha256": "old"}
    assert locate_span(i, "Prefix: 123 suffix") == (
        [8, 11],
        "unique_literal_reference_relocated",
    )
    assert locate_span(i, "Prefix: 1123 suffix")[0] is None
    assert locate_span(i, "123 and 123")[0] is None


def test_ambiguous_stale_span_retains_both_page_regions(tmp_path):
    folder = tmp_path / "PG001/text"
    folder.mkdir(parents=True)
    raw = block("INDEX NOMINUM MARTYRUM") + block("Agnes 12.\nFelix 15.")
    raw += block("INDEX RERUM MEMORABILIUM") + block("Fides 12.\nCaritas 15.")
    p = folder / "x-001.txt"
    p.write_text(raw)
    i = item(p, raw, "a", "12.")
    i["span"] = [0, 2]
    i["source_sha256"] = "old"
    result = process_volume(("PG001", [[1, 1]], str(tmp_path), [i]))
    assert len(result["assignments"]) == 2
    assert all(
        a["assignment_kind"] == "page_region_candidate" and a["evidence"]["ambiguous"]
        for a in result["assignments"]
    )


def fixture(tmp_path):
    folder = tmp_path / "corpus/PG001/text"
    folder.mkdir(parents=True)
    raw = block("INDEX NOMINUM MARTYRUM") + block("Agnes 12.\nFelix 15.")
    p = folder / "x-001.txt"
    p.write_text(raw)
    i = item(p, raw, "a", "12.")
    catalog = tmp_path / "catalog.sqlite"
    with sqlite3.connect(catalog) as db:
        db.executescript(
            """CREATE TABLE items(id PRIMARY KEY,volume,source,ref_raw,metadata_json);
          CREATE TABLE candidates(item_id,file,navigable); CREATE TABLE metadata(key PRIMARY KEY,json);"""
        )
        db.execute(
            "INSERT INTO items VALUES(?,?,?,?,?)",
            (
                "a",
                "PG001",
                str(p.resolve()),
                "12.",
                json.dumps({"source_span": i["span"], "source_sha256": sha(raw)}),
            ),
        )
    return catalog, tmp_path / "corpus"


def test_serial_parallel_rebuild_and_idempotent_apply(tmp_path):
    catalog, root = fixture(tmp_path)
    selection = {"version": VERSION, "volumes": {"PG001": [[1, 1]]}}
    a = build(catalog, root, selection, tmp_path / "a", 1)
    b = build(catalog, root, selection, tmp_path / "b", 2)
    assert a["logical_sha256"] == b["logical_sha256"]
    apply(catalog, tmp_path / "a/sections.sqlite")
    apply(catalog, tmp_path / "a/sections.sqlite")
    repeated = apply(catalog, tmp_path / "b/sections.sqlite")
    assert repeated["relationships_added"] == 0
    assert repeated["relationships_reused"] == 1
    with sqlite3.connect(catalog) as db:
        assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM item_editorial_sections").fetchone()[0]
            == 1
        )
        assert db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0


def test_changed_item_rolls_back_and_selection_records_ranges(tmp_path):
    catalog, root = fixture(tmp_path)
    selection = select_pages(catalog, None, 0)
    assert selection["volumes"] == {"PG001": [[1, 1]]}
    build(catalog, root, selection, tmp_path / "a", 1)
    with sqlite3.connect(catalog) as db:
        db.execute("UPDATE items SET ref_raw='changed'")
    with pytest.raises(ValueError, match="changed since staging"):
        apply(catalog, tmp_path / "a/sections.sqlite")
    with sqlite3.connect(catalog) as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='item_editorial_sections'"
        ).fetchone():
            assert (
                db.execute("SELECT count(*) FROM item_editorial_sections").fetchone()[0]
                == 0
            )


def test_comparison_separates_type_gain_from_new_membership(tmp_path):
    catalog, root = fixture(tmp_path)
    selection = select_pages(catalog, None, 0)
    build(catalog, root, selection, tmp_path / "old", 1)
    build(catalog, root, selection, tmp_path / "new", 1)
    with sqlite3.connect(tmp_path / "old/sections.sqlite") as db:
        db.execute("UPDATE sections SET index_type='index_unspecified'")
    result = compare(tmp_path / "new/sections.sqlite", tmp_path / "old/sections.sqlite")
    assert result['same_identity_items'] == 1
    assert result['counts'] == {'new_specific_type': 1}
    with sqlite3.connect(tmp_path / "old/sections.sqlite") as db:
        db.execute("DELETE FROM assignments")
    result = compare(tmp_path / "new/sections.sqlite", tmp_path / "old/sections.sqlite")
    assert result['counts'] == {'new_specific_type': 1, 'new_section_candidate': 1}


@pytest.mark.parametrize(
    "middle,tail,bridged",
    [
        ("INDICES AD LIBROS DE CONSOLATIONE PHILOSOPHIAE.", "INDEX VOCABULORUM.", True),
        ("AD CODINUM DE OFFICIIS.", "INDEX VOCABULORUM.", True),
        ("INDEX NOMINUM.", "INDEX VOCABULORUM.", False),
        ("INDEX", "INDEX NOMINUM.", False),
        ("PRAEFATIO", "INDEX VOCABULORUM.", False),
    ],
)
def test_two_sided_headers_require_concordance_and_no_boundary(
    tmp_path, middle, tail, bridged
):
    folder = tmp_path / "PG001/text"
    folder.mkdir(parents=True)
    texts = [
        block("100 INDEX VOCABULORUM. 101", "cabecalho") + block("Abba 12.\nAbbas 13."),
        block(middle, "cabecalho") + block("Acies 14.\nAcuta 15."),
        block("104 " + tail + " 105", "cabecalho") + block("Adesse 16.\nAdest 17."),
    ]
    for n, text in enumerate(texts, 1):
        (folder / f"x-{n:03d}.txt").write_text(text)
    i = item(folder / "x-002.txt", texts[1], "i", "14.")
    result = process_volume(("PG001", [[1, 3]], str(tmp_path), [i]))
    bridges = [
        a
        for a in result["assignments"]
        if a["assignment_kind"] == "bracketed_running_headers_candidate"
    ]
    assert bool(bridges) is bridged
    if bridged:
        assert bridges[0]["evidence"]["physical_window"] == [1, 2, 3]
        assert bridges[0]["evidence"]["classification_certified"] is False


def test_two_sided_headers_do_not_bridge_prose_or_missing_page(tmp_path):
    folder = tmp_path / "PG001/text"
    folder.mkdir(parents=True)
    for n in (1, 3):
        (folder / f"x-{n:03d}.txt").write_text(
            block("100 INDEX VOCABULORUM 101", "cabecalho")
        )
    raw = block("This is continuous prose about book 14. Nothing forms an index here.")
    p = folder / "x-002.txt"
    p.write_text(raw)
    result = process_volume(
        ("PG001", [[1, 3]], str(tmp_path), [item(p, raw, "i", "14.")])
    )
    assert not result["assignments"]
    raw = block("Acies 14.\nAcuta 15.")
    p.write_text(raw)
    (folder / "x-003.txt").unlink()
    result = process_volume(
        ("PG001", [[1, 3]], str(tmp_path), [item(p, raw, "i", "14.")])
    )
    assert not result["assignments"]


@pytest.mark.parametrize(
    "title", ["TABLE DES MOTS SYRIAQUES.", "1637 [span]INDEX IN CYRILLUM.[/span] 1688"]
)
def test_new_local_headers_preserve_raw_title_spans(tmp_path, title):
    folder = tmp_path / "PG001/text"
    folder.mkdir(parents=True)
    raw = block(title, "cabecalho") + block("Alpha 12.\nBeta 15.")
    p = folder / "x-001.txt"
    p.write_text(raw)
    result = process_volume(
        ("PG001", [[1, 1]], str(tmp_path), [item(p, raw, "i", "12.")])
    )
    assert result["assignments"]
    section = result["sections"][result["assignments"][0]["section_id"]]
    assert section["title_raw"] == title
    assert raw[slice(*section["title_span"])] == title
