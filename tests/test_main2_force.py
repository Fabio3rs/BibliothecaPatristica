from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main2


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("without_comparison", [False, True])
def test_verify_fix_force_bypasses_quality_gate(tmp_path, monkeypatch, force, without_comparison):
    pdf = tmp_path / "PG001.pdf"
    pdf.touch()
    images = [tmp_path / f"PG001-{page}.png" for page in (638, 639)]
    prepare = Mock(return_value=images)
    verify = Mock(return_value=images[1:])
    extract = Mock()
    monkeypatch.setattr(main2, "pages_to_images", prepare)
    monkeypatch.setattr(main2, "verify_all_parallel", verify)
    monkeypatch.setattr(main2, "ocr_images_to_text_parallel", extract)
    argv = ["main2.py", str(pdf), "--out", str(tmp_path / "out"),
            "--verify-fix", "--first", "638", "--last", "639", "--procs", "2"]
    if force:
        argv.append("--force")
    if without_comparison:
        argv.append("--do-not-reprocess-compare")
    monkeypatch.setattr(sys, "argv", argv)

    main2.main()

    assert prepare.call_args.kwargs["first_page"] == 638
    assert prepare.call_args.kwargs["last_page"] == 639
    if force:
        verify.assert_not_called()
    else:
        verify.assert_called_once()
    extract.assert_called_once()
    assert extract.call_args.args[0] == (images if force else images[1:])
    reason = "verify_forced" if force else "verify_failed"
    if without_comparison:
        reason += "_do_not_reprocess_compare"
    assert extract.call_args.kwargs["reprocess_reason"] == reason
    assert extract.call_args.kwargs["processes"] == 2


@pytest.mark.parametrize("flags", [[], ["--verify"], ["--verify-judge-llm"],
                                  ["--verify-fix", "--tesseract-cache-only"]])
def test_force_rejects_modes_where_it_would_be_ignored(monkeypatch, flags):
    prepare = Mock()
    monkeypatch.setattr(main2, "pages_to_images", prepare)
    monkeypatch.setattr(sys, "argv", ["main2.py", "unused.pdf", "--force", *flags])
    with pytest.raises(SystemExit) as error:
        main2.main()
    assert error.value.code == 2
    prepare.assert_not_called()


@pytest.mark.parametrize(("existing", "reason"), [
    (True, None),
    (True, "verify_forced"),
    (False, "verify_forced"),
    (True, "verify_forced_do_not_reprocess_compare"),
])
def test_worker_reprocesses_existing_xml_and_versions_reason(tmp_path, monkeypatch, existing, reason):
    text_dir = tmp_path / "PG001" / "text"
    text_dir.mkdir(parents=True)
    image = tmp_path / "PG001" / "images" / "PG001-638.png"
    text_path = text_dir / "PG001-638.txt"
    before = '<pagina estado="com_texto"><bloco>Textus prior.</bloco></pagina>'
    after = '<pagina estado="com_texto"><bloco>Textus correctus.</bloco></pagina>'
    if existing:
        text_path.write_text(before, encoding="utf-8")
    model = Mock(return_value=after)
    tesseract = Mock(return_value="Textus auxiliaris.")
    record = Mock()
    monkeypatch.setattr(main2, "llm_process_image_autoretry", model)
    monkeypatch.setattr(main2, "open_tesseract_cache_db", Mock())
    monkeypatch.setattr(main2, "init_tesseract_cache", Mock())
    monkeypatch.setattr(main2, "run_tesseract_cached", tesseract)
    monkeypatch.setattr(main2, "version_tesseract_result", Mock())
    monkeypatch.setattr(main2.ocr_versions_db, "open_versions_db", Mock())
    monkeypatch.setattr(main2.ocr_versions_db, "get_or_create_engine_version", Mock(return_value=1))
    monkeypatch.setattr(main2.ocr_versions_db, "record_ocr_result", record)

    result = main2._ocr_one(image, text_dir, lang="migne", algorithm="openai",
                            llm_model="test-model", ollama_url="unused", openai_base_url="unused",
                            openai_api_key=None, reprocess_reason=reason)

    if reason is None:
        assert result == before
        model.assert_not_called()
        record.assert_not_called()
        tesseract.assert_not_called()
    else:
        assert result == after
        assert text_path.read_text(encoding="utf-8") == after
        model.assert_called_once()
        assert model.call_args.kwargs["reprocess"] is True
        record.assert_called_once()
        assert record.call_args.kwargs["reprocess_reason"] == reason
        if reason.endswith("do_not_reprocess_compare"):
            tesseract.assert_not_called()
        else:
            tesseract.assert_called_once()


def test_sparse_pages_do_not_touch_neighbors_and_prepare_missing_images(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    chosen = images / "legacy-004.png"
    neighbor = images / "legacy-005.png"
    chosen.write_bytes(b"selected")
    neighbor.write_bytes(b"untouched")
    calls = []

    def convert(pdf, **kwargs):
        calls.append(kwargs)
        page = kwargs["first_page"]
        assert kwargs["last_page"] == page
        path = images / f"generated-{page:03d}.png"
        path.write_bytes(b"new")
        return [str(path)]

    monkeypatch.setattr(main2, "convert_from_path", convert)
    result = main2.pages_to_images(tmp_path / "PG001.pdf", images, selected_pages={4, 15})

    assert [path.name for path in result] == ["PG001-004.png", "PG001-015.png"]
    assert [call["first_page"] for call in calls] == [15]
    assert neighbor.read_bytes() == b"untouched"
    assert not (images / "PG001-005.png").exists()


def test_sparse_pages_reject_ambiguous_images(tmp_path, monkeypatch):
    (tmp_path / "one-004.png").touch()
    (tmp_path / "two-004.png").touch()
    convert = Mock()
    monkeypatch.setattr(main2, "convert_from_path", convert)
    with pytest.raises(RuntimeError, match="ambíguas"):
        main2.pages_to_images(tmp_path / "PG001.pdf", tmp_path, selected_pages={4})
    convert.assert_not_called()


def test_sparse_selection_respects_first_last_and_never_falls_back_to_whole_pdf(tmp_path, monkeypatch):
    convert = Mock()
    monkeypatch.setattr(main2, "convert_from_path", convert)
    assert main2.pages_to_images(tmp_path / "PG001.pdf", tmp_path, selected_pages={4, 15},
                                first_page=6, last_page=10) == []
    convert.assert_not_called()


@pytest.mark.parametrize("pages", ["0", "-2", "", "4,invalid"])
def test_cli_rejects_invalid_page_selection(monkeypatch, pages):
    monkeypatch.setattr(sys, "argv", ["main2.py", "unused.pdf", f"--pages={pages}"])
    with pytest.raises(SystemExit) as error:
        main2.main()
    assert error.value.code == 2


def test_cli_passes_exact_sparse_selection(tmp_path, monkeypatch):
    pdf = tmp_path / "PG001.pdf"
    pdf.touch()
    prepare = Mock(return_value=[])
    monkeypatch.setattr(main2, "pages_to_images", prepare)
    monkeypatch.setattr(sys, "argv", ["main2.py", str(pdf), "--out", str(tmp_path / "out"),
                                     "--pages", "4,15,638,4", "--verify-fix", "--force"])
    main2.main()
    assert prepare.call_args.kwargs["selected_pages"] == {4, 15, 638}
