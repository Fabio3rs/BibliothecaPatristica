from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.indexing.index_workplan import build_index_workplan


def _write_page(path: Path, header: str, body: str) -> None:
    path.write_text(
        (
            '<pagina estado="com_texto">'
            f'<bloco tipo="cabecalho">{header}</bloco>'
            f'<bloco tipo="texto_principal">{body}</bloco>'
            "</pagina>"
        ),
        encoding="utf-8",
    )


def test_workplan_separates_physical_sequence_from_facing_editorial_pages(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    first = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-001.txt"
    second = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-002.txt"
    third = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-003.txt"
    _write_page(first, "915 INDEX RERUM 916", "AARON. I, 10")
    _write_page(second, "917 INDEX RERUM 918", "ABEL. I, 11")
    _write_page(third, "919 TEXTUS 920", "body")

    filtered = {
        "source": "test",
        "candidate_sections": [
            {"heading": "INDEX RERUM", "marker": "INDEX RERUM", "file": str(first)},
            {"heading": "917 INDEX RERUM 918", "marker": "INDEX RERUM", "file": str(second)},
        ],
    }
    workplan = build_index_workplan(
        volume_id="PL001",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "chunks",
        max_files_per_chunk=1,
        chunk_overlap=0,
    )

    assert len(workplan["sections"]) == 1
    assert len(workplan["chunks"]) == 2
    evidence = next(
        item
        for item in workplan["sections"][0]["header_evidence"]
        if item["physical_file_seq"] == 1
    )
    assert evidence["physical_file_seq"] == 1
    assert evidence["editorial_page_candidates"] == [915, 916]
    assert "never an editorial page" in workplan["numbering_glossary"]["physical_file"]
    assert "non-physical source/reference data" in workplan["numbering_glossary"]["cited_reference"]
    assert "NUMBER PAGE-TITLE NUMBER+1" in workplan["numbering_glossary"]["editorial_page"]
    assert workplan["pipeline_purpose"].startswith("Read closing alphabetical")
    assert workplan["extraction_direction"] == "physical_end_to_start"
    assert workplan["chunks"][0]["physical_files"] == [str(second)]
    assert workplan["chunks"][1]["physical_files"] == [str(first)]
    estimate = workplan["chunks"][0]["deterministic_entry_estimate"]
    assert estimate["estimated_entry_count"]["likely"] >= 1


def test_workplan_fallback_region_is_low_confidence(tmp_path: Path) -> None:
    source_root = tmp_path / "PO001" / "text"
    source_root.mkdir(parents=True)
    page = source_root / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb-001.txt"
    _write_page(page, "1", "candidate")

    workplan = build_index_workplan(
        volume_id="PO001",
        source_root=source_root,
        collection="PO",
        filtered_pages={"source": "fallback", "candidate_files": [str(page)]},
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert workplan["sections"][0]["marker"] == "fallback_candidate_region"
    assert workplan["sections"][0]["confidence"] == "low"


def test_workplan_normalizes_absolute_artifact_paths_with_relative_source_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source_root = Path("teste/PO001/text")
    source_root.mkdir(parents=True)
    page = (tmp_path / source_root / "cccccccc-cccc-cccc-cccc-cccccccccccc-176.txt").resolve()
    _write_page(page, "175 INDEX 176", "AARON. 12")

    workplan = build_index_workplan(
        volume_id="PO001",
        source_root=source_root,
        collection="PO",
        filtered_pages={"source": "test", "candidate_files": [str(page)]},
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert workplan["manifest_status"] == "candidate"
    assert workplan["chunks"][0]["physical_files"] == [str(page)]
    assert workplan["scan_diagnostics"]["planned_chunk_count"] == 1


def test_general_and_alphabetical_workplans_use_opposite_physical_directions(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL002" / "text"
    source_root.mkdir(parents=True)
    pages = [
        source_root / f"dddddddd-dddd-dddd-dddd-dddddddddddd-{number:03d}.txt"
        for number in range(1, 4)
    ]
    for number, page in enumerate(pages, start=1):
        _write_page(page, str(100 + number), f"LEMMA {number} .... {number}")
    filtered = {
        "candidate_sections": [
            {"heading": "INDEX", "marker": "INDEX", "file": str(page)} for page in pages
        ]
    }

    general = build_index_workplan(
        volume_id="PL002",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "general",
        max_files_per_chunk=1,
        chunk_overlap=0,
    )
    alphabetical = build_index_workplan(
        volume_id="PL002",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "alphabetical",
        max_files_per_chunk=1,
        chunk_overlap=0,
    )

    assert [item["physical_files"][0] for item in general["chunks"]] == [
        str(page) for page in pages
    ]
    assert [item["physical_files"][0] for item in alphabetical["chunks"]] == [
        str(page) for page in reversed(pages)
    ]
    assert general["pipeline_purpose"].startswith("Read opening/front-matter")
    assert alphabetical["pipeline_purpose"].startswith("Read closing alphabetical")
    assert general["chunks"][0]["chunk_contract_version"] == 3
    assert general["chunks"][0]["target_search_policy"]["enabled"] is True
    assert general["chunk_policy"]["target_search"]["may_inspect_unowned_files"] is True
    assert alphabetical["chunks"][0]["chunk_contract_version"] == 2
    assert alphabetical["chunks"][0]["target_search_policy"]["enabled"] is False


def test_workplan_extends_heading_through_contiguous_candidate_continuations(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL003" / "text"
    source_root.mkdir(parents=True)
    pages = [
        source_root / f"eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee-{number:03d}.txt"
        for number in range(1, 7)
    ]
    for number, page in enumerate(pages, start=1):
        _write_page(page, str(200 + number), f"entry {number} .... {number}")
    filtered = {
        "candidate_files": [str(page) for page in pages[1:]],
        "candidate_sections": [
            {"heading": "INDEX RERUM", "marker": "INDEX RERUM", "file": str(pages[3])}
        ],
    }

    workplan = build_index_workplan(
        volume_id="PL003",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "chunks",
        max_files_per_chunk=10,
    )

    # Candidate files before the heading are scanner context, not index continuation.
    assert workplan["sections"][0]["physical_file_low"] == str(pages[3])
    assert workplan["sections"][0]["physical_file_high"] == str(pages[5])
    assert workplan["chunks"][0]["physical_files"] == [
        str(pages[5]),
        str(pages[4]),
        str(pages[3]),
    ]


def test_workplan_extends_section_beyond_prefilter_neighbors(tmp_path: Path) -> None:
    source_root = tmp_path / "PL006" / "text"
    source_root.mkdir(parents=True)
    pages = [
        source_root / f"acacacac-acac-acac-acac-acacacacacac-{number:03d}.txt"
        for number in range(1, 6)
    ]
    _write_page(pages[0], "1 INDEX CAPITUM 2", "AARON .... 10")
    _write_page(pages[1], "3 INDEX CAPITUM 4", "ABEL .... 11")
    _write_page(pages[2], "5 INDEX CAPITUM 6", "ABRAHAM .... 12")
    _write_page(pages[3], "7 INDEX CAPITUM 8", "ADAM .... 13")
    _write_page(pages[4], "9 TRACTATUS 10", "Prose without index references")

    workplan = build_index_workplan(
        volume_id="PL006",
        source_root=source_root,
        collection="PL",
        filtered_pages={
            "candidate_files": [str(pages[0])],
            "candidate_sections": [
                {
                    "heading": "INDEX CAPITUM",
                    "marker": "INDEX CAPITUM",
                    "file": str(pages[0]),
                }
            ],
        },
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert workplan["sections"][0]["physical_files"] == [
        str(page) for page in pages[:4]
    ]


def test_structural_workplan_stops_after_dense_list_continuation(tmp_path: Path) -> None:
    source_root = tmp_path / "PL177" / "text"
    source_root.mkdir(parents=True)
    first = source_root / "pl-195.txt"
    continuation = source_root / "pl-196.txt"
    body = source_root / "pl-197.txt"
    _write_page(
        first,
        "INCIPIUNT CAPITULA",
        "\n" + "\n".join(f"CAP. {value}. Titulus" for value in range(1, 16)),
    )
    _write_page(
        continuation,
        "APPENDIX AD OPERA",
        "\n" + "\n".join(f"CAP. {value}. Titulus" for value in range(16, 58)),
    )
    _write_page(
        body,
        "APPENDIX AD OPERA",
        "CAP. I. Titulus\nLong body prose without a structural list.",
    )

    workplan = build_index_workplan(
        volume_id="PL177",
        source_root=source_root,
        collection="PL",
        filtered_pages={
            "candidate_sections": [
                {
                    "heading": "INCIPIUNT CAPITULA",
                    "marker": "INCIPIUNT CAPITULA",
                    "file": str(first),
                }
            ]
        },
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert workplan["sections"][0]["physical_files"] == [
        str(first),
        str(continuation),
    ]


def test_chunk_overlap_is_context_not_duplicate_entry_ownership(tmp_path: Path) -> None:
    source_root = tmp_path / "PL004" / "text"
    source_root.mkdir(parents=True)
    pages = [
        source_root / f"ffffffff-ffff-ffff-ffff-ffffffffffff-{number:03d}.txt"
        for number in range(1, 6)
    ]
    for number, page in enumerate(pages, start=1):
        _write_page(page, str(300 + number), f"LEMMA {number} .... {number}")
    filtered = {
        "candidate_files": [str(page) for page in pages],
        "candidate_sections": [
            {"heading": "INDEX", "marker": "INDEX", "file": str(pages[0])}
        ],
    }

    workplan = build_index_workplan(
        volume_id="PL004",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "chunks",
        max_files_per_chunk=3,
        chunk_overlap=1,
    )

    owned = [
        path for chunk in workplan["chunks"] for path in chunk["physical_files"]
    ]
    assert owned == [str(page) for page in pages]
    assert len(owned) == len(set(owned))
    assert workplan["chunks"][1]["overlap_context_files"] == [str(pages[2])]


def test_distinct_headings_on_same_physical_scan_share_one_candidate_region(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL005" / "text"
    source_root.mkdir(parents=True)
    page = source_root / "abababab-abab-abab-abab-abababababab-010.txt"
    _write_page(page, "100 INDEX RERUM 101", "INDEX TOMI PRIMI\nAARON .... 15")
    filtered = {
        "candidate_files": [str(page)],
        "candidate_sections": [
            {
                "heading": "100 INDEX RERUM 101",
                "marker": "INDEX RERUM",
                "file": str(page),
            },
            {
                "heading": "INDEX TOMI PRIMI",
                "marker": "INDEX",
                "file": str(page),
            },
        ],
    }

    workplan = build_index_workplan(
        volume_id="PL005",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert len(workplan["sections"]) == 1
    assert len(workplan["chunks"]) == 1
    assert workplan["sections"][0]["marker_variants"] == ["INDEX RERUM", "INDEX"]
    assert workplan["sections"][0]["heading_variants"] == [
        "100 INDEX RERUM 101",
        "INDEX TOMI PRIMI",
    ]


def test_alternating_running_index_headers_form_one_section_with_bounded_context(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL060" / "text"
    source_root.mkdir(parents=True)
    pages = [
        source_root / f"cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd-{number:03d}.txt"
        for number in range(1, 13)
    ]
    markers = ("INDEX", "INDICES", "INDEX RERUM")
    for number, page in enumerate(pages, start=1):
        marker = markers[(number - 1) % len(markers)]
        _write_page(page, f"{number * 2 - 1} {marker} {number * 2}", f"LEMMA {number} .... {number}")
    filtered = {
        "candidate_sections": [
            {
                "heading": f"{number * 2 - 1} {markers[(number - 1) % 3]} {number * 2}",
                "marker": markers[(number - 1) % 3],
                "file": str(page),
            }
            for number, page in enumerate(pages, start=1)
        ]
    }

    workplan = build_index_workplan(
        volume_id="PL060",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "chunks",
        max_files_per_chunk=2,
        chunk_overlap=0,
        context_window=1,
    )

    assert len(workplan["sections"]) == 1
    assert len(workplan["chunks"]) == 6
    owned = [
        path for chunk in workplan["chunks"] for path in chunk["physical_files"]
    ]
    assert len(owned) == len(set(owned)) == len(pages)
    assert all(len(chunk["context_files"]) <= 4 for chunk in workplan["chunks"])


def test_ordo_rerum_is_mid_file_stop_boundary_not_alphabetical_section(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL072" / "text"
    source_root.mkdir(parents=True)
    before = source_root / "dededede-dede-dede-dede-dededededede-575.txt"
    mixed = source_root / "dededede-dede-dede-dede-dededededede-576.txt"
    after = source_root / "dededede-dede-dede-dede-dededededede-577.txt"
    _write_page(before, "1101 INDEX RERUM 1102", "TABULAE .... 110")
    _write_page(
        mixed,
        "1103 INDEX RERUM 1104",
        "VENERIUS .... 111\nORDO RERUM\nJOANNES PAPA III .... 1",
    )
    _write_page(after, "1105 ORDO RERUM 1106", "OPUSCULA .... 2")
    filtered = {
        "candidate_sections": [
            {
                "heading": "INDEX RERUM",
                "marker": "INDEX RERUM",
                "file": str(before),
                "line": 1,
            },
            {
                "heading": "ORDO RERUM",
                "marker": "ORDO RERUM",
                "file": str(mixed),
                "line": 2,
            },
            {
                "heading": "1105 ORDO RERUM 1106",
                "marker": "ORDO RERUM",
                "file": str(after),
                "line": 1,
            },
        ]
    }

    workplan = build_index_workplan(
        volume_id="PL072",
        source_root=source_root,
        collection="PL",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "chunks",
        max_files_per_chunk=5,
        chunk_overlap=0,
    )

    assert len(workplan["sections"]) == 1
    assert workplan["sections"][0]["physical_files"] == [str(mixed), str(before)]
    assert workplan["sections"][0]["marker"] == "INDEX RERUM"
    assert workplan["scan_diagnostics"]["semantic_boundary_count"] == 2
    assert workplan["semantic_boundaries"][0]["physical_file"] == str(mixed)
    assert workplan["semantic_boundaries"][0]["line"] == 2
    assert workplan["chunks"][0]["semantic_stop_boundaries"][0]["physical_file"] == str(mixed)
    assert str(after) not in {
        path
        for chunk in workplan["chunks"]
        for path in chunk["physical_files"]
    }


def test_ordo_rerum_remains_a_section_candidate_outside_alphabetical_pg_pl(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PO001" / "text"
    source_root.mkdir(parents=True)
    page = source_root / "efefefef-efef-efef-efef-efefefefefef-001.txt"
    _write_page(page, "ORDO RERUM", "OPUSCULA .... 2")
    filtered = {
        "candidate_sections": [
            {
                "heading": "ORDO RERUM",
                "marker": "ORDO RERUM",
                "file": str(page),
            }
        ]
    }

    workplan = build_index_workplan(
        volume_id="PO001",
        source_root=source_root,
        collection="PO",
        filtered_pages=filtered,
        pipeline_kind="alphabetical",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert len(workplan["sections"]) == 1
    assert workplan["semantic_boundaries"] == []


def test_general_pg_pl_workplan_retains_ordo_rerum_as_its_own_section(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PL024" / "text"
    source_root.mkdir(parents=True)
    page = source_root / "fafafafa-fafa-fafa-fafa-fafafafafafa-001.txt"
    _write_page(page, "ORDO RERUM", "OPUSCULA .... 2")

    workplan = build_index_workplan(
        volume_id="PL024",
        source_root=source_root,
        collection="PL",
        filtered_pages={
            "candidate_sections": [
                {
                    "heading": "ORDO RERUM",
                    "marker": "ORDO RERUM",
                    "file": str(page),
                }
            ]
        },
        pipeline_kind="general",
        chunk_output_dir=tmp_path / "chunks",
    )

    assert len(workplan["sections"]) == 1
    assert workplan["sections"][0]["marker"] == "ORDO RERUM"
    assert workplan["semantic_boundaries"] == []
