import json
import re
import sqlite3

import pytest

import resumo_serial
from resumo_v2 import analyze_page, parse_summary_candidate


NOTICE = (
    "This is a digital copy of a book scanned by Google. Usage guidelines: "
    "make non-commercial use of the files and retain the Google watermark. "
) * 8
SUMMARY = (
    "A página desenvolve uma argumentação histórica com atribuição explícita "
    "das opiniões e dos acontecimentos relatados na obra."
)


def _payload(*, mixed=False, cumulative=""):
    segments = [{"kind": "administrative", "work_key": "", "summary": "Aviso de digitalização."}]
    if mixed:
        segments.append({"kind": "body", "work_key": "", "summary": SUMMARY})
    return {
        "page_kinds": ["administrative", "body"] if mixed else ["administrative"],
        "segments": segments,
        "contributors": [],
        "cumulative_summary": cumulative,
        "source_conflicts": [],
        "administrative_reason": "Aviso técnico de digitalização.",
    }


def _parse(payload, *, text=NOTICE, title="", header=""):
    analysis = analyze_page(
        documento="PGX",
        pagina_num=1,
        pagina_file="page-001.txt",
        page_text=f'<pagina><bloco tipo="cabecalho">{header}</bloco></pagina>',
        ocr_clean=text,
        index_hints={
            "exact_start_candidates": [
                {"work_key": "work:a", "title_original": title, "confidence": 0.99}
            ] if title else [],
        },
    )
    return parse_summary_candidate(json.dumps(payload), analysis, {"work:a"})


@pytest.mark.parametrize("text", [NOTICE, "Termos extensos de uma biblioteca digital. " * 30])
def test_length_alone_requests_review_without_blocking(text):
    candidate = _parse(_payload(), text=text)

    assert candidate.status == "metadata_pending"
    assert candidate.validation_issues == ("administrative_long_text_review",)
    assert candidate.is_administrative_only


@pytest.mark.parametrize("header,text", [
    ("EPISTOLAE AD VIRGINES", "Texto breve."),
    ("CLEMENTIS EPISTOLAE AD VIRGINES", "Texto breve."),
    ("", "Google watermark. Epistolae ad Virgines. " + SUMMARY * 10),
])
def test_corroborated_work_start_still_blocks_discarding_content(header, text):
    candidate = _parse(_payload(), title="Epistolae ad Virgines", header=header, text=text)

    assert candidate.status == "context_provisional"
    assert "false_administrative_risk" in candidate.validation_issues


def test_catalog_start_without_source_confirmation_only_requests_review():
    candidate = _parse(_payload(), title="Epistolae ad Virgines")

    assert candidate.status == "metadata_pending"
    assert set(candidate.validation_issues) == {
        "administrative_index_conflict", "administrative_long_text_review",
    }


def test_generic_catalog_word_in_notice_does_not_confirm_work_start():
    candidate = _parse(_payload(), text="The index is available online. " + NOTICE, title="Index")

    assert candidate.status == "metadata_pending"
    assert "false_administrative_risk" not in candidate.validation_issues


@pytest.mark.parametrize("kinds", [["administrative"], ["administrative", "body"]])
def test_administrative_label_does_not_exempt_substantive_segments_from_context(kinds):
    payload = _payload(mixed=True)
    payload["page_kinds"] = kinds
    candidate = _parse(payload)

    assert not candidate.is_administrative_only
    assert candidate.status == "context_provisional"
    assert "missing_cumulative_summary" in candidate.validation_issues


def test_mixed_page_with_substantive_summary_is_not_discarded_as_administrative():
    candidate = _parse(
        _payload(mixed=True, cumulative=SUMMARY),
        title="Epistolae ad Virgines",
        header="Epistolae ad Virgines",
    )

    assert candidate.status == "valid"
    assert candidate.cumulative_summary == SUMMARY
    assert not candidate.is_administrative_only


def test_short_administrative_page_remains_valid():
    assert _parse(_payload(), text="Aviso breve.").status == "valid"


@pytest.mark.parametrize("model_context", ["", "Instruções de uso do Google Books."])
@pytest.mark.parametrize("resume_after_notice", [False, True])
def test_pipeline_continues_after_long_notice_and_preserves_work_context(
    tmp_path, monkeypatch, model_context, resume_after_notice,
):
    text_dir = tmp_path / "PGX" / "text"
    text_dir.mkdir(parents=True)
    for page, text in [(1, SUMMARY), (2, NOTICE), (3, SUMMARY)]:
        (text_dir / f"page-{page:03d}.txt").write_text(text, encoding="utf-8")
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    resumo_serial.init_resumo_schema(con)
    prompts = {}
    interrupted = False

    def fake_chat(prompt_system, prompt_user, **_kwargs):
        nonlocal interrupted
        page = int(re.search(r'physical_page="(\d+)"', prompt_user).group(1))
        if page == 3 and resume_after_notice and not interrupted:
            interrupted = True
            raise RuntimeError("interrupção simulada após aviso")
        assert page not in prompts, "A notice must not trigger another model call."
        prompts[page] = prompt_user
        if page == 2:
            return json.dumps(_payload(cumulative=model_context))
        return json.dumps({
            "page_kinds": ["body"],
            "segments": [{"kind": "body", "work_key": "", "summary": SUMMARY}],
            "contributors": [],
            "cumulative_summary": f"Síntese da obra na página {page}.",
            "source_conflicts": [],
            "administrative_reason": "",
        })

    monkeypatch.setattr(resumo_serial, "llm_chat", fake_chat)
    monkeypatch.setattr(resumo_serial, "_scripture_evidence_for_page", lambda *_args: {})
    monkeypatch.setattr(resumo_serial, "load_volume_index_hints", lambda *_args: {
        1: {"containing_work_candidates": [{"work_key": "work:original"}]},
    })
    kwargs = dict(
        volume_dir=text_dir.parent, con=con, model="test", base_url="http://unused",
        timeout=1, retries=1, provider="ollama", reasoning_effort="high",
        api_key_env="UNUSED", num_ctx=16384, dry_run=False, page_filter=None,
        verbose=False, versions_con=None, facsimile_mode="never", facsimile_threshold=2,
        lookahead_pages=2, force_replace_from=None, force_replace_through="next-work",
        promote=False, indices_db=tmp_path / "missing.db",
    )
    if resume_after_notice:
        with pytest.raises(ValueError, match="interrupção simulada"):
            resumo_serial.process_volume_v2(**kwargs)
    resumo_serial.process_volume_v2(**kwargs)

    assert sorted(prompts) == [1, 2, 3]
    assert "Síntese da obra na página 1." in prompts[3]
    assert '"active_work_key":"work:original"' in prompts[3]
    row = con.execute("SELECT * FROM resumo_generations WHERE pagina_num=2").fetchone()
    assert row["status"] == "metadata_pending"
    assert row["cumulative_summary"] == "Síntese da obra na página 1."
    assert json.loads(row["raw_response"])["cumulative_summary"] == model_context
    assert row["tainted_by_page"] is None
    assert row["is_current"] == 0
    assert con.execute("SELECT status FROM resumo_runs").fetchone()[0] == "completed"
    issue = con.execute(
        "SELECT issue_kind,severity,status FROM resumo_review_queue WHERE generation_id=?",
        (row["id"],),
    ).fetchone()
    assert tuple(issue) == ("administrative_long_text_review", "metadata", "pending")

    resumo_serial.process_volume_v2(**kwargs)
    assert sorted(prompts) == [1, 2, 3]
    con.close()
