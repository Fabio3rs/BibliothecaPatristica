import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import resumo_serial
from tools import generate_resumo_embeddings
from tools import export_enrichment_shards
from tools import render_publication_from_shards
from tools import hdbscan_resumo_embeddings
from tools import repair_resumo_cumulative_from_raw
from tools.translate_resumos_v2 import (
    build_translation_prompt,
    load_glossaries,
    parse_translation,
)
from resumo_v2 import (
    StaticPageAnalysis,
    SummaryCandidate,
    analyze_page,
    build_embedding_text,
    build_summary_search_text,
    current_summary_select_sql,
    extract_json_object_response,
    get_or_create_run,
    init_v2_schema,
    load_volume_index_hints,
    reconcile_candidate_work_keys,
    promote_segment,
    parse_summary_candidate,
    store_context_anchor,
    store_generation,
)


def _connection() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    init_v2_schema(con)
    return con


def _analysis() -> StaticPageAnalysis:
    return analyze_page(
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        page_text=(
            '<pagina estado="com_texto">'
            '<bloco tipo="cabecalho" bbox="30,10,970,70">EPISTOLAE AD VIRGINES</bloco>'
            '<bloco tipo="texto_principal" bbox="60,80,940,920">'
            + ("Texto latino introdutório da obra e de sua dedicatória. " * 20)
            + "</bloco></pagina>"
        ),
        ocr_clean="Texto latino introdutório da obra e de sua dedicatória. " * 20,
        index_hints={
            "exact_start_candidates": [
                {
                    "work_key": "work:virgines",
                    "title_original": "Epistolae ad Virgines",
                    "confidence": 0.99,
                }
            ]
        },
    )


def _candidate(status: str = "valid", summary: str = "Resumo válido da introdução da obra.") -> SummaryCandidate:
    return SummaryCandidate(
        page_kinds=("work_start", "dedication"),
        segments=(
            {
                "order": 1,
                "kind": "work_start",
                "work_key": "work:virgines",
                "summary": summary,
            },
        ),
        contributors=(),
        summary_display_pt=summary,
        cumulative_summary=summary,
        source_conflicts=(),
        administrative_reason="",
        primary_page_kind="work_start",
        status=status,
        validation_issues=(),
        context_reset=True,
        context_reset_confidence=0.99,
    )


def _run(con: sqlite3.Connection) -> sqlite3.Row:
    pages = [Path("page-175.txt"), Path("page-176.txt")]
    return get_or_create_run(
        con,
        documento="PG001",
        pages=pages,
        provider="ollama",
        model="gemma4:cloud",
    )


def test_cli_returns_nonzero_when_a_volume_has_a_fatal_error(tmp_path, monkeypatch):
    volume = tmp_path / "PG999"
    volume.mkdir()

    def fail_volume(**_kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(resumo_serial, "process_volume_v2", fail_volume)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resumo_serial.py",
            "--pipeline",
            "v2",
            "--volume-dir",
            str(volume),
            "--db",
            str(tmp_path / "resumos.sqlite"),
            "--indices-db",
            str(tmp_path / "indices.sqlite"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        resumo_serial.main()

    assert exc_info.value.code == 1


def test_volume_processing_lock_rejects_a_second_writer(tmp_path):
    db_path = tmp_path / "resumos.sqlite"
    first = resumo_serial.acquire_volume_processing_lock(db_path, "PG001")
    try:
        with pytest.raises(RuntimeError, match="já está sendo processado"):
            resumo_serial.acquire_volume_processing_lock(db_path, "PG001")
    finally:
        resumo_serial.release_volume_processing_lock(first)

    second = resumo_serial.acquire_volume_processing_lock(db_path, "PG001")
    resumo_serial.release_volume_processing_lock(second)


def _blank_key_candidate(*, kinds=("body",), segment_kinds=("body",)) -> SummaryCandidate:
    segments = tuple(
        {
            "order": order,
            "kind": kind,
            "work_key": "",
            "summary": f"Resumo concreto e suficientemente detalhado do segmento {order}.",
        }
        for order, kind in enumerate(segment_kinds, start=1)
    )
    return SummaryCandidate(
        page_kinds=tuple(kinds),
        segments=segments,
        contributors=(),
        summary_display_pt=" ".join(item["summary"] for item in segments),
        cumulative_summary="Síntese corrente suficientemente detalhada.",
        source_conflicts=(),
        administrative_reason="",
        primary_page_kind=segments[0]["kind"],
        status="valid",
        validation_issues=(),
        context_reset=False,
        context_reset_confidence=0.0,
    )


def test_missing_end_file_uses_next_start_with_boundary_overlap(tmp_path):
    pages = [tmp_path / f"page-{number}.txt" for number in range(1, 6)]
    indices = sqlite3.connect(":memory:")
    indices.row_factory = sqlite3.Row
    indices.executescript(
        """
        CREATE TABLE works(
            volume_id TEXT, work_order INTEGER, work_key TEXT,
            author_raw TEXT, title_raw TEXT, start_page INTEGER,
            end_page INTEGER, start_file TEXT, end_file TEXT,
            confidence REAL
        );
        CREATE TABLE index_strings(id INTEGER PRIMARY KEY, source_text TEXT);
        CREATE TABLE index_translations(
            id INTEGER PRIMARY KEY, string_id INTEGER,
            language TEXT, translated_text TEXT
        );
        INSERT INTO works VALUES
            ('PGX', 1, 'work:a', 'Autor A', 'Obra A', 1, NULL,
             'page-1.txt', NULL, 0.95),
            ('PGX', 2, 'work:b', 'Autor B', 'Obra B completa', 4, NULL,
             'page-4.txt', NULL, 0.90),
            ('PGX', 2, 'work:b_alias', 'Autor B', 'Obra B', 4, NULL,
             'page-4.txt', NULL, 0.90);
        """
    )

    hints = load_volume_index_hints(indices, "PGX", pages)

    assert [item["work_key"] for item in hints[2]["containing_work_candidates"]] == ["work:a"]
    assert {item["work_key"] for item in hints[4]["containing_work_candidates"]} == {
        "work:a",
        "work:b",
    }
    assert hints[4]["range_ambiguous"] is True
    assert hints[4]["exact_start_candidates"][0]["alias_work_keys"] == ["work:b_alias"]
    assert hints[2]["containing_work_candidates"][0]["range_source"] == "inferred_next_start"
    assert hints[5]["containing_work_candidates"][0]["range_source"] == "inferred_volume_end"


def test_work_key_is_inherited_when_model_omits_it():
    analysis = analyze_page(
        documento="PGX",
        pagina_num=2,
        pagina_file="page-2.txt",
        page_text="Texto patrístico contínuo. " * 30,
        ocr_clean="Texto patrístico contínuo. " * 30,
        index_hints={"containing_work_candidates": []},
    )

    candidate, next_key, resolution = reconcile_candidate_work_keys(
        _blank_key_candidate(), analysis, "work:a"
    )

    assert candidate.segments[0]["work_key"] == "work:a"
    assert next_key == "work:a"
    assert resolution["source"] == "inherited_previous"
    assert candidate.status == "valid"


def test_exact_start_switches_only_at_transition_segment():
    analysis = analyze_page(
        documento="PGX",
        pagina_num=4,
        pagina_file="page-4.txt",
        page_text="Fim da obra anterior. Início da obra seguinte. " * 20,
        ocr_clean="Fim da obra anterior. Início da obra seguinte. " * 20,
        index_hints={
            "exact_start_candidates": [
                {"work_key": "work:b", "title_original": "Obra B", "confidence": 0.95}
            ],
            "containing_work_candidates": [
                {"work_key": "work:a", "range_source": "inferred_next_start"},
                {"work_key": "work:b", "range_source": "inferred_volume_end"},
            ],
        },
    )
    source = _blank_key_candidate(
        kinds=("transition", "work_start"),
        segment_kinds=("body", "work_start"),
    )

    candidate, next_key, resolution = reconcile_candidate_work_keys(
        source, analysis, "work:a"
    )

    assert [item["work_key"] for item in candidate.segments] == ["work:a", "work:b"]
    assert next_key == "work:b"
    assert resolution["source"] == "index_exact_start"
    assert candidate.context_reset is True


def test_exact_index_start_resets_context_even_if_model_omits_work_start_kind():
    analysis = analyze_page(
        documento="PGX",
        pagina_num=4,
        pagina_file="page-4.txt",
        page_text="Página de título inequívoca da obra seguinte. " * 20,
        ocr_clean="Página de título inequívoca da obra seguinte. " * 20,
        index_hints={
            "exact_start_candidates": [
                {"work_key": "work:b", "title_original": "Obra B", "confidence": 0.95}
            ],
            "containing_work_candidates": [
                {"work_key": "work:b", "range_source": "inferred_volume_end"}
            ],
        },
    )

    candidate, next_key, _resolution = reconcile_candidate_work_keys(
        _blank_key_candidate(kinds=("title_page",), segment_kinds=("title_page",)),
        analysis,
        "work:a",
    )

    assert candidate.segments[0]["work_key"] == "work:b"
    assert next_key == "work:b"
    assert candidate.context_reset is True
    assert candidate.context_reset_confidence == 0.95


def test_unresolved_transition_keeps_active_key_without_blocking_context():
    analysis = analyze_page(
        documento="PGX",
        pagina_num=3,
        pagina_file="page-3.txt",
        page_text="Possível transição editorial sem título conclusivo. " * 20,
        ocr_clean="Possível transição editorial sem título conclusivo. " * 20,
        index_hints={"containing_work_candidates": []},
    )

    candidate, next_key, resolution = reconcile_candidate_work_keys(
        _blank_key_candidate(kinds=("transition",), segment_kinds=("transition",)),
        analysis,
        "work:a",
    )

    assert candidate.segments[0]["work_key"] == "work:a"
    assert next_key == "work:a"
    assert candidate.status == "metadata_pending"
    assert "possible_work_transition_unresolved" in candidate.validation_issues
    assert resolution["possible_change"] is True


def test_inferred_work_key_provenance_is_stored_as_untrusted_anchor():
    con = _connection()

    store_context_anchor(
        con,
        documento="PGX",
        pagina_num=2,
        pagina_file="page-2.txt",
        source_hash="generation-source",
        resolution={
            "work_key": "work:a",
            "source": "inferred_next_start",
            "confidence": 0.65,
            "possible_change": False,
        },
    )

    row = con.execute("SELECT * FROM resumo_context_anchors").fetchone()
    assert row["work_key"] == "work:a"
    assert row["confidence"] == 0.65
    assert row["is_trusted"] == 0
    assert json.loads(row["evidence_json"])["source"] == "inferred_next_start"


def test_extract_json_object_accepts_markdown_fences_and_trailing_text():
    payload = {
        "page_kinds": ["body"],
        "segments": [],
        "contributors": [],
        "cumulative_summary": "Síntese",
        "source_conflicts": [],
        "administrative_reason": "",
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)

    assert extract_json_object_response(f"```json\n{encoded}\n```") == payload
    assert extract_json_object_response(
        f"~~~JSON\n{encoded}\nObservação residual do modelo.\n~~~"
    ) == payload
    assert extract_json_object_response(
        f"Resposta em formato solicitado:\n{encoded}\n```"
    ) == payload


def test_v2_preserves_cumulative_summary_above_soft_target():
    cumulative = ("Síntese histórica e teológica preservada integralmente. " * 40).strip()
    payload = {
        "page_kinds": ["body"],
        "segments": [
            {
                "order": 1,
                "kind": "body",
                "work_key": "work:virgines",
                "summary": (
                    "A argumentação desenvolve de modo concreto o tema histórico "
                    "e teológico apresentado na página corrente."
                ),
            }
        ],
        "contributors": [],
        "cumulative_summary": cumulative,
        "source_conflicts": [],
        "administrative_reason": "",
    }

    candidate = parse_summary_candidate(
        json.dumps(payload, ensure_ascii=False),
        _analysis(),
        {"work:virgines"},
    )

    assert len(cumulative) > 1800
    assert candidate.cumulative_summary == cumulative
    assert "cumulative_summary_too_long" not in candidate.validation_issues
    assert candidate.status == "valid"


def test_v2_context_is_only_reduced_when_the_real_window_requires_it():
    previous = "INÍCIO " + ("contexto acumulado relevante " * 500) + " FIM"

    full = resumo_serial.fit_v2_previous_context(previous, "prompt curto", 131072)
    fitted = resumo_serial.fit_v2_previous_context(previous, "prompt curto", 4096)

    assert full == previous
    assert len(fitted) < len(previous)
    assert fitted.startswith("INÍCIO")
    assert fitted.endswith("FIM")
    assert "contexto intermediário omitido" in fitted


def test_cumulative_repair_restores_raw_and_rechains_tail():
    con = _connection()
    run = _run(con)
    analysis = _analysis()
    full = ("Síntese completa preservada no retorno bruto do modelo. " * 40).strip()
    truncated = full[:1800].rsplit(" ", 1)[0]
    first_candidate = replace(
        _candidate(status="metadata_pending"),
        cumulative_summary=truncated,
        validation_issues=("cumulative_summary_too_long",),
    )
    raw_first = json.dumps(
        {
            "page_kinds": ["work_start"],
            "segments": list(first_candidate.segments),
            "contributors": [],
            "cumulative_summary": full,
            "source_conflicts": [],
            "administrative_reason": "",
        },
        ensure_ascii=False,
    )
    first = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=None,
        ocr_result_id=None,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=first_candidate,
        search_text_pt="busca um",
        embedding_text="embedding um",
        analysis=analysis,
        raw_response=raw_first,
        facsimile_used=False,
        tainted_by_page=None,
    )
    second_candidate = _candidate(summary="Resumo válido da página seguinte da obra.")
    raw_second = json.dumps(
        {
            "page_kinds": list(second_candidate.page_kinds),
            "segments": list(second_candidate.segments),
            "contributors": [],
            "cumulative_summary": second_candidate.cumulative_summary,
            "source_conflicts": [],
            "administrative_reason": "",
        },
        ensure_ascii=False,
    )
    second = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=None,
        ocr_result_id=None,
        documento="PG001",
        pagina_num=176,
        pagina_file="page-176.txt",
        previous_generation=first,
        source_hash="source-b",
        provider="ollama",
        model="gemma4:cloud",
        candidate=second_candidate,
        search_text_pt="busca dois",
        embedding_text="embedding dois",
        analysis=analysis,
        raw_response=raw_second,
        facsimile_used=False,
        tainted_by_page=None,
    )

    _run_row, changes, report = repair_resumo_cumulative_from_raw.plan_repair(
        con, run_id=run["id"]
    )
    assert report["restored_summaries"] == 1
    assert report["chain_rows_changed"] == 2
    assert report["status_after"] == {"valid": 2}

    repair_resumo_cumulative_from_raw.apply_repair(
        con,
        run_id=run["id"],
        documento="PG001",
        changes=changes,
    )
    restored = con.execute(
        "SELECT * FROM resumo_generations WHERE id=?", (first["id"],)
    ).fetchone()
    rechained = con.execute(
        "SELECT * FROM resumo_generations WHERE id=?", (second["id"],)
    ).fetchone()
    assert restored["cumulative_summary"] == full
    assert restored["status"] == "valid"
    assert rechained["previous_chain_hash"] == restored["chain_hash"]
    review = con.execute(
        "SELECT status FROM resumo_review_queue WHERE generation_id=? AND issue_kind=?",
        (first["id"], "cumulative_summary_too_long"),
    ).fetchone()
    assert review["status"] == "resolved"

    _run_row, _changes, second_report = repair_resumo_cumulative_from_raw.plan_repair(
        con, run_id=run["id"]
    )
    assert second_report["restored_summaries"] == 0
    assert second_report["changed_generations"] == 0


def test_schema_uses_wal_safe_timestamps_and_clears_stale_embedding():
    con = _connection()
    run = _run(con)
    analysis = _analysis()
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=7,
        ocr_result_id=70,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(),
        search_text_pt="texto pesquisável",
        embedding_text="texto vetorial",
        analysis=analysis,
        raw_response="{}",
        facsimile_used=False,
        tainted_by_page=None,
    )
    assert generation["created_at"]
    con.execute(
        "UPDATE resumo_generations SET embedding=?, embedding_dim=3, embedding_model='m', embedding_source_hash='h' WHERE id=?",
        (b"abc", generation["id"]),
    )
    con.commit()
    time.sleep(1.05)
    con.execute(
        "UPDATE resumo_generations SET embedding_text='texto vetorial revisto' WHERE id=?",
        (generation["id"],),
    )
    con.commit()
    changed = con.execute("SELECT * FROM resumo_generations WHERE id=?", (generation["id"],)).fetchone()
    assert changed["embedding"] is None
    assert changed["embedding_dim"] is None
    assert changed["embedding_model"] is None
    assert changed["embedding_source_hash"] is None
    assert changed["updated_at"] > changed["created_at"]


def test_shadow_generation_does_not_replace_legacy_and_hybrid_requires_promotion():
    con = _connection()
    con.execute(
        """
        CREATE TABLE resumos (
            id INTEGER PRIMARY KEY,
            documento TEXT,
            pagina_num INTEGER,
            resumo_pagina TEXT,
            resumo_global TEXT,
            summary_page_clean TEXT,
            page_kind TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO resumos VALUES (7, 'PG001', 175, 'legado página', 'legado global', 'legado busca', 'unknown')"
    )
    con.commit()
    run = _run(con)
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=7,
        ocr_result_id=70,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(summary="novo resumo"),
        search_text_pt="nova busca",
        embedding_text="novo embedding",
        analysis=_analysis(),
        raw_response="{}",
        facsimile_used=True,
        tainted_by_page=None,
    )
    legacy = con.execute("SELECT * FROM resumos WHERE id=7").fetchone()
    assert legacy["resumo_pagina"] == "legado página"
    shadow = con.execute(current_summary_select_sql()).fetchone()
    assert shadow["effective_summary_page"] == "legado página"

    assert promote_segment(con, run["id"], "PG001", 175, 175) == 1
    published = con.execute(current_summary_select_sql()).fetchone()
    assert published["v2_generation_id"] == generation["id"]
    assert published["effective_summary_page"] == "novo resumo"


def test_non_promotable_segment_stays_in_shadow():
    con = _connection()
    run = _run(con)
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=None,
        ocr_result_id=None,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(status="context_provisional"),
        search_text_pt="busca",
        embedding_text="embedding",
        analysis=_analysis(),
        raw_response="{}",
        facsimile_used=False,
        tainted_by_page=175,
    )
    assert promote_segment(con, run["id"], "PG001", 175, 175) == 0
    assert con.execute("SELECT is_current FROM resumo_generations WHERE id=?", (generation["id"],)).fetchone()[0] == 0


def test_static_analysis_does_not_count_every_bbox_as_a_column_or_duplicate_full_ocr():
    analysis = _analysis()
    assert analysis.column_count == 1
    serialized = analysis.to_dict()
    assert "ocr_clean" not in serialized
    assert len(serialized["ocr_clean_preview"]) <= 400
    assert analysis.facsimile_score >= 2  # obra detectada no índice
    assert analysis.facsimile_score < 5


def test_derived_texts_deduplicate_canonical_labels():
    analysis = _analysis()
    search = build_summary_search_text(
        "Resumo principal",
        [{"summary": "Resumo principal"}],
        analysis,
        ["Epistolae ad Virgines", "Epistolæ ad Virgines"],
    )
    assert search.count("Resumo principal") == 1
    assert "Epistolae ad Virgines" in search
    embedding = build_embedding_text("Resumo principal", ["Epistolae ad Virgines"])
    assert embedding == "Epistolae ad Virgines. Resumo principal"


def test_legacy_upsert_preserves_keywords_and_row_identity():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    resumo_serial.init_resumo_schema(con)
    con.execute(
        """
        INSERT INTO resumos
            (documento,pagina_num,pagina_file,pagina_texto,resumo_pagina,resumo_global,
             modelo,criado_em,keywords_json,keywords_source,keywords_modelo)
        VALUES ('PGX',1,'old.txt','old','old summary','old global','old-model','date',
                '["preservar"]','pagina_texto','keyword-model')
        """
    )
    original_id = con.execute("SELECT id FROM resumos").fetchone()[0]
    resumo_serial.save_resumo(
        con,
        documento="PGX",
        pagina_num=1,
        pagina_file="new.txt",
        pagina_texto="new",
        resumo_pagina="new summary",
        resumo_global="new global",
        modelo="new-model",
        author_detected="Autor",
        work_detected="Obra",
        summary_page_clean="new summary",
        summary_global_clean="new global",
    )
    row = con.execute("SELECT * FROM resumos").fetchone()
    assert row["id"] == original_id
    assert row["keywords_json"] == '["preservar"]'
    assert row["keywords_source"] == "pagina_texto"
    assert row["keywords_modelo"] == "keyword-model"


def test_v2_pipeline_is_serial_resumable_and_keeps_results_in_shadow(tmp_path, monkeypatch):
    volume = tmp_path / "PGX" / "text"
    volume.mkdir(parents=True)
    for page in (1, 2):
        (volume / f"page-{page:03d}.txt").write_text(
            '<pagina estado="com_texto"><bloco tipo="texto_principal" '
            f'bbox="60,80,940,920">Conteúdo patrístico da página {page}. '
            + ("Argumento histórico e teológico explícito. " * 20)
            + "</bloco></pagina>",
            encoding="utf-8",
        )
    con = sqlite3.connect(tmp_path / "resumos.db")
    con.row_factory = sqlite3.Row
    resumo_serial.init_resumo_schema(con)
    for page in (1, 2):
        con.execute(
            """
            INSERT INTO resumos
                (documento,pagina_num,pagina_file,pagina_texto,resumo_pagina,resumo_global,modelo,criado_em)
            VALUES ('PGX',?,?,?,'resumo legado','síntese legada','legacy','date')
            """,
            (page, f"page-{page:03d}.txt", f"ocr {page}"),
        )
    con.commit()

    calls: list[str] = []

    def fake_chat(prompt_system, prompt_user, **_kwargs):
        calls.append(prompt_user)
        page = len(calls)
        return json.dumps(
            {
                "page_kinds": ["body"],
                "segments": [
                    {
                        "order": 1,
                        "kind": "body",
                        "work_key": "",
                        "summary": (
                            f"A página {page} desenvolve um argumento histórico e teológico "
                            "concreto, identificando seus conceitos e sua progressão textual."
                        ),
                    }
                ],
                "contributors": [],
                "cumulative_summary": (
                    f"Síntese acumulada até a página {page}, com o desenvolvimento "
                    "histórico e teológico da obra corrente."
                ),
                "source_conflicts": [],
                "administrative_reason": "",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(resumo_serial, "llm_chat", fake_chat)
    monkeypatch.setattr(
        resumo_serial,
        "_scripture_evidence_for_page",
        lambda *_args, **_kwargs: {"status": "ok", "candidates": [], "candidate_count": 0},
    )
    kwargs = dict(
        volume_dir=volume.parent,
        con=con,
        model="gemma4:cloud",
        base_url="http://unused",
        timeout=1,
        retries=1,
        provider="ollama",
        reasoning_effort="high",
        api_key_env="UNUSED",
        num_ctx=16384,
        dry_run=False,
        page_filter=None,
        verbose=False,
        versions_con=None,
        facsimile_mode="never",
        facsimile_threshold=2,
        lookahead_pages=2,
        force_replace_from=None,
        force_replace_through="next-work",
        promote=False,
        indices_db=tmp_path / "missing-indices.db",
    )
    resumo_serial.process_volume_v2(**kwargs)
    assert len(calls) == 2
    assert "Síntese acumulada até a página 1" in calls[1]
    assert con.execute("SELECT COUNT(*) FROM resumo_generations").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM resumo_generations WHERE is_current=1").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM resumos WHERE resumo_pagina='resumo legado'").fetchone()[0] == 2

    resumo_serial.process_volume_v2(**kwargs)
    assert len(calls) == 2  # retomada automática não chama páginas concluídas
    resumo_serial.process_volume_v2(**{**kwargs, "promote": True})
    assert len(calls) == 2
    assert con.execute("SELECT COUNT(*) FROM resumo_generations WHERE is_current=1").fetchone()[0] == 2


def test_translation_pass_receives_only_summary_search_and_glossary():
    con = _connection()
    run = _run(con)
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=None,
        ocr_result_id=None,
        documento="PG001",
        pagina_num=175,
        pagina_file="secret-ocr-file.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(summary="Resumo canônico em português para traduzir."),
        search_text_pt="Texto de busca em português.",
        embedding_text="Texto de embedding privado.",
        analysis=_analysis(),
        raw_response="RESPOSTA RAW QUE NÃO PODE SER ENVIADA",
        facsimile_used=True,
        tainted_by_page=None,
    )
    prompt = build_translation_prompt(
        generation,
        ["en", "fr", "it"],
        [{"title_original": "Epistolae", "title_translations": {"en": "Epistles"}}],
    )
    assert "Resumo canônico em português" in prompt
    assert "Texto de busca em português" in prompt
    assert "Epistles" in prompt
    assert "secret-ocr-file" not in prompt
    assert "embedding privado" not in prompt
    assert "RESPOSTA RAW" not in prompt
    translations = parse_translation(
        '{"translations":{'
        '"en":{"summary_display":"A sufficiently complete translated summary.",'
        '"cumulative_summary":"A sufficiently complete cumulative translation.",'
        '"search_text":"A sufficiently complete translated search text."},'
        '"fr":{"summary_display":"Un résumé traduit suffisamment complet pour affichage.",'
        '"cumulative_summary":"Une synthèse cumulative traduite suffisamment complète.",'
        '"search_text":"Un texte de recherche traduit suffisamment complet."},'
        '"it":{"summary_display":"Un riassunto tradotto sufficientemente completo.",'
        '"cumulative_summary":"Una sintesi cumulativa tradotta sufficientemente completa.",'
        '"search_text":"Un testo di ricerca tradotto sufficientemente completo."}}}',
        ["en", "fr", "it"],
    )
    assert translations["en"] == {
        "summary": "A sufficiently complete translated summary.",
        "cumulative": "A sufficiently complete cumulative translation.",
        "search": "A sufficiently complete translated search text.",
    }


def test_translation_glossary_reuses_all_index_languages():
    generations = _connection()
    generation = generations.execute(
        """
        SELECT 9 AS id,
               '[{"work_key":"work:virgines"}]' AS segments_json,
               '{}' AS static_analysis_json
        """
    ).fetchone()
    indices = sqlite3.connect(":memory:")
    indices.row_factory = sqlite3.Row
    indices.executescript(
        """
        CREATE TABLE works(work_key TEXT, author_raw TEXT, title_raw TEXT);
        CREATE TABLE index_strings(id INTEGER PRIMARY KEY, source_text TEXT);
        CREATE TABLE index_translations(
            id INTEGER PRIMARY KEY, string_id INTEGER, language TEXT, translated_text TEXT
        );
        INSERT INTO works VALUES ('work:virgines', 'Clemens', 'Epistolae ad virgines');
        INSERT INTO index_strings VALUES (1, 'Clemens'), (2, 'Epistolae ad virgines');
        INSERT INTO index_translations VALUES
            (1, 1, 'pt-br', 'Clemente'), (2, 1, 'en', 'Clement'),
            (3, 1, 'fr', 'Clément'), (4, 1, 'it', 'Clemente'),
            (5, 2, 'pt-br', 'Epístolas às virgens'),
            (6, 2, 'en', 'Epistles to virgins'),
            (7, 2, 'fr', 'Épîtres aux vierges'),
            (8, 2, 'it', 'Epistole alle vergini');
        """
    )
    glossary = load_glossaries(indices, [generation], ["en", "fr", "it"])["9"][0]
    assert glossary["work_key"] == "work:virgines"
    assert glossary["title_translations"] == {
        "pt-br": "Epístolas às virgens",
        "en": "Epistles to virgins",
        "fr": "Épîtres aux vierges",
        "it": "Epistole alle vergini",
    }


def test_v2_embedding_is_written_on_generation_and_is_idempotent(monkeypatch):
    con = _connection()
    run = _run(con)
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=None,
        ocr_result_id=None,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(),
        search_text_pt="texto pesquisável",
        embedding_text="texto vetorial v2",
        analysis=_analysis(),
        raw_response="{}",
        facsimile_used=False,
        tainted_by_page=None,
    )
    assert promote_segment(con, run["id"], "PG001", 175, 175) == 1
    calls = []

    def fake_embed(texts, model, ollama_url):
        calls.append((texts, model, ollama_url))
        return [[0.1, 0.2, 0.3] for _text in texts]

    monkeypatch.setattr(generate_resumo_embeddings, "embed_documents", fake_embed)
    assert generate_resumo_embeddings.ingest_v2(
        con, "embed-model", "http://unused", 10, None, None
    ) == (1, 1)
    row = con.execute("SELECT * FROM resumo_generations WHERE id=?", (generation["id"],)).fetchone()
    assert row["embedding"] is not None
    assert row["embedding_dim"] == 3
    assert row["embedding_model"] == "embed-model"
    assert generate_resumo_embeddings.ingest_v2(
        con, "embed-model", "http://unused", 10, None, None
    ) == (0, 0)
    assert len(calls) == 1


def test_hybrid_export_carries_v2_and_locale_overlays(tmp_path):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    resumo_serial.init_resumo_schema(con)
    init_v2_schema(con)
    con.execute(
        """
        INSERT INTO resumos
            (documento,pagina_num,pagina_file,pagina_texto,resumo_pagina,resumo_global,
             modelo,criado_em,keywords_json,keywords_source,keywords_modelo,
             author_detected,work_detected,summary_page_clean,summary_global_clean)
        VALUES ('PG001',175,'page-175.txt','ocr','legado','global legado','legacy','date',
                '["virgindade"]','resumo_pagina','kw-model','Clemente','Epístolas','legado','global')
        """
    )
    con.commit()
    run = _run(con)
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=con.execute("SELECT id FROM resumos").fetchone()[0],
        ocr_result_id=None,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(summary="Novo resumo canônico da página inicial."),
        search_text_pt="Busca canônica da página inicial.",
        embedding_text="Embedding canônico.",
        analysis=_analysis(),
        raw_response="{}",
        facsimile_used=True,
        tainted_by_page=None,
    )
    assert promote_segment(con, run["id"], "PG001", 175, 175) == 1
    con.execute(
        """
        INSERT INTO resumo_translations
            (generation_id,locale,source_hash,prompt_version,provider,model,
             summary_display,cumulative_summary,search_text,status)
        VALUES (?, 'en', 'translation-source', 'prompt', 'ollama', 'gemma4:cloud',
                'New canonical summary.', 'New cumulative summary.',
                'Canonical English search.', 'completed')
        """,
        (generation["id"],),
    )
    con.commit()
    rows = export_enrichment_shards.fetch_rows(con, "PG001")
    meta = export_enrichment_shards.export_doc("PG001", rows, tmp_path, False, 0)
    assert meta["pages"] == 1
    record = json.loads((tmp_path / "PG001.ndjson").read_text(encoding="utf-8"))
    assert record["summary_page"] == "Novo resumo canônico da página inicial."
    assert record["summary_generation"] == "v2"
    assert record["search_text_pt"] == "Busca canônica da página inicial."
    assert record["header_original"] == "EPISTOLAE AD VIRGINES"
    assert record["translations"]["en"]["summary_page"] == "New canonical summary."
    assert record["translations"]["en"]["summary_global"] == "New cumulative summary."
    assert record["keywords"] == ["virgindade"]

    loaded = render_publication_from_shards.load_shard(tmp_path / "PG001.ndjson")
    assert loaded[0].summary_generation == "v2"
    assert loaded[0].translations["en"]["search_text"] == "Canonical English search."


def test_v2_cluster_results_are_versioned_instead_of_overwriting_generation():
    con = _connection()
    run = _run(con)
    generation = store_generation(
        con,
        run_id=run["id"],
        legacy_resumo_id=None,
        ocr_result_id=None,
        documento="PG001",
        pagina_num=175,
        pagina_file="page-175.txt",
        previous_generation=None,
        source_hash="source-a",
        provider="ollama",
        model="gemma4:cloud",
        candidate=_candidate(),
        search_text_pt="busca",
        embedding_text="embedding",
        analysis=_analysis(),
        raw_response="{}",
        facsimile_used=False,
        tainted_by_page=None,
    )
    promote_segment(con, run["id"], "PG001", 175, 175)
    vector = np.asarray([0.1, 0.2, 0.3], dtype=np.float32)
    con.execute(
        """
        UPDATE resumo_generations SET embedding=?,embedding_dim=3,
               embedding_model='embed-model',embedding_source_hash='vector-source'
         WHERE id=?
        """,
        (vector.tobytes(), generation["id"]),
    )
    con.commit()
    rows, matrix = hdbscan_resumo_embeddings.load_v2_embeddings(con, "embed-model", 0)
    assert matrix.shape == (1, 3)
    fake_clusterer = SimpleNamespace(
        labels_=np.asarray([2]),
        probabilities_=np.asarray([0.91]),
        outlier_scores_=np.asarray([0.04]),
    )
    args = SimpleNamespace(
        umap_components_v2=2,
        umap_neighbors=5,
        min_cluster_size_page=3,
        min_cluster_size=3,
        min_samples=2,
        cluster_selection_epsilon=0.1,
        low_memory=True,
    )
    cluster_run = hdbscan_resumo_embeddings.save_v2_cluster_run(
        con,
        rows,
        np.asarray([[0.4, 0.5]], dtype=np.float32),
        fake_clusterer,
        args,
    )
    saved = con.execute(
        "SELECT * FROM resumo_generation_clusters WHERE cluster_run_id=?",
        (cluster_run,),
    ).fetchone()
    assert saved["generation_id"] == generation["id"]
    assert saved["cluster_id"] == 2
    assert saved["reduced_dim"] == 2
    assert con.execute("SELECT status FROM resumo_cluster_runs WHERE id=?", (cluster_run,)).fetchone()[0] == "completed"
