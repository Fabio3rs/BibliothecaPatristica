"""Physical-layout hypotheses must conserve OCR data and not force two columns."""
from collections import Counter
from pathlib import Path
from hashlib import sha256
import json
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_ocr.migne_hints import FORMATS, analyse_tsv, render_hint

FIXTURES = Path(__file__).parent / "fixtures/ocr_diff_v2"
HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def make_tsv(rows):
    out = HEADER + "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t\n"
    for line, (top, words) in enumerate(rows, 1):
        for index, (left, width, text, conf) in enumerate(words, 1):
            out += f"5\t1\t1\t1\t{line}\t{index}\t{left}\t{top}\t{width}\t10\t{conf}\t{text}\n"
    return out


def column_rows(n, start=100, count=12):
    width = 800 / n
    return [
        (start + i * 20, [w for c in range(n) for w in (
            (int(100 + c * width), int(width * .42), f"prima{c}_{i}", 90),
            (int(100 + c * width + width * .42 + 6), int(width * .42), f"ultima{c}_{i}-", 90))])
        for i in range(count)]


@pytest.mark.parametrize("columns", [1, 2, 3])
def test_column_counts_and_every_word_conserved(columns):
    result = analyse_tsv(make_tsv(column_rows(columns)), (1000, 1000))
    assert result["stats"]["column_counts"] == [columns]
    assert Counter(w["id"] for w in result["words"]) == Counter(i for b in result["blocks"] for i in b["word_ids"])
    assert all(0 <= v <= 1000 for b in result["blocks"] for v in b["bbox"])


def test_column_count_changes_within_page():
    rows = column_rows(1, 50, 8) + column_rows(2, 300, 10) + column_rows(3, 600, 12)
    result = analyse_tsv(make_tsv(rows), (1000, 1000))
    assert result["stats"]["column_counts"] == [1, 2, 3]
    for y, columns in [(110, 1), (400, 2), (720, 3)]:
        band = next(b for b in result["bands"] if b["bbox"][1] <= y <= b["bbox"][3])
        assert band["columns"] == columns


def test_hyphens_support_narrow_corridor_and_markers_are_preserved():
    rows = [(100 + i * 20, [(100, 390, "verbum-", 95), (490, 5, "A", 95),
                            (496, 390, "alterum", 95)]) for i in range(9)]
    result = analyse_tsv(make_tsv(rows), (1000, 1000))
    assert result["stats"]["column_counts"] == [2]
    assert any(e["hyphen_rows"] >= 3 for b in result["bands"] for e in b["boundary_evidence"])
    assert sum(len(b["word_ids"]) for b in result["blocks"] if b["kind_hint"] == "margin") == 9


def test_centered_heading_and_low_confidence_text_survive():
    rows = column_rows(2, 100, 10) + [(340, [(380, 100, "COTELERII", 90), (490, 90, "NOTÆ.", 90)]),
                                    (400, [(110, 300, "dubium<&>", 0)])]
    result = analyse_tsv(make_tsv(rows), (1000, 1000))
    assert any(b["text"] == "COTELERII NOTÆ." and b["kind_hint"] == "heading" for b in result["blocks"])
    xml = ET.fromstring(render_hint(result, "micro_xml"))
    assert "dubium<&>" in "".join(xml.itertext())


def test_blank_tsv_and_invalid_geometry():
    empty = analyse_tsv(make_tsv([]), (1000, 1000))
    assert empty["blocks"] == [] and empty["warnings"]
    with pytest.raises(ValueError, match="Dimensões"):
        analyse_tsv(make_tsv([]), (2000, 2000))
    with pytest.raises(ValueError, match="Unknown"):
        render_hint(empty, "other")


def test_pg001_638_splits_merged_tesseract_lines_without_losing_tokens():
    raw = (FIXTURES / "PG001_638_migne_raw.tsv").read_text()
    layout = analyse_tsv(raw, (2480, 3509))
    assert layout["stats"]["source_words"] == layout["stats"]["assigned_words"] == 939
    right = next(b for b in layout["blocks"] if b["text"].startswith("ditor exigit"))
    assert "adulteros et homicidas" in right["lines"][1]["text"]
    for format_name in FORMATS:
        hint = render_hint(layout, format_name)
        assert hint.count("adulteros") == 1 and hint.count("homicidas") == 1
        assert len(hint) < len(raw) / 4
    assert len(render_hint(layout, "block_tsv")) < 1.12 * len(render_hint(layout, "raw_text"))


@pytest.mark.parametrize("page,expected,words", [("018", [1, 2, 3], 778), ("129", [1, 2], 945)])
def test_real_migne_geometry_controls(page, expected, words):
    fixtures = Path(__file__).parent / "fixtures/migne_hints"
    manifest = json.loads((fixtures / "manifest.json").read_text())
    entry = next(p for p in manifest["pages"] if p["page"] == f"PG001:{int(page)}")
    raw = (fixtures / entry["tsv_file"]).read_bytes()
    assert sha256(raw).hexdigest() == entry["tsv_sha256"]
    layout = analyse_tsv(raw.decode(), tuple(entry["image_size"]))
    assert layout["stats"]["column_counts"] == expected
    assert layout["stats"]["assigned_words"] == layout["stats"]["source_words"] == words
    if page == "018":
        for y, columns in [(100, 1), (280, 2), (600, 3)]:
            band = next(b for b in layout["bands"] if b["bbox"][1] <= y <= b["bbox"][3])
            assert band["columns"] == columns
        assert any(e["persistent_narrow_corridor"] for b in layout["bands"] for e in b["boundary_evidence"])
