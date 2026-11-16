from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.index_translation import onomastic_catalog as pipeline
from scripts.index_translation.augmentation import run_tool_guided_chat
from scripts.index_translation.provider import build_translation_prompt
from scripts.index_translation import provider as translation_provider


def make_catalog(root: Path, rows: list[tuple[str, str, str, str, str]]) -> Path:
    path = root / "search.sqlite"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE items (
                id TEXT PRIMARY KEY, group_id TEXT, volume TEXT, section TEXT,
                label TEXT, ref_raw TEXT, source TEXT, state TEXT, metadata_json TEXT
            );
            CREATE TABLE candidate_storage (
                item_id TEXT, file TEXT, rank INTEGER, score REAL, navigable INTEGER,
                candidate_json BLOB, PRIMARY KEY(item_id,file)
            );
        """)
        for item_id, volume, section, label, source in rows:
            con.execute(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?)",
                (item_id, item_id + "-group", volume, section, label, "561", source,
                 "with_destinations", json.dumps({"index_category": "RERUM_ET_SENTENTIARUM",
                                                   "entity_type": "RES"})),
            )
            con.execute(
                "INSERT INTO candidate_storage VALUES (?,?,?,?,?,?)",
                (item_id, str(root / "teste" / volume / "text" / "dest.txt"), 1, 0.9, 1, None),
            )
    return path


def make_source(root: Path, volume: str, name: str, text: str = "Amoris divini circulus, 561.") -> Path:
    path = root / "teste" / volume / "text" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_alpha(root: Path, source: Path, label: str) -> Path:
    path = root / "alpha.sqlite"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE alphabetical_translations (
                id INTEGER PRIMARY KEY, source_text TEXT, language TEXT,
                source_kind TEXT, section_kind TEXT, sample_context TEXT,
                translated_text TEXT, model_name TEXT
            );
            CREATE TABLE alphabetical_sections (
                section_key TEXT PRIMARY KEY, volume_id TEXT, section_kind TEXT,
                heading_raw TEXT
            );
            CREATE TABLE alphabetical_entries (
                entry_key TEXT PRIMARY KEY, section_key TEXT, lemma_display TEXT,
                lemma_raw TEXT, index_entry_source_ocr_file TEXT
            );
        """)
        con.execute("INSERT INTO alphabetical_sections VALUES (?,?,?,?)",
                    ("s1", "PG003", "alphabetical_general", "INDEX RERUM ET VERBORUM."))
        con.execute("INSERT INTO alphabetical_entries VALUES (?,?,?,?,?)",
                    ("e1", "s1", label, label, str(source)))
        con.executemany(
            "INSERT INTO alphabetical_translations VALUES (?,?,?,?,?,?,?,?)",
            [(i, label, lang, "entry_title", "alphabetical_general",
              "INDEX RERUM ET VERBORUM.", translated, "legacy-model")
             for i, (lang, translated) in enumerate(
                 [("en", "Circle of divine love"), ("fr", "Cercle de l’amour divin")], 1)],
        )
    return path


def test_inventory_is_read_only_and_reports_missing_languages(tmp_path: Path) -> None:
    source = make_source(tmp_path, "PG003", "page.txt")
    catalog = make_catalog(tmp_path, [("a", "PG003", "INDEX RERUM ET VERBORUM", "Amoris divini circulus", str(source))])
    store = tmp_path / "translations.sqlite"
    result = pipeline.inventory(catalog, store, languages=["en", "fr"], root=tmp_path)
    assert result["occurrences"] == 1
    assert result["by_language"]["en"] == {"missing": 1}
    assert result["pending_occurrences"] == 1
    assert result["pending_sample"][0]["missing_languages"] == ["en", "fr"]
    assert not store.exists()
    assert sqlite3.connect(f"file:{catalog}?mode=ro", uri=True).execute("SELECT count(*) FROM items").fetchone()[0] == 1
    report = tmp_path / "pending.jsonl"
    pipeline.inventory(catalog, store, languages=["en", "fr"], root=tmp_path, out=report)
    assert json.loads(report.read_text().splitlines()[0])["id"] == "a"
    assert not store.exists()


def test_strict_reuse_distinguishes_homonyms_and_exports_real_fields(tmp_path: Path) -> None:
    source = make_source(tmp_path, "PG003", "page.txt")
    other = make_source(tmp_path, "PG004", "page.txt")
    label = "Amoris divini circulus"
    catalog = make_catalog(tmp_path, [
        ("a", "PG003", "INDEX RERUM ET VERBORUM", label, str(source)),
        ("b", "PG004", "INDEX RERUM ET VERBORUM", label, str(other)),
    ])
    alpha = make_alpha(tmp_path, source, label)
    store = tmp_path / "translations.sqlite"
    result = pipeline.run(catalog, store, languages=["en", "fr"], volume=None,
                          item_id="a", max_api_calls=0, model=None,
                          alphabetical_path=alpha, general_path=tmp_path / "none.db", root=tmp_path)
    assert result["reused_languages"] == 2
    second = pipeline.run(catalog, store, languages=["en", "fr"], volume=None,
                          item_id="b", max_api_calls=0, model=None,
                          alphabetical_path=alpha, general_path=tmp_path / "none.db", root=tmp_path)
    assert second.get("reused_languages", 0) == 0
    out = tmp_path / "mini.json"
    summary = pipeline.export(catalog, store, languages=["en", "fr"], out=out,
                              volume="PG003", limit=2, root=tmp_path)
    entry = json.loads(out.read_text())["entries"][0]
    assert summary["coverage"]["complete_occurrences"] == 1
    assert entry["id"] == "a"
    assert entry["reference_raw"] == "561"
    assert entry["source"] == "teste/PG003/text/page.txt"
    assert entry["destinations"][0]["path"] == "teste/PG003/text/dest.txt"
    assert entry["translations"]["en"]["text"] == "Circle of divine love"
    assert entry["translations"]["fr"]["provider"] == "alphabetical-legacy"
    first_snapshot = json.loads(out.read_text())["catalog_snapshot_sha256"]
    pipeline.review(store, item_id="a", language="en", state="rejected", reviewer="fixture")
    repeated = pipeline.run(catalog, store, languages=["en"], volume=None,
                            item_id="a", max_api_calls=0, model=None,
                            alphabetical_path=alpha, general_path=tmp_path / "none.db", root=tmp_path)
    assert repeated.get("reused_languages", 0) == 0
    assert pipeline.inventory(catalog, store, languages=["en"], item_id="a", root=tmp_path)["by_language"]["en"] == {"rejected": 1}
    pipeline.export(catalog, store, languages=["en", "fr"], out=out,
                    volume="PG003", limit=2, root=tmp_path)
    assert json.loads(out.read_text())["catalog_snapshot_sha256"] == first_snapshot


def test_resume_missing_languages_and_failed_attempts_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = make_source(tmp_path, "PG003", "a.txt", "one")
    b = make_source(tmp_path, "PG003", "b.txt", "two")
    catalog = make_catalog(tmp_path, [
        ("a", "PG003", "INDEX RERUM ET VERBORUM", "De gratia", str(a)),
        ("b", "PG003", "INDEX RERUM ET VERBORUM", "De pace", str(b)),
    ])
    store = tmp_path / "translations.sqlite"
    with pipeline.connect_store(store, create=True) as con:
        item = pipeline.occurrence(next(pipeline.iter_items(pipeline.connect_catalog(catalog), item_id="a")), root=tmp_path)
        pipeline.append_revision(con, item, language="en", translated_text="On grace",
                                 provider="test", model="test", origin="api")
        con.commit()
    tasks = []

    def fake_worker(task: dict) -> dict:
        tasks.append(task)
        assert task["retries"] == 1
        if task["source_text"] == "De pace" and len(tasks) == 2:
            raise RuntimeError("fixture failure")
        return {"translations": {lang: f"{task['source_text']} {lang}" for lang in task["languages"]},
                "usage": [{"prompt_tokens": 12, "completion_tokens": 4}]}

    monkeypatch.setattr(pipeline, "translate_candidate_worker", fake_worker)
    monkeypatch.setenv("TRANSLATION_TEST_KEY", "fixture-key")
    kwargs = dict(languages=["en", "fr"], volume="PG003", item_id=None,
                  max_api_calls=2, model="fixture-model", api_key_env="TRANSLATION_TEST_KEY",
                  alphabetical_path=tmp_path / "none.db", general_path=tmp_path / "none2.db", root=tmp_path)
    with pytest.raises(RuntimeError, match="fixture failure"):
        pipeline.run(catalog, store, **kwargs)
    with pipeline.connect_store(store, create=False) as con:
        assert con.execute("SELECT count(*) FROM translation_revisions").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM translation_attempts").fetchone()[0] == 1
    assert tasks[0]["languages"] == ["fr"]
    result = pipeline.run(catalog, store, **kwargs)
    assert result["api_calls"] == 1
    assert tasks[-1]["languages"] == ["en", "fr"]
    with pipeline.connect_store(store, create=False) as con:
        row = con.execute("SELECT api_usage_json FROM translation_revisions WHERE item_id='b' LIMIT 1").fetchone()
        assert json.loads(row[0])[0]["prompt_tokens"] == 12
        call_ids = {row[0] for row in con.execute("SELECT api_call_id FROM translation_revisions WHERE item_id='b'")}
        assert len(call_ids) == 1
        assert None not in call_ids


def test_api_budget_and_store_survive_catalog_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = make_source(tmp_path, "PG003", "a.txt")
    b = make_source(tmp_path, "PG003", "b.txt")
    rows = [
        ("a", "PG003", "INDEX RERUM ET VERBORUM", "De gratia", str(a)),
        ("b", "PG003", "INDEX RERUM ET VERBORUM", "De pace", str(b)),
    ]
    catalog = make_catalog(tmp_path, rows)
    store = tmp_path / "translations.sqlite"
    calls: list[str] = []

    def fake_worker(task: dict) -> dict:
        calls.append(task["source_text"])
        return {"translations": {"en": task["source_text"] + " translated"}}

    monkeypatch.setattr(pipeline, "translate_candidate_worker", fake_worker)
    monkeypatch.setenv("TRANSLATION_TEST_KEY", "fixture-key")
    result = pipeline.run(
        catalog, store, languages=["en"], volume="PG003", item_id=None,
        max_api_calls=1, model="fixture-model", api_key_env="TRANSLATION_TEST_KEY",
        alphabetical_path=tmp_path / "none.db", general_path=tmp_path / "none2.db",
        root=tmp_path,
    )
    assert result["api_calls"] == 1
    assert len(calls) == 1
    assert pipeline.inventory(catalog, store, languages=["en"], root=tmp_path)["by_language"]["en"] == {"valid": 1, "missing": 1}
    catalog.unlink()
    rebuilt = make_catalog(tmp_path, rows)
    assert rebuilt == catalog
    assert pipeline.inventory(rebuilt, store, languages=["en"], root=tmp_path)["by_language"]["en"] == {"valid": 1, "missing": 1}


def test_context_change_and_review_preserve_revisions(tmp_path: Path) -> None:
    source = make_source(tmp_path, "PG003", "page.txt")
    other = make_source(tmp_path, "PG003", "other.txt")
    catalog = make_catalog(tmp_path, [
        ("a", "PG003", "INDEX RERUM ET VERBORUM", "De gratia", str(source)),
        ("b", "PG003", "INDEX RERUM ET VERBORUM", "De pace", str(other)),
    ])
    store = tmp_path / "translations.sqlite"
    with pipeline.connect_catalog(catalog) as source_con, pipeline.connect_store(store, create=True) as con:
        item = pipeline.occurrence(next(pipeline.iter_items(source_con, item_id="a")), root=tmp_path)
        first = pipeline.append_revision(con, item, language="en", translated_text="On grace",
                                         provider="test", model="m1", origin="api")
        other_item = pipeline.occurrence(next(pipeline.iter_items(source_con, item_id="b")), root=tmp_path)
        pipeline.append_revision(con, other_item, language="en", translated_text="On peace",
                                 provider="test", model="m1", origin="api")
        con.commit()
    pipeline.review(store, item_id="a", language="en", state="approved", reviewer="fixture")
    with sqlite3.connect(catalog) as con:
        con.execute("UPDATE items SET ref_raw='562' WHERE id='a'")
    status = pipeline.inventory(catalog, store, languages=["en"], root=tmp_path)
    assert status["by_language"]["en"] == {"stale": 1, "valid": 1}
    with pipeline.connect_catalog(catalog) as source_con, pipeline.connect_store(store, create=True) as con:
        item = pipeline.occurrence(next(pipeline.iter_items(source_con, item_id="a")), root=tmp_path)
        second = pipeline.append_revision(con, item, language="en", translated_text="Concerning grace",
                                          provider="test", model="m2", origin="api")
        con.commit()
    with pipeline.connect_store(store, create=False) as con:
        assert con.execute("SELECT count(*) FROM translation_revisions WHERE item_id='a'").fetchone()[0] == 2
        assert pipeline.review_state(con, first) == "approved"
        assert pipeline.review_state(con, second) == "unreviewed"
    with sqlite3.connect(catalog) as con:
        con.execute("UPDATE items SET label='De gratia mutata' WHERE id='a'")
    status = pipeline.inventory(catalog, store, languages=["en"], root=tmp_path)
    assert status["by_language"]["en"] == {"stale": 1, "valid": 1}


def test_prompt_receives_catalog_context_and_usage_is_reported() -> None:
    _, prompt = build_translation_prompt(
        source_text="De gratia", contexts=[], languages=["en"],
        catalog_context={"section": "INDEX RERUM", "source_excerpt": "De gratia, 561"},
        legacy_hints={"en": "On grace"},
    )
    assert "<catalog_context_advisory>" in prompt
    assert "De gratia, 561" in prompt
    assert "<legacy_translation_hints_unverified>" in prompt
    response = {"choices": [{"message": {"content": '{"en":"On grace"}'}}],
                "usage": {"prompt_tokens": 14, "completion_tokens": 4}}
    result = run_tool_guided_chat(request_chat=lambda _: response, payload={}, messages=[],
                                  tools=[], handlers={})
    assert result.usage == ({"prompt_tokens": 14, "completion_tokens": 4},)


def test_provider_sends_extra_context_and_preserves_reported_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"en":"On grace"}'}}],
                    "usage": {"prompt_tokens": 18, "completion_tokens": 4}}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, _url, *, data: str, **_kwargs):
            requests.append(json.loads(data))
            return FakeResponse()

    monkeypatch.setattr(translation_provider, "make_session", FakeSession)
    result = translation_provider.translate_candidate_worker({
        "string_id": 1, "source_text": "De gratia", "contexts": [],
        "catalog_context": {"volume": "PG003", "section": "INDEX RERUM",
                            "source_excerpt": "De gratia, 561"},
        "legacy_hints": {"en": "Concerning grace"},
        "languages": ["en"], "model": "fixture-model", "base_url": "https://example.invalid/v1",
        "api_key": "fixture-key", "timeout": 10, "retries": 1, "tools_enabled": False,
    })
    sent_prompt = requests[0]["messages"][1]["content"]
    assert "De gratia, 561" in sent_prompt
    assert "Concerning grace" in sent_prompt
    assert result["usage"] == [{"prompt_tokens": 18, "completion_tokens": 4}]
