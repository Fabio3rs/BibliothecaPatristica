from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tools.ocr_embedding_ir import (
    build_variant_chunks,
    embedding_visible_text,
    extract_page_segments,
    structural_visible_text,
)
from tools.ocr_xml_utils import parse_ocr_xml_page
from tools.benchmark_ocr_parallel_pages import benchmark_parallel_pages
from tools.generate_ocr_embedding_experiment import (
    ParsedPage,
    PreparedVolume,
    connect_db,
    embed_chunks,
    rebuild_page_embeddings,
    volume_report,
    write_prepared,
)
from tools.generate_ocr_embeddings import SourceChoice, sha256_text
from tools.query_ocr_page_knn import page_knn


def _segments(raw: str, page_num: int = 1):
    return extract_page_segments(
        volume_id="PG999",
        work_label="Opus",
        page_num=page_num,
        source_file=f"PG999-{page_num:03d}.txt",
        source_hash=sha256_text(raw),
        page=parse_ocr_xml_page(raw),
        repeated_fingerprints=set(),
    )


def test_whitespace_is_preserved_for_analysis_then_normalized() -> None:
    raw = "Corpus alter-\nutram manet.\n\nJoan. I, 3."

    structural = structural_visible_text(raw)
    normalized = embedding_visible_text(raw)

    assert "alter-\nutram" in structural
    assert "\n\n" in structural
    assert "Corpus alterutram manet.\n\nJoan. I, 3." == normalized


def test_ir_splits_embedded_reference_list_without_trusting_xml_type() -> None:
    raw = """<pagina estado="com_texto">
    <bloco tipo="texto_principal" script="latino" bbox="10,10,500,900">
    54. Corpus argumenti per multas voces continuatur et rem theologicam explicat.

    76 Zachar. II, 10. 77 Hebr. I, 4. 78 Act. VIII, 34.

    (79) Sic Seguerianus. Alii et editi aliter legunt; verbum in codice deest.
    </bloco></pagina>"""

    segments = _segments(raw)

    assert [segment.inferred_role for segment in segments] == [
        "body",
        "scripture_references",
        "critical_apparatus",
    ]
    assert all("role_overrides_xml_type" in segment.flags for segment in segments[1:])


def test_variants_keep_apparatus_only_in_v0_and_split_body_scripts_in_v2() -> None:
    raw = """<pagina estado="com_texto">
    <bloco tipo="texto_principal" script="latino">52. Corpus Latinum satis longum ad experimentum semanticum.</bloco>
    <bloco tipo="texto_principal" script="grego">52. Λόγος θεολογικὸς καὶ νοητὸς πρὸς δοκιμήν.</bloco>
    <bloco tipo="aparato_critico" script="misto">(1) Sic mss. Alii et editi aliter legunt.</bloco>
    </pagina>"""
    chunks = build_variant_chunks(
        _segments(raw), variants=("v0", "v1", "v2"), max_chars=500, overlap_chars=20
    )

    v0 = [chunk for chunk in chunks if chunk.variant_id == "v0"]
    v1 = [chunk for chunk in chunks if chunk.variant_id == "v1"]
    v2 = [chunk for chunk in chunks if chunk.variant_id == "v2"]
    assert "Sic mss." in v0[0].text
    assert all("Sic mss." not in chunk.text for chunk in v1 + v2)
    assert {chunk.script for chunk in v2} == {"greek", "latin"}
    assert all(chunk.sources for chunk in chunks)


def test_repeated_habet_in_theological_body_is_not_apparatus() -> None:
    raw = """<pagina estado="com_texto">
    <bloco tipo="texto_principal" script="latino">
    Filius naturam Patris habet et vitam in semetipso habet. Ex his argumentum
    prosequitur auctor, neque creaturam neque opus Filium esse demonstrans.
    </bloco></pagina>"""

    segments = _segments(raw, page_num=73)

    assert len(segments) == 1
    assert segments[0].inferred_role == "body"


def test_repaired_xml_is_auditable_in_ir_and_volume_report(tmp_path: Path) -> None:
    prepared = _prepared_volume(tmp_path)
    repaired_raw = """<pagina estado="com_texto">
    <bloco tipo="texto_principal" script="latino">Corpus & anima.</bloco>
    </pagina>"""
    repaired_page = parse_ocr_xml_page(
        repaired_raw,
        repair_non_xml_ampersands=True,
    )
    repaired_segments = extract_page_segments(
        volume_id="PG999",
        work_label="Opus",
        page_num=3,
        source_file="PG999-003.txt",
        source_hash=sha256_text(repaired_raw),
        page=repaired_page,
        repeated_fingerprints=set(),
    )
    choice = SourceChoice(3, tmp_path / "PG999-003.txt", ())
    augmented = replace(
        prepared,
        pages=(*prepared.pages, ParsedPage(choice, repaired_raw, sha256_text(repaired_raw), repaired_page)),
        segments=(*prepared.segments, *repaired_segments),
    )

    report = volume_report(augmented)

    assert report["malformed_xml_pages"] == 0
    assert report["repaired_xml_pages"] == 1
    assert report["xml_repair_pages_by_kind"] == {
        "escaped_non_xml_ampersands": 1
    }
    assert report["xml_repair_counts"] == {"escaped_non_xml_ampersands": 1}
    assert "source_xml_repair:escaped_non_xml_ampersands=1" in repaired_segments[0].flags


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        max_chunk_chars=500,
        overlap_chars=20,
        repeated_header_min_pages=5,
        repeated_header_min_ratio=0.01,
    )


def _prepared_volume(tmp_path: Path) -> PreparedVolume:
    pages: list[ParsedPage] = []
    segments = []
    for page_num, latin, greek in (
        (1, "Deus unus et aeternus argumentum primum.", "Θεὸς εἷς καὶ ἀΐδιος λόγος πρῶτος."),
        (2, "Filius creator et non creatura argumentum alterum.", "Υἱὸς δημιουργός, οὐ δημιούργημα, λόγος δεύτερος."),
    ):
        raw = f"""<pagina estado="com_texto">
        <bloco tipo="texto_principal" script="latino">{latin}</bloco>
        <bloco tipo="texto_principal" script="grego">{greek}</bloco>
        </pagina>"""
        path = tmp_path / "PG999" / "text" / f"PG999-{page_num:03d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
        choice = SourceChoice(page_num, path, (path,))
        page = parse_ocr_xml_page(raw)
        pages.append(ParsedPage(choice, raw, sha256_text(raw), page))
        segments.extend(
            extract_page_segments(
                volume_id="PG999",
                work_label="Opus",
                page_num=page_num,
                source_file=path.name,
                source_hash=sha256_text(raw),
                page=page,
                repeated_fingerprints=set(),
            )
        )
    chunks = build_variant_chunks(
        segments, variants=("v0", "v1", "v2"), max_chars=500, overlap_chars=20
    )
    return PreparedVolume(
        volume_id="PG999",
        work_label="Opus",
        pages=tuple(pages),
        segments=tuple(segments),
        chunks=tuple(chunks),
        repeated_fingerprints=frozenset(),
    )


def test_db_keeps_only_hashes_metadata_vectors_and_page_centroids(
    tmp_path: Path, monkeypatch
) -> None:
    prepared = _prepared_volume(tmp_path)
    db_path = tmp_path / "experiment.db"

    def fake_embeddings(texts, model, ollama_url):
        vectors = []
        for text in texts:
            if "primum" in text or "πρῶτος" in text:
                vectors.append([1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0])
        return vectors

    monkeypatch.setattr(
        "tools.generate_ocr_embedding_experiment.embed_documents", fake_embeddings
    )
    with connect_db(db_path) as con:
        write_prepared(
            con,
            tmp_path,
            prepared,
            variants=("v0", "v1", "v2"),
            args=_args(),
        )
        con.commit()
        processed, _ = embed_chunks(
            con,
            prepared.chunks,
            model="model-test",
            ollama_url="http://invalid",
            batch_size=8,
            limit_chunks=0,
        )
        written, incomplete = rebuild_page_embeddings(
            con, prepared.chunks, model="model-test"
        )
        assert processed == len(prepared.chunks)
        assert written == 6
        assert incomplete == 0
        assert "raw_text" not in {row[1] for row in con.execute("PRAGMA table_info(segments)")}
        assert "normalized_text" not in {
            row[1] for row in con.execute("PRAGMA table_info(segments)")
        }
        assert "text" not in {row[1] for row in con.execute("PRAGMA table_info(chunks)")}
        assert con.execute("SELECT count(*) FROM chunk_sources").fetchone()[0] >= len(
            prepared.chunks
        )

    neighbors = page_knn(
        db_path,
        anchor_volume="PG999",
        anchor_page=1,
        variant_id="v2",
        model="model-test",
        top_k=1,
    )
    assert neighbors[0]["page_num"] == 2
    assert neighbors[0]["score"] == 0.0


def test_changed_chunks_invalidate_stale_page_centroids(
    tmp_path: Path, monkeypatch
) -> None:
    prepared = _prepared_volume(tmp_path)
    monkeypatch.setattr(
        "tools.generate_ocr_embedding_experiment.embed_documents",
        lambda texts, model, ollama_url: [[1.0, 0.0] for _text in texts],
    )
    with connect_db(tmp_path / "experiment.db") as con:
        write_prepared(
            con,
            tmp_path,
            prepared,
            variants=("v0", "v1", "v2"),
            args=_args(),
        )
        embed_chunks(
            con,
            prepared.chunks,
            model="model-test",
            ollama_url="http://invalid",
            batch_size=20,
            limit_chunks=0,
        )
        rebuild_page_embeddings(con, prepared.chunks, model="model-test")
        assert con.execute("SELECT count(*) FROM page_embeddings").fetchone()[0] == 6

        changed_segments = list(prepared.segments)
        changed_segments[0] = replace(
            changed_segments[0],
            normalized_text=changed_segments[0].normalized_text + " mutatum",
        )
        changed_chunks = build_variant_chunks(
            changed_segments,
            variants=("v0", "v1", "v2"),
            max_chars=500,
            overlap_chars=20,
        )
        changed_prepared = replace(
            prepared,
            segments=tuple(changed_segments),
            chunks=tuple(changed_chunks),
        )
        write_prepared(
            con,
            tmp_path,
            changed_prepared,
            variants=("v0", "v1", "v2"),
            args=_args(),
        )
        assert con.execute("SELECT count(*) FROM page_embeddings").fetchone()[0] < 6


def test_parallel_benchmark_reports_perfect_diagonal() -> None:
    vectors = {
        (1, "greek"): np.array([1.0, 0.0], dtype=np.float32),
        (1, "latin"): np.array([1.0, 0.0], dtype=np.float32),
        (2, "greek"): np.array([0.0, 1.0], dtype=np.float32),
        (2, "latin"): np.array([0.0, 1.0], dtype=np.float32),
    }

    report = benchmark_parallel_pages(vectors)

    assert report["pairs"] == 2
    assert report["recall_at_1"] == 1.0
    assert report["mrr"] == 1.0
    assert report["mean_margin"] == 1.0


def test_db_refuses_to_mix_chunk_configs_for_same_variant(tmp_path: Path) -> None:
    prepared = _prepared_volume(tmp_path)
    with connect_db(tmp_path / "experiment.db") as con:
        write_prepared(
            con,
            tmp_path,
            prepared,
            variants=("v2",),
            args=_args(),
        )
        con.commit()
        changed = _args()
        changed.overlap_chars = 10
        with pytest.raises(RuntimeError, match="use outro --db"):
            write_prepared(
                con,
                tmp_path,
                prepared,
                variants=("v2",),
                args=changed,
            )
