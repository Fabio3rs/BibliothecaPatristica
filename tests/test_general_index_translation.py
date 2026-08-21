from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.index_translation.database import (
    DETECTOR_NAME,
    DETECTOR_VERSION,
    analysis_is_cached,
    collect_pending_translations,
    collect_volume_candidates,
    connect_translation_db,
    detect_source_language,
    ensure_string_rows,
    init_translation_schema,
    load_analysis_summaries,
    upsert_analysis,
    upsert_translation_rows,
)
import scripts.run_index_extraction as general_runner
import scripts.index_translation.database as translation_db
import scripts.index_translation.provider as translation_provider


def initialize_general_db(path: Path) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "init_index_db.py"), "--db", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def seed_volume(path: Path) -> None:
    initialize_general_db(path)
    with connect_translation_db(path) as con:
        con.execute(
            "INSERT INTO volumes VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("PL001", "PL", "/tmp/text", "PL001", None, "now", "now"),
        )
        con.execute(
            """
            INSERT INTO works(
                work_key, volume_id, work_order, author_raw, title_raw, title_norm,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("PL001:work:1", "PL001", 1, "Augustinus", "De civitate Dei", None, 1, "{}"),
        )
        con.execute(
            """
            INSERT INTO index_sections(
                section_key, volume_id, work_key, scope_kind, index_kind, heading_raw,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PL001:section:1",
                "PL001",
                "PL001:work:1",
                "work_front",
                "INDEX_CAPITUM",
                "INDEX CAPITUM",
                1,
                "{}",
            ),
        )
        con.executemany(
            """
            INSERT INTO index_entries(
                section_key, entry_order, entry_raw, target_raw, note_raw,
                confidence, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("PL001:section:1", 1, "De gratia. 12", "De gratia", "vide etiam", 1, "{}"),
                ("PL001:section:1", 2, "De fide. 14", None, None, 1, "{}"),
                ("PL001:section:1", 3, "Augustinus. 15", "Augustinus", None, 1, "{}"),
            ],
        )
        con.commit()


def test_schema_has_translation_analysis_tables_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "indices.db"
    initialize_general_db(db_path)
    with sqlite3.connect(db_path) as con:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert {
        "index_strings",
        "index_translations",
        "index_string_analyses",
        "index_string_tokens",
    } <= tables
    assert {
        "idx_index_strings_source_text",
        "idx_index_translations_string_language",
        "idx_index_translations_language",
        "idx_index_translations_model_name",
        "idx_index_string_tokens_lemma",
        "idx_index_string_tokens_upos",
    } <= indexes


def test_candidates_use_semantic_fields_and_global_string_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "indices.db"
    seed_volume(db_path)
    with connect_translation_db(db_path) as con:
        init_translation_schema(con)
        candidates = ensure_string_rows(con, collect_volume_candidates(con, "PL001"))
        by_text = {item["source_text"]: item for item in candidates}
        assert set(by_text) == {
            "Augustinus",
            "De civitate Dei",
            "INDEX CAPITUM",
            "De gratia",
            "vide etiam",
            "De fide. 14",
        }
        assert set(by_text["Augustinus"]["source_kinds"]) == {
            "work_author",
            "entry_label",
        }
        augustine_id = int(by_text["Augustinus"]["string_id"])
        upsert_translation_rows(
            con,
            string_id=augustine_id,
            translations={"en": "Augustine"},
            model_name="test-model",
            sample_context="De civitate Dei",
        )
        pending = collect_pending_translations(con, candidates, ["en", "fr"])
        pending_by_text = {item["source_text"]: item for item in pending}
        assert pending_by_text["Augustinus"]["missing_languages"] == ["fr"]
        assert pending_by_text["De gratia"]["missing_languages"] == ["en", "fr"]


def test_language_detection_is_safe_for_mixed_po_and_unsupported_scripts() -> None:
    assert detect_source_language("De gratia", ["PL"])["language"] == "lat"
    assert detect_source_language("Περὶ πίστεως", ["PG"])["language"] == "grc"
    assert detect_source_language("ܫܠܡܐ", ["PG"])["status"] == "unsupported"
    assert detect_source_language("λόγος verbum", ["PG"])["status"] == "mixed"
    assert (
        detect_source_language(
            "De gratia", ["PO"], latin_resolver=lambda tokens: set(tokens)
        )["language"]
        == "lat"
    )
    assert (
        detect_source_language(
            "Texte français", ["PO"], latin_resolver=lambda _tokens: set()
        )["status"]
        == "unrecognized"
    )


def test_analysis_cache_and_token_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "indices.db"
    seed_volume(db_path)
    with connect_translation_db(db_path) as con:
        candidates = ensure_string_rows(con, collect_volume_candidates(con, "PL001"))
        candidate = next(item for item in candidates if item["source_text"] == "De gratia")
        detection = {"language": "lat", "status": "pending", "reason": "test"}
        upsert_analysis(
            con,
            string_id=int(candidate["string_id"]),
            detection=detection,
            analyzer_name="cltk-stanza",
            analyzer_version="1.5.0",
            status="ok",
            tokens=[
                {
                    "surface": "gratia",
                    "lemma": "gratia",
                    "upos": "NOUN",
                    "features": {"Case": "Abl"},
                    "sentence_index": 0,
                }
            ],
        )
        row = con.execute(
            "SELECT * FROM index_string_analyses WHERE string_id = ?",
            (candidate["string_id"],),
        ).fetchone()
        assert row["detector_name"] == DETECTOR_NAME
        assert row["detector_version"] == DETECTOR_VERSION
        assert analysis_is_cached(
            row,
            detection=detection,
            analyzer_name="cltk-stanza",
            analyzer_version="1.5.0",
        )
        summary = load_analysis_summaries(con, [int(candidate["string_id"])])
        assert summary[int(candidate["string_id"])]["tokens"] == [
            {
                "surface": "gratia",
                "lemma": "gratia",
                "upos": "NOUN",
                "features": {"Case": "Abl"},
            }
        ]


def test_prepare_cltk_analyses_reuses_persistent_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "indices.db"
    seed_volume(db_path)
    calls = 0

    monkeypatch.setattr(
        translation_db,
        "cltk_healthcheck",
        lambda *_args: {
            "status": "ok",
            "analyzer_name": "cltk-stanza",
            "analyzer_version": "1.5.0",
            "languages": {
                "lat": {"model_present": True},
                "grc": {"model_present": True},
            },
        },
    )

    def fake_bucket(**kwargs):
        nonlocal calls
        calls += 1
        return [
            (
                candidate,
                detection,
                {
                    "status": "ok",
                    "tokens": [
                        {"surface": "gratia", "lemma": "gratia", "upos": "NOUN"}
                    ],
                    "raw": {"word_count": 1},
                },
            )
            for candidate, detection in kwargs["items"]
        ]

    monkeypatch.setattr(translation_db, "_run_worker_bucket", fake_bucket)
    with connect_translation_db(db_path) as con:
        candidates = ensure_string_rows(con, collect_volume_candidates(con, "PL001"))
        first = translation_db.prepare_cltk_analyses(
            con,
            candidates=candidates,
            python_executable=tmp_path / "python",
            worker_script=tmp_path / "worker.py",
            dictionary_dir=tmp_path,
            workers=1,
            max_tokens=20,
        )
        second = translation_db.prepare_cltk_analyses(
            con,
            candidates=candidates,
            python_executable=tmp_path / "python",
            worker_script=tmp_path / "worker.py",
            dictionary_dir=tmp_path,
            workers=1,
            max_tokens=20,
        )
    assert first["analyzed"] == 6
    assert second["analyzed"] == 0
    assert second["cached"] == 6
    assert calls == 1


def test_cltk_worker_serializes_a_mock_document(monkeypatch) -> None:
    path = ROOT / "scripts" / "index_translation" / "cltk_worker.py"
    spec = importlib.util.spec_from_file_location("index_cltk_worker_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    word = SimpleNamespace(
        string="gratia",
        lemma="gratia",
        upos="NOUN",
        xpos="n-s---fb-",
        features={"Case": "Abl"},
        dependency_relation="obl",
        governor=0,
        index_char_start=3,
        index_char_stop=9,
        index_sentence=0,
        stop=False,
        confidence={"lemma": 0.9},
        annotation_sources={"lemma": "stanza"},
    )
    monkeypatch.setattr(
        module, "build_pipeline", lambda _language: SimpleNamespace(analyze=lambda _text: SimpleNamespace(words=[word]))
    )
    monkeypatch.setattr(module, "package_version", lambda: "1.5.0")
    result = module.analyze({"language": "lat", "text": "de gratia", "max_tokens": 10})
    assert result["status"] == "ok"
    assert result["tokens"][0]["lemma"] == "gratia"
    assert result["tokens"][0]["features"] == {"Case": "Abl"}


def test_cltk_worker_keeps_library_output_out_of_json_protocol(monkeypatch) -> None:
    path = ROOT / "scripts" / "index_translation" / "cltk_worker.py"
    spec = importlib.util.spec_from_file_location("index_cltk_protocol_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stdin = io.StringIO('{"request_id": 7, "action": "health"}\n')
    stdout = io.StringIO()
    stderr = io.StringIO()

    def noisy_handle(payload: dict[str, object]) -> dict[str, object]:
        print("third-party banner")
        return {"request_id": payload["request_id"], "status": "ok"}

    monkeypatch.setattr(module, "handle", noisy_handle)
    monkeypatch.setattr(module.sys, "stdin", stdin)
    monkeypatch.setattr(module.sys, "stdout", stdout)
    monkeypatch.setattr(module.sys, "stderr", stderr)

    assert module.serve() == 0
    assert json.loads(stdout.getvalue()) == {"request_id": 7, "status": "ok"}
    assert stderr.getvalue() == "third-party banner\n"


def test_cltk_worker_client_skips_noise_and_stale_responses(monkeypatch) -> None:
    fake_process = SimpleNamespace(
        stdin=io.StringIO(),
        stdout=io.StringIO(
            "banner\n"
            '{"request_id": 0, "status": "ok"}\n'
            '{"request_id": 1, "status": "ok", "tokens": []}\n'
        ),
        poll=lambda: None,
    )
    monkeypatch.setattr(
        translation_db.subprocess,
        "Popen",
        lambda *args, **kwargs: fake_process,
    )
    client = translation_db.CltkWorkerClient("python", "worker.py")

    response = client.request({"action": "analyze", "text": "De gratia"})

    assert response == {"request_id": 1, "status": "ok", "tokens": []}


def test_translation_stage_writes_four_languages_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "indices.db"
    seed_volume(db_path)

    def fake_translate(task: dict[str, object]) -> dict[str, object]:
        return {
            "string_id": task["string_id"],
            "source_text": task["source_text"],
            "sample_context": None,
            "translations": {
                language: f"{task['source_text']} [{language}]"
                for language in task["languages"]
            },
            "model_name": "test-model",
            "attempts": 1,
            "elapsed_s": 0.01,
            "tool_call_count": 0,
        }

    monkeypatch.setattr(general_runner, "translate_candidate_worker", fake_translate)
    kwargs = {
        "db_path": db_path,
        "volume_id": "PL001",
        "languages": ["en", "fr", "it", "pt-br"],
        "model": "test-model",
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "workers": 1,
        "timeout": 10,
        "retries": 1,
        "verbose": False,
        "tools_enabled": False,
        "dictionary_dir": tmp_path,
        "max_tool_rounds": 0,
        "cltk_enabled": False,
        "cltk_python": tmp_path / "missing-python",
        "cltk_workers": 1,
        "cltk_max_tokens": 20,
    }
    first = general_runner.run_translation_stage(**kwargs)
    assert first["candidate_strings"] == 6
    assert first["written_rows"] == 24
    second = general_runner.run_translation_stage(**kwargs)
    assert second["ran"] is False
    assert second["pending_strings"] == 0


def test_translation_stage_commits_completed_strings_before_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "indices.db"
    seed_volume(db_path)
    calls = 0

    def flaky_translate(task: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("translation failed for test")
        return {
            "string_id": task["string_id"],
            "source_text": task["source_text"],
            "sample_context": None,
            "translations": {language: "ok" for language in task["languages"]},
            "model_name": "test-model",
            "attempts": 1,
            "elapsed_s": 0.01,
            "tool_call_count": 0,
        }

    monkeypatch.setattr(general_runner, "translate_candidate_worker", flaky_translate)
    with pytest.raises(RuntimeError, match="translation failed"):
        general_runner.run_translation_stage(
            db_path=db_path,
            volume_id="PL001",
            languages=["en", "fr", "it", "pt-br"],
            model="test-model",
            base_url="https://example.invalid/v1",
            api_key="test-key",
            workers=1,
            timeout=10,
            retries=1,
            verbose=False,
            tools_enabled=False,
            dictionary_dir=tmp_path,
            max_tool_rounds=0,
            cltk_enabled=False,
            cltk_python=tmp_path / "missing-python",
            cltk_workers=1,
            cltk_max_tokens=20,
        )
    with connect_translation_db(db_path) as con:
        assert con.execute("SELECT count(*) FROM index_translations").fetchone()[0] == 4


def _translation_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "string_id": 1,
        "source_text": "De gratia",
        "contexts": [],
        "languages": ["en"],
        "model": "gpt-5-nano",
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "timeout": 10,
        "retries": 3,
        "backoff_base_s": 1.0,
        "backoff_max_s": 10.0,
        "jitter_ratio": 0.25,
        "tools_enabled": False,
        "max_tool_rounds": 0,
    }
    task.update(overrides)
    return task


class _FakeTranslationResponse:
    def __init__(
        self,
        status_code: int,
        *,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = text

    def json(self) -> dict[str, object]:
        return self._body


class _FakeTranslationSession:
    def __init__(self, responses: list[_FakeTranslationResponse]) -> None:
        self.responses = responses

    def __enter__(self) -> _FakeTranslationSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> _FakeTranslationResponse:
        return self.responses.pop(0)


def test_translation_worker_retries_rate_limit_with_backoff_and_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _FakeTranslationResponse(
            429,
            headers={"Retry-After": "3"},
            text="rate limited",
        ),
        _FakeTranslationResponse(
            200,
            body={
                "choices": [
                    {"message": {"content": json.dumps({"en": "On grace"})}}
                ]
            },
        ),
    ]
    sleeps: list[float] = []
    monkeypatch.setattr(
        translation_provider, "make_session", lambda: _FakeTranslationSession(responses)
    )
    monkeypatch.setattr(translation_provider.random, "uniform", lambda _a, _b: 0.2)
    monkeypatch.setattr(translation_provider.time, "sleep", sleeps.append)

    result = translation_provider.translate_candidate_worker(_translation_task())

    assert result["translations"] == {"en": "On grace"}
    assert result["attempts"] == 2
    assert result["retry_wait_s"] == 3.2
    assert sleeps == [3.2]


def test_translation_payload_rejects_serialized_language_map() -> None:
    with pytest.raises(ValueError, match="serialized structured data"):
        translation_provider.validate_translation_payload(
            {
                "pt-br": json.dumps(
                    {
                        "en": "On grace",
                        "pt-br": "Sobre a graça",
                    }
                )
            },
            ["pt-br"],
        )


def test_translation_payload_allows_editorial_braces() -> None:
    assert translation_provider.validate_translation_payload(
        {"en": "{OPUSCULA.} Exhortation to repentance."},
        ["en"],
    ) == {"en": "{OPUSCULA.} Exhortation to repentance."}


def test_translation_retry_delay_is_exponential_and_jittered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        translation_provider.random,
        "uniform",
        lambda lower, upper: upper,
    )

    assert translation_provider.retry_delay(
        attempt=3,
        base_s=1.0,
        max_s=30.0,
        jitter_ratio=0.25,
    ) == 5.0


def test_translation_worker_does_not_retry_permanent_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [_FakeTranslationResponse(401, text="invalid key")]
    sleeps: list[float] = []
    monkeypatch.setattr(
        translation_provider, "make_session", lambda: _FakeTranslationSession(responses)
    )
    monkeypatch.setattr(translation_provider.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="http 401"):
        translation_provider.translate_candidate_worker(_translation_task())

    assert sleeps == []


def test_translation_worker_does_not_retry_quota_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _FakeTranslationResponse(
            429,
            body={"error": {"code": "insufficient_quota"}},
            text="quota exhausted",
        )
    ]
    sleeps: list[float] = []
    monkeypatch.setattr(
        translation_provider, "make_session", lambda: _FakeTranslationSession(responses)
    )
    monkeypatch.setattr(translation_provider.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="http 429"):
        translation_provider.translate_candidate_worker(_translation_task())

    assert sleeps == []
