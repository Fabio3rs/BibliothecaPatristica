import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from tools.indexing.structural_target_repair import (
    _adjacent_heading_over_editorial_map,
    _explicit_page_hint,
    _scan_heading_file,
    _score_for_ref,
    EntryRef,
    HeadingCandidate,
    apply_manifest_volume,
    build_heading_catalog,
    build_repair_manifest,
    classify_segments,
    parse_marker,
    validate_repaired_payload,
)
from tools.indexing.structural_target_validation import validate_applied_report
from tools.indexing.index_operation_lock import (
    ActiveTransactionError,
    LockUnavailableError,
    index_operation_lock,
)
from scripts.repair_structural_targets import (
    copy_file_atomic,
    persist_transaction_state,
    recover_interrupted_transaction,
    run_apply,
    run_dry_run,
    sha256_file,
    sqlite_logical_sha,
    sqlite_snapshot,
    transaction_paths,
)


def write_page(path, body, *, note="", header=""):
    path.write_text(
        "<pagina>\n"
        f'<bloco tipo="cabecalho">{header}</bloco>\n'
        f'<bloco tipo="texto_principal">{body}</bloco>\n'
        f'<bloco tipo="nota">{note}</bloco>\n'
        "</pagina>\n",
        encoding="utf-8",
    )


def test_adjacent_explicit_heading_overrides_approximate_editorial_page_map():
    ref = EntryRef(
        section_index=0,
        entry_index=0,
        section_key="ordo",
        entry_key="chapter-25",
        entry_raw="CAPUT XXV. Iterum qui sub Valente ob religionem exsularent. 1384",
        family="chapter",
        ordinals=[25],
        query_raw="CAPUT XXV. Iterum qui sub Valente ob religionem exsularent. 1384",
        source_files=[],
        context=[],
        existing_target_file=None,
        existing_evidence=None,
        existing_target_is_generic_anchor=False,
        classification="eligible_structural",
        page_hint_int=1384,
    )
    mapped = HeadingCandidate(
        family="chapter",
        ordinal=25,
        file="page-697.txt",
        file_position=697,
        line=0,
        heading_raw="",
        title_raw="",
        context="",
        explicit_label=False,
        block_role="editorial_page_map",
        script="",
        bbox="",
    )
    adjacent = HeadingCandidate(
        family="chapter",
        ordinal=25,
        file="page-696.txt",
        file_position=696,
        line=40,
        heading_raw="CAPUT XXV.",
        title_raw="Iterum qui sub Valente ob religionem exsularant.",
        context="",
        explicit_label=True,
        block_role="body",
        script="latino",
        bbox="",
    )
    distant_competitor = HeadingCandidate(
        family="chapter",
        ordinal=25,
        file="page-615.txt",
        file_position=615,
        line=20,
        heading_raw="CAPUT XXV.",
        title_raw="Alia materia.",
        context="",
        explicit_label=True,
        block_role="body",
        script="latino",
        bbox="",
    )

    chosen = _adjacent_heading_over_editorial_map(
        ref,
        [mapped],
        [(0.575, adjacent), (0.21, distant_competitor)],
        minimum_margin=0.15,
    )

    assert chosen == [adjacent]
    assert _adjacent_heading_over_editorial_map(
        ref,
        [mapped],
        [(0.52, adjacent), (0.21, distant_competitor)],
        minimum_margin=0.15,
    ) == []
    assert _adjacent_heading_over_editorial_map(
        ref,
        [mapped],
        [(0.60, adjacent), (0.50, distant_competitor)],
        minimum_margin=0.15,
    ) == []


def test_manifest_skips_ocr_scan_when_payload_has_no_eligible_entries(tmp_path):
    source_root = tmp_path / "PL990" / "text"
    source_root.mkdir(parents=True)
    (source_root / "PL990-001.txt").write_text("not XML and must not be parsed", encoding="utf-8")
    payload_path = tmp_path / "PL990_indices.json"
    payload_path.write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PL990", "source_root": str(source_root)},
                "sections": [
                    {
                        "section_key": "alphabetical",
                        "index_kind": "INDEX ALPHABETICUS",
                        "entries": [{"entry_raw": "Aaron", "entry_kind": "term"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = build_repair_manifest(payload_path, workers=2)

    assert manifest["counts"]["eligible"] == 0
    assert manifest["heading_candidate_count"] == 0
    assert manifest["editorial_page_catalog"]["status"] == "skipped_no_eligible_entries"


def test_multi_volume_dry_run_keeps_deterministic_payload_order(tmp_path):
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    for volume_id in ("PL992", "PL991"):
        source_root = tmp_path / volume_id / "text"
        source_root.mkdir(parents=True)
        body = source_root / f"{volume_id}-001.txt"
        index = source_root / f"{volume_id}-002.txt"
        write_page(body, "CAPUT I. De doctrina christiana.")
        write_page(index, "INDEX CAPITUM\nCAPUT I. De doctrina christiana.")
        (payload_dir / f"{volume_id}_indices.json").write_text(
            json.dumps(
                {
                    "volume": {"volume_id": volume_id, "source_root": str(source_root)},
                    "works": [{"work_key": "work", "start_file": str(body), "end_file": str(body)}],
                    "sections": [
                        {
                            "section_key": "chapters",
                            "scope_kind": "work_front",
                            "work_key": "work",
                            "file_start": str(index),
                            "file_end": str(index),
                            "entries": [
                                {
                                    "entry_key": "chapter-1",
                                    "entry_kind": "chapter",
                                    "entry_raw": "CAPUT I. De doctrina christiana.",
                                    "raw_json": {},
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    report_path = tmp_path / "dry-run.json"
    result = run_dry_run(
        Namespace(
            payload_dir=payload_dir,
            volume=[],
            all_volumes=True,
            report=report_path,
            reviewed_overrides=None,
            resume=False,
            workers=2,
            minimum_sequence_coverage=0.8,
            minimum_title_similarity=0.58,
            minimum_unique_similarity=0.82,
            minimum_unique_margin=0.15,
            lock_file=tmp_path / "index.lock",
            state_dir=tmp_path / "state",
            lock_timeout=0,
        )
    )

    assert [item["volume_id"] for item in result["volumes"]] == ["PL991", "PL992"]
    assert result["counts"]["resolved"] == 2
    assert len(list(Path(result["parts_dir"]).glob("*.json"))) == 2


def test_validate_applied_report_handles_canonical_section_keys_and_duplicate_orders(tmp_path):
    target = tmp_path / "page-001.txt"
    target.write_text("CAPUT I. De fide.\n", encoding="utf-8")
    evidence = {
        "locator": "deterministic_structural_target_locator",
        "status": "resolved",
        "target_file": str(target),
    }
    entry_raw = "CAPUT I. — De fide."
    payload = {
        "volume": {"volume_id": "PL999"},
        "sections": [
            {
                "section_key": "legacy-section",
                "entries": [
                    {
                        "entry_order": 1,
                        "entry_raw": "another entry with the same order",
                        "target_file": None,
                        "raw_json": {},
                    },
                    {
                        "entry_order": 1,
                        "entry_raw": entry_raw,
                        "target_file": str(target),
                        "raw_json": {"physical_target_evidence": evidence},
                    },
                ],
            }
        ],
    }
    payload_path = tmp_path / "PL999_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    report = {
        "operation": "deterministic_structural_target_repair",
        "mode": "dry_run",
        "status": "complete",
        "apply_ready": True,
        "volumes": [
            {
                "volume_id": "PL999",
                "payload_file": str(payload_path),
                "patches": [
                    {
                        "section_index": 0,
                        "entry_index": 1,
                        "entry_raw_sha256": hashlib.sha256(entry_raw.encode("utf-8")).hexdigest(),
                        "after_target_file": str(target),
                        "physical_target_evidence": evidence,
                    }
                ],
            }
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    database = tmp_path / "indices.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE volumes (volume_id TEXT PRIMARY KEY);
        CREATE TABLE index_sections (
            section_key TEXT PRIMARY KEY,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id),
            raw_json TEXT NOT NULL
        );
        CREATE TABLE index_entries (
            section_key TEXT NOT NULL REFERENCES index_sections(section_key),
            entry_order INTEGER NOT NULL,
            entry_raw TEXT NOT NULL,
            target_file TEXT,
            raw_json TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO volumes VALUES (?)", ("PL999",))
    connection.execute(
        "INSERT INTO index_sections VALUES (?, ?, ?)",
        ("PL999::legacy-section", "PL999", json.dumps({"original_section_key": "legacy-section"})),
    )
    connection.execute(
        "INSERT INTO index_entries VALUES (?, ?, ?, ?, ?)",
        ("PL999::legacy-section", 1, "another entry with the same order", None, "{}"),
    )
    connection.execute(
        "INSERT INTO index_entries VALUES (?, ?, ?, ?, ?)",
        (
            "PL999::legacy-section",
            1,
            entry_raw,
            str(target),
            json.dumps({"physical_target_evidence": evidence}),
        ),
    )
    connection.commit()
    connection.close()

    result = validate_applied_report(report_path, database_path=database)

    assert result["patch_count"] == 1
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_violations"] == 0

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE index_entries SET target_file = NULL WHERE entry_raw = ?",
        (entry_raw,),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="SQLite does not contain"):
        validate_applied_report(report_path, database_path=database)


def test_validate_applied_report_accepts_an_explicit_reviewed_clear(tmp_path):
    evidence = {
        "locator": "deterministic_structural_target_locator",
        "status": "unresolved",
        "method": "reviewed_override",
        "disposition": "cleared_reviewed_invalid_legacy_target",
    }
    entry_raw = "CAP. II, 119-244"
    payload = {
        "volume": {"volume_id": "PL998"},
        "sections": [
            {
                "section_key": "ordo",
                "entries": [
                    {
                        "entry_order": 1,
                        "entry_raw": entry_raw,
                        "raw_json": {"physical_target_evidence": evidence},
                    }
                ],
            }
        ],
    }
    payload_path = tmp_path / "PL998_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    report = {
        "operation": "deterministic_structural_target_repair",
        "mode": "dry_run",
        "status": "complete",
        "apply_ready": True,
        "volumes": [
            {
                "volume_id": "PL998",
                "payload_file": str(payload_path),
                "patches": [
                    {
                        "section_index": 0,
                        "entry_index": 0,
                        "entry_raw_sha256": hashlib.sha256(entry_raw.encode()).hexdigest(),
                        "after_target_file": None,
                        "physical_target_evidence": evidence,
                    }
                ],
            }
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    database = tmp_path / "indices.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE volumes (volume_id TEXT PRIMARY KEY);
        CREATE TABLE index_sections (
            section_key TEXT PRIMARY KEY,
            volume_id TEXT NOT NULL REFERENCES volumes(volume_id),
            raw_json TEXT NOT NULL
        );
        CREATE TABLE index_entries (
            section_key TEXT NOT NULL REFERENCES index_sections(section_key),
            entry_order INTEGER NOT NULL,
            entry_raw TEXT NOT NULL,
            target_file TEXT,
            raw_json TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO volumes VALUES (?)", ("PL998",))
    connection.execute("INSERT INTO index_sections VALUES (?, ?, ?)", ("ordo", "PL998", "{}"))
    connection.execute(
        "INSERT INTO index_entries VALUES (?, ?, ?, ?, ?)",
        ("ordo", 1, entry_raw, None, json.dumps({"physical_target_evidence": evidence})),
    )
    connection.commit()
    connection.close()

    result = validate_applied_report(report_path, database_path=database)

    assert result["patch_count"] == 1
    assert result["integrity_check"] == "ok"


def test_parse_marker_covers_multilingual_and_ordinal_before_label_formats():
    cases = {
        "CAPUT XII. De disciplina.": ("chapter", [12]),
        "CHAPITRE TROISIÈME. De la foi.": ("chapter", [3]),
        "ΚΕΦ. ΙΓ΄. Περὶ τοῦ λόγου.": ("chapter", [13]),
        "الباب الثاني الكلام": ("chapter", [2]),
        "الباب ٣ نص": ("chapter", [3]),
        "I. CAP. Argumentum": ("chapter", [1]),
        "QUAESTIO PRIMA. Utrum sit.": ("question", [1]),
    }

    for raw, expected in cases.items():
        marker = parse_marker(raw, kind="chapter_entry")
        assert marker is not None
        assert (marker.family, marker.ordinals) == expected

    assert parse_marker("13 TRACTATUS I,", kind="chapter_entry") is None
    assert parse_marker("IV. Utrum sit.", kind="question_entry").family == "question"
    unnumbered_book = parse_marker("LIBER CONTRA FIDEM CATHOLICAM.")
    assert unnumbered_book.ordinals == []
    assert unnumbered_book.title_raw == "CONTRA FIDEM CATHOLICAM"
    assert parse_marker("LIBER DE SACRAMENTIS.").ordinals == []
    for raw in (
        "Tractatus de fide orthodoxa",
        "Sermo de octo beatitudinibus",
        "Liber de laude sanctorum",
        "Epistola de Judais",
        "Liber de reparatione lapsi",
    ):
        assert parse_marker(raw).ordinals == []
    assert parse_marker("EPISTOLA IIa.").ordinals == [2]
    assert parse_marker("CAPP. I et II. De fide.").ordinals == [1, 2]
    assert parse_marker("CAPP. I-II. De fide.").ordinals == [1, 2]
    assert parse_marker("CAPITA I et II. De fide.").ordinals == [1, 2]
    assert parse_marker("CAP. II, 119-244").ordinals == [2]
    assert parse_marker("CAP. XIII, 1-96").ordinals == [13]
    assert parse_marker("CAP. XI, 968-1007").ordinals == [11]
    assert parse_marker("CAP. XXIII, 992-1050").ordinals == [23]
    assert parse_marker("Epistola I ad Corinthios").ordinals == [1]
    assert parse_marker("CAP. XVII — De gratia").ordinals == [17]
    assert parse_marker("HOMILIA I ex capite Matthaei").ordinals == [1]
    assert parse_marker("ς'. Sextum capitulum.", kind="chapter_entry").ordinals == [6]
    assert parse_marker("ις'. Decimum sextum capitulum.", kind="chapter_entry").ordinals == [16]
    assert parse_marker("CAPUT PRIMUM, 47.", page_hint=47).ordinals == [1]
    assert parse_marker("LIBER III, 415 epistolas continens. 727", page_hint=727).ordinals == [3]


def test_page_hint_parser_rejects_regnal_year_but_keeps_page_after_christi():
    year_entry = {
        "entry_raw": "LIBER PRIMUS. — Pontificatus anno I, Christi 1198.",
        "target_raw": "LIBER PRIMUS. — Pontificatus anno I, Christi",
        "page_ref_int": 1198,
    }
    page_entry = {
        "entry_raw": "SERMO DE NATIVITATE JESU CHRISTI. 10",
        "target_raw": "SERMO DE NATIVITATE JESU CHRISTI.",
        "page_ref_int": 10,
    }
    ordinal_entry = {
        "entry_raw": "Epist. 1. Vocum quarumdam explanatio.",
        "target_raw": "Epist. 1. Vocum quarumdam explanatio.",
        "page_ref_int": 1,
    }

    assert _explicit_page_hint(year_entry) == (None, None)
    assert _explicit_page_hint(page_entry) == (10, "payload_page_ref")
    assert _explicit_page_hint(ordinal_entry) == (None, None)


def test_bare_ordinal_continuation_inherits_terminal_page_from_next_ocr_line():
    source = "/corpus/PL216/text/page-646.txt"
    payload = {
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR",
            "entries": [
                {
                    "entry_raw": "LIBER XII.",
                    "entry_kind": "book",
                },
                {
                    "entry_raw": "XL. — Decano et capitulo. — De electione",
                    "raw_json": {
                        "source_file": source,
                        "source_block_kind": "texto_principal",
                        "source_line_index": 1,
                    },
                },
                {
                    "entry_raw": "archiepiscopi Bituricensis. 48",
                    "page_ref_int": 48,
                    "target_file": "/legacy/index-page.txt",
                    "raw_json": {
                        "source_file": source,
                        "source_block_kind": "texto_principal",
                        "source_line_index": 2,
                    },
                },
            ],
        }],
    }

    segments, dispositions = classify_segments(payload)

    assert dispositions == []
    assert len(segments) == 1
    assert len(segments[0].entries) == 2
    repaired = segments[0].entries[1]
    assert repaired.ordinals == [40]
    assert repaired.page_hint_int == 48
    assert repaired.page_hint_source == "logical_entry_terminal"
    assert "archiepiscopi Bituricensis" in repaired.query_raw


def test_global_unique_editorial_page_can_recover_an_incorrect_work_window(tmp_path):
    source_root = tmp_path / "PL992" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PL992-001.txt"
    wrong_work_window = source_root / "PL992-002.txt"
    index = source_root / "PL992-003.txt"
    write_page(body, "Corpus without a machine-readable chapter heading.", header="1 CORPUS 2")
    write_page(wrong_work_window, "Unrelated work.", header="3 ALIUD OPUS 4")
    write_page(index, "ORDO RERUM", header="5 ORDO RERUM 6")
    payload = {
        "volume": {"volume_id": "PL992", "source_root": str(source_root)},
        "works": [{
            "work_key": "wrong-work",
            "start_file": str(wrong_work_window),
            "end_file": str(wrong_work_window),
        }],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "work_key": "wrong-work",
            "file_start": str(index),
            "file_end": str(index),
            "entries": [{
                "entry_key": "chapter-1",
                "entry_kind": "chapter",
                "entry_raw": "CAPUT I. Materia prima. 1",
                "page_ref_int": 1,
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL992_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    patch = manifest["patches"][0]
    assert patch["after_target_file"] == str(body)
    locator = patch["physical_target_evidence"]["editorial_page_locator"]
    assert locator["declared_body_window_match"] is False
    assert locator["scope_fallback"] == "global_unique_editorial_page_outside_declared_window"


def test_editorial_page_map_resolves_numbered_ibid_and_unnumbered_entries(tmp_path):
    source_root = tmp_path / "PL993" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PL993-001.txt"
    index = source_root / "PL993-002.txt"
    write_page(
        body,
        "Corpus whose structural headings were lost by OCR.",
        header="1 OPERIS INITIUM 2",
    )
    write_page(index, "ORDO RERUM", header="3 ORDO RERUM 4")
    payload = {
        "volume": {"volume_id": "PL993", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(body), "end_file": str(body)}],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "work_key": "work",
            "file_start": str(index),
            "file_end": str(index),
            "entries": [
                {
                    "entry_key": "chapter-1",
                    "entry_kind": "chapter",
                    "entry_raw": "CAPUT I. Prima materia. 1",
                    "target_raw": "CAPUT I. Prima materia.",
                    "page_ref_int": 1,
                    "raw_json": {},
                },
                {
                    "entry_key": "chapter-2",
                    "entry_kind": "chapter",
                    "entry_raw": "CAPUT II. Secunda materia. Ibid.",
                    "page_ref_raw": "Ibid.",
                    "raw_json": {},
                },
                {
                    "entry_key": "epistle",
                    "entry_kind": "epistle",
                    "entry_raw": "EPISTOLA AD CLERUM. 1",
                    "target_raw": "EPISTOLA AD CLERUM.",
                    "page_ref_int": 1,
                    "raw_json": {},
                },
            ],
        }],
    }
    payload_path = tmp_path / "PL993_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["eligible"] == 3
    assert manifest["counts"]["resolved"] == 3
    assert {patch["after_target_file"] for patch in manifest["patches"]} == {str(body)}
    evidence = {
        patch["entry_key"]: patch["physical_target_evidence"]
        for patch in manifest["patches"]
    }
    assert {item["method"] for item in evidence.values()} == {"editorial_page_map"}
    assert evidence["chapter-2"]["editorial_page_locator"]["page_hint_source"] == "immediate_ibid"


def test_structural_repair_keeps_capitula_about_scripture() -> None:
    payload = {
        "sections": [
            {
                "section_key": "scripture-commentary-capitula",
                "scope_kind": "work_internal",
                "index_kind": "capitula",
                "heading_raw": "ADNOTATIONES IN SCRIPTURAM SACRAM — CAPITULA",
                "entries": [
                    {
                        "entry_key": "caput-1",
                        "entry_kind": "chapter_entry",
                        "entry_raw": "CAPUT I. Explanatio Psalmi primi.",
                    }
                ],
            }
        ]
    }

    segments, dispositions = classify_segments(payload)

    assert len(segments) == 1
    assert segments[0].entries[0].entry_key == "caput-1"
    assert dispositions == []


def test_structural_repair_rejects_scripture_reference_index() -> None:
    payload = {
        "sections": [
            {
                "section_key": "scripture-locorum",
                "scope_kind": "volume_end",
                "index_kind": "capitula",
                "heading_raw": "INDEX LOCORUM SCRIPTURAE SACRAE",
                "entries": [
                    {
                        "entry_key": "citation-1",
                        "entry_kind": "chapter_entry",
                        "entry_raw": "CAPUT I. Psal. X, 3.",
                    }
                ],
            }
        ]
    }

    segments, dispositions = classify_segments(payload)

    assert segments == []
    assert dispositions[0]["classification"] == "ineligible_non_general_section"
    assert "scripture-reference" in dispositions[0]["reason"]


def test_heading_scan_rejects_citations_and_non_body_blocks(tmp_path):
    page = tmp_path / "page-001.txt"
    write_page(
        page,
        "\n".join(
            [
                "cap. 5, n. 3, citation in prose.",
                "Cap. 2, n. 6, another citation.",
                "13 TRACTATUS I,",
                "DE FIDE CATHOLICA.",
                "EPISTOLA IIa.",
                "AD SIMPLICIANUM. Studium Theophili.",
            ]
        ),
        note="CAPUT IX. Note-only false heading.",
        header="VITA OPERIS",
    )

    candidates = _scan_heading_file((0, str(page)))

    assert [(item.family, item.ordinal) for item in candidates] == [
        ("tract", 1),
        ("epistle", 2),
    ]
    assert candidates[0].heading_raw == "13 TRACTATUS I,"
    assert "DE FIDE CATHOLICA" in candidates[0].title_raw
    assert "AD SIMPLICIANUM" in candidates[1].title_raw


def test_heading_scan_accepts_explicit_structural_header(tmp_path):
    page = tmp_path / "page-001.txt"
    page.write_text(
        "<pagina>\n"
        '<bloco tipo="cabecalho" script="latino" bbox="1,2,3,4">CAPUT II.</bloco>\n'
        '<bloco tipo="cabecalho" script="latino">De institutione ecclesiae.</bloco>\n'
        '<bloco tipo="texto_principal">Corpus textus.</bloco>\n'
        "</pagina>\n",
        encoding="utf-8",
    )

    candidates = _scan_heading_file((0, str(page)))

    assert len(candidates) == 1
    assert candidates[0].ordinal == 2
    assert candidates[0].block_role == "header"
    assert candidates[0].script == "latino"
    assert candidates[0].bbox == "1,2,3,4"
    assert "institutione ecclesiae" in candidates[0].title_raw


def test_parallel_heading_scan_is_deterministic(tmp_path):
    files = []
    for ordinal, title in ((1, "Prima materia"), (2, "Secunda materia"), (3, "Tertia materia")):
        page = tmp_path / f"page-{ordinal:03d}.txt"
        write_page(page, f"CAPUT {ordinal}. {title}.")
        files.append(page)

    serial = build_heading_catalog(files, workers=1)
    parallel = build_heading_catalog(files, workers=2)
    project = lambda items: [
        (item.file_position, item.line, item.family, item.ordinal, item.heading_raw)
        for item in items
    ]

    assert project(parallel) == project(serial)


def test_parallel_segment_matching_is_deterministic(tmp_path):
    source_root = tmp_path / "PL998" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PL998-001.txt"
    index = source_root / "PL998-002.txt"
    write_page(body, "CAPUT I. De materia communi.")
    write_page(index, "INDEX CAPITUM")
    sections = []
    for section_number in range(2):
        sections.append(
            {
                "section_key": f"index-{section_number}",
                "scope_kind": "volume_end",
                "index_kind": "index_capitum",
                "file_start": str(index),
                "file_end": str(index),
                "entries": [
                    {
                        "entry_key": f"entry-{section_number}-{entry_number}",
                        "entry_kind": "chapter_entry",
                        "entry_raw": "CAPUT I. De materia communi.",
                        "raw_json": {},
                    }
                    for entry_number in range(250)
                ],
            }
        )
    payload_path = tmp_path / "PL998_indices.json"
    payload_path.write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PL998", "source_root": str(source_root)},
                "works": [],
                "sections": sections,
            }
        ),
        encoding="utf-8",
    )

    serial = build_repair_manifest(payload_path, workers=1)
    parallel = build_repair_manifest(payload_path, workers=2)
    serial.pop("scan_workers")
    parallel.pop("scan_workers")

    assert parallel == serial
    assert parallel["counts"]["eligible"] == 500


def test_short_generic_question_title_cannot_anchor_another_series():
    ref = EntryRef(
        section_index=0,
        entry_index=0,
        section_key="questions-a",
        entry_key="q1",
        entry_raw="1. Deus perfectio est: quid ergo opus fuit Christo ut nasceretur.",
        family="question",
        ordinals=[1],
        query_raw="Deus perfectio est: quid ergo opus fuit Christo ut nasceretur.",
        source_files=[],
        context=[],
        existing_target_file=None,
        existing_evidence=None,
        existing_target_is_generic_anchor=False,
        classification="eligible_structural_contextual",
    )
    candidate = HeadingCandidate(
        family="question",
        ordinal=1,
        file="page.txt",
        file_position=0,
        line=1,
        heading_raw="QUAESTIO PRIMA. Quid est Deus.",
        title_raw="Quid est Deus.",
        context="QUAESTIO PRIMA. Quid est Deus.",
        explicit_label=True,
        block_role="body",
        script="latino",
        bbox="",
    )

    assert _score_for_ref(ref, candidate) <= 0.78


def test_short_partial_token_does_not_receive_exact_substring_boost():
    ref = EntryRef(
        section_index=0,
        entry_index=0,
        section_key="ordo",
        entry_key="short-title",
        entry_raw="CAP. XV. De tibe.",
        family="chapter",
        ordinals=[15],
        query_raw="De tibe.",
        source_files=[],
        context=[],
        existing_target_file=None,
        existing_evidence=None,
        existing_target_is_generic_anchor=False,
        classification="eligible_structural_exact",
    )
    candidate = HeadingCandidate(
        family="chapter",
        ordinal=15,
        file="page.txt",
        file_position=0,
        line=1,
        heading_raw="CAP. XV. De Absimaro seu Tiberio.",
        title_raw="De Absimaro seu Tiberio.",
        context="CAP. XV. De Absimaro seu Tiberio.",
        explicit_label=True,
        block_role="body",
        script="latino",
        bbox="",
    )

    assert _score_for_ref(ref, candidate) < 0.82


def test_one_word_query_cannot_match_a_body_paragraph_as_unique_heading():
    ref = EntryRef(
        section_index=0,
        entry_index=0,
        section_key="sermons",
        entry_key="sermon-2",
        entry_raw="Sermo II.",
        family="sermon",
        ordinals=[2],
        query_raw="Sermo",
        source_files=[],
        context=[],
        existing_target_file=None,
        existing_evidence=None,
        existing_target_is_generic_anchor=False,
        classification="eligible_structural_exact",
    )
    candidate = HeadingCandidate(
        family="sermon",
        ordinal=2,
        file="page.txt",
        file_position=0,
        line=10,
        heading_raw="2. Long body paragraph mentioning sermo in passing.",
        title_raw="Long body paragraph mentioning sermo in passing.",
        context="2. Long body paragraph mentioning sermo in passing.",
        explicit_label=False,
        block_role="body",
        script="latino",
        bbox="",
    )

    assert _score_for_ref(ref, candidate) <= 0.78


def test_caput_to_titulus_alias_requires_and_uses_monotonic_sequence(tmp_path):
    source_root = tmp_path / "PL997" / "text"
    source_root.mkdir(parents=True)
    body_files = []
    for ordinal, roman, title in (
        (1, "I", "Prima materia"),
        (2, "II", "Secunda materia"),
        (3, "III", "Tertia materia"),
    ):
        page = source_root / f"PL997-{ordinal:03d}.txt"
        write_page(page, f"TIT. {roman}. {title}.")
        body_files.append(page)
    index = source_root / "PL997-004.txt"
    write_page(index, "INDEX CAPITUM")
    payload = {
        "volume": {"volume_id": "PL997", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(body_files[0]), "end_file": str(body_files[-1])}],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "work_key": "work",
            "file_start": str(index),
            "file_end": str(index),
            "entries": [
                {"entry_key": f"cap-{ordinal}", "entry_kind": "chapter", "entry_raw": f"CAP. {roman}. {title}.", "raw_json": {}}
                for ordinal, roman, title in (
                    (1, "I", "Prima materia"),
                    (2, "II", "Secunda materia"),
                    (3, "III", "Tertia materia"),
                )
            ],
        }],
    }
    payload_path = tmp_path / "PL997_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 3
    assert {patch["physical_target_evidence"]["method"] for patch in manifest["patches"]} == {
        "monotonic_heading_sequence"
    }


def test_dense_index_list_is_not_selected_but_later_body_on_same_scan_is(tmp_path):
    source_root = tmp_path / "PL996" / "text"
    source_root.mkdir(parents=True)
    mixed = source_root / "PL996-001.txt"
    write_page(
        mixed,
        "\n".join(
            [
                "CAP. I. List item one.",
                "CAP. II. List item two.",
                "CAP. III. List item three.",
                "CAP. IV. List item four.",
                "Prologus longus.",
                "Prima linea corporis.",
                "Secunda linea corporis.",
                "Tertia linea corporis.",
                "CAP. I. Body opening.",
                "Corpus textus amplissimus.",
            ]
        ),
    )
    payload = {
        "volume": {"volume_id": "PL996", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(mixed), "end_file": str(mixed)}],
        "sections": [{
            "section_key": "capitula",
            "scope_kind": "work_front",
            "work_key": "work",
            "file_start": str(mixed),
            "file_end": str(mixed),
            "entries": [{"entry_key": "body-one", "entry_kind": "chapter", "entry_raw": "CAP. I. Body opening.", "raw_json": {}}],
        }],
    }
    payload_path = tmp_path / "PL996_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    evidence = manifest["patches"][0]["physical_target_evidence"]
    assert evidence["matched_heading_raw"] == "CAP. I. Body opening."


def test_dense_bare_ordinal_list_inheriting_chapter_family_is_not_selected(tmp_path):
    source_root = tmp_path / "PL996" / "text"
    source_root.mkdir(parents=True)
    contents = source_root / "PL996-001.txt"
    body = source_root / "PL996-002.txt"
    write_page(
        contents,
        "\n".join(
            [
                "CAPITA LIBRI QUARTI",
                "I. Prima materia.",
                "II. Secunda materia.",
                "III. Tertia materia.",
                "IV. Quarta materia.",
            ]
        ),
    )
    write_page(body, "CAPUT I. Prima materia.\nCorpus textus amplissimus.")
    payload = {
        "volume": {"volume_id": "PL996", "source_root": str(source_root)},
        "works": [
            {"work_key": "work", "start_file": str(contents), "end_file": str(body)}
        ],
        "sections": [{
            "section_key": "capitula",
            "scope_kind": "work_front",
            "work_key": "work",
            "file_start": str(contents),
            "file_end": str(contents),
            "entries": [{
                "entry_key": "body-one",
                "entry_kind": "chapter",
                "entry_raw": "CAP. I. Prima materia.",
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL996_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    patch = manifest["patches"][0]
    assert patch["after_target_file"].endswith("PL996-002.txt")
    assert patch["physical_target_evidence"]["matched_heading_raw"].startswith("CAPUT I")


def test_pagewide_index_heading_marks_split_structural_blocks_as_dense(tmp_path):
    index_page = tmp_path / "PL996-001.txt"
    write_page(
        index_page,
        "\n".join(
            [
                "HOMIL. ET SERM. INDEX.",
                "HOMILIA VII. Dominica quarta Adventus.",
                "HOMILIA VIII. In Vigilia Nativitatis.",
                "HOMILIA IX. In Nativitate Domini.",
                "HOMILIA X. In eadem solemnitate.",
            ]
        ),
    )

    candidates = _scan_heading_file((0, str(index_page)))

    homilies = [candidate for candidate in candidates if candidate.family == "homily"]
    assert len(homilies) == 4
    assert all(candidate.in_dense_structural_list for candidate in homilies)
    assert all(candidate.page_has_dense_structural_list for candidate in homilies)


def test_numbered_body_paragraphs_do_not_quarantine_the_whole_page(tmp_path):
    body_page = tmp_path / "PL996-001.txt"
    write_page(
        body_page,
        "\n".join(
            [
                "CAPUT V.",
                "De grammatica.",
                "1. Grammatica est scientia recte loquendi.",
                "2. Ars praeceptis regulisque consistit.",
                "3. Oratio est contextus verborum cum sensu.",
                "4. Divisiones grammaticae artis enumerantur.",
                "CAPUT VI.",
                "De partibus orationis.",
            ]
        ),
    )

    candidates = _scan_heading_file((0, str(body_page)))

    assert any(candidate.in_dense_structural_list for candidate in candidates)
    explicit_chapters = [
        candidate
        for candidate in candidates
        if candidate.family == "chapter" and candidate.explicit_label
    ]
    assert explicit_chapters
    assert all(not candidate.page_has_dense_structural_list for candidate in explicit_chapters)


def test_editorial_page_map_can_target_dense_body_page_outside_index_sources(tmp_path):
    source_root = tmp_path / "PL996" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PL996-001.txt"
    contents = source_root / "PL996-002.txt"
    write_page(
        body,
        "\n".join(
            [
                "CAPUT I. Prima materia.",
                "CAPUT II. Secunda materia.",
                "CAPUT III. Tertia materia.",
                "CAPUT IV. Quarta materia.",
            ]
        ),
        header="101 102",
    )
    write_page(contents, "ORDO RERUM\nCAPUT I. 101", header="201 202")
    payload = {
        "volume": {"volume_id": "PL996", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(body), "end_file": str(body)}],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "work_key": "work",
            "file_start": str(contents),
            "file_end": str(contents),
            "entries": [{
                "entry_key": "chapter-one",
                "entry_kind": "chapter",
                "entry_raw": "CAPUT I. 101",
                "page_ref_int": 101,
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL996_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    patch = manifest["patches"][0]
    assert patch["after_target_file"] == str(body)
    assert patch["physical_target_evidence"]["method"] == "editorial_page_map"


def test_explicit_index_source_files_override_distant_semantic_file_end(tmp_path):
    source_root = tmp_path / "PL996" / "text"
    source_root.mkdir(parents=True)
    index = source_root / "PL996-001.txt"
    body = source_root / "PL996-002.txt"
    semantic_end = source_root / "PL996-003.txt"
    write_page(index, "ORDO RERUM\nCAPUT I. Prima materia. 101", header="1 2")
    write_page(body, "CAPUT I. Prima materia.\nCorpus textus.", header="101 102")
    write_page(semantic_end, "Aliud opus.", header="201 202")
    payload = {
        "volume": {"volume_id": "PL996", "source_root": str(source_root)},
        "works": [{
            "work_key": "work",
            "start_file": str(body),
            "end_file": str(semantic_end),
        }],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_front",
            "work_key": "work",
            "file_start": str(index),
            "file_end": str(semantic_end),
            "raw_json": {"source_files": [str(index)]},
            "entries": [{
                "entry_key": "chapter-one",
                "entry_kind": "chapter",
                "entry_raw": "CAPUT I. Prima materia. 101",
                "page_ref_int": 101,
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL996_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    assert manifest["patches"][0]["after_target_file"] == str(body)


def test_exact_short_title_prefix_can_resolve_below_fuzzy_threshold(tmp_path):
    source_root = tmp_path / "PL996" / "text"
    source_root.mkdir(parents=True)
    contents = source_root / "PL996-001.txt"
    body = source_root / "PL996-002.txt"
    decoy = source_root / "PL996-003.txt"
    write_page(contents, "INDEX CAPITUM\nCAP. V. De grammatica.")
    write_page(body, "CAPUT V.\nDe grammatica. Grammatica est scientia recte loquendi.")
    write_page(decoy, "CAPUT V.\nDe geometria. Geometria est disciplina mensurarum.")
    payload = {
        "volume": {"volume_id": "PL996", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(body), "end_file": str(decoy)}],
        "sections": [{
            "section_key": "capitula",
            "scope_kind": "work_front",
            "work_key": "work",
            "file_start": str(contents),
            "file_end": str(contents),
            "entries": [{
                "entry_key": "chapter-five",
                "entry_kind": "chapter",
                "entry_raw": "CAP. V. De grammatica.",
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL996_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    patch = manifest["patches"][0]
    assert patch["after_target_file"].endswith("PL996-002.txt")
    assert patch["physical_target_evidence"]["method"] == "unique_heading_exact_title_prefix"


def test_serialized_numbered_body_paragraph_does_not_inherit_chapter_family():
    payload = {
        "volume": {"volume_id": "PL996"},
        "sections": [{
            "section_key": "body-fragment",
            "scope_kind": "work_front",
            "entries": [
                {
                    "entry_key": "chapter-eleven",
                    "entry_raw": "CAPUT XI. In Bethlehem natus est Christus.",
                    "raw_json": {"entry_kind": "chapter_heading"},
                },
                {
                    "entry_key": "paragraph-one",
                    "entry_raw": "1. Praediximus nativitatem Domini nostri ex Virgine.",
                    "raw_json": {"entry_kind": "numbered_paragraph"},
                },
            ],
        }],
    }

    segments, dispositions = classify_segments(payload)

    assert [ref.entry_key for segment in segments for ref in segment.entries] == [
        "chapter-eleven"
    ]
    assert any(
        item["classification"] == "ineligible_numbered_body_paragraph"
        for item in dispositions
    )


def test_short_plural_chapter_list_with_bare_ordinals_is_not_selected(tmp_path):
    source_root = tmp_path / "PL996" / "text"
    source_root.mkdir(parents=True)
    contents = source_root / "PL996-001.txt"
    body = source_root / "PL996-002.txt"
    write_page(contents, "CAPITULA\nI. Prima materia.\nII. Secunda materia.\nIII. Tertia materia.")
    write_page(body, "CAPUT I. Prima materia.\nCorpus textus.")
    payload = {
        "volume": {"volume_id": "PL996", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(contents), "end_file": str(body)}],
        "sections": [{
            "section_key": "capitula",
            "scope_kind": "work_front",
            "work_key": "work",
            "file_start": str(contents),
            "file_end": str(contents),
            "entries": [{
                "entry_key": "body-one",
                "entry_kind": "chapter",
                "entry_raw": "CAP. I. Prima materia.",
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL996_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    assert manifest["patches"][0]["after_target_file"].endswith("PL996-002.txt")


def test_direct_body_source_provenance_can_resolve_an_owned_heading(tmp_path):
    source_root = tmp_path / "PL995" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PL995-001.txt"
    write_page(body, "EPISTOLA XIV. ENNODIUS FAUSTO.")
    payload = {
        "volume": {"volume_id": "PL995", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(body), "end_file": str(body)}],
        "sections": [{
            "section_key": "divisions",
            "scope_kind": "work_front",
            "work_key": "work",
            "file_start": str(body),
            "file_end": str(body),
            "raw_json": {"entries_are_body_headings": True},
            "entries": [{
                "entry_key": "epistle-14",
                "entry_kind": "epistle_division",
                "entry_raw": "EPISTOLA XIV. ENNODIUS FAUSTO.",
                "raw_json": {"source_files": [str(body)]},
            }],
        }],
    }
    payload_path = tmp_path / "PL995_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["resolved"] == 1
    assert manifest["patches"][0]["physical_target_evidence"]["method"] == "direct_source_heading"


def test_wrapped_ordo_title_is_joined_for_search_without_merging_payload_rows(tmp_path):
    source_root = tmp_path / "PL994" / "text"
    source_root.mkdir(parents=True)
    body = source_root / "PL994-001.txt"
    index = source_root / "PL994-002.txt"
    write_page(body, "CAPUT I. De horis canonicis passionis commemoratione.")
    write_page(index, "ORDO RERUM")
    payload = {
        "volume": {"volume_id": "PL994", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(body), "end_file": str(body)}],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "work_key": "work",
            "file_start": str(index),
            "file_end": str(index),
            "entries": [
                {
                    "entry_key": "chapter-1",
                    "entry_kind": "chapter",
                    "entry_raw": "CAP. I. De horis canonicis 873",
                    "target_raw": "CAP. I. De horis canonicis",
                    "raw_json": {},
                },
                {
                    "entry_key": "continuation",
                    "entry_kind": "continuation",
                    "entry_raw": "passionis commemoratione.",
                    "raw_json": {},
                },
            ],
        }],
    }
    payload_path = tmp_path / "PL994_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["eligible"] == 1
    assert manifest["counts"]["resolved"] == 1
    assert manifest["patches"][0]["entry_key"] == "chapter-1"


def make_payload(tmp_path):
    source_root = tmp_path / "PL999" / "text"
    source_root.mkdir(parents=True)
    first = source_root / "PL999-001.txt"
    second = source_root / "PL999-002.txt"
    index = source_root / "PL999-003.txt"
    write_page(first, "CAPUT I. De prima materia.")
    write_page(second, "CAPUT II. De secunda materia.")
    write_page(index, "CAPUT I. Index entry that must be excluded.")
    payload = {
        "volume": {"volume_id": "PL999", "source_root": str(source_root)},
        "works": [
            {
                "work_key": "work-1",
                "start_file": str(first),
                "end_file": str(second),
            }
        ],
        "sections": [
            {
                "section_key": "ordo-1",
                "scope_kind": "volume_end",
                "work_key": "work-1",
                "file_start": str(index),
                "file_end": str(index),
                "entries": [
                    {
                        "entry_key": "chapter-1",
                        "entry_kind": "chapter_entry",
                        "entry_raw": "CAPUT I. De prima materia. 1",
                        "raw_json": {},
                    },
                    {
                        "entry_key": "chapter-2",
                        "entry_kind": "chapter_entry",
                        "entry_raw": "CAPUT II. De secunda materia. 2",
                        "target_file": str(first),
                        "raw_json": {
                            "prior_payload_locator": {
                                "target_anchor": "verified work opening file; chapter file not separately resolved"
                            }
                        },
                    },
                ],
            }
        ],
    }
    payload_path = tmp_path / "PL999_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload_path, payload


def test_manifest_resolves_body_headings_and_apply_validates_hashes(tmp_path):
    payload_path, before = make_payload(tmp_path)
    manifest = build_repair_manifest(payload_path, workers=2)

    assert manifest["attempted_equals_eligible"] is True
    assert manifest["counts"] == {
        "eligible": 2,
        "resolved": 2,
        "ambiguous": 0,
        "unresolved": 0,
        "conflict": 0,
        "already_canonical": 0,
        "preserved_existing": 0,
        "patches": 2,
    }
    assert all("PL999-003" not in patch["after_target_file"] for patch in manifest["patches"])
    second_patch = next(patch for patch in manifest["patches"] if patch["entry_key"] == "chapter-2")
    assert second_patch["physical_target_evidence"]["disposition"] == "replaced_generic_work_anchor"
    assert second_patch["physical_target_evidence"]["replaced_target_file"].endswith("PL999-001.txt")

    path, after = apply_manifest_volume(manifest)
    validate_repaired_payload(copy.deepcopy(before), after, manifest)
    assert path == payload_path.resolve()
    assert after["sections"][0]["entries"][0]["target_file"].endswith("PL999-001.txt")
    evidence = after["sections"][0]["entries"][0]["raw_json"]["physical_target_evidence"]
    assert evidence["status"] == "resolved"
    assert evidence["locator"] == "deterministic_structural_target_locator"

    tampered = copy.deepcopy(after)
    tampered["works"][0]["title_raw"] = "unauthorized change"
    with pytest.raises(ValueError, match="outside the allowed"):
        validate_repaired_payload(copy.deepcopy(before), tampered, manifest)

    payload_path.write_text(json.dumps({**before, "changed": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="payload hash changed"):
        apply_manifest_volume(manifest)


def test_manifest_replaces_non_resolving_evidence_without_a_target(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    entry = payload["sections"][0]["entries"][0]
    entry["raw_json"] = {
        "physical_target_evidence": {
            "status": "unresolved",
            "method": "table_literal_only",
            "reason": "No body-heading sequence was attempted.",
        }
    }
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    patch = next(item for item in manifest["patches"] if item["entry_key"] == "chapter-1")
    evidence = patch["physical_target_evidence"]
    assert evidence["status"] == "resolved"
    assert evidence["disposition"] == "replaced_unresolved_prior_evidence"
    assert evidence["superseded_evidence"]["method"] == "table_literal_only"


def test_manifest_preserves_resolved_evidence_from_another_producer(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    entry = payload["sections"][0]["entries"][0]
    entry["target_file"] = payload["works"][0]["start_file"]
    entry["raw_json"] = {
        "physical_target_evidence": {
            "status": "resolved",
            "method": "manual_body_heading_review",
            "target_file": entry["target_file"],
        }
    }
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["preserved_existing"] == 1
    assert not any(item["entry_key"] == "chapter-1" for item in manifest["patches"])


def test_monotonic_sequence_replaces_unverified_legacy_target(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    wrong = Path(payload["volume"]["source_root"]) / "PL999-004.txt"
    write_page(wrong, "This is a continuation page without a chapter heading.")
    payload["works"][0]["end_file"] = str(wrong)
    entry = payload["sections"][0]["entries"][1]
    entry["target_file"] = str(wrong)
    entry["raw_json"] = {}
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["apply_ready"] is True
    assert manifest["counts"]["conflict"] == 0
    patch = next(item for item in manifest["patches"] if item["entry_key"] == "chapter-2")
    assert patch["after_target_file"].endswith("PL999-002.txt")
    assert patch["physical_target_evidence"]["disposition"] == "replaced_unverified_legacy_target"


def test_strong_candidate_replaces_unverified_index_page_anchor(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    index = Path(payload["sections"][0]["file_start"])
    entry = payload["sections"][0]["entries"][1]
    entry["target_file"] = str(index)
    entry["raw_json"] = {}
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["conflict"] == 0
    patch = next(item for item in manifest["patches"] if item["entry_key"] == "chapter-2")
    assert patch["after_target_file"].endswith("PL999-002.txt")
    assert patch["physical_target_evidence"]["disposition"] == "replaced_index_page_anchor"


def test_editorial_page_map_replaces_far_legacy_page_mismatch(tmp_path):
    source_root = tmp_path / "PL989" / "text"
    source_root.mkdir(parents=True)
    correct = source_root / "PL989-001.txt"
    wrong = source_root / "PL989-002.txt"
    index = source_root / "PL989-003.txt"
    write_page(correct, "Corpus textus.", header="1 CORPUS 2")
    write_page(wrong, "Alia materia.", header="9 ALIA MATERIA 10")
    write_page(index, "ORDO RERUM", header="11 ORDO RERUM 12")
    payload = {
        "volume": {"volume_id": "PL989", "source_root": str(source_root)},
        "works": [{"work_key": "work", "start_file": str(correct), "end_file": str(wrong)}],
        "sections": [{
            "section_key": "ordo",
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "work_key": "work",
            "file_start": str(index),
            "file_end": str(index),
            "entries": [{
                "entry_key": "chapter-1",
                "entry_kind": "chapter",
                "entry_raw": "CAPUT I. Materia prima. 1",
                "page_ref_int": 1,
                "target_file": str(wrong),
                "raw_json": {},
            }],
        }],
    }
    payload_path = tmp_path / "PL989_indices.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["counts"]["conflict"] == 0
    patch = manifest["patches"][0]
    assert patch["after_target_file"] == str(correct)
    assert patch["physical_target_evidence"]["disposition"] == (
        "replaced_editorial_mismatch_target"
    )


def test_index_compendium_has_no_structural_repair_candidates(tmp_path):
    payload = {
        "volume": {"volume_id": "PL991"},
        "sections": [
            {
                "section_key": "title",
                "scope_kind": "volume_front",
                "index_kind": "volume_title",
                "heading_raw": "PATROLOGIAE LATINAE TOMUS CCXXI. INDICES.",
                "entries": [],
            },
            {
                "section_key": "methodical",
                "scope_kind": "volume_end",
                "index_kind": "INDEX METHODICUS",
                "heading_raw": "INDEX METHODICUS, ORDINE ALPHABETICO DIGESTUS",
                "entries": [{
                    "entry_key": "false-chapter",
                    "entry_kind": "chapter",
                    "entry_raw": "CAPUT I. Cross-volume reference. 10",
                }],
            },
        ],
    }

    segments, dispositions = classify_segments(payload)

    assert segments == []
    assert {item["classification"] for item in dispositions} == {
        "ineligible_index_compendium"
    }


def test_thematic_cross_volume_index_capitula_are_not_work_chapters():
    payload = {
        "volume": {
            "volume_id": "PL990",
            "scan_summary": {
                "closing_material": "Index Patristico-Theologicus; concordance owned by the alphabetical pipeline."
            },
        },
        "sections": [{
            "section_key": "thematic-index-chapters",
            "scope_kind": "volume_end",
            "index_kind": "INDEX CAPITUM",
            "heading_raw": "INDEX CAPITUM",
            "entries": [{
                "entry_key": "topic-1",
                "entry_kind": "chapter_entry",
                "entry_raw": "I. De existentia Dei.",
            }],
        }],
    }

    segments, dispositions = classify_segments(payload)

    assert segments == []
    assert dispositions[0]["classification"] == "ineligible_thematic_reference_index"


def test_indiculus_catalogue_is_not_treated_as_local_structural_targets():
    payload = {
        "volume": {"volume_id": "PL990"},
        "sections": [{
            "section_key": "indiculus",
            "scope_kind": "volume_front",
            "index_kind": "INDICULUS",
            "heading_raw": (
                "INDICULUS LIBRORUM, TRACTATUUM ET EPISTOLARUM "
                "SANCTI AUGUSTINI"
            ),
            "entries": [{
                "entry_key": "catalogue-heading",
                "entry_kind": "chapter_entry",
                "entry_raw": "CAPUT PRIMUM. CONTRA PAGANOS.",
            }],
        }],
    }

    segments, dispositions = classify_segments(payload)

    assert segments == []
    assert dispositions[0]["classification"] == "ineligible_retrospective_scope"


def test_monotonic_sequence_replaces_target_from_stale_locator_version(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    wrong = Path(payload["volume"]["source_root"]) / "PL999-004.txt"
    write_page(wrong, "This is a continuation page without a chapter heading.")
    payload["works"][0]["end_file"] = str(wrong)
    entry = payload["sections"][0]["entries"][1]
    entry["target_file"] = str(wrong)
    entry["raw_json"] = {
        "physical_target_evidence": {
            "locator": "deterministic_structural_target_locator",
            "locator_version": 3,
            "status": "resolved",
            "method": "monotonic_heading_sequence",
            "target_file": str(wrong),
            "reason": "Resolved by an older locator implementation.",
        }
    }
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path)

    assert manifest["apply_ready"] is True
    assert manifest["counts"]["conflict"] == 0
    patch = next(item for item in manifest["patches"] if item["entry_key"] == "chapter-2")
    evidence = patch["physical_target_evidence"]
    assert patch["after_target_file"].endswith("PL999-002.txt")
    assert evidence["disposition"] == "replaced_stale_locator_target"
    assert evidence["superseded_evidence"]["locator_version"] == 3


def test_reviewed_override_can_clear_a_verified_false_legacy_target(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    entry = payload["sections"][0]["entries"][0]
    wrong = Path(payload["volume"]["source_root"]) / "PL999-004.txt"
    write_page(wrong, "Continuation without the indexed structural opening.")
    payload["works"][0]["end_file"] = str(wrong)
    entry["target_file"] = str(wrong)
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    override = {
        "volume_id": "PL999",
        "section_key": "ordo-1",
        "entry_key": "chapter-1",
        "entry_raw_sha256": hashlib.sha256(entry["entry_raw"].encode("utf-8")).hexdigest(),
        "before_target_file": wrong.name,
        "after_target_file": None,
        "review_id": "test-reviewed-clear",
        "reason": "The legacy target was inspected and is not a chapter opening.",
    }

    manifest = build_repair_manifest(payload_path, reviewed_overrides=[override])

    assert manifest["apply_ready"] is True
    assert manifest["reviewed_override_count"] == 1
    patch = next(item for item in manifest["patches"] if item["entry_key"] == "chapter-1")
    assert patch["after_target_file"] is None
    assert patch["physical_target_evidence"]["disposition"] == (
        "cleared_reviewed_invalid_legacy_target"
    )
    path, after = apply_manifest_volume(manifest)
    validate_repaired_payload(payload, after, manifest)
    assert path == payload_path.resolve()
    assert "target_file" not in after["sections"][0]["entries"][0]
    payload_path.write_text(json.dumps(after), encoding="utf-8")

    repeated = build_repair_manifest(payload_path, reviewed_overrides=[override])

    assert repeated["reviewed_override_count"] == 0
    assert repeated["reviewed_override_already_applied_count"] == 1
    assert repeated["counts"]["patches"] == 0


def test_apply_command_writes_backup_before_payload(tmp_path):
    payload_path, original = make_payload(tmp_path)
    manifest = build_repair_manifest(payload_path, workers=1)
    report_path = tmp_path / "dry-run.json"
    report_path.write_text(
        json.dumps(
            {
                "operation": "deterministic_structural_target_repair",
                "mode": "dry_run",
                "apply_ready": True,
                "config": {
                    "minimum_sequence_coverage": 0.8,
                    "minimum_title_similarity": 0.58,
                    "minimum_unique_similarity": 0.82,
                    "minimum_unique_margin": 0.15,
                },
                "volumes": [manifest],
            }
        ),
        encoding="utf-8",
    )
    backup_dir = tmp_path / "backup"
    applied_report = tmp_path / "applied.json"
    database = tmp_path / "indices.db"
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "scripts" / "import_index_json.py"),
            "--input",
            str(payload_path),
            "--db",
            str(database),
            "--replace",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = run_apply(
        Namespace(
            apply_report=report_path,
            backup_dir=backup_dir,
            applied_report=applied_report,
            reimport_db=database,
            payload_dir=tmp_path,
            workers=2,
        )
    )

    assert result["patch_count"] == 2
    assert json.loads((backup_dir / payload_path.name).read_text(encoding="utf-8")) == original
    updated = json.loads(payload_path.read_text(encoding="utf-8"))
    assert updated["sections"][0]["entries"][1]["target_file"].endswith("PL999-002.txt")
    assert json.loads(applied_report.read_text(encoding="utf-8"))["mode"] == "apply"
    assert (backup_dir / database.name).is_file()
    with sqlite3.connect(database) as con:
        target = con.execute(
            "SELECT target_file FROM index_entries WHERE entry_raw LIKE 'CAPUT II.%'"
        ).fetchone()[0]
        assert target.endswith("PL999-002.txt")


def test_apply_can_select_only_resolved_reviewed_overrides(tmp_path):
    payload_path, original = make_payload(tmp_path)
    entry = original["sections"][0]["entries"][0]
    reviewed_target = Path(original["volume"]["source_root"]) / "PL999-001.txt"
    override = {
        "volume_id": "PL999",
        "section_key": "ordo-1",
        "entry_key": "chapter-1",
        "entry_raw_sha256": hashlib.sha256(entry["entry_raw"].encode("utf-8")).hexdigest(),
        "before_target_file": None,
        "after_target_file": reviewed_target.name,
        "review_id": "test-reviewed-resolved-only",
        "reason": "The chapter opening was checked manually in OCR and facsimile.",
    }
    manifest = build_repair_manifest(payload_path, workers=1, reviewed_overrides=[override])
    assert len(manifest["patches"]) == 2
    report_path = tmp_path / "dry-run-reviewed-only.json"
    report_path.write_text(
        json.dumps(
            {
                "operation": "deterministic_structural_target_repair",
                "mode": "dry_run",
                "apply_ready": False,
                "config": {},
                "volumes": [manifest],
            }
        ),
        encoding="utf-8",
    )

    result = run_apply(
        Namespace(
            apply_report=report_path,
            backup_dir=tmp_path / "backup-reviewed-only",
            applied_report=tmp_path / "applied-reviewed-only.json",
            reimport_db=None,
            payload_dir=tmp_path,
            workers=1,
            only_reviewed_resolved=True,
        )
    )

    updated = json.loads(payload_path.read_text(encoding="utf-8"))
    first, second = updated["sections"][0]["entries"]
    assert result["patch_count"] == 1
    assert result["selection"] == "reviewed_resolved_only"
    assert first["target_file"] == str(reviewed_target)
    assert first["raw_json"]["physical_target_evidence"]["method"] == "reviewed_override"
    assert second["target_file"].endswith("PL999-001.txt")
    assert "physical_target_evidence" not in second["raw_json"]


def test_corpus_lock_blocks_concurrent_readers_and_writers(tmp_path):
    lock_path = tmp_path / "index.lock"

    with index_operation_lock(lock_path, exclusive=True):
        with pytest.raises(LockUnavailableError, match="lock is busy"):
            with index_operation_lock(lock_path, exclusive=False):
                pass
        with pytest.raises(LockUnavailableError, match="lock is busy"):
            with index_operation_lock(lock_path, exclusive=True):
                pass

    with index_operation_lock(lock_path, exclusive=True):
        pass


def test_active_transaction_blocks_other_index_operations(tmp_path):
    lock_path = tmp_path / "index.lock"
    active = tmp_path / ".structural-target-repair" / "active_transaction.json"
    active.parent.mkdir()
    active.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ActiveTransactionError, match="must be recovered"):
        with index_operation_lock(lock_path, exclusive=False):
            pass
    with index_operation_lock(
        lock_path,
        exclusive=True,
        active_transaction_path=active,
        allow_active_transaction=True,
    ):
        pass


def test_apply_failpoint_rolls_back_payload_and_database(tmp_path, monkeypatch):
    payload_path, original = make_payload(tmp_path)
    manifest = build_repair_manifest(payload_path, workers=1)
    report_path = tmp_path / "dry-run.json"
    report_path.write_text(
        json.dumps(
            {
                "operation": "deterministic_structural_target_repair",
                "mode": "dry_run",
                "apply_ready": True,
                "config": {},
                "volumes": [manifest],
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "indices.db"
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "scripts" / "import_index_json.py"),
            "--input",
            str(payload_path),
            "--db",
            str(database),
            "--replace",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    before_db_sha = sqlite_logical_sha(database, state_dir)
    monkeypatch.setenv("STRUCTURAL_TARGET_REPAIR_FAILPOINT", "AFTER_FIRST_JSON")

    with pytest.raises(RuntimeError, match="AFTER_FIRST_JSON"):
        run_apply(
            Namespace(
                apply_report=report_path,
                backup_dir=tmp_path / "backup-failed",
                applied_report=tmp_path / "applied-failed.json",
                reimport_db=database,
                payload_dir=tmp_path,
                workers=2,
                state_dir=state_dir,
                lock_file=tmp_path / "index.lock",
                lock_timeout=0,
            )
        )

    assert json.loads(payload_path.read_text(encoding="utf-8")) == original
    assert sqlite_logical_sha(database, state_dir) == before_db_sha
    assert not transaction_paths(state_dir)[0].exists()
    transaction = json.loads(
        (tmp_path / "backup-failed" / "transaction_state.json").read_text(encoding="utf-8")
    )
    assert transaction["phase"] == "ROLLED_BACK"


def test_recovery_replaces_corrupt_database_from_durable_backup(tmp_path):
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    target = payload_dir / "PL999_indices.json"
    target.write_text('{"status":"before"}\n', encoding="utf-8")
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    backup = backup_dir / target.name
    copy_file_atomic(target, backup)
    database = tmp_path / "indices.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('before')")
        connection.commit()
    database_backup = backup_dir / database.name
    before_db_sha = sqlite_snapshot(database, database_backup)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    active_path, _ = transaction_paths(state_dir)
    state = {
        "schema_version": 1,
        "operation": "deterministic_structural_target_repair",
        "transaction_id": "test-corrupt-db",
        "phase": "INSTALLING_DATABASE",
        "sequence": 0,
        "backup_dir": str(backup_dir),
        "files": [
            {
                "target": str(target),
                "backup": str(backup),
                "before_sha256": sha256_file(backup),
                "after_sha256": sha256_file(target),
            }
        ],
        "database": {
            "target": str(database),
            "backup": str(database_backup),
            "before_sha256": before_db_sha,
            "after_sha256": before_db_sha,
        },
    }
    persist_transaction_state(active_path, state)
    database.write_bytes(b"not a database")

    recovered = recover_interrupted_transaction(state_dir, payload_dir)

    assert recovered["phase"] == "ROLLED_BACK"
    assert sqlite_logical_sha(database, state_dir) == before_db_sha
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "before"
    assert not active_path.exists()


def test_committed_recovery_recreates_applied_report_and_returns_success(tmp_path):
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    target = payload_dir / "PL999_indices.json"
    target.write_text('{"status":"after"}\n', encoding="utf-8")
    backup_dir = tmp_path / "backup-committed"
    backup_dir.mkdir()
    backup = backup_dir / target.name
    backup.write_text('{"status":"before"}\n', encoding="utf-8")
    report_path = tmp_path / "dry-run-committed.json"
    report_path.write_text("{}\n", encoding="utf-8")
    applied_report = tmp_path / "applied-committed.json"
    summary = {
        "operation": "deterministic_structural_target_repair",
        "mode": "apply",
        "transaction_id": "committed-test",
    }
    state_dir = tmp_path / "state-committed"
    state_dir.mkdir()
    active_path, _ = transaction_paths(state_dir)
    state = {
        "schema_version": 1,
        "operation": "deterministic_structural_target_repair",
        "transaction_id": "committed-test",
        "phase": "COMMITTED",
        "sequence": 0,
        "source_report": str(report_path),
        "backup_dir": str(backup_dir),
        "files": [
            {
                "target": str(target),
                "backup": str(backup),
                "before_sha256": sha256_file(backup),
                "after_sha256": sha256_file(target),
            }
        ],
        "database": None,
        "applied_report": str(applied_report),
        "applied_summary": summary,
    }
    persist_transaction_state(active_path, state)

    result = run_apply(
        Namespace(
            apply_report=report_path,
            payload_dir=payload_dir,
            state_dir=state_dir,
            lock_file=tmp_path / "index-committed.lock",
            lock_timeout=0,
        )
    )

    assert result == summary
    assert json.loads(applied_report.read_text(encoding="utf-8")) == summary
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "after"}
    assert not active_path.exists()


def test_retrospective_scope_is_not_eligible(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    payload["sections"][0]["scope_kind"] = "retrospective_table"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path, workers=1)

    assert manifest["counts"]["eligible"] == 0
    assert manifest["counts"]["patches"] == 0
    assert manifest["dispositions"][0]["classification"] == "ineligible_retrospective_scope"


def test_multiordinal_entry_is_quarantined_from_singular_target(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    payload["sections"][0]["entries"] = [
        {
            "entry_key": "chapters-1-2",
            "entry_kind": "chapter_entry",
            "entry_raw": "CAPP. I et II. De prima et secunda materia.",
            "raw_json": {},
        }
    ]
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path, workers=1)

    assert manifest["counts"]["eligible"] == 0
    assert manifest["counts"]["patches"] == 0
    assert manifest["dispositions"][0]["classification"] == "ineligible_multi_ordinal"


def test_unresolved_evidence_stays_in_report_sidecar(tmp_path):
    payload_path, payload = make_payload(tmp_path)
    payload["sections"][0]["entries"] = [
        {
            "entry_key": "chapter-99",
            "entry_kind": "chapter_entry",
            "entry_raw": "CAPUT XCIX. Materia quae nusquam invenitur.",
            "raw_json": {},
        }
    ]
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_repair_manifest(payload_path, workers=1)

    assert manifest["counts"]["unresolved"] == 1
    assert manifest["counts"]["patches"] == 0
    assert len(manifest["observations"]) == 1
    assert manifest["observations"][0]["physical_target_evidence"]["status"] == "unresolved"
