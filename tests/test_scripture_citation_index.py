from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patristica_pipeline.scripture_citation_index import (
    ScanTask,
    build_citation_database,
    citation_logical_text,
    enrich_locator_items_from_citation_db,
    scan_file_task,
    search_citations,
)


def _write_sample_volume(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "teste" / "PO999" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PO999-001.txt"
    body.write_text(
        """<pagina tipo="conteudo">
<cabecalho>[109]</cabecalho>
<bloco tipo="aparato_critico" script="latino" bbox="0,0,900,1000">
Gen. 1,4,5,7; I Rois, II, 8; I Cor. II, 4;
I Corin-
thios 3, 18-
20. Ex Psalmo CXII. v. 7. Deut. I, 16, et XVI, 18;
Psal. 4, 9; 91, 1; Luc. XXIV, 4 et 5. caritate
</bloco>
</pagina>""",
        encoding="utf-8",
    )
    index_file = source_root / "PO999-002.txt"
    index_file.write_text(
        """<pagina tipo="indice">
<bloco tipo="texto_principal">I Rois, II, 8 ..... 109</bloco>
</pagina>""",
        encoding="utf-8",
    )
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    payload = {
        "sections": [
            {
                "section_key": "PO999:index",
                "file_start": str(index_file),
                "file_end": str(index_file),
            }
        ],
        "entries": [
            {"entry_key": "PO999:index:e1", "section_key": "PO999:index"}
        ],
        "refs": [{"entry_key": "PO999:index:e1", "page_ref_int": 109}],
        "scripture_refs": [
            {
                "entry_key": "PO999:index:e1",
                "ref_order": 1,
                "ref_raw": "I Rois, II, 8",
                "book_raw": "I Rois",
                "book_norm": "1 Samuel",
                "chapter_start": 2,
                "verse_start": 8,
            }
        ],
    }
    (payload_dir / "PO999_alphabetical_indices.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return source_root, payload_dir


def _semantic_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as con:
        return con.execute(
            """SELECT f.source_relpath, g.block_index, g.source_start,
                      g.detector_rule, o.item_order, o.book_key,
                      o.chapter_start, o.verse_start, o.chapter_end,
                      o.verse_end, o.normalization_status
               FROM citation_occurrences o
               JOIN citation_groups g ON g.group_id = o.group_id
               JOIN citation_files f ON f.file_id = g.file_id
               ORDER BY f.source_relpath, g.block_index, g.source_start,
                        g.detector_rule, o.item_order"""
        ).fetchall()


def test_logical_text_joins_words_but_preserves_numeric_ranges() -> None:
    source = "I Corin-\nthios\u200b 3, 18‑\n20"

    logical = citation_logical_text(source)

    assert logical.text == "i corinthios 3, 18-20"
    assert source[slice(*logical.source_span(0, len(logical.text)))] == source


def test_scanner_detects_main_text_xml_without_losing_raw_offsets(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "teste" / "PL998" / "text"
    source_root.mkdir(parents=True)
    path = source_root / "PL998-001.txt"
    path.write_text(
        """<pagina tipo="conteudo">
<bloco tipo="texto_principal" script="latino" bbox="10,20,490,900">
In textu legitur Ioan. III, 16; I Corin-\nthios 13, 4; Gen. 2.
</bloco>
<bloco tipo="aparato_critico" script="latino" bbox="10,910,490,990">
Rom. 5, 8
</bloco>
<bloco tipo="cabecalho" script="latino" bbox="10,0,490,15">Ps. XXII.</bloco>
</pagina>""",
        encoding="utf-8",
    )

    result = scan_file_task(
        ScanTask(
            volume_id="PL998",
            collection="PL",
            source_root=str(source_root),
            file_path=str(path),
            physical_index=0,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )

    found = {
        (group["context_kind"], occurrence["ref_norm"]): (
            group,
            occurrence,
        )
        for group in result["groups"]
        for occurrence in group["occurrences"]
    }
    assert ("body", "São João 3,16") in found
    assert ("body", "1 Coríntios 13,4") in found
    assert ("body", "Gênesis 2") in found
    assert ("critical_apparatus", "Romanos 5,8") in found
    assert ("header", "Salmos 22") in found

    group, occurrence = found[("body", "1 Coríntios 13,4")]
    assert group["block_type"] == "texto_principal"
    assert occurrence["book_raw"] == "I Corin-\nthios"
    assert group["citation_raw"] == "I Corin-\nthios 13, 4"
    assert group["evidence_json"]["matching_pipeline"].endswith("+regex")


def test_malformed_xml_fallback_remains_body_citation_source(tmp_path: Path) -> None:
    source_root = tmp_path / "teste" / "PG998" / "text"
    source_root.mkdir(parents=True)
    path = source_root / "PG998-001.txt"
    path.write_text(
        """<pagina tipo="conteudo">
<bloco tipo="texto_principal">Vide Gen. 1, 1 & confer Rom. 5, 8.</bloco>
</pagina>""",
        encoding="utf-8",
    )

    result = scan_file_task(
        ScanTask(
            volume_id="PG998",
            collection="PG",
            source_root=str(source_root),
            file_path=str(path),
            physical_index=0,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )

    assert result["parser_status"] == "xml_fallback"
    assert result["groups"][0]["context_kind"] == "body"
    assert {
        occurrence["ref_norm"]
        for group in result["groups"]
        for occurrence in group["occurrences"]
    } == {"Gênesis 1,1", "Romanos 5,8"}


def test_psalm_heading_inheritance_rejects_impossible_psalm_number(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "teste" / "PL997" / "text"
    source_root.mkdir(parents=True)
    path = source_root / "PL997-001.txt"
    path.write_text(
        """<pagina tipo="conteudo">
<bloco tipo="texto_principal">
EX PSALMO D. Vers. 1. EX PSALMO CLI. Vers. 2.
</bloco>
</pagina>""",
        encoding="utf-8",
    )

    result = scan_file_task(
        ScanTask(
            volume_id="PL997",
            collection="PL",
            source_root=str(source_root),
            file_path=str(path),
            physical_index=0,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )

    refs = {
        occurrence["ref_norm"]
        for group in result["groups"]
        for occurrence in group["occurrences"]
    }
    assert refs == {"Salmos 151", "Salmos 151,2"}
    assert all(not ref.startswith("Salmos 500") for ref in refs)


def test_chapter_only_notes_do_not_consume_next_footnote_number(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "teste" / "PG997" / "text"
    source_root.mkdir(parents=True)
    path = source_root / "PG997-001.txt"
    path.write_text(
        """<pagina tipo="conteudo">
<bloco tipo="rodape">
4S Dan. XIII. 44 Gen. XXXIX. 45 Matth. XVIII, 15. 46 1 Cor. 2.11.
</bloco>
<bloco tipo="texto_principal">
col. 689; la sagesse 10 divine; mes os. 9 guérissent; Gen. 2.
</bloco>
</pagina>""",
        encoding="utf-8",
    )

    result = scan_file_task(
        ScanTask(
            volume_id="PG997",
            collection="PG",
            source_root=str(source_root),
            file_path=str(path),
            physical_index=0,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    refs = {
        occurrence["ref_norm"]
        for group in result["groups"]
        for occurrence in group["occurrences"]
    }

    assert refs == {
        "Daniel 13",
        "Gênesis 39",
        "Gênesis 2",
        "São Mateus 18,15",
        "1 Coríntios 2,11",
    }
    assert "Daniel 13,44" not in refs
    assert "Colossenses 689" not in refs
    assert "Sabedoria 10" not in refs
    assert "Oseias 9" not in refs
    daniel = next(
        group
        for group in result["groups"]
        if group["occurrences"][0]["ref_norm"] == "Daniel 13"
    )
    assert daniel["citation_raw"] == "Dan. XIII"
    assert daniel["evidence_json"]["note_call"] == {
        "raw": "4S",
        "number_start": 45,
        "number_end": None,
        "ocr_numeric_repair": True,
    }


def test_scanner_handles_lists_historical_names_and_roman_boundaries(
    tmp_path: Path,
) -> None:
    source_root, _ = _write_sample_volume(tmp_path)
    path = source_root / "PO999-001.txt"

    result = scan_file_task(
        ScanTask(
            volume_id="PO999",
            collection="PO",
            source_root=str(source_root),
            file_path=str(path),
            physical_index=0,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    occurrences = [
        occurrence
        for group in result["groups"]
        for occurrence in group["occurrences"]
    ]
    normalized = {item["ref_norm"] for item in occurrences}

    assert {"Gênesis 1,4", "Gênesis 1,5", "Gênesis 1,7"} <= normalized
    assert "1 Samuel 2,8" in normalized
    assert "1 Coríntios 2,4" in normalized
    assert "1 Coríntios 3,18-20" in normalized
    assert "Salmos 112,7" in normalized
    assert {"Deuteronômio 1,16", "Deuteronômio 16,18"} <= normalized
    assert {"Salmos 4,9", "Salmos 91,1"} <= normalized
    assert {"São Lucas 24,4", "São Lucas 24,5"} <= normalized
    assert not any(
        item["book_key"] == "1 corintios"
        and item["chapter_start"] == 1
        and item["verse_start"] is None
        for item in occurrences
    )
    assert result["pages"][0]["editorial_page"] == 109

    dotless_path = source_root / "PO999-003.txt"
    dotless_path.write_text("I Cor. ıı, 4", encoding="utf-8")
    dotless = scan_file_task(
        ScanTask(
            volume_id="PO999",
            collection="PO",
            source_root=str(source_root),
            file_path=str(dotless_path),
            physical_index=2,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    assert dotless["groups"][0]["occurrences"][0]["ref_norm"] == "1 Coríntios 2,4"

    stopwords_path = source_root / "PO999-004.txt"
    stopwords_path.write_text(
        "ex 10, 2; si 4, 5; ne 2, 1; est 3, 4",
        encoding="utf-8",
    )
    stopwords = scan_file_task(
        ScanTask(
            volume_id="PO999",
            collection="PO",
            source_root=str(source_root),
            file_path=str(stopwords_path),
            physical_index=3,
            is_index_source=False,
            observed_aliases=(),
            estimator_pages=(),
            profile_fingerprint="fixture",
        )
    )
    assert stopwords["groups"] == []


def test_database_is_incremental_searchable_and_excludes_index_sources(
    tmp_path: Path,
) -> None:
    source_root, payload_dir = _write_sample_volume(tmp_path)
    db_path = tmp_path / "citations.db"
    volumes = [("PO999", "PO", source_root)]

    first = build_citation_database(
        db_path=db_path,
        volumes=volumes,
        workers=2,
        payload_dir=payload_dir,
    )
    second = build_citation_database(
        db_path=db_path,
        volumes=volumes,
        workers=2,
        payload_dir=payload_dir,
    )

    assert first["scanned_file_count"] == 2
    assert first["seed_link_count"] >= 1
    assert second["scanned_file_count"] == 0
    assert second["skipped_file_count"] == 2
    editorial = search_citations(
        db_path=db_path,
        volume_id="PO999",
        editorial_page=109,
        book="1 Samuel",
    )
    assert len(editorial) == 1
    assert editorial[0]["ref_norm"] == "1 Samuel 2,8"
    assert editorial[0]["linked_to_index"] == 1
    assert editorial[0]["source_relpath"] == "PO999-001.txt"
    physical = search_citations(
        db_path=db_path,
        volume_id="PO999",
        physical_page=1,
        text_query="caritate",
    )
    assert physical
    assert {item["source_relpath"] for item in physical} == {"PO999-001.txt"}
    with_index = search_citations(
        db_path=db_path,
        volume_id="PO999",
        physical_page=2,
        include_index=True,
    )
    assert with_index
    assert search_citations(
        db_path=db_path,
        volume_id="PO999",
        physical_page=2,
    ) == []
    enriched, artifact = enrich_locator_items_from_citation_db(
        [
            {
                "locator_key": "PO999:index:e1::ref:000001",
                "section_file_start": str(source_root / "PO999-002.txt"),
                "section_file_end": str(source_root / "PO999-002.txt"),
                "cited_pages": [109],
                "candidates": [],
                "scripture_ref": {
                    "book_key": "1 samuel",
                    "chapter_start": 2,
                    "verse_start": 8,
                },
            }
        ],
        db_path=db_path,
        source_root=source_root,
    )
    assert artifact["coverage_status"] == "complete"
    assert enriched[0]["candidates"][0]["file"].endswith("PO999-001.txt")
    assert enriched[0]["candidates"][0]["candidate_role"] == (
        "persistent_scripture_evidence"
    )
    body_file = source_root / "PO999-001.txt"
    body_file.write_text(
        body_file.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    _, stale_artifact = enrich_locator_items_from_citation_db(
        [],
        db_path=db_path,
        source_root=source_root,
    )
    assert stale_artifact["coverage_status"] == "stale"
    assert stale_artifact["stale_file_count"] == 1


def test_body_and_ocr_note_call_are_persisted_in_database(tmp_path: Path) -> None:
    source_root = tmp_path / "teste" / "PL997" / "text"
    source_root.mkdir(parents=True)
    path = source_root / "PL997-001.txt"
    path.write_text(
        """<pagina tipo="conteudo">
<bloco tipo="texto_principal">Vide Gen. I, 1.</bloco>
<bloco tipo="rodape">[4S] Dan. XIII.</bloco>
</pagina>""",
        encoding="utf-8",
    )
    db_path = tmp_path / "citations.db"

    build_citation_database(
        db_path=db_path,
        volumes=[("PL997", "PL", source_root)],
        workers=1,
        payload_dir=tmp_path / "missing-payloads",
    )

    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            """SELECT g.context_kind, o.ref_norm, g.evidence_json
               FROM citation_occurrences o
               JOIN citation_groups g ON g.group_id = o.group_id
               ORDER BY g.block_index, o.item_order"""
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("body", "Gênesis 1,1"),
        ("footer", "Daniel 13"),
    ]
    assert json.loads(rows[1][2])["note_call"] == {
        "number_end": None,
        "number_start": 45,
        "ocr_numeric_repair": True,
        "raw": "[4S]",
    }


def test_one_and_two_workers_produce_identical_semantic_rows(
    tmp_path: Path,
) -> None:
    source_root, payload_dir = _write_sample_volume(tmp_path)
    volumes = [("PO999", "PO", source_root)]
    serial_db = tmp_path / "serial.db"
    parallel_db = tmp_path / "parallel.db"

    build_citation_database(
        db_path=serial_db,
        volumes=volumes,
        workers=1,
        payload_dir=payload_dir,
    )
    build_citation_database(
        db_path=parallel_db,
        volumes=volumes,
        workers=2,
        payload_dir=payload_dir,
    )

    assert _semantic_rows(serial_db) == _semantic_rows(parallel_db)
