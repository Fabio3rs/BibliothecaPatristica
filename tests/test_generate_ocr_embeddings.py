from pathlib import Path

import pytest

from patristica_pipeline.ocr_xml_utils import parse_ocr_xml_page
from tools.generate_ocr_embeddings import (
    ParsedSource,
    SourceChoice,
    connect_db,
    discover_source_pages,
    embed_prepared,
    prepare_page,
    repeated_header_fingerprints,
    sha256_text,
    split_text_windows,
    write_prepared_volume,
)
from tools.query_ocr_embeddings import search


def source(page_num: int, raw: str) -> ParsedSource:
    path = Path(f"PG001-{page_num:03d}.txt")
    return ParsedSource(
        choice=SourceChoice(page_num, path, (path,)),
        raw=raw,
        source_hash=f"hash-{page_num}",
        page=parse_ocr_xml_page(raw),
    )


def test_discovery_prefers_canonical_file_for_duplicate_page(tmp_path: Path) -> None:
    text_dir = tmp_path / "PL020" / "text"
    text_dir.mkdir(parents=True)
    canonical = text_dir / "PL020-001.txt"
    legacy = text_dir / "uuid-0001.txt"
    canonical.write_text("novo", encoding="utf-8")
    legacy.write_text("antigo", encoding="utf-8")

    choices = discover_source_pages(tmp_path, "PL020")

    assert len(choices) == 1
    assert choices[0].path == canonical
    assert len(choices[0].candidates) == 2


def test_repeated_headers_are_removed_but_greek_body_is_preserved() -> None:
    pages = [
        source(
            index,
            f"""<pagina estado="com_texto">
            <bloco tipo="cabecalho" script="latino">DE TRINITATE {index}</bloco>
            <bloco tipo="texto_principal" script="grego">λόγος θεολογικός αριθμός {index} com contexto suficiente para busca semântica e muitas palavras relevantes preservadas no corpo desta pagina antiga.</bloco>
            <rodape>{index}</rodape>
            <notas>comentário do transcritor</notas>
            </pagina>""",
        )
        for index in range(1, 6)
    ]
    repeated, _ = repeated_header_fingerprints(pages, min_pages=5, min_ratio=0.0)

    prepared = prepare_page(
        pages[0],
        repeated,
        min_page_chars=20,
        max_chunk_chars=200,
        overlap_chars=20,
    )

    assert prepared.status == "ready"
    assert "λόγος θεολογικός" in prepared.clean_text
    assert "<pagina" not in prepared.clean_text
    assert "<bloco" not in prepared.clean_text
    assert "DE TRINITATE" not in prepared.clean_text
    assert "comentário do transcritor" not in prepared.clean_text
    assert prepared.removed_blocks["repeated_running_text"] == 1
    assert prepared.removed_blocks["isolated_locator"] == 1
    assert prepared.removed_blocks["root_notes"] == 1


def test_substantial_notes_and_footers_are_preserved() -> None:
    raw = """<pagina estado="com_texto">
      <bloco tipo="texto_principal" script="latino">Corpus principal com texto suficiente para a pagina ser mantida.</bloco>
      <bloco tipo="nota" script="misto">Nota critica longa com variante grega λόγος e explicacao latina relevante.</bloco>
      <bloco tipo="rodape" script="grego">Continuação substancial do texto grego no rodape: θεολογικὸς λόγος completo.</bloco>
      <bloco tipo="nota_marginal" script="latino">A</bloco>
    </pagina>"""

    prepared = prepare_page(
        source(1, raw),
        set(),
        min_page_chars=20,
        max_chunk_chars=500,
        overlap_chars=20,
    )

    assert "Nota critica longa" in prepared.clean_text
    assert "Continuação substancial" in prepared.clean_text
    assert prepared.removed_blocks["isolated_locator"] == 1


def test_repeated_argumentative_header_is_preserved() -> None:
    raw = """<pagina estado="com_texto">
      <bloco tipo="cabecalho" script="latino">RESPONSIO</bloco>
      <bloco tipo="texto_principal" script="latino">Resposta extensa com premissas suficientes para explicar a inferencia e a conclusao do autor neste argumento teologico.</bloco>
    </pagina>"""
    prepared = prepare_page(
        source(1, raw),
        {"responsio"},
        min_page_chars=20,
        max_chunk_chars=500,
        overlap_chars=20,
    )

    assert prepared.status == "ready"
    assert prepared.clean_text.startswith("RESPONSIO")


def test_empty_xml_page_is_dropped() -> None:
    prepared = prepare_page(
        source(1, '<pagina estado="vazio"><bloco>rastro</bloco></pagina>'),
        set(),
        min_page_chars=10,
        max_chunk_chars=200,
        overlap_chars=20,
    )

    assert prepared.status == "dropped"
    assert prepared.drop_reason == "xml_pagina_vazia"
    assert prepared.chunks == ()


def test_chunk_windows_respect_limit_and_overlap() -> None:
    text = " ".join(f"token{i}" for i in range(200))
    chunks = split_text_windows(text, max_chars=220, overlap_chars=30)

    assert len(chunks) > 1
    assert all(len(chunk) <= 220 for chunk in chunks)
    assert "token0" in chunks[0]
    assert "token199" in chunks[-1]


def test_database_stores_vectors_and_hashes_but_not_clean_text(
    tmp_path: Path, monkeypatch
) -> None:
    raw = """<pagina estado="com_texto" tipo="texto">
      <bloco tipo="texto_principal" script="latino">Texto latino suficientemente longo para produzir um embedding de teste com varias palavras e contexto semantico util.</bloco>
    </pagina>"""
    prepared = prepare_page(
        source(1, raw),
        set(),
        min_page_chars=20,
        max_chunk_chars=500,
        overlap_chars=20,
    )
    report = {
        "pages": 1,
        "ready_pages": 1,
        "dropped_pages": 0,
        "shadowed_source_files": 0,
        "repeated_headers": [],
    }
    config = {
        "min_page_chars": 20,
        "max_chunk_chars": 500,
        "overlap_chars": 20,
        "repeated_header_min_pages": 5,
        "repeated_header_min_ratio": 0.01,
    }
    monkeypatch.setattr(
        "tools.generate_ocr_embeddings.embed_documents",
        lambda texts, model, ollama_url: [[1.0, 2.0] for _ in texts],
    )

    with connect_db(tmp_path / "ocr_embeddings.db") as con:
        write_prepared_volume(
            con, tmp_path, "PG001", [prepared], report, config
        )
        con.commit()
        count, _ = embed_prepared(
            con,
            {"PG001": [prepared]},
            model="modelo-teste",
            ollama_url="http://invalid",
            batch_size=4,
            limit_chunks=0,
        )
        assert count == 1
        assert "clean_text" not in {
            row[1] for row in con.execute("PRAGMA table_info(pages)")
        }
        assert "text" not in {
            row[1] for row in con.execute("PRAGMA table_info(chunks)")
        }
        assert con.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 1
        count_again, _ = embed_prepared(
            con,
            {"PG001": [prepared]},
            model="modelo-teste",
            ollama_url="http://invalid",
            batch_size=4,
            limit_chunks=0,
        )
        assert count_again == 0


def test_limited_prepare_does_not_prune_other_pages(tmp_path: Path) -> None:
    page1 = prepare_page(
        source(1, "Texto latino longo com palavras suficientes para manter esta primeira pagina no indice semantico experimental."),
        set(),
        min_page_chars=20,
        max_chunk_chars=500,
        overlap_chars=20,
    )
    page2 = prepare_page(
        source(2, "Outro texto latino longo com palavras suficientes para manter a segunda pagina no indice semantico experimental."),
        set(),
        min_page_chars=20,
        max_chunk_chars=500,
        overlap_chars=20,
    )
    config = {
        "min_page_chars": 20,
        "max_chunk_chars": 500,
        "overlap_chars": 20,
        "repeated_header_min_pages": 5,
        "repeated_header_min_ratio": 0.01,
    }
    full_report = {
        "pages": 2,
        "ready_pages": 2,
        "dropped_pages": 0,
        "shadowed_source_files": 0,
        "repeated_headers": [],
    }
    limited_report = dict(full_report, pages=1, ready_pages=1)

    with connect_db(tmp_path / "ocr_embeddings.db") as con:
        write_prepared_volume(
            con, tmp_path, "PG001", [page1, page2], full_report, config
        )
        write_prepared_volume(
            con,
            tmp_path,
            "PG001",
            [page1],
            limited_report,
            config,
            prune_missing=False,
        )
        assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 2


def test_query_rehydrates_text_from_source_file(tmp_path: Path, monkeypatch) -> None:
    text_dir = tmp_path / "PG001" / "text"
    text_dir.mkdir(parents=True)
    page_path = text_dir / "PG001-001.txt"
    raw = """<pagina estado="com_texto"><bloco tipo="texto_principal" script="grego">λόγος θεολογικός com varias palavras latinas adicionais para formar um trecho semantico completo e pesquisavel.</bloco></pagina>"""
    page_path.write_text(raw, encoding="utf-8")
    parsed = ParsedSource(
        choice=SourceChoice(1, page_path, (page_path,)),
        raw=raw,
        source_hash=sha256_text(raw),
        page=parse_ocr_xml_page(raw),
    )
    prepared = prepare_page(
        parsed,
        set(),
        min_page_chars=20,
        max_chunk_chars=500,
        overlap_chars=20,
    )
    report = {
        "pages": 1,
        "ready_pages": 1,
        "dropped_pages": 0,
        "shadowed_source_files": 0,
        "repeated_headers": [],
    }
    config = {
        "min_page_chars": 20,
        "max_chunk_chars": 500,
        "overlap_chars": 20,
        "repeated_header_min_pages": 5,
        "repeated_header_min_ratio": 0.01,
    }
    db_path = tmp_path / "ocr_embeddings.db"
    monkeypatch.setattr(
        "tools.generate_ocr_embeddings.embed_documents",
        lambda texts, model, ollama_url: [[1.0, 2.0] for _ in texts],
    )
    with connect_db(db_path) as con:
        write_prepared_volume(
            con, tmp_path, "PG001", [prepared], report, config
        )
        con.commit()
        embed_prepared(
            con,
            {"PG001": [prepared]},
            model="modelo-teste",
            ollama_url="http://invalid",
            batch_size=4,
            limit_chunks=0,
        )
    monkeypatch.setattr(
        "tools.query_ocr_embeddings.embed_queries",
        lambda queries, model, ollama_url, task_description: [[1.0, 2.0]],
    )

    results = search(
        db_path,
        "teologia",
        model="modelo-teste",
        ollama_url="http://invalid",
        volumes=["PG001"],
        top_k=1,
    )

    assert len(results) == 1
    assert "λόγος θεολογικός" in results[0]["text"]
    assert results[0]["score"] == pytest.approx(1.0)
