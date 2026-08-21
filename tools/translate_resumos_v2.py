#!/usr/bin/env python3
"""Segunda passada de internacionalização das gerações de resumo v2.

O modelo recebe somente o resumo canônico, o texto de busca derivado e um
glossário estrutural dos índices. OCR, fac-símile e contexto serial nunca entram
nesta etapa.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from resumo_serial import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, llm_chat  # noqa: E402
from resumo_v2 import (  # noqa: E402
    TRANSLATION_PROMPT_VERSION,
    init_v2_schema,
    normalize_whitespace,
    open_indices_readonly,
    sha256_text,
    translation_source_hash,
)


DEFAULT_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_INDICES_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
LOCALE_NAMES = {"en": "inglês", "fr": "francês", "it": "italiano"}
DEFAULT_LOCALES = ("en", "fr", "it")

SYSTEM_PROMPT = """\
Você traduz metadados de uma biblioteca patrística para vários idiomas na mesma
chamada. Traduza fielmente, preservando nomes, títulos, termos técnicos e
referências. Mantenha entre os idiomas escolhas terminológicas, grau de
formalidade, estrutura e nível de detalhe equivalentes, sem fazer traduções
palavra por palavra que prejudiquem a naturalidade de cada língua. Use o
glossário quando houver correspondência estrutural ou literal. Não acrescente
fatos, não resuma novamente e não use OCR ou conhecimento externo.
`cumulative_summary` é a síntese já consolidada da obra corrente: traduza-a sem
fundir nela o resumo da página. `search_text` é texto de recuperação lexical:
traduza as expressões, preservando nomes, títulos e referências úteis à busca.

Retorne JSON puro com esta forma exata:
{"translations":{"en":{"summary_display":"...","cumulative_summary":"...","search_text":"..."},"fr":{"summary_display":"...","cumulative_summary":"...","search_text":"..."},"it":{"summary_display":"...","cumulative_summary":"...","search_text":"..."}}}
Inclua exatamente todos os códigos pedidos, sem notas nem chaves adicionais.
""".strip()


def normalize_locales(values: list[str] | tuple[str, ...]) -> list[str]:
    locales: list[str] = []
    for raw in values:
        for part in str(raw).split(","):
            locale = part.strip().lower()
            if not locale:
                continue
            if locale not in LOCALE_NAMES:
                raise ValueError(f"Locale não suportado: {locale}")
            if locale not in locales:
                locales.append(locale)
    return locales


def build_translation_prompt(
    generation: sqlite3.Row,
    locales: list[str] | tuple[str, ...],
    glossary: list[dict[str, Any]],
) -> str:
    requested = normalize_locales(locales)
    payload = {
        "target_locales": requested,
        "target_languages": {locale: LOCALE_NAMES[locale] for locale in requested},
        "summary_display_pt": str(generation["summary_display_pt"] or ""),
        "cumulative_summary_pt": str(generation["cumulative_summary"] or ""),
        "search_text_pt": str(generation["search_text_pt"] or ""),
        "glossary": glossary,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_translation(
    raw: str, locales: list[str] | tuple[str, ...]
) -> dict[str, dict[str, str]]:
    requested = normalize_locales(locales)
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict) or set(payload) != {"translations"}:
        raise ValueError("Resposta de tradução não é objeto JSON")
    translations = payload.get("translations")
    if not isinstance(translations, dict) or set(translations) != set(requested):
        raise ValueError(
            "Idiomas da tradução não correspondem ao conjunto solicitado: "
            f"esperado={sorted(requested)} recebido="
            f"{sorted(translations) if isinstance(translations, dict) else type(translations).__name__}"
        )
    parsed: dict[str, dict[str, str]] = {}
    for locale in requested:
        item = translations.get(locale)
        if not isinstance(item, dict) or set(item) != {
            "summary_display", "cumulative_summary", "search_text"
        }:
            raise ValueError(f"Estrutura inválida para o idioma {locale}")
        summary = normalize_whitespace(item.get("summary_display"))
        cumulative = normalize_whitespace(item.get("cumulative_summary"))
        search = normalize_whitespace(item.get("search_text"))
        if len(summary) < 20 or len(cumulative) < 20 or len(search) < 20:
            raise ValueError(f"Resposta de tradução curta ou incompleta para {locale}")
        parsed[locale] = {
            "summary": summary,
            "cumulative": cumulative,
            "search": search,
        }
    return parsed


def _generation_work_keys(generation: sqlite3.Row) -> set[str]:
    keys: set[str] = set()
    for field in ("segments_json", "static_analysis_json"):
        try:
            payload = json.loads(generation[field] or ("[]" if field == "segments_json" else "{}"))
        except json.JSONDecodeError:
            continue
        if field == "segments_json":
            candidates = payload
        else:
            candidates = [
                *(payload.get("exact_start_candidates") or []),
                *(payload.get("containing_work_candidates") or []),
            ]
        for item in candidates:
            if isinstance(item, dict) and item.get("work_key"):
                keys.add(str(item["work_key"]))
    return keys


def load_glossaries(
    indices: sqlite3.Connection | None,
    generations: list[sqlite3.Row],
    locales: list[str] | tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    requested = normalize_locales(locales)
    glossary_languages = ["pt-br", *requested]
    all_keys = sorted({key for row in generations for key in _generation_work_keys(row)})
    translated: dict[str, dict[str, Any]] = {}
    if indices is not None:
        for offset in range(0, len(all_keys), 500):
            batch = all_keys[offset : offset + 500]
            placeholders = ",".join("?" for _ in batch)
            if not batch:
                continue
            work_rows = indices.execute(
                f"""
                SELECT w.work_key, w.author_raw, w.title_raw
                  FROM works w WHERE w.work_key IN ({placeholders})
                """,
                batch,
            ).fetchall()
            source_texts = sorted(
                {
                    str(value)
                    for row in work_rows
                    for value in (row["author_raw"], row["title_raw"])
                    if value
                }
            )
            translations_by_source: dict[str, dict[str, str]] = {}
            if source_texts and glossary_languages:
                source_placeholders = ",".join("?" for _ in source_texts)
                locale_placeholders = ",".join("?" for _ in glossary_languages)
                translation_rows = indices.execute(
                    f"""
                    SELECT s.source_text,t.language,t.translated_text
                      FROM index_strings s
                      JOIN index_translations t ON t.string_id=s.id
                     WHERE s.source_text IN ({source_placeholders})
                       AND t.language IN ({locale_placeholders})
                     ORDER BY t.id
                    """,
                    [*source_texts, *glossary_languages],
                ).fetchall()
                for translation in translation_rows:
                    translations_by_source.setdefault(
                        str(translation["source_text"]), {}
                    ).setdefault(
                        str(translation["language"]),
                        str(translation["translated_text"] or ""),
                    )
            for row in work_rows:
                author_original = str(row["author_raw"] or "")
                title_original = str(row["title_raw"] or "")
                translated[str(row["work_key"])] = {
                    "work_key": str(row["work_key"]),
                    "author_original": author_original,
                    "author_translations": translations_by_source.get(author_original, {}),
                    "title_original": title_original,
                    "title_translations": translations_by_source.get(title_original, {}),
                }
    return {
        str(row["id"]): [translated[key] for key in sorted(_generation_work_keys(row)) if key in translated]
        for row in generations
    }


def _translate_task(task: dict[str, Any]) -> dict[str, Any]:
    row = task["generation"]
    raw = llm_chat(
        prompt_system=SYSTEM_PROMPT,
        prompt_user=build_translation_prompt(row, task["locales"], task["glossary"]),
        provider=task["provider"],
        model=task["model"],
        base_url=task["base_url"],
        timeout=task["timeout"],
        reasoning_effort=task["reasoning_effort"],
        api_key_env=task["api_key_env"],
        num_ctx=task["num_ctx"],
    )
    translations = parse_translation(raw, task["locales"])
    return {"translations": translations, "raw": raw}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--indices-db", type=Path, default=DEFAULT_INDICES_DB)
    parser.add_argument(
        "--locales",
        default=",".join(DEFAULT_LOCALES),
        help="Idiomas traduzidos juntos por chamada (padrão: en,fr,it)",
    )
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--openai-url", default="https://api.openai.com/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--include-shadow", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        locales = normalize_locales([args.locales])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not locales:
        raise SystemExit("Informe ao menos um idioma em --locales")
    if set(locales) != set(DEFAULT_LOCALES):
        raise SystemExit(
            "A passada de produção deve traduzir conjuntamente en,fr,it; "
            "use --locales en,fr,it"
        )
    locales = [locale for locale in DEFAULT_LOCALES if locale in locales]
    if not 1 <= args.jobs <= 20:
        raise SystemExit("--jobs deve estar entre 1 e 20")
    if args.batch_size < 1:
        raise SystemExit("--batch-size deve ser maior que zero")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    init_v2_schema(con)
    where = ["g.status IN ('valid','metadata_pending')"]
    params: list[Any] = []
    if args.run_id is not None:
        where.append("g.run_id=?")
        params.append(args.run_id)
    elif not args.include_shadow:
        where.append("g.is_current=1")
    indices = open_indices_readonly(args.indices_db)
    completed = 0
    written = 0
    failed = 0
    existing = 0
    scanned = 0
    cursor: tuple[str, int, int] | None = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            while args.limit is None or scanned < args.limit:
                batch_where = list(where)
                batch_params = list(params)
                if cursor is not None:
                    batch_where.append(
                        "(g.documento>? OR (g.documento=? AND g.pagina_num>?) "
                        "OR (g.documento=? AND g.pagina_num=? AND g.id>?))"
                    )
                    batch_params.extend(
                        [cursor[0], cursor[0], cursor[1], cursor[0], cursor[1], cursor[2]]
                    )
                fetch_limit = min(
                    args.batch_size,
                    (args.limit - scanned) if args.limit is not None else args.batch_size,
                )
                generations = con.execute(
                    f"""
                    SELECT g.id,g.run_id,g.documento,g.pagina_num,g.output_hash,
                           g.summary_display_pt,g.cumulative_summary,g.search_text_pt,g.segments_json,
                           g.static_analysis_json
                      FROM resumo_generations g
                     WHERE {' AND '.join(batch_where)}
                     ORDER BY g.documento,g.pagina_num,g.id LIMIT ?
                    """,
                    [*batch_params, fetch_limit],
                ).fetchall()
                if not generations:
                    break
                last = generations[-1]
                cursor = (last["documento"], int(last["pagina_num"]), int(last["id"]))
                scanned += len(generations)
                glossaries = load_glossaries(indices, generations, locales)
                tasks: list[dict[str, Any]] = []
                for row in generations:
                    glossary = glossaries.get(str(row["id"]), [])
                    glossary_hash = sha256_text(
                        json.dumps(glossary, ensure_ascii=False, sort_keys=True)
                    )
                    source_hashes = {
                        locale: translation_source_hash(row, locale, glossary_hash)
                        for locale in locales
                    }
                    done_locales = {
                        str(done["locale"])
                        for done in con.execute(
                            f"""
                            SELECT locale,source_hash FROM resumo_translations
                             WHERE generation_id=?
                               AND locale IN ({','.join('?' for _ in locales)})
                               AND prompt_version=? AND model=? AND status='completed'
                            """,
                            [row["id"], *locales, TRANSLATION_PROMPT_VERSION, args.model],
                        ).fetchall()
                        if str(done["source_hash"]) == source_hashes[str(done["locale"])]
                    }
                    if done_locales == set(locales):
                        existing += 1
                        continue
                    tasks.append(
                        {
                            "generation": row,
                            "locales": locales,
                            "glossary": glossary,
                            "glossary_hash": glossary_hash,
                            "source_hashes": source_hashes,
                            "provider": args.provider,
                            "model": args.model,
                            "base_url": args.openai_url if args.provider == "openai" else args.ollama_url,
                            "timeout": args.timeout,
                            "reasoning_effort": args.reasoning_effort,
                            "api_key_env": args.api_key_env,
                            "num_ctx": args.num_ctx,
                        }
                    )
                future_tasks = {
                    executor.submit(_translate_task, task): task for task in tasks
                }
                for future in concurrent.futures.as_completed(future_tasks):
                    task = future_tasks[future]
                    row = task["generation"]
                    try:
                        result = future.result()
                        status = "completed"
                        error = ""
                        completed += 1
                    except Exception as exc:
                        result = {"translations": {}, "raw": ""}
                        status = "error"
                        error = str(exc)[:2000]
                        failed += 1
                    with con:
                        for locale in locales:
                            translated = result["translations"].get(locale, {})
                            con.execute(
                                """
                                INSERT INTO resumo_translations
                                    (generation_id,locale,source_hash,prompt_version,provider,model,
                                    glossary_hash,summary_display,cumulative_summary,search_text,
                                    raw_response,status,error_message)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT(generation_id,locale,source_hash,prompt_version,model)
                                DO UPDATE SET summary_display=excluded.summary_display,
                                              cumulative_summary=excluded.cumulative_summary,
                                              search_text=excluded.search_text,
                                              raw_response=excluded.raw_response,
                                              status=excluded.status,
                                              error_message=excluded.error_message,
                                              updated_at=CURRENT_TIMESTAMP
                                """,
                                (
                                    row["id"], locale, task["source_hashes"][locale],
                                    TRANSLATION_PROMPT_VERSION, args.provider, args.model,
                                    task["glossary_hash"], translated.get("summary", ""),
                                    translated.get("cumulative", ""), translated.get("search", ""),
                                    result["raw"], status, error,
                                ),
                            )
                            written += 1
                    print(f"{row['documento']}:{row['pagina_num']} {status}", flush=True)
    finally:
        if indices is not None:
            indices.close()
        con.close()
    print(
        f"Páginas concluídas: {completed}; linhas gravadas: {written}; falhas: {failed}; "
        f"já completas: {existing}; examinadas: {scanned}; idiomas: {','.join(locales)}"
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
