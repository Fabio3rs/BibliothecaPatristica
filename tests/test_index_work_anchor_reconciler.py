from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.index_work_anchor_reconciler import (
    _candidate_has_material_start_sequence,
    reconcile_work_anchors,
)


def _write_page(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<pagina><bloco tipo=\"texto_principal\">{body}</bloco></pagina>",
        encoding="utf-8",
    )


def test_reconciler_uses_index_hint_text_and_editorial_map(tmp_path: Path) -> None:
    source_root = tmp_path / "PG001" / "text"
    index_file = source_root / "page-739.txt"
    target_file = source_root / "page-175.txt"
    end_file = source_root / "page-254.txt"
    _write_page(index_file, "ORDO RERUM EPISTOLAE DUAE AD VIRGINES 519")
    _write_page(target_file, "EPISTOLAE DUAE AD VIRGINES")
    _write_page(end_file, "FINIS")
    payload = {
        "volume": {
            "volume_id": "PG001",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PG001:work:virgines",
                "work_order": 1,
                "title_raw": "EPISTOLAE DUAE AD VIRGINES",
                "title_norm": "Epistolae duae ad virgines",
                "start_page": 579,
                "end_page": 508,
                "start_file": str(index_file),
                "end_file": str(index_file),
                "source_section_key": "PG001:index:end",
                "raw_json": {},
            }
        ],
        "sections": [
            {
                "section_key": "PG001:index:end",
                "scope_kind": "volume_end",
                "file_start": str(index_file),
                "file_end": str(index_file),
                "entries": [
                    {
                        "target_raw": "EPISTOLAE DUAE AD VIRGINES",
                        "page_ref_int": 349,
                        "raw_json": {},
                    }
                ],
            }
        ],
        "notes": [],
    }
    editorial = {
        "files": [
            {
                "file": str(target_file),
                "best_guess": [349, 350],
                "confidence": 0.9,
            },
            {
                "file": str(end_file),
                "best_guess": [507, 508],
                "confidence": 0.9,
            },
            {
                "file": str(index_file),
                "best_guess": [1477, 1478],
                "confidence": 0.9,
            },
        ]
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                        "entry_id": "work:0",
                        "status": "resolved",
                        "candidates": [
                        {
                            "file": str(target_file),
                            "image_file": str(source_root.parent / "images" / "PG001-175.png"),
                            "score": 11.5,
                            "probability": 0.95,
                            "inferred_printed_page": 349,
                            "estimator_pages": [349, 350],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "body_name_match", "weight": 2.5}
                            ],
                        },
                        {
                            "file": str(index_file),
                            "score": 9.0,
                            "probability": 0.05,
                            "inferred_printed_page": 1477,
                            "estimator_pages": [1477, 1478],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "body_name_match", "weight": 2.5}
                            ],
                        },
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages=editorial,
        locator=fake_locator,
    )
    work = payload["works"][0]

    assert report["changed_count"] == 1
    assert work["start_page"] == 349
    assert work["end_page"] == 508
    assert work["start_file"] == str(target_file.resolve())
    assert work["end_file"] == str(index_file)
    assert "end_file" not in report["items"][0]["changes"]
    assert "deterministic_anchor_reconciliation" in work["raw_json"]


def test_reconciler_preserves_resolved_manual_anchor_review(tmp_path: Path) -> None:
    source_root = tmp_path / "PG001" / "text"
    index_file = source_root / "page-010.txt"
    target_file = source_root / "page-175.txt"
    _write_page(index_file, "ORDO RERUM OPUS REVISUM 349")
    _write_page(target_file, "OPUS REVISUM")
    payload = {
        "volume": {"volume_id": "PG001", "source_root": str(source_root)},
        "works": [
            {
                "work_key": "PG001:work:revisum",
                "work_order": 1,
                "title_raw": "OPUS REVISUM",
                "start_page": 349,
                "start_file": str(target_file),
                "source_section_key": "PG001:index:end",
                "raw_json": {
                    "manual_anchor_review": {
                        "status": "resolved",
                        "evidence": "OCR and paired image inspected",
                    },
                    "work_anchor_rerun": {"status": "unresolved"},
                },
            }
        ],
        "sections": [
            {
                "section_key": "PG001:index:end",
                "scope_kind": "volume_end",
                "file_start": str(index_file),
                "file_end": str(index_file),
            }
        ],
    }

    def locator_must_not_run(_request: dict) -> dict:
        raise AssertionError("manual review must bypass automatic relocation")

    report = reconcile_work_anchors(
        payload,
        editorial_pages={
            "files": [
                {
                    "file": str(target_file),
                    "best_guess": [351, 352],
                    "confidence": 0.9,
                }
            ]
        },
        locator=locator_must_not_run,
    )

    assert report["changed_count"] == 0
    assert report["items"][0]["status"] == "manually_resolved"
    assert report["items"][0]["decision_reason"] == "manual_anchor_review"
    assert payload["works"][0]["start_page"] == 349
    assert payload["works"][0]["start_file"] == str(target_file)
    assert "work_anchor_rerun" not in payload["works"][0]["raw_json"]


def test_reconciler_does_not_exclude_material_volume_end_appendix(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG031" / "text"
    appendix_start = source_root / "page-773.txt"
    target_file = source_root / "page-830.txt"
    appendix_end = source_root / "page-937.txt"
    _write_page(appendix_start, "APPENDIX OPERUM")
    _write_page(target_file, "LITURGIA S BASILII")
    _write_page(appendix_end, "APPENDIX FINIS")
    payload = {
        "volume": {"volume_id": "PG031", "source_root": str(source_root)},
        "works": [
            {
                "work_key": "PG031:work:liturgia",
                "title_raw": "Liturgia S. Basilii",
                "start_page": 1630,
                "start_file": None,
                "raw_json": {},
            }
        ],
        "sections": [
            {
                "section_key": "PG031:appendix",
                "scope_kind": "volume_end",
                "index_kind": "APPENDIX OPERUM S. BASILII",
                "heading_raw": "APPENDIX OPERUM S. BASILII",
                "file_start": str(appendix_start),
                "file_end": str(appendix_end),
            }
        ],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(target_file),
                            "score": 12.0,
                            "probability": 0.99,
                            "candidate_role": "target_candidate",
                            "estimator_pages": [1629, 1630],
                            "estimator_confidence": 0.99,
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0}
                            ],
                        }
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    assert report["changed_count"] == 1
    assert payload["works"][0]["start_file"] == str(target_file.resolve())
    assert report["items"][0]["chosen_candidate"]["excluded_index_source"] is False


def test_reconciler_accepts_confirmed_material_start_sequence_despite_score_gap(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG030" / "text"
    target_file = source_root / "page-024.txt"
    runner_up = source_root / "page-026.txt"
    _write_page(target_file, "ORATIO II")
    _write_page(runner_up, "ORATIO II")
    payload = {
        "volume": {"volume_id": "PG030", "source_root": str(source_root)},
        "works": [
            {
                "work_key": "PG030:work:oratio_ii",
                "title_raw": "Oratio II",
                "start_page": 38,
                "start_file": None,
                "raw_json": {},
            }
        ],
        "sections": [],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "ambiguous",
                    "ambiguity_reason": "candidate_probability_gap",
                    "candidates": [
                        {
                            "file": str(target_file),
                            "score": 15.0,
                            "probability": 0.55,
                            "candidate_role": "target_candidate",
                            "estimator_pages": [37, 38],
                            "estimator_confidence": 0.94,
                            "header_sequence": {
                                "start_file": str(target_file),
                                "end_file": str(runner_up),
                                "is_sequence_start": True,
                                "match_count": 6,
                            },
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0},
                                {"kind": "header_sequence_start", "weight": 1.5},
                            ],
                        },
                        {
                            "file": str(runner_up),
                            "score": 14.2,
                            "probability": 0.45,
                            "candidate_role": "mixed_signal",
                            "estimator_pages": [41, 42],
                            "estimator_confidence": 0.95,
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0}
                            ],
                        },
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    assert report["changed_count"] == 1
    assert report["items"][0]["decision_reason"] == (
        "resolved_by_material_start_sequence"
    )
    assert payload["works"][0]["start_file"] == str(target_file.resolve())
    assert payload["works"][0]["start_page"] == 38


def test_material_start_sequence_does_not_rewrite_unsupported_declared_page() -> None:
    candidate = {
        "candidate_role": "target_candidate",
        "estimator_pages": [1797, 1798],
        "estimator_confidence": 0.9,
        "header_sequence": {"is_sequence_start": True, "match_count": 8},
        "evidence": [{"kind": "header_name_match"}],
    }

    assert not _candidate_has_material_start_sequence(
        candidate,
        preferred_page=1795,
    )


def test_reconciler_searches_stable_title_nucleus_for_descriptive_index_title(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PG001" / "text"
    index_file = source_root / "page-004.txt"
    target_file = source_root / "page-175.txt"
    _write_page(index_file, "ELENCHUS EPISTOLAE DUAE AD VIRGINES 349")
    _write_page(target_file, "EPISTOLAE DUAE AD VIRGINES")
    long_title = (
        "Epistolae duae ad virgines (Syriace et Latine), interprete "
        "D. Cl. Villecourt, S. R. E. cardinali."
    )
    payload = {
        "volume": {
            "volume_id": "PG001",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PG001:work:epistolae_duae_ad_virgines",
                "work_order": 1,
                "title_raw": long_title,
                "title_norm": long_title,
                "start_page": 349,
                "end_page": None,
                "start_file": None,
                "end_file": None,
                "source_section_key": "PG001:index",
                "raw_json": {},
            }
        ],
        "sections": [
            {
                "section_key": "PG001:index",
                "scope_kind": "volume_front",
                "file_start": str(index_file),
                "file_end": str(index_file),
                "entries": [],
            }
        ],
        "notes": [],
    }

    def fake_locator(request: dict) -> dict:
        entry = request["entries"][0]
        assert entry["query_match_mode"] == "best"
        assert "Epistolae duae ad virgines" in entry["query_names"]
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(index_file),
                            "score": 12.5,
                            "probability": 0.7,
                            "estimator_pages": [7, 8],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "body_name_match", "weight": 2.5}
                            ],
                        },
                        {
                            "file": str(target_file),
                            "image_file": str(
                                source_root.parent / "images" / "PG001-175.png"
                            ),
                            "score": 11.5,
                            "probability": 0.3,
                            "inferred_printed_page": 349,
                            "estimator_pages": [349, 350],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "header_name_fuzzy", "weight": 1.9}
                            ],
                        },
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    assert report["changed_count"] == 1
    assert report["items"][0]["decision_reason"] == "resolved"
    assert report["items"][0]["chosen_candidate"]["image_file"].endswith(
        "PG001-175.png"
    )
    assert payload["works"][0]["start_file"] == str(target_file.resolve())
    assert payload["works"][0]["start_page"] == 349


def test_reconciler_marks_ambiguous_anchor_for_agent_rerun(tmp_path: Path) -> None:
    source_root = tmp_path / "PGX" / "text"
    index_file = source_root / "page-010.txt"
    first_candidate = source_root / "page-100.txt"
    second_candidate = source_root / "page-200.txt"
    _write_page(index_file, "ORDO RERUM OPUS DUBIUM 349")
    _write_page(first_candidate, "OPUS DUBIUM")
    _write_page(second_candidate, "OPUS DUBIUM")
    payload = {
        "volume": {
            "volume_id": "PGX",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PGX:work:dubium",
                "work_order": 1,
                "title_raw": "OPUS DUBIUM",
                "title_norm": "Opus dubium",
                "start_page": 349,
                "end_page": 400,
                "start_file": str(index_file),
                "end_file": None,
                "source_section_key": "PGX:index:end",
                "raw_json": {},
            }
        ],
        "sections": [
            {
                "section_key": "PGX:index:end",
                "scope_kind": "volume_end",
                "file_start": str(index_file),
                "file_end": str(index_file),
                "entries": [],
            }
        ],
        "notes": [],
    }

    def fake_locator(_request: dict) -> dict:
        candidates = [
            {
                "file": str(path),
                "score": score,
                "probability": probability,
                "candidate_role": "target_candidate",
                "estimator_pages": [349, 350],
                "estimator_confidence": 0.9,
                "header_sequence": {
                    "start_file": str(path),
                    "end_file": str(path),
                    "match_count": 4,
                },
                "evidence": [{"kind": "header_name_match", "weight": 4.0}],
            }
            for path, score, probability in (
                (first_candidate, 10.0, 0.52),
                (second_candidate, 9.9, 0.48),
            )
        ]
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "ambiguous",
                    "ambiguity_reason": "candidate_probability_gap",
                    "candidates": candidates,
                }
            ]
        }

    original_start_file = payload["works"][0]["start_file"]
    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    marker = payload["works"][0]["raw_json"]["work_anchor_rerun"]
    assert report["changed_count"] == 0
    assert report["annotation_changed_count"] == 1
    assert report["payload_mutation_count"] == 1
    assert report["ambiguous_count"] == 1
    assert payload["works"][0]["start_file"] == original_start_file
    assert marker["status"] == "ambiguous"
    assert marker["locator"]["ambiguity_reason"] == "candidate_probability_gap"
    assert len(marker["locator"]["candidates"]) == 2
    assert marker["locator"]["candidates"][0]["header_sequence"]["match_count"] == 4
    assert any("paired page image" in check for check in marker["agent_checks"])


def test_reconciler_preserves_declared_start_at_beginning_of_header_sequence(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PGS" / "text"
    current_file = source_root / "page-100.txt"
    later_file = source_root / "page-110.txt"
    _write_page(current_file, "101 OPUS CONTINUUM 102")
    _write_page(later_file, "121 OPUS CONTINUUM 122")
    sequence = {
        "start_file": str(current_file),
        "end_file": str(later_file),
        "match_count": 6,
        "is_sequence_start": True,
    }
    payload = {
        "volume": {
            "volume_id": "PGS",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PGS:work:continuum",
                "work_order": 1,
                "title_raw": "OPUS CONTINUUM",
                "title_norm": "Opus continuum",
                "start_page": 121,
                "end_page": 200,
                "start_file": str(current_file),
                "end_file": None,
                "source_section_key": None,
                "raw_json": {},
            }
        ],
        "sections": [],
        "notes": [],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(later_file),
                            "score": 14.0,
                            "probability": 0.9,
                            "inferred_printed_page": 121,
                            "estimator_pages": [121, 122],
                            "estimator_confidence": 0.9,
                            "header_sequence": {
                                **sequence,
                                "is_sequence_start": False,
                            },
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0}
                            ],
                        },
                        {
                            "file": str(current_file),
                            "score": 10.0,
                            "probability": 0.1,
                            "inferred_printed_page": 101,
                            "estimator_pages": [101, 102],
                            "estimator_confidence": 0.9,
                            "header_sequence": sequence,
                            "evidence": [
                                {"kind": "header_sequence_match", "weight": 2.5}
                            ],
                        },
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={
            "files": [
                {
                    "file": str(current_file),
                    "best_guess": [101, 102],
                    "confidence": 0.9,
                }
            ]
        },
        locator=fake_locator,
    )

    work = payload["works"][0]
    assert report["changed_count"] == 1
    assert report["items"][0]["decision_reason"] == (
        "current_anchor_confirmed_by_header_sequence"
    )
    assert work["start_file"] == str(current_file)
    assert work["start_page"] == 101


def test_reconciler_preserves_declared_page_when_estimator_pair_contains_it(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PGP" / "text"
    target_file = source_root / "page-159.txt"
    _write_page(target_file, "309 DE SACRIS IMAGINIBUS 310")
    payload = {
        "volume": {
            "volume_id": "PGP",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PGP:work:imagines",
                "work_order": 1,
                "title_raw": "DE SACRIS IMAGINIBUS",
                "title_norm": "De sacris imaginibus",
                "start_page": 310,
                "end_page": None,
                "start_file": None,
                "end_file": None,
                "source_section_key": None,
                "raw_json": {},
            }
        ],
        "sections": [],
        "notes": [],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(target_file),
                            "score": 11.0,
                            "probability": 1.0,
                            "inferred_printed_page": 309,
                            "estimator_pages": [309, 310],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0}
                            ],
                        }
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    assert report["changed_count"] == 1
    assert payload["works"][0]["start_file"] == str(target_file.resolve())
    assert payload["works"][0]["start_page"] == 310
    assert "start_page" not in report["items"][0]["changes"]


def test_reconciler_marks_shared_boundary_without_clipping_end_page(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "POX" / "text"
    first_file = source_root / "page-001.txt"
    second_file = source_root / "page-100.txt"
    _write_page(first_file, "OPUS PRIMUM")
    _write_page(second_file, "OPUS SECUNDUM")
    payload = {
        "volume": {
            "volume_id": "POX",
            "collection": "PO",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "POX:work:1",
                "work_order": 1,
                "title_raw": "OPUS PRIMUM",
                "start_page": 1,
                "end_page": 185,
                "start_file": str(first_file),
                "end_file": str(second_file),
                "source_section_key": "POX:table",
                "raw_json": {},
            },
            {
                "work_key": "POX:work:2",
                "work_order": 2,
                "title_raw": "OPUS SECUNDUM",
                "start_page": 185,
                "end_page": 360,
                "start_file": str(second_file),
                "end_file": None,
                "source_section_key": "POX:table",
                "raw_json": {},
            },
        ],
        "sections": [
            {
                "section_key": "POX:table",
                "scope_kind": "volume_end",
                "file_start": None,
                "file_end": None,
                "entries": [],
            }
        ],
        "notes": [],
    }

    report = reconcile_work_anchors(
        payload,
        editorial_pages=None,
        locator=lambda _request: {"entries": []},
    )

    first = payload["works"][0]
    marker = first["raw_json"]["work_anchor_rerun"]
    assert first["end_page"] == 185
    assert "end_page" not in report["items"][0]["changes"]
    assert report["items"][0]["status"] == "unresolved"
    assert marker["status"] == "ambiguous"
    assert marker["reason"] == (
        "overlapping_editorial_boundaries_require_scan_layout_review"
    )


def test_reconciler_sends_composite_work_container_to_agent(tmp_path: Path) -> None:
    source_root = tmp_path / "PGC" / "text"
    index_file = source_root / "page-010.txt"
    candidate_file = source_root / "page-100.txt"
    _write_page(index_file, "ORDO RERUM")
    _write_page(candidate_file, "OPUS PRIMUM")
    payload = {
        "volume": {
            "volume_id": "PGC",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PGC:container",
                "work_order": 1,
                "title_raw": "OPUS PRIMUM / OPUS SECUNDUM / OPUS TERTIUM / OPUS QUARTUM",
                "title_norm": "Opera collecta",
                "start_page": 1,
                "end_page": 400,
                "start_file": str(index_file),
                "end_file": None,
                "source_section_key": "PGC:index",
                "raw_json": {},
            }
        ],
        "sections": [
            {
                "section_key": "PGC:index",
                "scope_kind": "volume_end",
                "file_start": str(index_file),
                "file_end": str(index_file),
                "entries": [],
            }
        ],
        "notes": [],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(candidate_file),
                            "score": 20.0,
                            "probability": 1.0,
                            "estimator_pages": [199, 200],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0}
                            ],
                        }
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    marker = payload["works"][0]["raw_json"]["work_anchor_rerun"]
    assert report["changed_count"] == 0
    assert report["items"][0]["decision_reason"] == "composite_work_requires_agent"
    assert marker["reason"] == "composite_work_requires_agent"
    assert payload["works"][0]["start_file"] == str(index_file)


def test_reconciler_rejects_candidate_start_after_declared_end(tmp_path: Path) -> None:
    source_root = tmp_path / "PGI" / "text"
    index_file = source_root / "page-010.txt"
    candidate_file = source_root / "page-700.txt"
    _write_page(index_file, "ORDO RERUM")
    _write_page(candidate_file, "LIBRI QUINQUE")
    payload = {
        "volume": {
            "volume_id": "PGI",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PGI:work",
                "work_order": 1,
                "title_raw": "LIBRI QUINQUE",
                "title_norm": "Libri quinque",
                "start_page": 433,
                "end_page": 1320,
                "start_file": str(index_file),
                "end_file": None,
                "source_section_key": "PGI:index",
                "raw_json": {},
            }
        ],
        "sections": [
            {
                "section_key": "PGI:index",
                "scope_kind": "volume_end",
                "file_start": str(index_file),
                "file_end": str(index_file),
                "entries": [],
            }
        ],
        "notes": [],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(candidate_file),
                            "score": 12.0,
                            "probability": 1.0,
                            "inferred_printed_page": 1353,
                            "estimator_pages": [1353, 1354],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "header_name_match", "weight": 4.0}
                            ],
                        }
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={"files": []},
        locator=fake_locator,
    )

    assert report["changed_count"] == 0
    assert report["items"][0]["decision_reason"] == (
        "candidate_start_page_after_end_page"
    )
    assert payload["works"][0]["start_file"] == str(index_file)
    assert payload["works"][0]["start_page"] == 433


def test_reconciler_rejects_running_header_index_page_candidate(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "PGH" / "text"
    current_file = source_root / "page-013.txt"
    later_header = source_root / "page-015.txt"
    _write_page(current_file, "FRAGMENTA IN PROVERBIA SALOMONIS")
    _write_page(later_header, "FRAGMENTA IN PROVERBIA")
    payload = {
        "volume": {
            "volume_id": "PGH",
            "collection": "PG",
            "source_root": str(source_root),
        },
        "works": [
            {
                "work_key": "PGH:work:proverbia",
                "work_order": 1,
                "title_raw": "Fragmenta in Proverbia Salomonis",
                "start_page": 17,
                "end_page": 34,
                "start_file": str(current_file),
                "end_file": None,
                "source_section_key": None,
                "raw_json": {},
            }
        ],
        "sections": [],
        "notes": [],
    }

    def fake_locator(_request: dict) -> dict:
        return {
            "entries": [
                {
                    "entry_id": "work:0",
                    "status": "resolved",
                    "candidates": [
                        {
                            "file": str(later_header),
                            "score": 8.5,
                            "probability": 1.0,
                            "candidate_role": "index_page_candidate",
                            "inferred_printed_page": 21,
                            "estimator_pages": [21, 22],
                            "estimator_confidence": 0.9,
                            "evidence": [
                                {"kind": "header_sequence_start", "weight": 1.5}
                            ],
                        }
                    ],
                }
            ]
        }

    report = reconcile_work_anchors(
        payload,
        editorial_pages={
            "files": [
                {
                    "file": str(current_file),
                    "best_guess": [47, 48],
                    "confidence": 0.9,
                }
            ]
        },
        locator=fake_locator,
    )

    assert report["changed_count"] == 0
    assert report["items"][0]["decision_reason"] == (
        "no_non_index_candidate_with_title_evidence"
    )
    assert payload["works"][0]["start_file"] == str(current_file)
    assert payload["works"][0]["start_page"] == 17
