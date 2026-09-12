from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.indexing.index_payload_evidence import verify_index_payload_evidence


def _write_page(path: Path, body: str) -> None:
    path.write_text(
        f'<pagina estado="com_texto"><bloco tipo="texto_principal">{body}</bloco></pagina>',
        encoding="utf-8",
    )


def test_verifier_finds_exact_general_entry_and_keeps_physical_anchor(tmp_path: Path) -> None:
    source_root = tmp_path / "PO001" / "text"
    source_root.mkdir(parents=True)
    page = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-009.txt"
    _write_page(page, "Aaron consecratio. I, 519")
    payload = {
        "volume": {"volume_id": "PO001", "source_root": str(source_root)},
        "works": [],
        "sections": [
            {
                "section_key": "PO001:index:1",
                "file_start": str(page),
                "file_end": str(page),
                "entries": [{"entry_raw": "Aaron consecratio. I, 519", "target_raw": "Aaron consecratio"}],
            }
        ],
        "notes": [],
    }

    report = verify_index_payload_evidence(payload, sample_size=None)

    assert report["counts"] == {"exact": 1}
    assert report["results"][0]["best_file"] == str(page)


def test_verifier_accepts_fuzzy_ocr_and_flags_collapsed_entries(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    page = source_root / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb-001.txt"
    _write_page(page, "AAR0N consecratio. I, 519")
    payload = {
        "schema_version": 1,
        "volume": {"volume_id": "PL001", "source_root": str(source_root)},
        "sections": [
            {
                "section_key": "PL001:index:1",
                "file_start": str(page),
                "file_end": str(page),
            }
        ],
        "entries": [
            {
                "entry_key": "PL001:entry:1",
                "section_key": "PL001:index:1",
                "lemma_raw": "AARON consecratio",
                "entry_raw": (
                    "AARON consecratio. I, 519 ABINADAB imperium. I, 79 "
                    "ABRA unica filia. II, 325"
                ),
            }
        ],
    }

    report = verify_index_payload_evidence(payload, sample_size=None, fuzzy_threshold=0.7)

    assert report["counts"] == {"fuzzy": 1}
    assert report["segmentation_suspect_count"] == 1


def test_verifier_joins_declared_physical_sources_for_cross_page_entry(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_root.mkdir(parents=True)
    left = source_root / "cccccccc-cccc-cccc-cccc-cccccccccccc-010.txt"
    right = source_root / "cccccccc-cccc-cccc-cccc-cccccccccccc-011.txt"
    _write_page(left, "AARON. De interpreta-")
    _write_page(right, "tione nominis, 15")
    entry_raw = "AARON. De interpretatione nominis, 15"
    payload = {
        "schema_version": 1,
        "volume": {"volume_id": "PG001", "source_root": str(source_root)},
        "sections": [
            {
                "section_key": "PG001:index:1",
                "file_start": str(left),
                "file_end": str(right),
            }
        ],
        "entries": [
            {
                "entry_key": "PG001:entry:1",
                "section_key": "PG001:index:1",
                "lemma_raw": entry_raw,
                "entry_raw": entry_raw,
                "raw_json": {"source_files": [str(left), str(right)]},
            }
        ],
    }

    report = verify_index_payload_evidence(payload, sample_size=None)

    assert report["counts"] == {"exact_cross_page": 1}
    assert report["verified_ratio"] == 1.0
    assert report["declared_cross_page_entry_count"] == 1
    assert report["cross_page_entry_raw_not_found_count"] == 0
    assert report["results"][0]["cross_page_entry_raw_method"] == "exact_cross_page"


def test_verifier_uses_literal_row_for_inherited_lemma_and_full_section_range(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PO001" / "text"
    source_root.mkdir(parents=True)
    first = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-010.txt"
    second = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-011.txt"
    _write_page(first, "EXODUS XXXII")
    _write_page(second, "— — 15. . . . . . 76, 10")
    payload = {
        "schema_version": 1,
        "volume": {"volume_id": "PO001", "source_root": str(source_root)},
        "sections": [
            {
                "section_key": "PO001:index:1",
                "file_start": str(first),
                "file_end": str(second),
            }
        ],
        "entries": [
            {
                "entry_key": "PO001:entry:1",
                "section_key": "PO001:index:1",
                "section_start_file": str(first),
                "lemma_raw": "EXOD. XXXII, 15",
                "entry_raw": "— — 15. . . . . . 76, 10",
                "raw_json": {
                    "inheritance": {
                        "book_inherited": True,
                        "chapter_inherited": True,
                    }
                },
            }
        ],
    }

    report = verify_index_payload_evidence(payload, sample_size=None)

    assert report["counts"] == {"exact": 1}
    assert report["results"][0]["best_file"] == str(second)
    assert report["results"][0]["query_raw"].startswith("— — 15")


def test_verifier_suggests_extending_scope_for_exact_neighbor(tmp_path: Path) -> None:
    source_root = tmp_path / "PG001" / "text"
    source_root.mkdir(parents=True)
    declared = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-010.txt"
    neighbor = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-011.txt"
    _write_page(declared, "INDEX ANALYTICUS")
    _write_page(neighbor, "Hieremias, 83.")
    payload = {
        "schema_version": 1,
        "volume": {"volume_id": "PG001", "source_root": str(source_root)},
        "sections": [
            {
                "section_key": "PG001:index:1",
                "file_start": str(declared),
                "file_end": str(declared),
            }
        ],
        "entries": [
            {
                "entry_key": "PG001:entry:1",
                "section_key": "PG001:index:1",
                "lemma_raw": "Hieremias",
                "entry_raw": "Hieremias, 83.",
            }
        ],
    }

    report = verify_index_payload_evidence(payload, sample_size=None)

    result = report["results"][0]
    assert result["method"] == "exact_neighbor_outside_declared_scope"
    assert result["best_file"] == str(neighbor)
    assert result["scope_repair"]["action"] == "extend_file_end"
