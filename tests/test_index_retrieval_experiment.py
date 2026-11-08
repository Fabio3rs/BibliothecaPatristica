import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import requests

from tools.indexing import semantic_retrieval_experiment as experiment
from tools.indexing import retrieval_preparation as preparation


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    folder = root / "PG999/text"
    folder.mkdir(parents=True)
    for number in range(1, 76):
        (folder / f"PG999-{number:03d}.txt").write_text(
            f'<pagina><bloco tipo="cabecalho">{number}</bloco>'
            f'<bloco tipo="texto_principal">gratia fides corpus {number}</bloco>'
            '<bloco tipo="aparato_critico">testimonium fidei</bloco>'
            '<notas>THIS IS METADATA NOT PRINTED TEXT</notas></pagina>')
    db = tmp_path / "source.sqlite"
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE alphabetical_sections (section_key TEXT, volume_id TEXT, section_kind TEXT);
        CREATE TABLE alphabetical_entries (entry_key TEXT, section_key TEXT, lemma_raw TEXT,
            entry_raw TEXT, context_raw TEXT, entry_kind TEXT);
        CREATE TABLE alphabetical_refs (ref_id INTEGER, entry_key TEXT, ref_order INTEGER,
            locator_status TEXT, raw_json TEXT, range_end_raw TEXT, ref_kind TEXT,
            target_file TEXT, ref_raw TEXT, editorial_anchor_file TEXT);
        INSERT INTO alphabetical_sections VALUES ('s1','PG999','analytic_subject');
        INSERT INTO alphabetical_entries VALUES ('e1','s1','Gratia','Gratia, 10, 11',NULL,'lemma');
        INSERT INTO alphabetical_entries VALUES ('e2','s1','Fides','Fides, 20',NULL,'lemma');
    ''')
    evidence = json.dumps({"compact_locator": {"evidence": [{"kind": "header_pair"}]}})
    for ref_id, entry, page in [(1, "e1", 10), (2, "e1", 11), (3, "e2", 20)]:
        con.execute("INSERT INTO alphabetical_refs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ref_id, entry, ref_id, "resolved", evidence, None, "editorial_page",
                     str(folder / f"PG999-{page:03d}.txt"), str(page), str(folder / "PG999-075.txt")))
    con.commit()
    con.close()
    return root, db, tmp_path


def args_for(corpus, *extra):
    root, db, tmp = corpus
    return experiment.parser().parse_args([
        "--root", str(root), "--db", str(db), "--experiment-db", str(tmp / "experiment.sqlite"),
        "--out", str(tmp / "out"), "--dimensions", "4", "--sample-size", "2", *extra])


class Response:
    def __init__(self, value, status=200):
        self.value, self.status_code = value, status

    def json(self):
        return self.value

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Session:
    def __init__(self, model=experiment.DEFAULT_MODEL):
        self.calls = []
        self.model = model
        self.digest = "fixed-model-digest"
        self.error = None

    def get(self, url, timeout):
        return Response({"models": [{"name": self.model, "digest": self.digest}]})

    def post(self, url, json, timeout):
        self.calls.append(json)
        if self.error:
            return self.error
        return Response({"model": self.model,
                         "embeddings": [[len(t) % 7 + 1, 1, 2, 3] for t in json["input"]]})


class FakeChunker:
    hash = "test-only-tokenizer"

    def __init__(self, args):
        pass

    def chunks(self, text):
        mid = len(text) // 2
        return [{"text": text[:mid], "char_start": 0, "char_end": mid},
                {"text": text[mid:], "char_start": mid, "char_end": len(text)}]


def test_all_correct_pages_retained_when_selecting_one_ref(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root, ref_ids=[1])
    assert len(labels) == 1
    assert len(labels[0]["gold_files"]) == 2
    assert [r["ref_id"] for r in labels[0]["references"]] == [1, 2]


def test_jev_labels_separate_and_ranges_not_guessed(corpus):
    root, db, _ = corpus
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_refs SET raw_json=? WHERE ref_id=1",
                    (json.dumps({"jev_validation": {"decision": True}}),))
        con.execute("UPDATE alphabetical_refs SET ref_kind='editorial_range',range_end_raw='22' WHERE ref_id=3")
    labels, excluded = experiment.load_labels(db, root)
    assert excluded == {"jev_label_excluded": 1, "range_targets_not_exhaustively_verified": 1,
                        "concept_with_excluded_resolved_occurrence": 1}
    assert not labels
    labels, _ = experiment.load_labels(db, root, include_jev=True)
    assert labels[0]["label_origin"] == "mixed"
    assert len(labels[0]["gold_files"]) == 2


def test_source_readonly(corpus):
    _, db, _ = corpus
    con = experiment.readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM alphabetical_refs")
    con.close()


def test_missing_target_not_invented(corpus):
    root, db, _ = corpus
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_refs SET target_file='/missing.txt'")
    labels, excluded = experiment.load_labels(db, root)
    assert not labels
    assert excluded["target_missing_or_wrong_volume"] == 3


def test_seed_random_fifty_neighbors_and_full_volume(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root)
    pages = experiment.load_pages(root, ["PG999"])
    args = args_for(corpus)
    plan = experiment.candidate_plan(labels[0], pages, args)
    assert plan == experiment.candidate_plan(labels[0], pages, args)
    assert plan["random_count"] == 50
    assert set(labels[0]["gold_files"]) <= plan["base"].keys()
    assert not set(plan["hard_pool"]) & plan["base"].keys()
    for num in (8, 9, 12, 13):
        key = next(p for p in pages if pages[p]["physical_page"] == num)
        assert "neighbor" in plan["base"][key]
    args.scope = "volume"
    assert len(experiment.candidate_plan(labels[0], pages, args)["base"]) == 75


def test_changed_gold_stops_run(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root)
    path = Path(next(iter(labels[0]["gold_files"])))
    path.write_text("altered")
    with pytest.raises(ValueError, match="changed"):
        experiment.candidate_plan(labels[0], experiment.load_pages(root, ["PG999"]), args_for(corpus))


def test_xml_apparatus_retained_metadata_excluded(corpus):
    root, _, _ = corpus
    text = next(iter(experiment.load_pages(root, ["PG999"]).values()))["text"]
    assert "testimonium fidei" in text
    assert "METADATA" not in text


def test_multiple_positive_metrics_and_margins():
    ranking = [{"path": "a", "score": .9}, {"path": "b", "score": .8}, {"path": "c", "score": .7}]
    m = experiment.ranking_metrics(ranking, {"b", "c"})
    assert m["recall@1"] == 0 and m["recall@5"] == 1
    assert m["reciprocal_rank"] == .5
    assert m["margin_best_gold_vs_best_distractor"] == pytest.approx(-.1)
    assert m["margin_worst_gold_vs_best_distractor"] == pytest.approx(-.2)
    with pytest.raises(ValueError, match="positive"):
        experiment.ranking_metrics(ranking, {"missing"})


def test_batching_cache_wal_and_no_truncation(corpus):
    args = args_for(corpus, "--batch-size", "2")
    db = experiment.ExperimentDB(args.experiment_db)
    session = Session()
    client = experiment.OllamaCache(db, "run", args, session)
    result = client.vectors(["alpha", "beta", "gamma", "alpha"])
    assert len(session.calls) == 2
    assert session.calls[0]["truncate"] is False
    assert session.calls[0]["dimensions"] == 4
    assert session.calls[0]["options"]["num_ctx"] == 32768
    assert isinstance(session.calls[0]["input"], list)
    assert np.linalg.norm(result[0]) == pytest.approx(1)
    assert db.con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()
    db = experiment.ExperimentDB(args.experiment_db)
    client = experiment.OllamaCache(db, "run2", args, session)
    np.testing.assert_allclose(result, client.vectors(["alpha", "beta", "gamma", "alpha"]))
    assert len(session.calls) == 2
    session.digest = "changed-model-digest"
    client = experiment.OllamaCache(db, "run3", args, session)
    client.vectors(["alpha"])
    assert len(session.calls) == 3
    db.close()


@pytest.mark.parametrize("values", [[[1, 2]], [[0, 0, 0, 0]], [[float('nan'), 1, 2, 3]], []])
def test_reject_invalid_vectors(corpus, values):
    args = args_for(corpus)
    db = experiment.ExperimentDB(args.experiment_db)
    session = Session()
    session.error = Response({"model": args.model, "embeddings": values})
    client = experiment.OllamaCache(db, "run", args, session)
    with pytest.raises(ValueError):
        client.vectors(["a"])
    assert db.con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
    db.close()


def test_retries_bounded_and_cache_not_poisoned(corpus, monkeypatch):
    monkeypatch.setattr(experiment.time, "sleep", lambda _: None)
    args = args_for(corpus, "--retries", "1")
    db = experiment.ExperimentDB(args.experiment_db)
    session = Session()
    session.error = Response({}, 503)
    client = experiment.OllamaCache(db, "run", args, session)
    with pytest.raises(requests.HTTPError):
        client.vectors(["a"])
    assert len(session.calls) == 2
    assert db.con.execute("SELECT COUNT(*) FROM http_batches").fetchone()[0] == 2
    db.close()


def test_qwen_token_windows_overlap_preserve_original_text():
    class Tokenizer:
        def __call__(self, text, **kwargs):
            return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}
    chunker = experiment.QwenChunks.__new__(experiment.QwenChunks)
    chunker.tokenizer = Tokenizer()
    chunker.size, chunker.overlap = 250, 50
    text = " ".join(f"λέξη{i}" for i in range(501))
    chunks = chunker.chunks(text)
    assert [(c["token_start"], c["token_end"]) for c in chunks] == [(0, 250), (200, 450), (400, 501)]
    assert all(c["text"] == text[c["char_start"]:c["char_end"]] for c in chunks)


def test_chunk_max_and_instruct_format():
    class Client:
        def vectors(self, texts):
            return np.array([[1., 0.] if "Query:" in t or t in {"theme", "hit"} else [0., 1.] for t in texts])
    class Chunker:
        def chunks(self, text):
            return [{"text": t, "char_start": 0, "char_end": len(t)} for t in text.split()]
    query = {"query": "theme", "query_id": "q", "volume": "v", "label_origin": "locator_evidence", "gold_files": {"a": "sha"}}
    pages = {"a": {"text": "miss hit", "physical_page": 1}, "b": {"text": "miss", "physical_page": 2}}
    results = experiment.evaluate(query, pages, {"a": ["gold"], "b": ["random"]}, Client(), Chunker(), ["chunks"], {})
    assert all(r["ranking"][0]["path"] == "a" and r["ranking"][0]["score"] == 1 for r in results)
    assert experiment.get_detailed_instruct(experiment.INSTRUCTION, "theme").endswith("\nQuery: theme")


def test_summary_missing_keeps_candidate_denominator():
    class Client:
        def vectors(self, texts):
            return np.array([[1., 0.]] * len(texts))
    q = {"query": "theme", "query_id": "q", "gold_files": {"a": "sha"}}
    result = experiment.evaluate(q, {}, {"a": ["gold"], "b": ["random"]}, Client(), None, ["summary"], {"a": {"text": "x"}})
    assert result[0]["status"] == "summary_incomplete_candidate_set"
    assert result[0]["missing_files"] == ["b"]


def test_no_api_real_data_preparation_no_source_mutation(corpus):
    args = args_for(corpus, "--no-api")
    before = args.db.read_bytes()
    def forbidden(*_):
        raise AssertionError("No API/tokenizer allowed")
    summary = experiment.run(args, client_factory=forbidden, chunker_factory=forbidden)
    assert summary["status"] == "prepared_no_api"
    assert args.db.read_bytes() == before
    assert (args.out / "sample.json").exists()
    assert (args.out / "report.md").exists()


def test_end_to_end_mocked_and_reuse_sample_full_volume(corpus):
    args = args_for(corpus, "--batch-size", "16", "--hard-pool-size", "10", "--variants", "page", "chunks")
    before = args.db.read_bytes()
    session = Session()
    def factory(db, run_id, args):
        return experiment.OllamaCache(db, run_id, args, session)
    summary = experiment.run(args, client_factory=factory, chunker_factory=FakeChunker)
    assert summary["status"] == "completed"
    assert args.db.read_bytes() == before
    results = json.loads((args.out / "results.json").read_text())
    assert len(results) == 8
    assert all(r["candidate_count"] >= 50 for r in results)
    assert all(r["metrics"]["gold_ranks"] for r in results)
    args.sample_manifest = args.out / "sample.json"
    args.out = args.out.parent / "full"
    args.scope = "volume"
    summary = experiment.run(args, client_factory=factory, chunker_factory=FakeChunker)
    assert summary["embedding_stats"]["cache_hits"] > 0
    assert all(r["candidate_count"] == 75 for r in json.loads((args.out / "results.json").read_text()))


def test_existing_non_experiment_db_refused(corpus):
    _, db, _ = corpus
    before = db.read_bytes()
    with pytest.raises(ValueError, match="non-experiment"):
        experiment.ExperimentDB(db)
    assert db.read_bytes() == before


def test_identical_lemmas_do_not_merge_different_entries(corpus):
    root, db, _ = corpus
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_entries SET lemma_raw='Ejus libri'")
    labels, _ = experiment.load_labels(db, root)
    assert len(labels) == 2
    assert sorted(len(q["gold_files"]) for q in labels) == [1, 2]


def test_numeric_ocr_fragments_not_sampled_as_concepts(corpus):
    root, db, _ = corpus
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_entries SET lemma_raw='123' WHERE entry_key='e2'")
    labels, excluded = experiment.load_labels(db, root)
    assert len(labels) == 1
    assert excluded["not_a_concept"] == 1


def test_manifest_tampering_rejected(corpus):
    args = args_for(corpus, "--no-api")
    experiment.run(args)
    manifest = args.out / "sample.json"
    data = json.loads(manifest.read_text())
    data["queries"][0]["query"] = "different subject"
    manifest.write_text(json.dumps(data))
    args.sample_manifest = manifest
    args.out = args.out.parent / "tampered"
    with pytest.raises(ValueError, match="manifest changed"):
        experiment.run(args)


def test_production_db_cannot_be_used_as_experiment(corpus):
    args = args_for(corpus, "--no-api")
    args.experiment_db = args.db
    before = args.db.read_bytes()
    with pytest.raises(ValueError, match="must differ"):
        experiment.run(args)
    assert args.db.read_bytes() == before


@pytest.mark.parametrize("reference", ["301 et seq.", "301 et seqq.", "301 sqq.", "301 sq.", "301 ff."])
def test_open_range_in_literal_entry_excludes_entire_concept(corpus, reference):
    root, db, _ = corpus
    with sqlite3.connect(db) as con:
        con.execute("UPDATE alphabetical_entries SET entry_raw=? WHERE entry_key='e1'",
                    (f"Gratia, {reference}",))
    labels, excluded = experiment.load_labels(db, root)
    assert [q["entry_key"] for q in labels] == ["e2"]
    assert excluded["range_targets_not_exhaustively_verified"] == 2


def test_old_manifest_reaudited_without_modification(corpus):
    args = args_for(corpus, "--no-api")
    experiment.run(args)
    manifest = args.out / "sample.json"
    data = json.loads(manifest.read_text())
    data["queries"][0]["entry_raw"] = "Gratia, 301 et seq."
    data["snapshot_sha256"] = experiment.digest(experiment.dump(data["queries"]))
    manifest.write_text(json.dumps(data))
    before = manifest.read_bytes()
    args.sample_manifest = manifest
    args.out = args.out.parent / "reaudit"
    summary = experiment.run(args)
    assert summary["queries"] == 1
    assert len(summary["audit_excluded_queries"]) == 1
    assert manifest.read_bytes() == before
    audits = json.loads((args.out / "label_audit.json").read_text())
    assert any("open_range_requires_complete_verified_targets" in a["exclusion_reasons"] for a in audits)


def test_placeholder_filter_does_not_discard_short_or_nonlatin_text(corpus):
    root, db, _ = corpus
    folder = root / "PG999/text"
    (folder / "PG999-001.txt").write_text('<pagina><bloco tipo="cabecalho">1</bloco><bloco>[ilegivel]</bloco></pagina>')
    pages = experiment.load_pages(root, ["PG999"])
    page = pages[str(folder / "PG999-001.txt")]
    assert page["excluded_reason"]
    assert "[ilegivel]" in page["blocks"][1]["source_text"]
    labels, _ = experiment.load_labels(db, root)
    plan = experiment.candidate_plan(labels[0], pages, args_for(corpus))
    assert str(folder / "PG999-001.txt") not in set(plan["base"]) | set(plan["hard_pool"])
    for text in ("ܦܘܠܚܐ", "Χριστός", "Pax", "[ilegível] Gratia"):
        assert preparation.text_exclusion(text) is None
    for text in ("[ilegivel]", "[ilegível] 123", "[unreadable]", "123 —"):
        assert preparation.text_exclusion(text)


def test_excluded_positive_fails_instead_of_shrinking_gold(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root)
    target = Path(next(iter(labels[0]["gold_files"])))
    target.write_text("[ilegivel]")
    with pytest.raises(ValueError, match="Gold page.*excluded"):
        experiment.candidate_plan(labels[0], experiment.load_pages(root, ["PG999"]), args_for(corpus))


def test_source_blocks_mapping_and_xml_never_embedded():
    raw = '<pagina><bloco tipo="texto_principal" script="grego" bbox="12345">θεο-\nλογία &amp; α</bloco><bloco tipo="nota" script="latino">Nota brevis.</bloco><notas>GENERATED</notas></pagina>'
    ocr = experiment.parse_ocr_xml_page(raw)
    text, blocks = preparation.prepare_blocks(ocr)
    assert text == "θεολογία & α\n\nNota brevis."
    assert blocks[0]["source_text"] == "θεο-\nλογία & α"
    assert blocks[0]["script"] == "grego"
    assert "θεο-\nλογία" in blocks[0]["structural_text"]
    assert blocks[0]["source_mapping"]
    for block in blocks:
        assert text[block["char_start"]:block["char_end"]] == block["text"]
        for span in block["source_mapping"]:
            if span["operation"] == "equal":
                assert block["source_text"][span["source_start"]:span["source_end"]] == block["text"][span["clean_start"]:span["clean_end"]]
    assert all(s not in text for s in ("bbox", "12345", "GENERATED", "<bloco", "script"))
    page = {"text": text, "blocks": blocks}
    chunks = preparation.page_units(page, FakeChunker(None), "block")
    assert len(chunks) == 4
    assert all(len(c["source_spans"]) == 1 for c in chunks)
    assert all(text[c["char_start"]:c["char_end"]] == c["text"] for c in chunks)
    assert len(preparation.page_units(page, FakeChunker(None), "page")) == 2


def test_context_queries_are_additive_and_explicit(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root)
    q = labels[0]
    q["context_raw"] = "De fide"
    pages = experiment.load_pages(root, ["PG999"])
    session = Session()
    args = args_for(corpus)
    cache = experiment.ExperimentDB(args.experiment_db)
    client = experiment.OllamaCache(cache, "context", args, session)
    results = experiment.evaluate(q, pages, {p: ["gold"] for p in q["gold_files"]},
                                  client, FakeChunker(args), ["chunks"], {}, include_context=True)
    assert [r["query_mode"] for r in results] == ["original", "instruct", "contextual", "contextual_instruct"]
    assert results[2]["query_text_sent"] == "De fide\nGratia"
    assert all(p["unit_count"] == 4 for r in results for p in r["ranking"])
    # Numeric-only header excluded; body and apparatus stay separate.
    assert all(len(p["winning_unit"]["source_spans"]) == 1 for r in results for p in r["ranking"])
    assert all("<bloco" not in t for call in session.calls for t in call["input"])
    q["context_raw"] = None
    results = experiment.evaluate(q, pages, {p: ["gold"] for p in q["gold_files"]},
                                  client, None, ["page"], {}, include_context=True)
    assert len(results) == 2
    cache.close()


def test_freeze_final_candidates_hashes_and_no_remining(corpus):
    args = args_for(corpus, "--variants", "page", "--hard-pool-size", "10")
    session = Session()
    def factory(db, run_id, args):
        return experiment.OllamaCache(db, run_id, args, session)
    experiment.run(args, client_factory=factory)
    original = json.loads((args.out / "candidate_manifest.json").read_text())
    args.sample_manifest = args.out / "sample.json"
    args.candidate_manifest = args.out / "candidate_manifest.json"
    args.out = args.out.parent / "frozen"
    args.seed = 99
    args.random_pages = 1
    args.no_api = True
    def forbidden(*_):
        raise AssertionError("Frozen preflight cannot call API")
    summary = experiment.run(args, client_factory=forbidden)
    assert not summary["hard_mining_pending"]
    assert json.loads((args.out / "candidate_manifest.json").read_text()) == original
    pages = experiment.load_pages(args.root, ["PG999"])
    queries = json.loads(args.sample_manifest.read_text())["queries"]
    bad = json.loads(json.dumps(original))
    bad["scope"] = "volume"
    with pytest.raises(ValueError, match="integrity"):
        preparation.restore_candidates(bad, queries, pages, "sampled")
    path = next(iter(next(iter(original["queries"].values()))["sources"]))
    pages[path]["text_sha256"] = "changed"
    with pytest.raises(ValueError, match="source changed"):
        preparation.restore_candidates(original, queries, pages, "sampled")


def test_repeated_block_roles_are_preserved():
    ocr = experiment.parse_ocr_xml_page('<pagina><bloco tipo="nota">Alpha</bloco><bloco tipo="nota">Beta</bloco></pagina>')
    text, blocks = preparation.prepare_blocks(ocr)
    assert text == "Alpha\n\nBeta"
    assert len(blocks) == 2
    assert blocks[0]["block_id"] != blocks[1]["block_id"]


def test_numeric_table_material_preserved_inside_usable_page():
    ocr = experiment.parse_ocr_xml_page('<pagina><bloco tipo="titulo">Concordia</bloco><bloco tipo="texto_principal">108 198</bloco></pagina>')
    text, blocks = preparation.prepare_blocks(ocr)
    assert text == "Concordia\n\n108 198"
    assert not blocks[1]["excluded_reason"]


def test_audit_source_context_is_not_inserted_into_query(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root)
    q = labels[0]
    source = Path(q["references"][0]["source_file"])
    source.write_text("Rachel. Gratia, 10, 11.")
    audit = experiment.audit_label_sources(q, root)
    match = audit["source_excerpts"][0]["matches"][0]
    assert "Rachel" in match["quote"]
    assert match["quote"] == source.read_text()[match["start"]:match["end"]]
    assert q["context_raw"] is None
    assert audit["human_review_status"] == "pending"


@pytest.mark.parametrize("kind", ["nota_marginal", "nota marginal", "note_marginal", "nombre_marginal"])
@pytest.mark.parametrize("marker", ["A", "B", "C", "D", "[A]", "A B C D", "Α Β Γ Δ", "123", "IV"])
def test_xml_marginal_markers_removed_only_as_whole_blocks(kind, marker):
    ocr = experiment.parse_ocr_xml_page(
        f'<pagina><bloco tipo="{kind}">{marker}</bloco>'
        '<bloco tipo="texto_principal">A Deo. Codex B legit C. D dicitur.</bloco>'
        '<bloco tipo="nota">A</bloco>'
        '<bloco tipo="nota_marginal">A Deo gratia.</bloco></pagina>')
    text, blocks = preparation.prepare_blocks(ocr)
    assert blocks[0]["excluded_reason"].startswith("xml_marginal_")
    assert blocks[0]["source_text"] == marker
    assert blocks[0]["char_start"] is None
    assert text == "A Deo. Codex B legit C. D dicitur.\n\nA\n\nA Deo gratia."
    legacy, old = preparation.prepare_blocks(ocr, "legacy-v2")
    assert old[0]["excluded_reason"] is None
    assert legacy.startswith(marker)


def test_repeated_headers_keep_argumentative_titles_and_notes(corpus):
    root, _, _ = corpus
    folder = root / "PG999/text"
    for n in range(1, 4):
        (folder / f"PG999-{n:03d}.txt").write_text(
            f'<pagina><bloco tipo="cabecalho">{n} IN GENESIM LIB. III.</bloco>'
            '<bloco tipo="cabecalho">QUÆSTIO DE FIDE</bloco>'
            '<bloco tipo="texto_principal">IN GENESIM LIB. III. Corpus argumenti.</bloco>'
            '<bloco tipo="nota">Sic mss. A legit B.</bloco>'
            '<bloco tipo="rodape">Gen. I, 1. Rom. V, 3.</bloco></pagina>')
    pages = experiment.load_pages(root, ["PG999"])
    page = pages[str(folder / 'PG999-001.txt')]
    assert page["blocks"][0]["excluded_reason"] == "xml_repeated_running_header"
    assert "QUÆSTIO DE FIDE" in page["text"]
    assert "Corpus argumenti" in page["text"]
    assert "Sic mss. A legit B." in page["text"]
    assert "Gen. I, 1. Rom. V, 3." in page["text"]
    report = experiment.cleaning_report(pages)
    assert report["PG999"]["excluded_blocks"]["xml_repeated_running_header"] == 3
    assert any(e["reason"] == "xml_repeated_running_header" for e in report["PG999"]["events"])


def test_unique_title_not_dropped_or_letters_deleted_inside_body():
    ocr = experiment.parse_ocr_xml_page(
        '<pagina><bloco tipo="cabecalho">DE INCARNATIONE</bloco>'
        '<bloco tipo="texto_principal">A\nDeo procedit. B et C leguntur.</bloco>'
        '<bloco tipo="nota_marginal">ABBA</bloco></pagina>')
    text, blocks = preparation.prepare_blocks(ocr)
    assert text == "DE INCARNATIONE\n\nA Deo procedit. B et C leguntur.\n\nABBA"
    assert all(not b["excluded_reason"] for b in blocks)


@pytest.mark.parametrize("old_version", ["retrieval-preparation-v2", "retrieval-preparation-v3"])
def test_refresh_candidate_texts_preserves_original_sources(corpus, old_version):
    args = args_for(corpus, "--no-api", "--hard-negatives", "0", "--text-cleaning", "legacy-v2")
    folder = args.root / "PG999/text"
    for n in range(1, 76):
        p = folder / f"PG999-{n:03d}.txt"
        p.write_text(p.read_text().replace('</pagina>', '<bloco tipo="nota_marginal">A</bloco></pagina>'))
    experiment.run(args)
    queries = json.loads((args.out / "sample.json").read_text())["queries"]
    manifest = json.loads((args.out / "candidate_manifest.json").read_text())
    # Reproduce the already-published v2 manifest envelope.
    manifest["version"] = old_version
    manifest["sha256"] = preparation.canonical_hash({k: v for k, v in manifest.items() if k != 'sha256'})
    pages = experiment.load_pages(args.root, ["PG999"])
    with pytest.raises(ValueError, match="version"):
        preparation.restore_candidates(manifest, queries, pages, "sampled")
    plans = preparation.restore_candidates(manifest, queries, pages, "sampled", refresh_texts=True)
    assert all(p["text_refresh"] for p in plans.values())
    assert all(plans[k]["selected"] == manifest["queries"][k]["selected"] for k in plans)
    refreshed = preparation.freeze_candidates(queries, plans, pages, "sampled")
    assert refreshed["version"] == preparation.PREPARATION_VERSION
    preparation.restore_candidates(refreshed, queries, pages, "sampled")
    path = next(iter(next(iter(manifest["queries"].values()))["selected"]))
    pages[path]["ocr_sha256"] = "changed-original"
    with pytest.raises(ValueError, match="source changed"):
        preparation.restore_candidates(manifest, queries, pages, "sampled", refresh_texts=True)


def test_cleaned_layout_only_positive_is_not_silently_removed(corpus):
    root, db, _ = corpus
    labels, _ = experiment.load_labels(db, root)
    path = Path(next(iter(labels[0]["gold_files"])))
    path.write_text('<pagina><bloco tipo="nota_marginal">A</bloco></pagina>')
    pages = experiment.load_pages(root, ["PG999"])
    assert pages[str(path)]["excluded_reason"] == "no_substantive_text_after_xml_cleaning"
    with pytest.raises(ValueError, match="Gold page.*excluded"):
        experiment.candidate_plan(labels[0], pages, args_for(corpus))


@pytest.mark.parametrize("symbol", ["•", "†", "—", "***", "⋆ •"])
def test_symbol_filter_is_only_marginal_and_versioned(symbol):
    ocr = experiment.parse_ocr_xml_page(
        f'<pagina><bloco tipo="nota_marginal">{symbol}</bloco>'
        '<bloco tipo="texto_principal">Verbum † significat.</bloco>'
        '<bloco tipo="nota_marginal">† Vide infra.</bloco>'
        '<bloco tipo="tabela">123 456</bloco>'
        f'<bloco tipo="aparato_critico">{symbol}</bloco></pagina>')
    text, blocks = preparation.prepare_blocks(ocr, "xml-v2")
    assert blocks[0]["excluded_reason"] == "xml_marginal_symbol_only"
    assert all(b["excluded_reason"] is None for b in blocks[1:])
    assert "123 456" in text and "† Vide infra." in text
    _, old = preparation.prepare_blocks(ocr, "xml-v1")
    assert old[0]["excluded_reason"] is None


class WholeChunk:
    def chunks(self, text):
        return [{"text": text, "char_start": 0, "char_end": len(text),
                 "token_start": 0, "token_end": len(text.split())}]


def prepared_page(xml):
    text, blocks = preparation.prepare_blocks(experiment.parse_ocr_xml_page(xml))
    return {"text": text, "blocks": blocks}


@pytest.mark.parametrize("kind,title", [("cabecalho", "IN PSALMUM XVI."),
                                      ("texto_principal", "LIBER IV."),
                                      ("titulo_secao", "De fide")])
def test_short_title_attached_with_exact_spans(kind, title):
    page = prepared_page(f'<pagina><bloco tipo="{kind}" script="latino">{title}</bloco>'
                         '<bloco tipo="nota_marginal">A</bloco>'
                         '<bloco tipo="texto_principal" script="latino">Corpus de fide.</bloco></pagina>')
    units = preparation.page_units(page, WholeChunk(), "block-context")
    assert len(units) == 1
    unit = units[0]
    assert unit["text"] == title + "\n\nCorpus de fide."
    assert len(unit["source_spans"]) == 2
    assert unit["group"]["title_context"] == "attached"
    assert unit["token_offset_space"] == "block_group"
    assert page["text"][unit["char_start"]:unit["char_end"]] == unit["text"]
    assert len(preparation.page_units(page, WholeChunk(), "block")) == 2


@pytest.mark.parametrize("middle", ['<bloco tipo="nota">Nota.</bloco>',
                                   '<bloco tipo="aparato_critico">Nota.</bloco>',
                                   '<bloco tipo="texto_principal" script="grego">λόγος</bloco>'])
def test_title_context_does_not_cross_notes_or_scripts(middle):
    page = prepared_page('<pagina><bloco tipo="cabecalho" script="latino">LIBER IV.</bloco>'
                         + middle + '<bloco tipo="texto_principal" script="latino">Corpus.</bloco></pagina>')
    units = preparation.page_units(page, WholeChunk(), "block-context")
    assert len(units) == 3
    assert units[0]["group"]["title_context"] == "unattached"
    assert units[0]["text"] == "LIBER IV."


def test_title_chain_and_orphan_preserved_without_guessing_prose():
    page = prepared_page('<pagina><bloco tipo="cabecalho">LIBER IV.</bloco>'
                         '<bloco tipo="titulo">De fide</bloco>'
                         '<bloco tipo="texto_principal">In psalmum dixit propheta.</bloco>'
                         '<bloco tipo="texto_principal">Pax.</bloco>'
                         '<bloco tipo="titulo">CAPUT V.</bloco></pagina>')
    units = preparation.page_units(page, WholeChunk(), "block-context")
    assert len(units) == 3
    assert len(units[0]["source_spans"]) == 3
    assert units[1]["text"] == "Pax."
    assert units[2]["group"]["title_context"] == "unattached"
    assert units[2]["text"] == "CAPUT V."


def test_context_groups_obey_chunk_token_budget_and_offsets():
    class Tokenizer:
        def __call__(self, text, **kwargs):
            return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}
    chunker = experiment.QwenChunks.__new__(experiment.QwenChunks)
    chunker.tokenizer = Tokenizer()
    chunker.size, chunker.overlap = 250, 50
    page = prepared_page('<pagina><bloco tipo="titulo">LIBER IV.</bloco>'
                         '<bloco tipo="texto_principal">' + ' '.join(['fides'] * 600) + '</bloco></pagina>')
    units = preparation.page_units(page, chunker, "block-context")
    assert len(units) == 3 and all(u["token_count"] <= 250 for u in units)
    assert units[0]["text"].startswith("LIBER IV.\n\nfides")
    assert len(units[0]["source_spans"]) == 2
    assert len(units[-1]["source_spans"]) == 1
    assert all(page["text"][u["char_start"]:u["char_end"]] == u["text"] for u in units)


def test_sieve_metrics_all_positives_and_candidate_fraction(tmp_path):
    ranking = [{"path": str(i), "score": 1 - i / 100} for i in range(1, 61)]
    m = experiment.ranking_metrics(ranking, {"2", "22"})
    assert m["recall@20"] == .5 and m["recall@30"] == 1
    assert m["hit@20"] == 1 and m["all_gold@20"] == 0 and m["all_gold@30"] == 1
    assert m["candidate_fraction@20"] == pytest.approx(1 / 3)
    small = experiment.ranking_metrics(ranking[:3], {"2"})
    assert small["candidate_fraction@30"] == 1
    row = {"status": "evaluated", "variant": "chunks", "query_mode": "instruct",
           "label_origin": "previous_jev", "literal_in_any_gold": False, "volume": "PG069", "metrics": m}
    aggregated = experiment.aggregate([row])
    overall = next(a for a in aggregated if a["stratum"] == "all")
    assert overall["Recall@20"] == .5 and overall["AllGold@30"] == 1
    experiment.reports(tmp_path, [], {"status": "completed", "metrics": aggregated})
    for name in ("report.md", "metrics.csv"):
        text = (tmp_path / name).read_text()
        assert "Recall@20" in text and "Recall@30" in text and "AllGold" in text


def test_context_preflight_no_api_records_groups(corpus):
    args = args_for(corpus, "--no-api", "--hard-negatives", "0", "--chunk-boundaries", "block-context")
    path = args.root / 'PG999/text/PG999-010.txt'
    path.write_text('<pagina><bloco tipo="titulo">LIBER I.</bloco>'
                    '<bloco tipo="texto_principal">Corpus de fide.</bloco></pagina>')
    summary = experiment.run(args)
    groups = json.loads((args.out / 'block_groups.json').read_text())
    assert groups[str(path)][0]["title_context"] == "attached"
    assert summary["embedding_calls"] == 0
    assert summary["title_context_groups"]["attached"] == 1
