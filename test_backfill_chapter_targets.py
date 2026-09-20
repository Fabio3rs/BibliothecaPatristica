from scripts.backfill_chapter_targets import prepare_payload_backfill


def _payload(existing_target=None):
    return {
        "volume": {"volume_id": "PG001", "source_root": "/tmp/PG001/text"},
        "sections": [
            {
                "section_key": "section-1",
                "raw_json": {},
                "entries": [
                    {
                        "entry_key": "entry-1",
                        "entry_raw": "CAPP. I et II. Argumentum.",
                        "target_file": existing_target,
                        "raw_json": {},
                    }
                ],
            }
        ],
    }


def _probe(*, accepted=True, target="/tmp/PG001/text/page-101.txt"):
    refs = [
        {
            "ordinal": 1,
            "ordinal_roman": "I",
            "status": "resolved",
            "target": {
                "target_file": target,
                "physical_page": 101,
                "heading_raw": "CAPUT I",
                "line": 2,
                "style": "caput",
                "normalized_similarity": 1.0,
            },
        },
        {
            "ordinal": 2,
            "ordinal_roman": "II",
            "status": "resolved",
            "target": {
                "target_file": "/tmp/PG001/text/page-105.txt",
                "physical_page": 105,
                "heading_raw": "CAPUT II",
                "line": 3,
                "style": "caput",
                "normalized_similarity": 0.9,
            },
        },
    ]
    return {
        "sections": [
            {
                "section_key": "section-1",
                "marker_run_accepted": accepted,
                "selected_marker_family": "chapter_label",
                "proposed_marker_coverage": 1.0,
                "strategy": "test_strategy",
                "body_window": {"start_file": "a", "end_file": "b", "file_count": 2},
                "entries": [
                    {
                        "entry_key": "entry-1",
                        "ordinal_raw": "CAPP. I et II.",
                        "status": "resolved",
                        "chapter_refs": refs,
                    }
                ],
            }
        ]
    }


def test_prepare_payload_backfill_sets_first_target_and_keeps_all_evidence():
    updated, report = prepare_payload_backfill(_payload(), _probe())

    entry = updated["sections"][0]["entries"][0]
    evidence = entry["raw_json"]["ex_post_chapter_target_locator"]
    assert entry["target_file"] == "/tmp/PG001/text/page-101.txt"
    assert [item["ordinal"] for item in evidence["chapter_targets"]] == [1, 2]
    assert report["proposed_update_count"] == 1


def test_prepare_payload_backfill_does_not_change_rejected_run():
    updated, report = prepare_payload_backfill(_payload(), _probe(accepted=False))

    assert updated == _payload()
    assert report["proposed_update_count"] == 0


def test_prepare_payload_backfill_preserves_matching_existing_target():
    payload = _payload(existing_target="/tmp/PG001/text/page-105.txt")
    updated, report = prepare_payload_backfill(payload, _probe())

    assert updated == payload
    assert report["proposed_update_count"] == 0
    assert report["existing_conflict_count"] == 0


def test_prepare_payload_backfill_rejects_section_on_existing_target_conflict():
    payload = _payload(existing_target="/tmp/PG001/text/page-999.txt")
    updated, report = prepare_payload_backfill(payload, _probe())

    assert updated == payload
    assert report["proposed_update_count"] == 0
    assert report["existing_conflict_count"] == 1


def test_prepare_payload_backfill_honors_section_selection():
    payload = _payload()
    updated, report = prepare_payload_backfill(
        payload,
        _probe(),
        allowed_sections={"different-section"},
    )

    assert updated == payload
    assert report["proposed_update_count"] == 0
    assert report["sections"][0]["status"] == "excluded_by_selection"


def _collapsed_payload(*, mixed_existing_targets=False):
    work_start = "/tmp/PG001/text/page-001.txt"
    return {
        "volume": {"volume_id": "PG001", "source_root": "/tmp/PG001/text"},
        "works": [{"work_key": "work-1", "start_file": work_start}],
        "sections": [
            {
                "section_key": "section-1",
                "work_key": "work-1",
                "raw_json": {},
                "entries": [
                    {
                        "entry_key": f"entry-{ordinal}",
                        "entry_raw": f"CAPUT {ordinal}",
                        "target_file": (
                            "/tmp/PG001/text/page-002.txt"
                            if mixed_existing_targets and ordinal == 5
                            else work_start
                        ),
                        "raw_json": {},
                    }
                    for ordinal in range(1, 6)
                ],
            }
        ],
    }


def _segmented_probe():
    return {
        "sections": [
            {
                "section_key": "section-1",
                "marker_run_accepted": True,
                "selected_marker_family": "chapter_label",
                "proposed_marker_coverage": 1.0,
                "normalized_similarity_mean": 0.95,
                "resolved_targets_monotonic": True,
                "segmented_run_count": 2,
                "segment_coverages": [1.0, 1.0],
                "strategy": "test_segmented_strategy",
                "body_window": {"start_file": "a", "end_file": "b", "file_count": 10},
                "entries": [
                    {
                        "entry_key": f"entry-{ordinal}",
                        "ordinal_raw": str(ordinal),
                        "status": "resolved",
                        "chapter_refs": [
                            {
                                "ordinal": ordinal,
                                "ordinal_roman": str(ordinal),
                                "status": "resolved",
                                "target": {
                                    "target_file": f"/tmp/PG001/text/page-{100 + ordinal:03d}.txt",
                                    "physical_page": 100 + ordinal,
                                    "heading_raw": f"CAPUT {ordinal}",
                                    "line": 1,
                                    "style": "caput",
                                    "normalized_similarity": 0.95,
                                },
                            }
                        ],
                    }
                    for ordinal in range(1, 6)
                ],
            }
        ]
    }


def test_prepare_payload_backfill_replaces_systematic_work_start_collapse():
    work_start = "/tmp/PG001/text/page-001.txt"
    updated, report = prepare_payload_backfill(
        _collapsed_payload(),
        _segmented_probe(),
        replace_collapsed_work_start_targets=True,
    )

    entries = updated["sections"][0]["entries"]
    assert report["proposed_update_count"] == 5
    assert report["existing_conflict_count"] == 0
    assert report["replaced_conflict_count"] == 5
    assert entries[0]["target_file"] == "/tmp/PG001/text/page-101.txt"
    evidence = entries[0]["raw_json"]["ex_post_chapter_target_locator"]
    assert evidence["replaced_target_file"] == work_start
    assert evidence["replacement_reason"] == "systematic_work_start_anchor_collapse"


def test_prepare_payload_backfill_does_not_replace_mixed_existing_targets():
    payload = _collapsed_payload(mixed_existing_targets=True)
    updated, report = prepare_payload_backfill(
        payload,
        _segmented_probe(),
        replace_collapsed_work_start_targets=True,
    )

    assert updated == payload
    assert report["proposed_update_count"] == 0
    assert report["existing_conflict_count"] == 5
    assert report["replaced_conflict_count"] == 0


def test_prepare_payload_backfill_clears_unresolved_prior_collapsed_anchor():
    payload = _collapsed_payload()
    probe = _segmented_probe()
    payload["sections"][0]["raw_json"]["ex_post_chapter_target_locator"] = {
        "version": 4,
        "replacement_mode": "systematic_work_start_anchor_collapse",
    }
    for ordinal, entry in enumerate(payload["sections"][0]["entries"][:4], start=1):
        entry["target_file"] = f"/tmp/PG001/text/page-{100 + ordinal:03d}.txt"
    unresolved = probe["sections"][0]["entries"][-1]
    unresolved["status"] = "partial"
    unresolved["chapter_refs"][0]["status"] = "missing"
    unresolved["chapter_refs"][0].pop("target")

    updated, report = prepare_payload_backfill(
        payload,
        probe,
        replace_collapsed_work_start_targets=True,
    )

    cleared = updated["sections"][0]["entries"][-1]
    assert cleared["target_file"] is None
    assert report["proposed_update_count"] == 5
    assert report["cleared_collapsed_target_count"] == 1
    assert report["verified_existing_target_count"] == 4
    evidence = cleared["raw_json"]["ex_post_chapter_target_locator"]
    assert evidence["replacement_reason"] == (
        "cleared_unresolved_systematic_work_start_anchor"
    )
