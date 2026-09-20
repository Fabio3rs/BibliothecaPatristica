import json

from scripts import run_index_extraction


def test_validate_and_import_existing_always_replaces(monkeypatch, tmp_path):
    payload_file = tmp_path / "PG001_indices.json"
    payload_file.write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PG001"},
                "works": [],
                "sections": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(
        run_index_extraction,
        "write_pipeline_quality_reports",
        lambda **kwargs: calls.append(("quality", kwargs)),
    )
    monkeypatch.setattr(
        run_index_extraction,
        "import_payload",
        lambda *args, **kwargs: calls.append(("import", args, kwargs)),
    )

    counts = run_index_extraction.validate_and_import_existing_payload(
        payload_file=payload_file,
        assembled_file=None,
        intermediate_dir=tmp_path,
        db_path=tmp_path / "indices.db",
        volume_id="PG001",
        evidence_sample_size=20,
        max_unverified_ratio=0.25,
        skip_evidence_check=False,
    )

    assert counts == {"works": 0, "sections": 0, "entries": 0}
    assert calls[0][0] == "quality"
    assert calls[1][0] == "import"
    assert calls[1][1][2] is True
    assert calls[1][2]["caller_holds_lock"] is True
