from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys

from PIL import Image
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import export_training as et


def png_bytes(width: int = 40, height: int = 12) -> bytes:
    image = Image.new("L", (width, height), 255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_consensus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            page_id TEXT,
            volume TEXT,
            line_image BLOB,
            reviewed_text TEXT,
            status TEXT
        );
        CREATE TABLE line_versions (
            id INTEGER PRIMARY KEY,
            line_id INTEGER,
            provider TEXT,
            model TEXT,
            text_content TEXT,
            text_hash TEXT,
            source_score REAL,
            run_id TEXT
        );
        """
    )
    for line_id in range(1, 9):
        conn.execute(
            "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?)",
            (
                line_id,
                f"PG001-{line_id:03d}",
                "PG001",
                png_bytes(),
                None,
                "inferred",
            ),
        )
    return conn


def add_version(
    conn: sqlite3.Connection,
    line_id: int,
    provider: str,
    model: str,
    text: str,
    score: float,
    run_id: str,
) -> None:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO line_versions
            (line_id, provider, model, text_content, text_hash, source_score, run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (line_id, provider, model, text, text_hash, score, run_id),
    )


def test_weighted_consensus_deduplicates_runs_and_keeps_line_id_isolation():
    conn = make_consensus_db()
    repeated = "Texto repetido por dois motores"
    add_version(conn, 1, "tesseract", "migne", repeated, 0.8, "run-1")
    add_version(conn, 1, "tesseract", "migne", repeated, 0.9, "run-2")
    add_version(conn, 1, "another-provider", "migne", repeated, 0.6, "run-2b")
    add_version(conn, 1, "openai", "gpt", repeated, 0.7, "run-3")

    three_non_tesseract = "Tres modelos externos concordam"
    add_version(conn, 2, "openai", "gpt-a", three_non_tesseract, 0.7, "a")
    add_version(conn, 2, "openai", "gpt-b", three_non_tesseract, 0.8, "b")
    add_version(conn, 2, "ollama", "qwen", three_non_tesseract, 0.9, "c")

    two_non_tesseract = "Dois modelos nao bastam"
    add_version(conn, 3, "openai", "gpt-a", two_non_tesseract, 0.9, "a")
    add_version(conn, 3, "ollama", "qwen", two_non_tesseract, 0.9, "b")

    tesseract_only = "Tesseract sozinho vale apenas dois"
    add_version(conn, 4, "tesseract", "migne", tesseract_only, 1.0, "a")

    same_global_text = "Mesmo texto em imagens distintas"
    add_version(conn, 6, "openai", "gpt-a", same_global_text, 1.0, "a")
    add_version(conn, 7, "ollama", "qwen", same_global_text, 1.0, "a")
    conn.commit()

    candidates, warnings, rejected, decisions = et.select_weighted_candidates(conn)
    by_id = {candidate.line_id: candidate for candidate in candidates}

    assert set(by_id) == {1, 2}
    assert by_id[1].consensus_score == 3
    assert by_id[1].model_count == 2
    assert by_id[1].engines == ("gpt", "migne")
    assert by_id[2].consensus_score == 3
    assert by_id[2].model_count == 3
    assert warnings == []
    assert rejected == {}
    assert sum(row["decision"] == "selected" for row in decisions) == 2


def test_weighted_consensus_conflict_warns_and_uses_stable_winner():
    conn = make_consensus_db()
    first = "Primeira leitura historica valida"
    second = "Segunda leitura historica valida"
    add_version(conn, 5, "tesseract", "migne", first, 0.80, "tess-old")
    add_version(conn, 5, "openai", "gpt", first, 0.75, "gpt")
    add_version(conn, 5, "tesseract", "migne", second, 0.95, "tess-new")
    add_version(conn, 5, "ollama", "qwen", second, 0.90, "qwen")
    conn.commit()

    candidates, warnings, _, _ = et.select_weighted_candidates(conn)

    assert len(candidates) == 1
    assert candidates[0].text == second
    assert candidates[0].consensus_score == 3
    assert len(warnings) == 1
    assert warnings[0]["type"] == "consensus_conflict"
    assert warnings[0]["selected"]["text"] == second
    assert warnings[0]["alternatives"][0]["text"] == first


def test_weighted_consensus_conflict_can_be_excluded():
    conn = make_consensus_db()
    first = "Primeira leitura historica valida"
    second = "Segunda leitura historica valida"
    add_version(conn, 5, "tesseract", "migne", first, 0.80, "tess-old")
    add_version(conn, 5, "openai", "gpt", first, 0.75, "gpt")
    add_version(conn, 5, "tesseract", "migne", second, 0.95, "tess-new")
    add_version(conn, 5, "ollama", "qwen", second, 0.90, "qwen")
    conn.commit()

    candidates, warnings, _, decisions = et.select_weighted_candidates(
        conn,
        exclude_conflicts=True,
    )

    assert candidates == []
    assert len(warnings) == 1
    assert any(
        row["line_id"] == 5
        and row["decision"] == "not_selected"
        and row["reason"] == "consensus_conflict"
        for row in decisions
    )


def test_bundle_text_hygiene():
    assert et.normalize_bundle_text("  λο\u0301γος  ") == "λόγος"
    for invalid in (
        "",
        "12345678",
        "linha\nnova",
        "a\tb",
        "text\u200bhidden",
        "text � broken",
        "текст estranho",
        "......... texto",
        "\\u0041 artifact",
    ):
        try:
            et.normalize_bundle_text(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Texto deveria ser rejeitado: {invalid!r}")
    assert et.normalize_bundle_text("Digitized by Google") == "Digitized by Google"


def test_optional_unicharset_filter_supports_space_and_multicodepoint(tmp_path):
    unicharset_path = tmp_path / "unicharset"
    unicharset_path.write_text(
        "\n".join(
            (
                "5",
                "NULL 0 Common 0",
                "a 3 Latin 0",
                "b 3 Latin 0",
                "ch 3 Latin 0",
                "λόγος 3 Greek 0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    entries = et.load_unicharset(unicharset_path)

    assert " " in entries
    assert et.text_is_covered_by_unicharset("ch a b", entries)
    assert et.text_is_covered_by_unicharset("λόγος", entries)
    assert not et.text_is_covered_by_unicharset("c a", entries)
    assert et.normalize_bundle_text("ch a b", entries) == "ch a b"
    try:
        et.normalize_bundle_text("ch a z", entries)
    except ValueError as exc:
        assert str(exc) == "unknown_unicharset_character"
    else:
        raise AssertionError("Caractere fora do unicharset deveria ser rejeitado")

    extended_path = tmp_path / "extended-unicharset"
    extended_path.write_text(
        "2\nNULL 0 Common 0\nтекст 3 Cyrillic 0\n",
        encoding="utf-8",
    )
    extended = et.load_unicharset(extended_path)
    assert et.normalize_bundle_text("текст", extended) == "текст"


def test_manual_review_overrides_consensus_and_does_not_require_votes():
    conn = make_consensus_db()
    model_text = "Texto escolhido pelos modelos"
    add_version(conn, 1, "tesseract", "migne", model_text, 0.9, "t")
    add_version(conn, 1, "openai", "gpt", model_text, 0.8, "g")
    add_version(conn, 4, "tesseract", "migne", model_text, 0.9, "t")
    add_version(conn, 4, "openai", "gpt", model_text, 0.8, "g")
    conn.execute(
        "UPDATE lines SET reviewed_text = ?, status = 'corrected' WHERE id = 1",
        ("Texto corrigido manualmente",),
    )
    conn.execute(
        "UPDATE lines SET reviewed_text = ?, status = 'approved' WHERE id = 2",
        ("Amostra aprovada sem consenso",),
    )
    conn.execute(
        "UPDATE lines SET reviewed_text = ?, status = 'rejected' WHERE id = 3",
        ("Amostra rejeitada manualmente",),
    )
    conn.execute(
        "UPDATE lines SET reviewed_text = '12345678', status = 'corrected' WHERE id = 4"
    )
    conn.commit()

    candidates, _, rejected, decisions = et.select_weighted_candidates(conn)
    by_id = {candidate.line_id: candidate for candidate in candidates}

    assert set(by_id) == {1, 2}
    assert by_id[1].text == "Texto corrigido manualmente"
    assert by_id[1].text_source == "manual_review"
    assert by_id[1].consensus_overridden is True
    assert by_id[1].consensus_text == model_text
    assert by_id[2].consensus_score == 0
    assert by_id[2].review_status == "approved"
    assert rejected["review_status_rejected"] == 1
    assert rejected["manual_review_numeric_only"] == 1
    assert any(
        row["line_id"] == 1
        and row["text_source"] == "weighted_consensus"
        and row["reason"] == "manual_review_override"
        for row in decisions
    )


def test_bundle_shuffle_is_seeded_and_changes_line_id_order():
    candidates = [
        make_record(index, f"texto {index}", 40, 12).candidate
        for index in range(1, 12)
    ]

    first = et.shuffled_candidates(candidates, seed=0)
    repeated = et.shuffled_candidates(candidates, seed=0)
    other = et.shuffled_candidates(candidates, seed=1)

    assert [item.line_id for item in first] == [item.line_id for item in repeated]
    assert [item.line_id for item in first] != [item.line_id for item in candidates]
    assert [item.line_id for item in first] != [item.line_id for item in other]
    assert [item.line_id for item in candidates] == list(range(1, 12))


def test_bundle_input_hash_includes_ground_truth_provenance():
    consensus_record = make_record(1, "Texto identico", 40, 12)
    manual_candidate = replace(
        consensus_record.candidate,
        text_source="manual_review",
        review_status="approved",
        consensus_text="Texto identico",
    )
    manual_record = replace(consensus_record, candidate=manual_candidate)

    assert et._bundle_input_hash([consensus_record]) != et._bundle_input_hash(
        [manual_record]
    )


def make_record(line_id: int, text: str, width: int, height: int) -> et.LineRecord:
    blob = png_bytes(width, height)
    candidate = et.ConsensusCandidate(
        line_id=line_id,
        page_id=f"PG001-{line_id:03d}",
        volume="PG001",
        corpus="PG",
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        consensus_score=3,
        model_count=2,
        engines=("gpt", "migne"),
        best_source_score=1.0,
    )
    return et.LineRecord(
        candidate=candidate,
        png_bytes=blob,
        png_sha256=hashlib.sha256(blob).hexdigest(),
        source_width=width,
        source_height=height,
        source_mode="L",
    )


def test_page_planning_coordinates_page_break_and_oversize():
    config = et.BundleConfig(
        page_width=200,
        page_height=100,
        margin=10,
        line_gap=8,
    )
    records = [make_record(index, f"texto {index}", 100, 20) for index in range(1, 5)]
    pages = et.plan_pages(records, config)

    assert len(pages) == 2
    assert [len(page.lines) for page in pages] == [3, 1]
    first = pages[0].lines[0]
    assert first.box_lbrt == (10, 70, 110, 90)
    assert first.box_lbrt[2] - first.box_lbrt[0] == first.width
    assert first.box_lbrt[3] - first.box_lbrt[1] == first.height

    oversize = make_record(9, "linha muito larga", 190, 20)
    oversize_pages = et.plan_pages([oversize], config)
    assert len(oversize_pages) == 1
    assert oversize_pages[0].width == 256
    assert oversize_pages[0].lines[0].tiff_page == 0


def test_box_graphemes_and_multipage_tiff(tmp_path):
    config = et.BundleConfig(
        page_width=160,
        page_height=64,
        margin=8,
        line_gap=8,
    )
    records = [
        make_record(1, "á b", 80, 20),
        make_record(2, "λόγος", 80, 20),
        make_record(3, "tertia", 80, 20),
    ]
    pages = et.plan_pages(records, config)
    box_path = tmp_path / "bundle.box"
    tif_path = tmp_path / "bundle.tif"
    et.write_box(pages, box_path)
    et.write_tiff(pages, tif_path)

    box_lines = box_path.read_text(encoding="utf-8").splitlines()
    assert box_lines[0].startswith("á ")
    assert box_lines[1].startswith("  ")
    assert "\\t " not in box_path.read_text(encoding="utf-8")
    assert any(line.startswith("\t ") for line in box_path.read_text(encoding="utf-8").split("\n"))
    with tifffile.TiffFile(tif_path) as tiff:
        assert len(tiff.pages) == len(pages)
        assert tiff.pages[0].shape == (64, 160)


def test_mode_defaults_to_legacy(monkeypatch):
    monkeypatch.setattr("sys.argv", ["export_training.py"])
    args = et.parse_args()
    assert args.mode == "lines"
    assert args.bundle_size == 5000
    assert args.min_consensus_score == 3
    assert args.exclude_conflicts is False
    assert args.verbose is False
    assert et.BundleConfig().seed == 0

    monkeypatch.setattr(
        "sys.argv",
        ["export_training.py", "--keep-tiff", "--exclude-conflicts", "--verbose"],
    )
    args = et.parse_args()
    assert args.keep_tiff is True
    assert args.exclude_conflicts is True
    assert args.verbose is True


def test_legacy_export_still_writes_png_and_gt(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE lines (
            id INTEGER PRIMARY KEY,
            volume TEXT,
            agreement_score REAL,
            status TEXT,
            detected_lang TEXT,
            reviewed_text TEXT,
            qwen_text TEXT,
            line_image BLOB
        )
        """
    )
    conn.execute(
        "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            "PG001",
            1.0,
            "inferred",
            "Latin",
            "Digitized by Google",
            "",
            png_bytes(),
        ),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "legacy-out"
    et.export(str(db_path), out, per_volume=150, min_score=1.0, seed=1)

    assert (out / "line_0000001.png").is_file()
    assert (out / "line_0000001.gt.txt").read_text(encoding="utf-8") == "Digitized by Google"
    assert len(json.loads((out / "manifest.json").read_text())) == 1
