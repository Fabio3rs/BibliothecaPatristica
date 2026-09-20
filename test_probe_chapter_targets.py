from scripts.probe_chapter_targets import (
    extract_entry_ordinals,
    entry_ordinal_source,
    existing_path,
    find_latin_chapter_headings,
    greek_numeral_to_int,
    is_retrospective_section,
    locate_section,
    normalize_ocr_evidence,
    normalized_text_similarity,
    ordered_ocr_search_text,
    roman_to_int,
    section_body_bounds,
    select_best_marker_run,
    split_local_marker_runs,
    split_expected_ref_runs,
)


def test_existing_path_resolves_payload_basename_against_source_root(tmp_path):
    source_root = tmp_path / "volume" / "text"
    source_root.mkdir(parents=True)
    physical_file = source_root / "page-001.txt"
    physical_file.write_text("CAPUT I", encoding="utf-8")

    assert existing_path("page-001.txt", tmp_path, source_root) == physical_file.resolve()


def test_roman_to_int_rejects_noncanonical_values():
    assert roman_to_int("LIX") == 59
    assert roman_to_int("IIII") is None
    assert roman_to_int("ABC") is None


def test_extract_entry_ordinals_handles_grouped_chapters():
    ordinal_raw, ordinals = extract_entry_ordinals(
        {
            "entry_raw": "CAPP. IV, V, VI. Quanta mala ex hoc fonte profluxerint.",
            "raw_json": {"ordinal_raw": "CAPP. IV, V, VI."},
        }
    )

    assert ordinal_raw == "CAPP. IV, V, VI."
    assert ordinals == [4, 5, 6]


def test_extract_entry_ordinals_falls_back_to_entry_raw():
    _, ordinals = extract_entry_ordinals(
        {"entry_raw": "Cap. I. Christus Deus; magnifice de Christo sentiendum."}
    )

    assert ordinals == [1]


def test_extract_entry_ordinals_accepts_arabic_chapter_label():
    marker, ordinals = extract_entry_ordinals(
        {"entry_raw": "Caput 12. De disciplina ecclesiastica."}
    )

    assert marker == "Caput 12."
    assert ordinals == [12]


def test_extract_entry_ordinals_accepts_bare_arabic_prefix():
    marker, ordinals = extract_entry_ordinals(
        {"entry_raw": "12. De disciplina ecclesiastica."}
    )

    assert marker == "12."
    assert ordinals == [12]


def test_extract_entry_ordinals_accepts_greek_numeral_prefix():
    assert greek_numeral_to_int("ιγ΄") == 13
    assert greek_numeral_to_int("ς΄") == 6
    assert greek_numeral_to_int("ις΄") == 16
    marker, ordinals = extract_entry_ordinals(
        {"entry_raw": "ιγ'. Περὶ τοῦ λόγου."}
    )

    assert marker == "ιγ'."
    assert ordinals == [13]


def test_extract_entry_ordinals_accepts_trailing_chapter_references():
    marker, ordinals = extract_entry_ordinals(
        {
            "entry_raw": (
                "Argumentum primum exponitur. Cap. 1, 2. — "
                "Deinde adversarii refutantur. Cap. 2-4."
            )
        }
    )

    assert marker == "Cap. 1, 2 | Cap. 2-4"
    assert ordinals == [1, 2, 3, 4]
    assert entry_ordinal_source(
        {"entry_raw": "Argumentum primum exponitur. Cap. 1, 2."}
    ) == "trailing_chapter_reference"


def test_extract_entry_ordinals_does_not_parse_capite_or_capitula_as_cap_abbreviation():
    assert extract_entry_ordinals(
        {"entry_raw": "In epistolae capite ostenditur quae mandavit."}
    )[1] == []
    assert extract_entry_ordinals(
        {"entry_raw": "Item ejusdem sancti Gregorii papa capitula vi."}
    )[1] == []


def test_find_latin_chapter_headings_ignores_index_abbreviation():
    text = """CAPP. I et II. Argumentum primum.
CAPUT I.
Ecclesia Dei quae incolit Romam.
CAPUT SECUNDUM.
"""

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [1, 2]


def test_find_latin_chapter_headings_accepts_common_variants():
    text = """609 CAPUT PRIMUM¹.
CAP. II.
INTERROGATIO III ET RESPONSIO.
IV. Peroratio.
"""

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [1, 2, 3, 4]


def test_find_latin_chapter_headings_accepts_arabic_and_greek_labels():
    text = "CAPUT 12. Argumentum.\nΚΕΦ. ΙΓ΄. Περὶ τοῦ λόγου."

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [12, 13]


def test_parallel_latin_and_greek_chapter_runs_are_scored_separately():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": title}
        for ordinal, title in ((1, "De Magis"), (2, "De pueris interfectis"))
    ]
    hits = [
        {"ordinal": 1, "style": "cap", "position": [1, 1], "title_raw": "De Magis", "qualified": False},
        {"ordinal": 2, "style": "cap", "position": [2, 1], "title_raw": "De pueris interfectis", "qualified": False},
        {"ordinal": 1, "style": "kephalaion", "position": [1, 2], "title_raw": "Περὶ τῶν Μάγων", "qualified": False},
        {"ordinal": 2, "style": "kephalaion", "position": [2, 2], "title_raw": "Περὶ τῶν παίδων", "qualified": False},
    ]

    selected = select_best_marker_run(expected, hits)

    assert selected["family"] == "chapter_label"
    assert selected["coverage"] == 1.0
    assert [hit["style"] for hit in selected["path_hits"]] == ["cap", "cap"]


def test_find_latin_chapter_headings_accepts_gendered_word_ordinal():
    text = "568 INTERROGATIO PRIMA.\nArgumentum."

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [1]


def test_find_latin_chapter_headings_does_not_join_separate_ocr_blocks():
    text = "divinam Scri-\nVII. Cæterum cum ita nos ab initio cepisset"

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [7]


def test_find_latin_chapter_headings_rejects_inline_chapter_citation():
    text = "Expeditio regis, ut supra dictum est cap. X, ad finem pervenit."

    assert find_latin_chapter_headings(text) == []


def test_find_latin_chapter_headings_rejects_lowercase_page_citation_at_line_start():
    text = "cap. 1 p. 93"

    assert find_latin_chapter_headings(text) == []


def test_find_latin_chapter_headings_accepts_numbered_paragraph_marker():
    text = "3. (CAP. I.) Anno itaque Verbi incarnati."

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [1]


def test_ordered_ocr_search_text_preserves_interleaved_header_block(tmp_path):
    page = tmp_path / "page-001.txt"
    page.write_text(
        """<pagina>
<bloco tipo="texto_principal">Finis prioris.</bloco>
<bloco tipo="cabecalho">CAPUT PRIMUM.</bloco>
<bloco tipo="texto_principal">Argumentum novi libri.</bloco>
</pagina>""",
        encoding="utf-8",
    )

    assert ordered_ocr_search_text(page).splitlines() == [
        "Finis prioris.",
        "CAPUT PRIMUM.",
        "Argumentum novi libri.",
    ]


def test_section_body_bounds_uses_associated_work_start_and_next_work(tmp_path):
    files = []
    for number in range(1, 6):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    work = {"work_key": "work-1", "start_file": str(files[1]), "end_file": None}
    payload = {
        "works": [work, {"work_key": "work-2", "start_file": str(files[4])}],
        "sections": [],
    }
    section = {
        "section_key": "section-1",
        "work_key": "work-1",
        "file_start": str(files[0]),
        "file_end": str(files[0]),
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (1, 4)


def test_section_body_bounds_includes_shared_start_file(tmp_path):
    files = []
    for number in range(1, 4):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    work = {"work_key": "work-1", "start_file": str(files[1]), "end_file": None}
    payload = {
        "works": [work, {"work_key": "work-2", "start_file": str(files[1])}],
        "sections": [],
    }
    section = {
        "section_key": "section-1",
        "work_key": "work-1",
        "file_start": str(files[0]),
        "file_end": str(files[0]),
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (1, 2)


def test_section_body_bounds_prefers_direct_work_front_evidence_over_repaired_start(tmp_path):
    files = []
    for number in range(1, 7):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    section = {
        "section_key": "section-1",
        "scope_kind": "work_front",
        "work_key": "work-1",
        "file_start": str(files[0]),
        "file_end": str(files[0]),
    }
    payload = {
        "works": [
            {
                "work_key": "work-1",
                "start_file": str(files[2]),
                "source_section_key": "section-1",
            },
            {"work_key": "work-2", "start_file": str(files[5])},
        ],
        "sections": [section],
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (0, 5)


def test_section_body_bounds_ignores_unassociated_fragment_inside_open_work(tmp_path):
    files = []
    for number in range(1, 7):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    section = {
        "section_key": "section-1",
        "scope_kind": "work_front",
        "work_key": "work-1",
        "file_start": str(files[0]),
        "file_end": str(files[0]),
    }
    payload = {
        "works": [
            {
                "work_key": "work-1",
                "start_file": str(files[0]),
                "source_section_key": "section-1",
            },
            {"work_key": "work-2", "start_file": str(files[5])},
        ],
        "sections": [
            section,
            {"section_key": "narrative-fragment", "file_start": str(files[2])},
        ],
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (0, 5)


def test_section_body_bounds_ignores_shared_start_behind_section_end(tmp_path):
    files = []
    for number in range(1, 7):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    work = {"work_key": "work-1", "start_file": str(files[0]), "end_file": None}
    payload = {
        "works": [
            work,
            {"work_key": "duplicate-container", "start_file": str(files[0])},
            {"work_key": "work-2", "start_file": str(files[5])},
        ],
        "sections": [],
    }
    section = {
        "section_key": "section-1",
        "work_key": "work-1",
        "file_start": str(files[1]),
        "file_end": str(files[2]),
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (2, 5)


def test_retrospective_section_detection_uses_scope_or_stable_key():
    assert is_retrospective_section({"scope_kind": "volume_end"})
    assert is_retrospective_section({"section_key": "PL001:section:volume_end:index"})
    assert not is_retrospective_section({"scope_kind": "work_front"})


def test_locate_section_rejects_low_similarity_prefix_run(tmp_path):
    files = []
    index_page = tmp_path / "page-001.txt"
    index_page.write_text("INDEX CAPITUM", encoding="utf-8")
    files.append(index_page)
    for number in range(1, 6):
        path = tmp_path / f"page-{number + 1:03d}.txt"
        path.write_text(
            f"<pagina><bloco tipo=\"texto_principal\">CAPUT {number}. Materia aliena.</bloco></pagina>",
            encoding="utf-8",
        )
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    section = {
        "section_key": "section-1",
        "scope_kind": "work_front",
        "work_key": "work-1",
        "file_start": str(index_page),
        "file_end": str(index_page),
        "entries": [
            {
                "entry_key": f"entry-{number}",
                "entry_raw": f"Caput {number}. Argumentum proprium distinctum.",
            }
            for number in range(1, 6)
        ],
    }
    payload = {
        "works": [
            {
                "work_key": "work-1",
                "start_file": str(files[1]),
                "end_file": str(files[-1]),
            }
        ],
        "sections": [section],
    }

    result = locate_section(section, payload, files, positions, tmp_path)

    assert result["proposed_marker_coverage"] == 1.0
    assert result["marker_run_accepted"] is False
    assert result["rejection_reason"] == "weak_title_evidence_for_non_trailing_ordinal_run"


def test_section_body_bounds_includes_next_section_boundary_file(tmp_path):
    files = []
    for number in range(1, 6):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    work = {
        "work_key": "work-1",
        "start_file": str(files[0]),
        "end_file": str(files[4]),
    }
    section = {
        "section_key": "section-1",
        "work_key": "work-1",
        "file_start": str(files[0]),
        "file_end": str(files[0]),
    }
    payload = {
        "works": [work],
        "sections": [
            section,
            {"section_key": "section-2", "file_start": str(files[3])},
        ],
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (0, 4)


def test_section_body_bounds_searches_closed_work_for_terminal_index(tmp_path):
    files = []
    for number in range(1, 7):
        path = tmp_path / f"page-{number:03d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    positions = {path.resolve(): index for index, path in enumerate(files)}
    section = {
        "section_key": "section-1",
        "scope_kind": "work_front",
        "work_key": "work-1",
        "file_start": str(files[5]),
        "file_end": str(files[5]),
    }
    payload = {
        "works": [
            {
                "work_key": "work-1",
                "start_file": str(files[0]),
                "end_file": str(files[5]),
                "source_section_key": "section-1",
            }
        ],
        "sections": [section],
    }

    assert section_body_bounds(section, payload, files, positions, tmp_path) == (0, 6)


def test_extract_entry_ordinals_does_not_parse_liber_as_roman():
    _, ordinals = extract_entry_ordinals({"entry_raw": "Liber primus ad Nationes."})

    assert ordinals == []


def test_extract_entry_ordinals_accepts_latin_word_ordinal():
    _, ordinals = extract_entry_ordinals({"entry_raw": "CAPUT PRIMUM, vers. 21-119"})

    assert ordinals == [1]


def test_find_latin_chapter_headings_rejects_numeric_table_rows():
    text = """III. XII et XI
IV. V Idus
V. IV
III. Parergorum primi laterculi explicatio.
"""

    assert [item["ordinal"] for item in find_latin_chapter_headings(text)] == [3]


def test_normalize_ocr_evidence_joins_hyphenation_and_folds_ligatures():
    assert normalize_ocr_evidence("Præclara hospi-\ntalitàs") == "praeclara hospitalitas"


def test_normalized_similarity_prefers_same_ocr_cleaned_title():
    query = "Parergorum primi laterculi explicatio."
    assert normalized_text_similarity(query, "Parergo-\nrum primi laterculi explicatio") > 0.95
    assert normalized_text_similarity(query, "XII et XI") < 0.3


def test_select_best_marker_run_prefers_complete_family_and_matching_title():
    expected = [
        {"ref": {"ordinal": 1}, "allows_qualified": False, "query_raw": "Argumentum primum"},
        {"ref": {"ordinal": 2}, "allows_qualified": False, "query_raw": "Argumentum secundum"},
        {"ref": {"ordinal": 3}, "allows_qualified": False, "query_raw": "Parergorum explicatio"},
    ]
    hits = [
        {"ordinal": 1, "style": "cap", "position": [20, 1], "title_raw": "Aliud", "qualified": False},
        {"ordinal": 2, "style": "cap", "position": [21, 1], "title_raw": "Aliud", "qualified": False},
        {"ordinal": 1, "style": "ordinal", "position": [2, 1], "title_raw": "Argumentum primum", "qualified": False},
        {"ordinal": 2, "style": "ordinal", "position": [3, 1], "title_raw": "Argumentum secundum", "qualified": False},
        {"ordinal": 3, "style": "ordinal", "position": [3, 2], "title_raw": "XII et XI", "qualified": False},
        {"ordinal": 3, "style": "ordinal", "position": [4, 1], "title_raw": "Parergorum explicatio", "qualified": False},
    ]

    selected = select_best_marker_run(expected, hits)

    assert selected["family"] == "ordinal"
    assert selected["accepted"] is True
    assert [hit["position"] for hit in selected["path_hits"]] == [[2, 1], [3, 1], [4, 1]]


def test_select_best_marker_run_rejects_sparse_incidental_matches():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": "Argumentum"}
        for ordinal in range(1, 6)
    ]
    hits = [
        {"ordinal": 2, "style": "cap", "position": [20, 1], "title_raw": "Aliud", "qualified": False},
        {"ordinal": 4, "style": "cap", "position": [30, 1], "title_raw": "Aliud", "qualified": False},
    ]

    selected = select_best_marker_run(expected, hits)

    assert selected["coverage"] == 0.4
    assert selected["accepted"] is False
    assert selected["path_hits"] == [None] * 5


def test_select_best_marker_run_reuses_heading_for_overlapping_entry_ranges():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": "Argumentum"}
        for ordinal in (1, 2, 2, 3)
    ]
    hits = [
        {"ordinal": ordinal, "style": "caput", "position": [ordinal, 1], "title_raw": "Argumentum", "qualified": False}
        for ordinal in (1, 2, 3)
    ]

    selected = select_best_marker_run(expected, hits)

    assert selected["accepted"] is True
    assert [hit["ordinal"] for hit in selected["path_hits"]] == [1, 2, 2, 3]


def test_title_evidence_can_prefer_nearly_complete_correct_work():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": f"Titulus {ordinal}"}
        for ordinal in range(1, 6)
    ]
    hits = [
        *[
            {"ordinal": ordinal, "style": "cap", "position": [ordinal, 1], "title_raw": f"Titulus {ordinal}", "qualified": False}
            for ordinal in range(1, 5)
        ],
        *[
            {"ordinal": ordinal, "style": "cap", "position": [100 + ordinal, 1], "title_raw": "Materia aliena", "qualified": False}
            for ordinal in range(1, 6)
        ],
    ]

    selected = select_best_marker_run(expected, hits, prefer_title_evidence=True)

    assert selected["coverage"] == 0.8
    assert [hit["position"][0] if hit else None for hit in selected["path_hits"]] == [1, 2, 3, 4, None]


def test_local_marker_runs_do_not_stitch_separate_chapter_sequences():
    hits = [
        {"ordinal": 1, "style": "caput", "position": [1, 1], "title_raw": "A", "qualified": False},
        {"ordinal": 2, "style": "caput", "position": [2, 1], "title_raw": "B", "qualified": False},
        {"ordinal": 1, "style": "caput", "position": [10, 1], "title_raw": "C", "qualified": False},
        {"ordinal": 3, "style": "caput", "position": [11, 1], "title_raw": "D", "qualified": False},
    ]
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": "Argumentum"}
        for ordinal in (1, 2, 3)
    ]

    assert [len(run) for run in split_local_marker_runs(hits)] == [2, 2]
    selected = select_best_marker_run(expected, hits)
    assert selected["coverage"] == 0.6667
    assert selected["accepted"] is False


def test_segmented_index_matches_same_number_of_ordered_body_runs():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": f"Liber I {ordinal}"}
        for ordinal in (1, 2, 3)
    ] + [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": f"Liber II {ordinal}"}
        for ordinal in (1, 2)
    ]
    hits = [
        {"ordinal": ordinal, "style": "caput", "position": [ordinal, 1], "title_raw": f"Liber I {ordinal}", "qualified": False}
        for ordinal in (1, 2, 3)
    ] + [
        {"ordinal": ordinal, "style": "caput", "position": [10 + ordinal, 1], "title_raw": f"Liber II {ordinal}", "qualified": False}
        for ordinal in (1, 2)
    ]

    assert [len(run) for run in split_expected_ref_runs(expected)] == [3, 2]
    selected = select_best_marker_run(expected, hits, prefer_title_evidence=True)

    assert selected["accepted"] is True
    assert selected["segmented_run_count"] == 2
    assert selected["segment_coverages"] == [1.0, 1.0]
    assert [hit["position"][0] for hit in selected["path_hits"]] == [1, 2, 3, 11, 12]


def test_title_alignment_skips_body_chapter_omitted_from_index():
    expected = [
        {"ref": {"ordinal": 1}, "allows_qualified": False, "query_raw": "De abbate"},
        {"ref": {"ordinal": 2}, "allows_qualified": False, "query_raw": "De priore"},
        {"ref": {"ordinal": 3}, "allows_qualified": False, "query_raw": "De cellerario"},
    ]
    hits = [
        {"ordinal": 1, "style": "caput", "position": [1, 1], "title_raw": "De abbate", "qualified": False},
        {"ordinal": 2, "style": "caput", "position": [2, 1], "title_raw": "De hostiis", "qualified": False},
        {"ordinal": 3, "style": "caput", "position": [3, 1], "title_raw": "De priore", "qualified": False},
        {"ordinal": 4, "style": "caput", "position": [4, 1], "title_raw": "De cellerario", "qualified": False},
    ]

    selected = select_best_marker_run(expected, hits, prefer_title_evidence=True)

    assert selected["accepted"] is True
    assert [hit["ordinal"] for hit in selected["path_hits"]] == [1, 3, 4]


def test_title_alignment_does_not_shift_to_unrelated_nearby_heading():
    expected = [
        {"ref": {"ordinal": 1}, "allows_qualified": False, "query_raw": "De abbate"},
        {"ref": {"ordinal": 2}, "allows_qualified": False, "query_raw": "De priore"},
    ]
    hits = [
        {"ordinal": 1, "style": "caput", "position": [1, 1], "title_raw": "De abbate", "qualified": False},
        {"ordinal": 2, "style": "caput", "position": [2, 1], "title_raw": "De priore", "qualified": False},
        {"ordinal": 3, "style": "caput", "position": [3, 1], "title_raw": "Materia aliena", "qualified": False},
    ]

    selected = select_best_marker_run(expected, hits, prefer_title_evidence=True)

    assert [hit["ordinal"] for hit in selected["path_hits"]] == [1, 2]


def test_segmented_index_rejects_mismatched_body_run_count():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": "Argumentum"}
        for ordinal in (1, 2, 1, 2)
    ]
    hits = [
        {"ordinal": ordinal, "style": "caput", "position": [position, 1], "title_raw": "Argumentum", "qualified": False}
        for position, ordinal in enumerate((1, 2, 1, 2, 1, 2), start=1)
    ]

    selected = select_best_marker_run(expected, hits)

    assert selected["accepted"] is False


def test_segmented_index_does_not_count_excluded_source_index_run():
    expected = [
        {"ref": {"ordinal": ordinal}, "allows_qualified": False, "query_raw": "Argumentum"}
        for ordinal in (1, 2, 1, 2)
    ]
    hits = [
        {"ordinal": ordinal, "style": "caput", "position": [position, 1], "title_raw": "Argumentum", "qualified": False}
        for position, ordinal in enumerate((1, 2, 1, 2), start=1)
    ] + [
        {"ordinal": ordinal, "style": "caput", "position": [100 + ordinal, 1], "title_raw": "Argumentum", "qualified": False, "excluded": True}
        for ordinal in (1, 2)
    ]

    selected = select_best_marker_run(expected, hits)

    assert selected["accepted"] is True
    assert selected["segmented_run_count"] == 2
