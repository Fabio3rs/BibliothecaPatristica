from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.alphabetical_compact_pipeline import (
    CompactPipelineError,
    assemble_compact_payload,
    build_deterministic_locator_results,
    build_locator_items,
    build_repair_request,
    coerce_semantic_payload,
    merge_repair_results,
    shard_repair_request,
    shard_locator_items,
    validate_locator_results,
)


def semantic_payload(*, ref_count: int = 2, collection: str = "PL") -> dict:
    refs = []
    for ref_order in range(1, ref_count + 1):
        refs.append(
            {
                "entry_key": "VOL:index:e1",
                "ref_order": ref_order,
                "ref_kind": "editorial_page",
                "ref_raw": str(100 + ref_order),
                "page_ref_raw": str(100 + ref_order),
                "page_ref_int": 100 + ref_order,
                "target_file": None,
                "target_file_probability": None,
                "raw_json": {},
            }
        )
    return {
        "schema_version": 1,
        "generated_at": None,
        "volume": {
            "volume_id": f"{collection}001",
            "collection": collection,
            "source_root": f"/corpus/{collection}001/text",
            "volume_label": f"{collection} 1",
        },
        "sections": [
            {
                "section_key": "VOL:index",
                "section_kind": "onomastic_person",
                "heading_raw": "INDEX NOMINUM",
            }
        ],
        "nodes": [],
        "entries": [
            {
                "entry_key": "VOL:index:e1",
                "section_key": "VOL:index",
                "entry_order": 1,
                "lemma_raw": "Aaron",
                "entry_raw": "Aaron, 101, 102",
                "context_raw": "Aaron, 101, 102",
                "target_file_best": "/stale/value.txt",
            }
        ],
        "refs": refs,
        "scripture_refs": [],
        "coverage": {},
        "notes": [],
    }


def resolved(entry_key: str, ref_order: int, target: str, confidence: float) -> dict:
    return {
        "entry_key": entry_key,
        "ref_order": ref_order,
        "status": "resolved",
        "target_file": target,
        "confidence": confidence,
        "evidence": [{"kind": "header_pair"}],
    }


def test_coerce_accepts_canonical_fragments_and_explicit_lists() -> None:
    payload = semantic_payload(ref_count=1)
    assert coerce_semantic_payload(payload)["refs"][0]["page_ref_int"] == 101

    fragments = [
        {
            "volume_id": "PL001",
            "status": "complete",
            "sections": payload["sections"],
            "entries": payload["entries"],
            "refs": payload["refs"],
        }
    ]
    assert coerce_semantic_payload(fragments)["volume"]["volume_id"] == "PL001"

    explicit = coerce_semantic_payload(
        volume=payload["volume"],
        sections=payload["sections"],
        entries=payload["entries"],
        refs=payload["refs"],
    )
    assert len(explicit["entries"]) == 1

    assembled = coerce_semantic_payload(
        {
            "schema_version": 1,
            "volume_id": "PL001",
            "status": "complete",
            "data": fragments[0],
        }
    )
    assert set(assembled) == {
        "schema_version",
        "generated_at",
        "volume",
        "sections",
        "nodes",
        "entries",
        "refs",
        "scripture_refs",
        "coverage",
        "notes",
    }


def test_locator_item_exposes_unambiguous_coordinate_contract() -> None:
    payload = semantic_payload(ref_count=1)
    payload["sections"][0].update(
        {
            "file_start": "/corpus/PL001/text/index-900.txt",
            "file_end": "/corpus/PL001/text/index-905.txt",
        }
    )
    payload["entries"][0]["editorial_anchor_file"] = (
        "/corpus/PL001/text/index-901.txt"
    )

    item = build_locator_items(payload)[0]
    contract = item["locator_contract"]

    assert item["locator_format_version"] == 2
    assert contract["index_source"]["index_entry_source_ocr_file"].endswith(
        "index-901.txt"
    )
    assert contract["cited_location"][
        "cited_editorial_page_start_number"
    ] == 101
    assert contract["target_resolution"]["status"] == "pending"
    assert "resolved_target_ocr_file" not in contract["index_source"]


def test_page_less_work_locator_can_resolve_from_strict_three_signal_bundle() -> None:
    item = {
        "locator_key": "PG003:e1::ref:000001",
        "entry_key": "PG003:e1",
        "ref_order": 1,
        "ref_kind": "target_locator",
        "ref_raw": "Myst. Theol. cap. 2",
        "cited_pages": [],
        "candidates": [
            {
                "file": "/corpus/PG003/text/work-010.txt",
                "probability": 0.96,
                "helper_status": "resolved",
                "helper_is_best": True,
                "candidate_role": "target_candidate",
                "evidence": [
                    {"kind": "body_work_locator_match"},
                    {"kind": "work_locator_number_cooccurrence"},
                    {"kind": "section_heading_family_match"},
                ],
            }
        ],
    }

    resolved_items, pending = build_deterministic_locator_results([item])

    assert pending == []
    assert resolved_items[0]["target_file"].endswith("work-010.txt")
    report = validate_locator_results([item], resolved_items)
    assert report["status"] == "ok"


def test_repair_request_is_checkpointable_in_bounded_shards() -> None:
    request = {
        "schema_version": 1,
        "items": [
            {"locator": {"entry_key": f"e{index}", "ref_order": 1}}
            for index in range(5)
        ],
    }

    shards = shard_repair_request(request, shard_size=2)

    assert [shard["item_count"] for shard in shards] == [2, 2, 1]
    assert [shard["repair_shard_id"] for shard in shards] == [
        "repair-0001",
        "repair-0002",
        "repair-0003",
    ]


@pytest.mark.parametrize(
    "section",
    [
        {
            "section_key": "VOL:ordo",
            "section_kind": "ordo_rerum",
            "heading_raw": "INDEX",
        },
        {
            "section_key": "VOL:ordo",
            "section_kind": "alphabetical_general",
            "heading_raw": "ORDO RERUM QUAE IN HOC TOMO CONTINENTUR",
        },
    ],
)
def test_ordo_rerum_is_rejected_as_boundary_only(section: dict) -> None:
    payload = semantic_payload(ref_count=1)
    payload["sections"] = [section]
    payload["entries"][0]["section_key"] = section["section_key"]
    with pytest.raises(CompactPipelineError, match="boundary-only"):
        coerce_semantic_payload(payload)


def test_editorial_closure_is_rejected_from_compact_alpha_payload() -> None:
    payload = semantic_payload(ref_count=1)
    payload["sections"][0]["section_kind"] = "editorial_closure"
    payload["sections"][0]["heading_raw"] = "ADDENDA ET CORRIGENDA"

    with pytest.raises(CompactPipelineError, match="general index pipeline"):
        coerce_semantic_payload(payload)


def test_distinct_biblical_citations_must_be_split_into_entries() -> None:
    payload = semantic_payload(ref_count=2)
    payload["entries"][0]["entry_kind"] = "scripture_citation"
    payload["scripture_refs"] = [
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "Rom. 1, 1",
        },
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 2,
            "ref_role": "citation",
            "ref_raw": "Rom. 2, 1",
        },
    ]

    with pytest.raises(CompactPipelineError, match="split distinct passages"):
        coerce_semantic_payload(payload)


def test_remissive_biblical_entry_requires_a_material_ref() -> None:
    payload = semantic_payload(ref_count=0)
    payload["entries"][0]["entry_kind"] = "scripture_citation"
    payload["scripture_refs"] = [
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "Rom. 1, 1",
        }
    ]

    with pytest.raises(CompactPipelineError, match="has no material refs"):
        coerce_semantic_payload(payload)

    payload["sections"][0]["raw_json"] = {
        "material_reference_mode": "source_only"
    }
    assert coerce_semantic_payload(payload)["scripture_refs"]

    payload["sections"][0]["raw_json"] = {}
    payload["entries"][0]["raw_json"] = {
        "material_reference_mode": "source_only"
    }
    assert coerce_semantic_payload(payload)["scripture_refs"]


def test_locator_items_are_per_reference_and_include_facing_page_candidates() -> None:
    semantic = semantic_payload()
    semantic["refs"][0].update(
        {
            "page_ref_col": "b",
            "line_ref_raw": "lin. 12",
            "range_start_int": 101,
            "range_end_int": 103,
        }
    )
    items = build_locator_items(
        semantic,
        {
            "files": [
                {
                    "file": "/corpus/page-050.txt",
                    "best_left_page": 101,
                    "best_right_page": 102,
                    "confidence_label": "high",
                    "evidence": [{"kind": "header_pair"}],
                }
            ]
        },
    )

    assert [(item["entry_key"], item["ref_order"]) for item in items] == [
        ("VOL:index:e1", 1),
        ("VOL:index:e1", 2),
    ]
    assert items[0]["candidates"][0]["file"] == "/corpus/page-050.txt"
    assert items[1]["candidates"][0]["file"] == "/corpus/page-050.txt"
    assert items[0]["page_ref_col"] == "b"
    assert items[0]["line_ref_raw"] == "lin. 12"
    assert items[0]["range_start_int"] == 101
    assert items[0]["range_end_int"] == 103
    assert items[0]["cited_pages"] == [101, 103, 102]


def test_locator_items_keep_bounded_secondary_editorial_hypotheses() -> None:
    semantic = semantic_payload(ref_count=1)
    semantic["refs"][0]["page_ref_int"] = None
    semantic["refs"][0]["page_ref_raw"] = "105"
    items = build_locator_items(
        semantic,
        {
            "files": [
                {
                    "file": "/corpus/page-050.txt",
                    "best_guess": [101, 102],
                    "confidence": 0.6,
                    "candidate_editorial_pages": [
                        {
                            "pages": [101, 102],
                            "evidence": [{"kind": "header_pair"}],
                        },
                        {
                            "pages": [105, 106],
                            "evidence": [{"kind": "header_pair_nonconsecutive"}],
                        },
                        {
                            "pages": [999],
                            "evidence": [{"kind": "body_top_fallback"}],
                        },
                    ],
                }
            ]
        },
    )

    assert items[0]["cited_pages"] == [105]
    assert items[0]["candidates"][0]["file"] == "/corpus/page-050.txt"
    assert items[0]["candidates"][0]["matched_page"] == 105


def test_locator_items_allow_empty_map_for_po() -> None:
    items = build_locator_items(semantic_payload(ref_count=1, collection="PO"), None)
    assert items[0]["candidates"] == []


def test_deterministic_locator_requires_unique_page_and_scripture_evidence() -> None:
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": {"book_norm": "Gênesis", "chapter_start": 1, "verse_start": 1},
        "candidates": [
            {
                "file": "/corpus/page-001.txt",
                "probability": 0.91,
                "evidence": [
                    {"kind": "header_pair", "detail": "101/102"},
                    {"kind": "scripture_regex", "raw": "Gen. 1, 1"},
                ],
            }
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert pending == []
    assert resolved_results[0]["target_file"] == "/corpus/page-001.txt"
    assert resolved_results[0]["status"] == "resolved"


@pytest.mark.parametrize(
    "evidence",
    [
        [{"kind": "header_pair"}],
        [
            {"kind": "page_drift_explanation"},
            {"kind": "scripture_regex"},
        ],
    ],
)
def test_deterministic_locator_rejects_single_signal_or_drift_only(
    evidence: list[dict],
) -> None:
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": {"book_norm": "Gênesis", "chapter_start": 1, "verse_start": 1},
        "candidates": [
            {
                "file": "/corpus/page-001.txt",
                "probability": 0.99,
                "evidence": evidence,
            }
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert resolved_results == []
    assert len(pending) == 1


def test_deterministic_locator_keeps_competing_dual_candidates_pending() -> None:
    candidate = {
        "probability": 0.95,
        "evidence": [
            {"kind": "editorial_header_match"},
            {"kind": "scripture_regex"},
        ],
    }
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": {"book_norm": "Gênesis", "chapter_start": 1, "verse_start": 1},
        "candidates": [
            {"file": "/corpus/page-001.txt", **candidate},
            {"file": "/corpus/page-101.txt", **candidate},
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert resolved_results == []
    assert len(pending) == 1


def test_deterministic_locator_resolves_unique_generic_dual_evidence() -> None:
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": None,
        "candidates": [
            {
                "file": "/corpus/page-001.txt",
                "probability": 0.96,
                "candidate_role": "target_candidate",
                "helper_status": "resolved",
                "helper_is_best": True,
                "evidence": [
                    {"kind": "inferred_page_match", "raw": "101"},
                    {"kind": "body_name_match", "raw": "Aaron"},
                ],
            }
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert pending == []
    assert resolved_results[0]["target_file"] == "/corpus/page-001.txt"
    assert resolved_results[0]["evidence"][-1]["kind"] == (
        "deterministic_dual_evidence"
    )


def test_deterministic_locator_keeps_generic_numeric_only_candidate_pending() -> None:
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": None,
        "candidates": [
            {
                "file": "/corpus/page-001.txt",
                "probability": 0.99,
                "candidate_role": "target_candidate",
                "helper_status": "resolved",
                "helper_is_best": True,
                "evidence": [{"kind": "inferred_page_match", "raw": "101"}],
            }
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert resolved_results == []
    assert len(pending) == 1


def test_deterministic_locator_accepts_unique_internal_name_locator_pair() -> None:
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": None,
        "candidates": [
            {
                "file": "/corpus/page-001.txt",
                "probability": 0.88,
                "candidate_role": "target_candidate",
                "helper_status": "resolved",
                "helper_is_best": True,
                "evidence": [
                    {"kind": "body_locator_match", "raw": "114"},
                    {
                        "kind": "body_locator_name_unique",
                        "raw": "Gregorius + 114",
                    },
                    {
                        "kind": "internal_locator_vs_editorial_sequence",
                        "raw": "114 vs 153-154 with neighbor_fit",
                    },
                    {"kind": "body_name_match", "raw": "Gregorius"},
                ],
            }
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert pending == []
    assert resolved_results[0]["target_file"] == "/corpus/page-001.txt"


def test_deterministic_locator_keeps_crossed_name_hints_as_region_evidence() -> None:
    item = {
        "entry_key": "VOL:index:e1",
        "ref_order": 1,
        "scripture_ref": None,
        "candidates": [
            {
                "file": "/corpus/work-a-001.txt",
                "probability": 0.97,
                "candidate_role": "target_candidate",
                "helper_status": "resolved",
                "helper_is_best": True,
                "evidence": [
                    {"kind": "body_name_match", "raw": "Gregorius"},
                    {
                        "kind": "onomastic_neighbor_cohort_unique",
                        "raw": "Zacharias + Stephanus in the same local family",
                    },
                ],
            }
        ],
    }

    resolved_results, pending = build_deterministic_locator_results([item])

    assert resolved_results == []
    assert len(pending) == 1


def test_semantic_coercion_applies_only_forced_mechanical_repairs() -> None:
    payload = semantic_payload(ref_count=2)
    payload["entries"][0]["entry_kind"] = "scripture_citation"
    payload["refs"][0].update(
        {
            "page_ref_raw": "101",
            "page_ref_int": None,
            "scripture_ref_order": None,
            "target_file": "/premature.txt",
            "target_file_probability": 0.8,
        }
    )
    payload["refs"][1].update(
        {
            "ref_raw": "ibid.",
            "page_ref_raw": "ibid.",
            "page_ref_int": None,
            "scripture_ref_order": None,
        }
    )
    payload["scripture_refs"] = [
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "Gen. 1, 1",
        }
    ]

    coerced = coerce_semantic_payload(payload)

    assert coerced["entries"][0]["target_file_best"] is None
    assert coerced["refs"][0]["page_ref_int"] == 101
    assert coerced["refs"][0]["target_file"] is None
    assert coerced["refs"][0]["scripture_ref_order"] == 1
    assert coerced["refs"][1]["page_ref_int"] == 101
    assert coerced["refs"][1]["scripture_ref_order"] == 1
    repair_kinds = {
        repair["kind"]
        for repair in coerced["refs"][1]["raw_json"]["deterministic_repairs"]
    }
    assert "resolve_immediate_ibid" in repair_kinds
    assert "link_single_scripture_parent" in repair_kinds


def test_locator_item_carries_only_its_linked_scripture_reference() -> None:
    payload = semantic_payload(ref_count=1)
    payload["entries"][0]["entry_kind"] = "scripture_citation"
    payload["refs"][0]["scripture_ref_order"] = 1
    payload["scripture_refs"] = [
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 1,
            "ref_role": "citation",
            "ref_raw": "I Cor. 1, 4",
            "book_raw": "I Cor.",
            "book_norm": "1 Coríntios",
            "chapter_start": 1,
            "verse_start": 4,
        }
    ]

    item = build_locator_items(payload)[0]

    assert item["scripture_ref_order"] == 1
    assert item["scripture_ref"] == {
        "ref_order": 1,
        "ref_role": "citation",
        "ref_raw": "I Cor. 1, 4",
        "book_raw": "I Cor.",
        "book_norm": "1 Coríntios",
        "chapter_start": 1,
        "verse_start": 4,
    }


def test_sharding_is_deterministic_and_has_no_overlap() -> None:
    payload = semantic_payload(ref_count=81)
    items = build_locator_items(payload)
    shards = shard_locator_items(items)
    reversed_shards = shard_locator_items(list(reversed(items)))

    assert [shard["item_count"] for shard in shards] == [40, 40, 1]
    assert reversed_shards == shards
    owned = [
        (item["entry_key"], item["ref_order"])
        for shard in shards
        for item in shard["items"]
    ]
    assert len(owned) == len(set(owned)) == 81
    assert [shard["shard_id"] for shard in shards] == [
        "locator-0001",
        "locator-0002",
        "locator-0003",
    ]


def test_locator_shard_truncates_repeated_semantic_context() -> None:
    payload = semantic_payload(ref_count=40)
    payload["entries"][0]["entry_raw"] = "A" * 1_000_000
    payload["entries"][0]["context_raw"] = "B" * 1_000_000

    shard = shard_locator_items(build_locator_items(payload))[0]
    encoded = json.dumps(shard, ensure_ascii=False).encode("utf-8")

    assert len(shard["items"][0]["entry_excerpt"]) == 300
    assert len(shard["items"][0]["context_excerpt"]) == 300
    assert len(encoded) < 80_000


def test_validation_requires_exact_results_and_repair_for_ambiguity() -> None:
    items = build_locator_items(semantic_payload())
    report = validate_locator_results(
        items,
        [
            resolved("VOL:index:e1", 1, "/page-a.txt", 0.8),
            {
                "entry_key": "VOL:index:e1",
                "ref_order": 2,
                "status": "ambiguous",
                "target_file": None,
            },
        ],
    )

    assert report["status"] == "pending_repair"
    assert report["accepted_count"] == 1
    assert report["pending"][0]["ref_order"] == 2


def test_validation_accepts_auditable_terminal_result_without_second_agent() -> None:
    items = build_locator_items(semantic_payload(ref_count=1))
    report = validate_locator_results(
        items,
        [
            {
                "entry_key": "VOL:index:e1",
                "ref_order": 1,
                "status": "unrecoverable_ocr",
                "target_file": None,
                "reason": "The candidate headers are illegible.",
                "evidence": [
                    {"kind": "ocr_search", "detail": "No readable digits"}
                ],
            }
        ],
    )

    assert report["status"] == "ok"
    assert report["accepted_results"][0]["attempted_evidence"] == [
        {"kind": "ocr_search", "detail": "No readable digits"}
    ]


def test_assembler_assigns_each_ref_and_best_entry_target() -> None:
    payload = assemble_compact_payload(
        semantic_payload(),
        [
            resolved("VOL:index:e1", 1, "/page-a.txt", 0.8),
            resolved("VOL:index:e1", 2, "/page-b.txt", 0.95),
        ],
    )

    assert [ref["target_file"] for ref in payload["refs"]] == [
        "/page-a.txt",
        "/page-b.txt",
    ]
    assert payload["entries"][0]["target_file_best"] == "/page-b.txt"
    assert payload["coverage"]["locator_status"] == "complete"


def test_best_entry_target_tie_uses_lower_ref_order() -> None:
    payload = assemble_compact_payload(
        semantic_payload(),
        [
            resolved("VOL:index:e1", 1, "/page-a.txt", 0.9),
            resolved("VOL:index:e1", 2, "/page-b.txt", 0.9),
        ],
    )
    assert payload["entries"][0]["target_file_best"] == "/page-a.txt"


def test_repair_request_contains_only_pending_locator_context() -> None:
    items = build_locator_items(semantic_payload())
    base_results = [
        resolved("VOL:index:e1", 1, "/page-a.txt", 0.8),
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 2,
            "status": "ambiguous",
        },
    ]
    report = validate_locator_results(items, base_results)
    request = build_repair_request(items, report)

    assert request["item_count"] == 1
    assert request["items"][0]["locator"]["ref_order"] == 2
    assert "entries" not in request


def test_post_repair_accepts_auditable_unrecoverable_and_marks_partial() -> None:
    semantic = semantic_payload()
    base = [
        resolved("VOL:index:e1", 1, "/page-a.txt", 0.8),
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 2,
            "status": "ambiguous",
        },
    ]
    repaired = [
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 2,
            "status": "unrecoverable_ocr",
            "target_file": None,
            "reason": "header and body digits are illegible",
            "attempted_evidence": [
                {"file": "/candidate.txt", "search": "102", "result": "no signal"}
            ],
        }
    ]

    merged = merge_repair_results(base, repaired)
    payload = assemble_compact_payload(semantic, merged, post_repair=True)

    assert payload["refs"][1]["target_file"] is None
    assert payload["coverage"]["locator_status"] == "partial"
    assert "entries_status" not in payload["coverage"]
    assert "semantic entry extraction is unchanged" in payload["coverage"][
        "locator_status_reason"
    ]
    assert payload["coverage"]["locator_unrecoverable_refs"][0]["ref_order"] == 2


def test_post_repair_rejects_unrecoverable_without_attempted_evidence() -> None:
    results = [
        resolved("VOL:index:e1", 1, "/page-a.txt", 0.8),
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 2,
            "status": "unrecoverable_ocr",
            "target_file": None,
            "reason": "OCR digits cannot be recovered",
        },
    ]
    with pytest.raises(CompactPipelineError, match="attempted evidence"):
        assemble_compact_payload(semantic_payload(), results, post_repair=True)


def test_post_repair_accepts_auditable_ambiguity_and_preserves_all_attempts() -> None:
    results = [
        resolved("VOL:index:e1", 1, "/page-a.txt", 0.8),
        {
            "entry_key": "VOL:index:e1",
            "ref_order": 2,
            "status": "ambiguous",
            "target_file": None,
            "reason": "Both neighbors carry the same damaged facing-page header.",
            "competing_candidates": [
                {"file": "/page-b.txt", "probability": 0.5},
                {"file": "/page-c.txt", "probability": 0.5},
            ],
            "attempted_evidence": [{"kind": "neighbor_sequence"}],
            "attempted_files": ["/page-b.txt", "/page-c.txt"],
            "attempted_searches": ["rg -n 102"],
        },
    ]

    payload = assemble_compact_payload(
        semantic_payload(), results, post_repair=True
    )

    locator = payload["refs"][1]["raw_json"]["compact_locator"]
    assert locator["status"] == "ambiguous"
    assert len(locator["competing_candidates"]) == 2
    assert locator["attempted_evidence"] == [{"kind": "neighbor_sequence"}]
    assert locator["attempted_files"] == ["/page-b.txt", "/page-c.txt"]
    assert locator["attempted_searches"] == ["rg -n 102"]
    assert payload["coverage"]["locator_status"] == "partial"
    assert payload["coverage"]["locator_ambiguous_refs"][0]["ref_order"] == 2


def test_validation_sends_out_of_volume_target_to_repair(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    outside = tmp_path / "other.txt"
    outside.write_text("101", encoding="utf-8")
    items = build_locator_items(semantic_payload(ref_count=1))

    report = validate_locator_results(
        items,
        [resolved("VOL:index:e1", 1, str(outside), 0.9)],
        source_root=source_root,
    )

    assert report["status"] == "pending_repair"
    assert (
        report["pending"][0]["reason"]
        == "resolved target_file points outside source_root"
    )


def test_validation_sends_index_section_target_to_repair(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    section_start = source_root / "page-100.txt"
    target = source_root / "page-105.txt"
    section_end = source_root / "page-110.txt"
    for path in (section_start, target, section_end):
        path.write_text("INDEX", encoding="utf-8")
    semantic = semantic_payload(ref_count=1)
    semantic["sections"][0]["file_start"] = str(section_start)
    semantic["sections"][0]["file_end"] = str(section_end)
    items = build_locator_items(semantic)

    report = validate_locator_results(
        items,
        [resolved("VOL:index:e1", 1, str(target), 0.9)],
        source_root=source_root,
    )

    assert report["status"] == "pending_repair"
    assert (
        report["pending"][0]["reason"]
        == "resolved target_file points inside the physical index section"
    )


def test_validation_requires_page_specific_evidence() -> None:
    items = build_locator_items(semantic_payload(ref_count=1))
    result = resolved("VOL:index:e1", 1, "/page-a.txt", 0.9)
    result["evidence"] = []

    report = validate_locator_results(items, [result])

    assert report["pending"][0]["reason"] == (
        "resolved result requires non-empty page-specific evidence"
    )


@pytest.mark.parametrize("kind", ["scripture_regex", "body_citation_match"])
def test_body_scripture_hit_is_not_page_specific_locator_evidence(kind: str) -> None:
    items = build_locator_items(semantic_payload(ref_count=1))
    result = resolved("VOL:index:e1", 1, "/page-a.txt", 0.9)
    result["evidence"] = [{"kind": kind}]

    report = validate_locator_results(items, [result])

    assert report["status"] == "pending_repair"
    assert report["pending"][0]["reason"] == (
        "resolved result requires non-empty page-specific evidence"
    )
