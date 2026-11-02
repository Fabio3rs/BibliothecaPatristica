"""Cache provenance and single-request repair contracts, without HTTP/inference."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from PIL import Image
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_ocr import cached_repair as repair
from offline_ocr.selection import digest

FIXTURES = Path(__file__).parent / "fixtures/ocr_diff_v2"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not call HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


@pytest.fixture
def sources(tmp_path):
    image = tmp_path / "PG001-638.png"
    Image.new("L", (16, 16), 255).save(image)
    image_hash = digest(image.read_bytes())
    draft = (FIXTURES / "PG001_638_current.xml").read_text()
    cache_text = (FIXTURES / "PG001_638_migne_cached.txt").read_text()
    db, cache_db = tmp_path / "versions.db", tmp_path / "tesseract.db"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE ocr_engine_versions (id INTEGER PRIMARY KEY, engine TEXT, model TEXT, prompt_key TEXT);
            CREATE TABLE ocr_results (id INTEGER PRIMARY KEY, engine_version_id INTEGER, volume_id TEXT,
                page_num INTEGER, image_hash TEXT, text_content TEXT, text_hash TEXT, is_current INTEGER,
                created_at TEXT);
            INSERT INTO ocr_engine_versions VALUES (1, 'openai', 'gpt-5-mini', 'PROMPT');
            INSERT INTO ocr_engine_versions VALUES (2, 'tesseract', 'migne', 'tesseract_cache');
            INSERT INTO ocr_engine_versions VALUES (3, 'ollama', 'qwen3.5:cloud', 'PROMPT');
        """)
        for row_id, engine, text, date in [(809912, 1, draft, '2026-06-26'),
                                           (853769, 2, cache_text, '2026-07-18'),
                                           (357449, 3, draft, '2026-04-15')]:
            con.execute("INSERT INTO ocr_results VALUES (?, ?, 'PG001', 638, ?, ?, ?, ?, ?)",
                        (row_id, engine, image_hash, text, digest(text.encode()), row_id == 809912, date))
    key = image_hash + ':migne:adaptive_soft:2.0:31:4'
    with sqlite3.connect(cache_db) as con:
        con.execute("CREATE TABLE tesseract_cache (image_hash TEXT PRIMARY KEY, lang TEXT, result TEXT, created_at TEXT)")
        con.execute("INSERT INTO tesseract_cache VALUES (?, 'migne', ?, '2026-07-18')", (key, cache_text))
        con.execute("INSERT INTO tesseract_cache VALUES (?, 'lat+grc', 'wrong reference', '2026-08-01')", (image_hash,))
    return {"image": image, "db": db, "cache_db": cache_db, "draft": draft, "cache_text": cache_text}


def prepare(sources, directory, **kwargs):
    return repair.prepare(sources["image"], directory, db_path=sources["db"],
                          cache_db_path=sources["cache_db"], volume="PG001", page=638,
                          expect_model="gpt-5-mini", **kwargs)


def test_preparation_selects_latest_llm_and_exact_cache_without_database_writes(sources, tmp_path):
    before = [p.read_bytes() for p in (sources["db"], sources["cache_db"])]
    directory = tmp_path / "experiment"
    manifest = prepare(sources, directory)
    assert manifest["source_version"]["id"] == 809912
    assert manifest["cache"]["characters"] == len(sources["cache_text"])
    assert not manifest["inference_performed"]
    assert (directory / "draft.xml").read_bytes() == sources["draft"].encode()
    assert (directory / "tesseract_cached.txt").read_bytes() == sources["cache_text"].encode()
    prompt = (directory / "cached_text.txt").read_text()
    assert sources["cache_text"] in prompt and sources["draft"] in prompt
    assert "wrong reference" not in prompt
    assert "adulteros" not in (directory / "system.txt").read_text()
    assert before == [p.read_bytes() for p in (sources["db"], sources["cache_db"])]
    assert repair.load_experiment(directory)[1] == manifest


@pytest.mark.parametrize("change", ["cache_missing", "cache_lang", "other_image", "text_hash", "other_model"])
def test_missing_or_mismatched_sources_fail_before_preparation(sources, tmp_path, change):
    if change.startswith("cache"):
        with sqlite3.connect(sources["cache_db"]) as con:
            con.execute("DELETE FROM tesseract_cache" if change == "cache_missing" else
                        "UPDATE tesseract_cache SET lang='lat+grc'")
    else:
        with sqlite3.connect(sources["db"]) as con:
            if change == "other_model":
                con.execute("UPDATE ocr_engine_versions SET model='different'")
            else:
                column = 'image_hash' if change == 'other_image' else 'text_hash'
                con.execute(f"UPDATE ocr_results SET {column}='different'")
    directory = tmp_path / "experiment"
    with pytest.raises(ValueError):
        prepare(sources, directory)
    assert not directory.exists()


def test_explicit_tesseract_version_cannot_be_used_as_llm_draft(sources, tmp_path):
    with pytest.raises(ValueError, match="No matching"):
        prepare(sources, tmp_path / "experiment", version_id=853769)


@pytest.mark.parametrize("parameters,effort,expected", [
    (None, None, "medium"),
    ({"service_tier": "flex"}, None, "medium"),
    ({"reasoning_effort": "high"}, None, "high"),
    ({"reasoning_effort": "low"}, "xhigh", "xhigh"),
])
def test_one_request_uses_frozen_inputs_and_saves_diff_without_key(sources, tmp_path, monkeypatch,
                                                                parameters, effort, expected):
    directory, output = tmp_path / "experiment", tmp_path / "results"
    prepare(sources, directory)
    # Once frozen, the experiment is independent of subsequent DB changes.
    with sqlite3.connect(sources["cache_db"]) as con:
        con.execute("DELETE FROM tesseract_cache")
    monkeypatch.setenv("TEST_REPAIR_API_KEY", "secret-fixture-key")
    corrected = (FIXTURES / "PG001_638_previous.xml").read_text()
    calls = []

    def post(url, *, json, headers, timeout):
        calls.append(json)
        assert url == "https://api.openai.com/v1/chat/completions"
        assert headers == {"Authorization": "Bearer secret-fixture-key"}
        assert json["model"] == "gpt-5.4-mini"
        content = json["messages"][1]["content"]
        assert sources["draft"] in content[0]["text"]
        assert sources["cache_text"] in content[0]["text"]
        assert content[1]["image_url"]["detail"] == "original"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "temperature" not in json
        assert json["reasoning_effort"] == expected
        if parameters and "service_tier" in parameters:
            assert json["service_tier"] == parameters["service_tier"]

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": corrected}, "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 123}}
        return Response()

    original_parameters = dict(parameters) if parameters is not None else None
    result = repair.run(directory, output, api_key_env="TEST_REPAIR_API_KEY", post=post,
                        parameters=parameters, reasoning_effort=effort)
    assert parameters == original_parameters
    assert result["reasoning_effort"] == expected
    assert len(calls) == 1 and result["counts"] == {"ok": 1}
    assert result["inspection"]["xml_errors"] == []
    assert result["inspection"]["changed"] and not result["inspection"]["quality_verified"]
    assert 'adulteros' in (output / "r001_cached_text.xml").read_text()
    assert (output / "changes.diff").read_text().startswith("--- draft.xml")
    assert all("secret-fixture-key" not in p.read_text() for p in output.iterdir() if p.is_file())
    config = json.loads((output / "run.json").read_text())
    assert config["version"] == repair.VERSION
    assert config["parameters"]["reasoning_effort"] == expected


@pytest.mark.parametrize("value", [None, "invalid"])
def test_invalid_reasoning_parameter_blocks_request(sources, tmp_path, value):
    directory, output = tmp_path / "experiment", tmp_path / "results"
    prepare(sources, directory)
    with pytest.raises(ValueError, match="reasoning_effort must"):
        repair.run(directory, output, parameters={"reasoning_effort": value}, api_key_env=None)
    assert not output.exists()


def test_cli_forwards_explicit_reasoning_effort(monkeypatch, tmp_path):
    from scripts.playgrounds import ocr_repair_cached as cli
    received = []

    def run(*args, **kwargs):
        received.append(kwargs)
        return {"counts": {"ok": 1}}

    monkeypatch.setattr(repair, "run", run)
    monkeypatch.setattr(sys, "argv", ["ocr_repair_cached.py", "run", "--experiment", str(tmp_path),
                                     "--out", str(tmp_path / "result"), "--reasoning-effort", "high"])
    assert cli.main() == 0
    assert received[0]["reasoning_effort"] == "high"


@pytest.mark.parametrize("name", ["draft.xml", "tesseract_cached.txt", "cached_text.txt", "system.txt", "image"])
def test_changed_frozen_input_blocks_call(sources, tmp_path, name):
    directory, output = tmp_path / "experiment", tmp_path / "results"
    prepare(sources, directory)
    path = sources["image"] if name == "image" else directory / name
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match="changed"):
        repair.run(directory, output, api_key_env=None)
    assert not output.exists()


def test_timeout_is_logged_without_retry(sources, tmp_path):
    directory, output = tmp_path / "experiment", tmp_path / "results"
    prepare(sources, directory)
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("fixture")

    result = repair.run(directory, output, api_key_env=None, post=post)
    assert calls == [1] and result["counts"] == {"error": 1}
    assert not (output / "r001_cached_text.xml").exists()
    assert json.loads((output / "r001_cached_text.result.json").read_text())["error_type"] == "Timeout"


def test_prepare_does_not_overwrite_experiment(sources, tmp_path):
    directory = tmp_path / "experiment"
    prepare(sources, directory)
    with pytest.raises(FileExistsError):
        prepare(sources, directory)
