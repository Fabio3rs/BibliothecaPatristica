#!/usr/bin/env python3
"""Playground isolado para comparar prompts de resumo patrístico.

Os bancos de produção são abertos obrigatoriamente em modo somente-leitura.
Somente o banco informado por ``--db`` recebe escrita. O playground não promove
resultados, não altera resumos existentes e não regenera embeddings/HDBSCAN.

Exemplo:
    python scripts/playgrounds/resumo_prompt_playground.py run \
        --pages PG001:175 PG020:27 PG043:166 \
        --provider openai --model gpt-5-mini --repetitions 3 --jobs 3

    python scripts/playgrounds/resumo_prompt_playground.py report
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from facsimile_transport import encode_facsimile_for_transport  # noqa: E402
from resumo_serial import (  # noqa: E402
    SYSTEM_PROMPT,
    build_user_prompt,
    resolve_page_facsimile,
    truncate_context,
)
from scripts.limpeza_ocr import clean_ocr_text_optimized  # noqa: E402


DEFAULT_DB = PROJECT_ROOT / "data" / "playgrounds" / "resumo_prompt_playground.db"
DEFAULT_RESUMOS_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_INDICES_DB = PROJECT_ROOT / "data" / "patristic_indices.db"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_VLLM_URL = "http://localhost:8000/v1"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


STRUCTURED_SYSTEM_PROMPT = """\
Você é um editor de metadados de uma biblioteca patrística digital.

Produza dados úteis simultaneamente para leitura humana, busca lexical e
similaridade semântica. Trabalhe somente com as evidências fornecidas. O texto
pode conter corpo de obra, prefácio, dedicatória, introdução editorial,
bibliografia, índice, aparato crítico ou uma transição entre obras.

REGRAS:
1. Responda em português do Brasil, preservando entre parênteses títulos,
   nomes e termos técnicos originais quando isso melhorar a identificação.
2. Prefácios, dedicatórias, páginas de título, bibliografias, índices, listas de
   obras e aparatos críticos possuem conteúdo descritível; não os classifique
   como administrativos apenas por não serem o corpo principal da obra.
3. Use page_kind="administrative" somente para página vazia, encadernação,
   aviso técnico de digitalização ou ruído sem conteúdo bibliográfico,
   histórico, editorial, filosófico ou teológico recuperável.
4. Uma pista de catálogo é evidência auxiliar, não licença para inventar. Se a
   pista disser que uma obra começa nesta página, verifique o OCR/fac-símile e
   considere que a página pode também terminar a obra anterior.
5. Diferencie autor da obra de editor, tradutor, dedicatário e comentador.
6. summary_display deve ser legível no site e explicar concretamente o conteúdo
   novo. retrieval_text deve ser autocontido e conservar nomes, obras, temas,
   argumentos, referências bíblicas e termos úteis para busca/KNN.
7. cumulative_summary deve representar a obra atual, ter no máximo 1.800
   caracteres e ser reiniciada quando houver mudança segura de obra.
8. Não use Markdown, comentários sobre OCR ou raciocínio interno.

Retorne JSON puro neste formato:
{
  "page_kind": "body|work_start|transition|preface|dedication|title_page|index|bibliography|critical_apparatus|administrative|illegible|other",
  "author": "autor principal da obra ou Não identificado",
  "work": "título da obra ou Não identificado",
  "contributors": ["nome — função"],
  "summary_display": "resumo para o leitor",
  "retrieval_text": "descrição autocontida para busca e similaridade",
  "cumulative_summary": "síntese acumulada concisa da obra atual",
  "transition": {
    "detected": false,
    "previous_work": "",
    "new_work": ""
  },
  "administrative_reason": ""
}
""".strip()


@dataclass(frozen=True)
class PromptVariant:
    name: str
    description: str
    legacy: bool = False
    use_index_hints: bool = False
    use_clean_ocr: bool = False
    use_previous_context: bool = True
    include_facsimile: bool = False


VARIANTS: dict[str, PromptVariant] = {
    "baseline_legacy": PromptVariant(
        name="baseline_legacy",
        description="Prompt atual, OCR bruto e contexto anterior; sem hints.",
        legacy=True,
    ),
    "structured_guardrails": PromptVariant(
        name="structured_guardrails",
        description="Taxonomia estruturada e regras administrativas; sem hints.",
    ),
    "structured_index_hint": PromptVariant(
        name="structured_index_hint",
        description="Guardrails mais hints de obra do índice.",
        use_index_hints=True,
    ),
    "structured_index_hint_clean": PromptVariant(
        name="structured_index_hint_clean",
        description="Hints e OCR limpo pelo saneador compartilhado.",
        use_index_hints=True,
        use_clean_ocr=True,
    ),
    "structured_index_hint_no_context": PromptVariant(
        name="structured_index_hint_no_context",
        description="Hints sem síntese anterior, para medir viés de ancoragem.",
        use_index_hints=True,
        use_clean_ocr=True,
        use_previous_context=False,
    ),
    "structured_index_hint_facsimile": PromptVariant(
        name="structured_index_hint_facsimile",
        description="Hints, OCR limpo e fac-símile quando disponível.",
        use_index_hints=True,
        use_clean_ocr=True,
        include_facsimile=True,
    ),
}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    repetitions INTEGER NOT NULL,
    jobs INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento TEXT NOT NULL,
    pagina_num INTEGER NOT NULL,
    pagina_file TEXT NOT NULL,
    ocr_raw TEXT NOT NULL,
    ocr_clean TEXT NOT NULL,
    current_summary_page TEXT NOT NULL,
    current_summary_global TEXT NOT NULL,
    current_author TEXT NOT NULL,
    current_work TEXT NOT NULL,
    previous_summary_global TEXT NOT NULL,
    previous_author TEXT NOT NULL,
    previous_work TEXT NOT NULL,
    index_hints_json TEXT NOT NULL,
    facsimile_path TEXT NOT NULL,
    facsimile_sha256 TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE(documento, pagina_num, source_hash)
);

CREATE TABLE IF NOT EXISTS prompt_variants (
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    config_json TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    PRIMARY KEY(experiment_id, name)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    variant_name TEXT NOT NULL,
    repetition INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    facsimile_attached INTEGER NOT NULL DEFAULT 0,
    system_prompt TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    raw_response TEXT NOT NULL DEFAULT '',
    parsed_json TEXT NOT NULL DEFAULT '',
    canonical_json TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    automatic_score REAL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(experiment_id, case_id, variant_name, repetition)
);

CREATE INDEX IF NOT EXISTS idx_playground_runs_experiment
    ON runs(experiment_id, variant_name, case_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()


def sha256_file(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def connect_playground(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.executescript(SCHEMA_SQL)
    return con


def normalize_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return {"high": 0.95, "medium": 0.70, "low": 0.40}.get(
        str(value or "").strip().lower(), 0.0
    )


def parse_page_spec(spec: str) -> tuple[str, int]:
    if ":" not in spec:
        raise argparse.ArgumentTypeError(f"Página inválida: {spec}; use DOC:PAGINA")
    doc, page = spec.rsplit(":", 1)
    try:
        return doc.strip().upper(), int(page)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Página inválida: {spec}") from exc


def translated_work_rows(indices: sqlite3.Connection, volume_id: str) -> list[dict[str, Any]]:
    rows = indices.execute(
        """
        SELECT w.*,
               ta.translated_text AS author_pt,
               tt.translated_text AS title_pt
        FROM works w
        LEFT JOIN index_strings sa ON sa.source_text = w.author_raw
        LEFT JOIN index_translations ta
          ON ta.string_id = sa.id AND ta.language = 'pt-br'
        LEFT JOIN index_strings st ON st.source_text = w.title_raw
        LEFT JOIN index_translations tt
          ON tt.string_id = st.id AND tt.language = 'pt-br'
        WHERE w.volume_id = ?
        ORDER BY w.work_order, w.work_key
        """,
        (volume_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def compact_work_hint(work: dict[str, Any], exact_start: bool) -> dict[str, Any]:
    return {
        "work_key": work["work_key"],
        "exact_physical_start": exact_start,
        "author_original": work.get("author_raw") or "",
        "author_pt": work.get("author_pt") or "",
        "title_original": work.get("title_raw") or "",
        "title_pt": work.get("title_pt") or "",
        "editorial_start_page": work.get("start_page"),
        "editorial_end_page": work.get("end_page"),
        "confidence": normalize_confidence(work.get("confidence")),
    }


def collect_index_hints(
    resumos: sqlite3.Connection,
    indices: sqlite3.Connection,
    documento: str,
    pagina_num: int,
    pagina_file: str,
) -> dict[str, Any]:
    file_rows = resumos.execute(
        "SELECT pagina_file, pagina_num FROM resumos WHERE documento = ?",
        (documento,),
    ).fetchall()
    file_to_page = {row["pagina_file"]: int(row["pagina_num"]) for row in file_rows}

    starts: list[dict[str, Any]] = []
    containing: list[dict[str, Any]] = []
    for work in translated_work_rows(indices, documento):
        start_name = Path(work.get("start_file") or "").name
        end_name = Path(work.get("end_file") or "").name
        start_phys = file_to_page.get(start_name)
        end_phys = file_to_page.get(end_name)
        exact_start = bool(start_name and start_name == pagina_file)
        if exact_start:
            starts.append(compact_work_hint(work, exact_start=True))
        if (
            start_phys is not None
            and end_phys is not None
            and start_phys <= pagina_num <= end_phys
        ):
            containing.append(compact_work_hint(work, exact_start=exact_start))

    return {
        "documento": documento,
        "pagina_fisica": pagina_num,
        "exact_start_candidates": starts,
        "containing_work_candidates": containing,
        "exact_start_ambiguous": len(starts) > 1,
        "range_ambiguous": len(containing) > 1,
    }


def snapshot_case(
    resumos: sqlite3.Connection,
    indices: sqlite3.Connection,
    documento: str,
    pagina_num: int,
) -> dict[str, Any]:
    row = resumos.execute(
        "SELECT * FROM resumos WHERE documento = ? AND pagina_num = ?",
        (documento, pagina_num),
    ).fetchone()
    if row is None:
        raise ValueError(f"Página ausente em patristica_resumos.db: {documento}:{pagina_num}")
    previous = resumos.execute(
        """
        SELECT resumo_global, author_detected, work_detected
        FROM resumos
        WHERE documento = ? AND pagina_num < ?
        ORDER BY pagina_num DESC LIMIT 1
        """,
        (documento, pagina_num),
    ).fetchone()

    raw = str(row["pagina_texto"] or "")
    clean, _meta = clean_ocr_text_optimized(raw)
    page_path = PROJECT_ROOT / "teste" / documento / "text" / row["pagina_file"]
    facsimile = resolve_page_facsimile(page_path)
    hints = collect_index_hints(
        resumos, indices, documento, pagina_num, str(row["pagina_file"])
    )
    result = {
        "documento": documento,
        "pagina_num": pagina_num,
        "pagina_file": str(row["pagina_file"]),
        "ocr_raw": raw,
        "ocr_clean": clean,
        "current_summary_page": str(row["resumo_pagina"] or ""),
        "current_summary_global": str(row["resumo_global"] or ""),
        "current_author": str(row["author_detected"] or ""),
        "current_work": str(row["work_detected"] or ""),
        "previous_summary_global": str(previous[0] or "") if previous else "",
        "previous_author": str(previous[1] or "") if previous else "",
        "previous_work": str(previous[2] or "") if previous else "",
        "index_hints_json": json.dumps(hints, ensure_ascii=False, sort_keys=True),
        "facsimile_path": str(facsimile or ""),
        "facsimile_sha256": sha256_file(facsimile),
    }
    result["source_hash"] = sha256_text(
        raw,
        result["previous_summary_global"],
        result["index_hints_json"],
        result["facsimile_sha256"],
    )
    return result


def store_case(con: sqlite3.Connection, case: dict[str, Any]) -> int:
    fields = [
        "documento",
        "pagina_num",
        "pagina_file",
        "ocr_raw",
        "ocr_clean",
        "current_summary_page",
        "current_summary_global",
        "current_author",
        "current_work",
        "previous_summary_global",
        "previous_author",
        "previous_work",
        "index_hints_json",
        "facsimile_path",
        "facsimile_sha256",
        "source_hash",
    ]
    con.execute(
        f"INSERT OR IGNORE INTO cases ({','.join(fields)},captured_at) "
        f"VALUES ({','.join('?' for _ in fields)},?)",
        [case[field] for field in fields] + [now_iso()],
    )
    row = con.execute(
        "SELECT id FROM cases WHERE documento=? AND pagina_num=? AND source_hash=?",
        (case["documento"], case["pagina_num"], case["source_hash"]),
    ).fetchone()
    con.commit()
    return int(row["id"])


def build_structured_prompt(case: dict[str, Any], variant: PromptVariant) -> str:
    ocr = case["ocr_clean"] if variant.use_clean_ocr else case["ocr_raw"]
    context = case["previous_summary_global"] if variant.use_previous_context else ""
    context = context[:6000]
    ocr = ocr[:30000]
    parts = [
        f"<page document=\"{case['documento']}\" physical_page=\"{case['pagina_num']}\">"
    ]
    if variant.use_index_hints:
        parts.extend(
            [
                "<catalog_hints>",
                case["index_hints_json"],
                "</catalog_hints>",
                "As pistas acima podem conter obras abrangentes ou concorrentes. "
                "Use exact_physical_start como forte sinal de transição, mas confirme no conteúdo.",
            ]
        )
    parts.extend(
        [
            "<previous_context>",
            context or "(sem contexto anterior nesta variante)",
            "</previous_context>",
            "<previous_metadata>",
            json.dumps(
                {"author": case["previous_author"], "work": case["previous_work"]},
                ensure_ascii=False,
            ),
            "</previous_metadata>",
        ]
    )
    if variant.include_facsimile:
        parts.append(
            "<facsimile>O fac-símile da mesma página está anexado. Use-o para "
            "conferir hierarquia visual, títulos, colunas e transições; não o descreva.</facsimile>"
        )
    parts.extend(["<current_page>", ocr or "(sem OCR recuperável)", "</current_page>", "</page>"])
    return "\n".join(parts)


def render_prompts(case: dict[str, Any], variant: PromptVariant) -> tuple[str, str]:
    if variant.legacy:
        page_text = case["ocr_raw"]
        context = truncate_context(case["previous_summary_global"], page_text, 128000)
        user = build_user_prompt(
            contexto_previo=context,
            page_text=page_text,
            page_num=int(case["pagina_num"]),
            doc_name=str(case["documento"]),
            author=str(case["previous_author"]),
            work=str(case["previous_work"]),
            facsimile_attached=False,
        )
        return SYSTEM_PROMPT, user
    return STRUCTURED_SYSTEM_PROMPT, build_structured_prompt(case, variant)


def strip_response_wrapper(raw: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()


def parse_canonical(raw: str, variant: PromptVariant) -> tuple[dict[str, Any] | None, str]:
    candidate = strip_response_wrapper(raw)
    try:
        parsed = json.loads(candidate)
    except Exception as exc:
        return None, f"JSON inválido: {exc}"
    if not isinstance(parsed, dict):
        return None, "A resposta JSON não é um objeto"

    if variant.legacy:
        required = {"traducao_compacta", "sintese_acumulada"}
        if not required.issubset(parsed):
            return None, f"Campos ausentes: {sorted(required - set(parsed))}"
        page_summary = str(parsed.get("traducao_compacta") or "").strip()
        administrative = normalize_marker(page_summary) == "conteudo administrativo"
        canonical = {
            "page_kind": "administrative" if administrative else "unknown",
            "author": str(parsed.get("autor") or "").strip(),
            "work": str(parsed.get("obra") or "").strip(),
            "contributors": [],
            "summary_display": page_summary,
            "retrieval_text": page_summary,
            "cumulative_summary": str(parsed.get("sintese_acumulada") or "").strip(),
            "transition": {"detected": False, "previous_work": "", "new_work": ""},
            "administrative_reason": "",
        }
        return canonical, ""

    required = {
        "page_kind",
        "author",
        "work",
        "summary_display",
        "retrieval_text",
        "cumulative_summary",
        "transition",
    }
    if not required.issubset(parsed):
        return None, f"Campos ausentes: {sorted(required - set(parsed))}"
    return parsed, ""


def normalize_marker(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def normalized_tokens(value: str) -> set[str]:
    norm = normalize_marker(value)
    return {
        token
        for token in re.findall(r"[a-z0-9]+", norm)
        if len(token) > 2 and token not in {"nao", "identificado", "sao", "santo", "obra"}
    }


def overlaps_hint(value: str, candidates: Iterable[dict[str, Any]], field: str) -> bool:
    actual = normalized_tokens(value)
    if not actual:
        return False
    for candidate in candidates:
        expected = normalized_tokens(candidate.get(f"{field}_pt") or candidate.get(f"{field}_original") or "")
        if expected and len(actual & expected) / min(len(actual), len(expected)) >= 0.5:
            return True
    return False


def score_output(case: dict[str, Any], canonical: dict[str, Any] | None) -> tuple[float, list[str]]:
    if canonical is None:
        return 0.0, ["json_invalido"]
    score = 100.0
    findings: list[str] = []
    hints = json.loads(case["index_hints_json"])
    starts = hints.get("exact_start_candidates") or []
    kind = normalize_marker(canonical.get("page_kind") or "")
    display = str(canonical.get("summary_display") or "").strip()
    retrieval = str(canonical.get("retrieval_text") or "").strip()
    cumulative = str(canonical.get("cumulative_summary") or "").strip()
    is_admin = kind == "administrative" or normalize_marker(display) == "conteudo administrativo"

    if starts and is_admin:
        score -= 45
        findings.append("administrativo_em_inicio_indexado")
    elif len(case["ocr_clean"]) >= 500 and is_admin:
        score -= 30
        findings.append("administrativo_com_ocr_substancial")

    if starts:
        if not str(canonical.get("author") or "").strip():
            score -= 10
            findings.append("autor_vazio_em_inicio")
        elif len(starts) == 1 and not overlaps_hint(canonical.get("author", ""), starts, "author"):
            score -= 5
            findings.append("autor_diverge_do_hint")
        if not str(canonical.get("work") or "").strip():
            score -= 10
            findings.append("obra_vazia_em_inicio")
        elif len(starts) == 1 and not overlaps_hint(canonical.get("work", ""), starts, "title"):
            score -= 5
            findings.append("obra_diverge_do_hint")

    if not is_admin:
        if len(display) < 120:
            score -= 8
            findings.append("resumo_display_curto")
        elif len(display) > 1200:
            score -= 5
            findings.append("resumo_display_longo")
        if len(retrieval) < 140:
            score -= 8
            findings.append("retrieval_text_curto")
        elif len(retrieval) > 2200:
            score -= 5
            findings.append("retrieval_text_longo")

    if len(cumulative) > 2200:
        score -= 12
        findings.append("sintese_acumulada_excessiva")
    combined = " ".join([display, retrieval, cumulative])
    if re.search(r"\*\*|```|<think>|thinking process", combined, flags=re.IGNORECASE):
        score -= 20
        findings.append("markup_ou_raciocinio_vazado")
    if re.search(r"\b(?:o ocr|qualidade do ocr|fac-s[ií]mile anexado)\b", combined, flags=re.IGNORECASE):
        score -= 10
        findings.append("comentario_sobre_a_fonte")
    return max(score, 0.0), findings


def make_user_content(prompt: str, image_path: Path | None) -> str | list[dict[str, Any]]:
    if image_path is None:
        return prompt
    encoded, mime_type = encode_facsimile_for_transport(image_path)
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
    ]


def call_openai(
    system_prompt: str,
    user_prompt: str,
    model: str,
    base_url: str,
    api_key_env: str,
    timeout: int,
    reasoning_effort: str,
    image_path: Path | None,
    require_api_key: bool,
    openai_extras: bool,
) -> tuple[str, dict[str, Any]]:
    api_key = os.getenv(api_key_env)
    if require_api_key and not api_key:
        raise RuntimeError(f"Variável {api_key_env} não definida")
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": make_user_content(user_prompt, image_path)},
        ],
        "response_format": {"type": "json_object"},
    }
    if openai_extras:
        payload["service_tier"] = "flex"
    if openai_extras and reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return str(content or ""), data.get("usage") or {}


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    base_url: str,
    timeout: int,
    num_ctx: int,
    image_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    user_message: dict[str, Any] = {"role": "user", "content": user_prompt}
    if image_path is not None:
        encoded, _mime_type = encode_facsimile_for_transport(image_path)
        user_message["images"] = [encoded]
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            user_message,
        ],
        "options": {"temperature": 0.2, "top_p": 0.5, "num_ctx": num_ctx},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    usage = {
        "prompt_tokens": data.get("prompt_eval_count"),
        "completion_tokens": data.get("eval_count"),
    }
    if usage["prompt_tokens"] is not None and usage["completion_tokens"] is not None:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return str((data.get("message") or {}).get("content") or ""), usage


def execute_task(task: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    variant = VARIANTS[task["variant_name"]]
    image_path = Path(task["case"]["facsimile_path"]) if (
        variant.include_facsimile and task["case"]["facsimile_path"]
    ) else None
    result = dict(task)
    result["facsimile_attached"] = int(image_path is not None)
    if args.dry_run:
        result.update(status="dry_run", raw_response="", usage={}, error_message="")
        result["latency_ms"] = 0
        return result
    try:
        if args.provider in {"openai", "vllm"}:
            default_url = DEFAULT_OPENAI_URL if args.provider == "openai" else DEFAULT_VLLM_URL
            raw, usage = call_openai(
                task["system_prompt"], task["user_prompt"], args.model,
                args.base_url or default_url, args.api_key_env,
                args.timeout, args.reasoning_effort, image_path,
                require_api_key=args.provider == "openai",
                openai_extras=args.provider == "openai",
            )
        else:
            raw, usage = call_ollama(
                task["system_prompt"], task["user_prompt"], args.model,
                args.base_url or DEFAULT_OLLAMA_URL, args.timeout,
                args.num_ctx, image_path,
            )
        result.update(status="completed", raw_response=raw, usage=usage, error_message="")
    except Exception as exc:
        result.update(status="error", raw_response="", usage={}, error_message=str(exc))
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def usage_value(usage: dict[str, Any], key: str) -> int | None:
    aliases = {
        "prompt_tokens": ["prompt_tokens", "input_tokens"],
        "completion_tokens": ["completion_tokens", "output_tokens"],
        "total_tokens": ["total_tokens"],
    }[key]
    for alias in aliases:
        value = usage.get(alias)
        if value is not None:
            return int(value)
    return None


def store_run(con: sqlite3.Connection, result: dict[str, Any], args: argparse.Namespace) -> None:
    variant = VARIANTS[result["variant_name"]]
    parsed: dict[str, Any] | None = None
    parse_error = ""
    if result["status"] == "completed":
        parsed, parse_error = parse_canonical(result["raw_response"], variant)
    score, findings = score_output(result["case"], parsed) if result["status"] == "completed" else (None, [])
    status = "parse_error" if result["status"] == "completed" and parsed is None else result["status"]
    error_message = parse_error or result["error_message"]
    canonical_json = json.dumps(parsed, ensure_ascii=False, sort_keys=True) if parsed else ""
    usage = result.get("usage") or {}
    con.execute(
        """
        INSERT INTO runs (
            experiment_id,case_id,variant_name,repetition,provider,model,
            facsimile_attached,system_prompt,user_prompt,input_hash,raw_response,
            parsed_json,canonical_json,status,error_message,latency_ms,
            prompt_tokens,completion_tokens,total_tokens,automatic_score,
            findings_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            result["experiment_id"], result["case_id"], result["variant_name"],
            result["repetition"], args.provider, args.model,
            result["facsimile_attached"], result["system_prompt"], result["user_prompt"],
            result["input_hash"], result["raw_response"],
            strip_response_wrapper(result["raw_response"]), canonical_json,
            status, error_message, result["latency_ms"],
            usage_value(usage, "prompt_tokens"), usage_value(usage, "completion_tokens"),
            usage_value(usage, "total_tokens"), score,
            json.dumps(findings, ensure_ascii=False), now_iso(),
        ),
    )
    con.commit()


def select_variants(spec: str) -> list[PromptVariant]:
    names = list(VARIANTS) if spec == "all" else [item.strip() for item in spec.split(",") if item.strip()]
    unknown = [name for name in names if name not in VARIANTS]
    if unknown:
        raise ValueError(f"Variantes desconhecidas: {', '.join(unknown)}")
    return [VARIANTS[name] for name in names]


def create_experiment(
    con: sqlite3.Connection,
    args: argparse.Namespace,
    variants: list[PromptVariant],
) -> str:
    experiment_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    config = {
        "pages": args.pages,
        "variants": [variant.name for variant in variants],
        "dry_run": args.dry_run,
        "resumos_db": str(args.resumos_db),
        "indices_db": str(args.indices_db),
        "reasoning_effort": args.reasoning_effort,
        "num_ctx": args.num_ctx,
    }
    con.execute(
        "INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)",
        (
            experiment_id, args.name or experiment_id, args.provider, args.model,
            args.repetitions, args.jobs, json.dumps(config, ensure_ascii=False), now_iso(),
        ),
    )
    for variant in variants:
        system = SYSTEM_PROMPT if variant.legacy else STRUCTURED_SYSTEM_PROMPT
        variant_config = {
            "legacy": variant.legacy,
            "use_index_hints": variant.use_index_hints,
            "use_clean_ocr": variant.use_clean_ocr,
            "use_previous_context": variant.use_previous_context,
            "include_facsimile": variant.include_facsimile,
        }
        con.execute(
            "INSERT INTO prompt_variants VALUES (?,?,?,?,?,?)",
            (
                experiment_id, variant.name, variant.description,
                json.dumps(variant_config, ensure_ascii=False, sort_keys=True),
                system, sha256_text(system, json.dumps(variant_config, sort_keys=True)),
            ),
        )
    con.commit()
    return experiment_id


def run_experiment(args: argparse.Namespace) -> None:
    variants = select_variants(args.variants)
    pages = [parse_page_spec(spec) for spec in args.pages]
    with connect_readonly(args.resumos_db) as resumos, connect_readonly(args.indices_db) as indices, connect_playground(args.db) as out:
        experiment_id = create_experiment(out, args, variants)
        cases: list[tuple[int, dict[str, Any]]] = []
        for documento, pagina_num in pages:
            case = snapshot_case(resumos, indices, documento, pagina_num)
            case_id = store_case(out, case)
            cases.append((case_id, case))
            hints = json.loads(case["index_hints_json"])
            print(
                f"[case] {documento}:{pagina_num} ocr={len(case['ocr_clean'])} "
                f"starts={len(hints['exact_start_candidates'])} image={'yes' if case['facsimile_path'] else 'no'}"
            )

        tasks: list[dict[str, Any]] = []
        for case_id, case in cases:
            for variant in variants:
                system_prompt, user_prompt = render_prompts(case, variant)
                for repetition in range(1, args.repetitions + 1):
                    tasks.append(
                        {
                            "experiment_id": experiment_id,
                            "case_id": case_id,
                            "case": case,
                            "variant_name": variant.name,
                            "repetition": repetition,
                            "system_prompt": system_prompt,
                            "user_prompt": user_prompt,
                            "input_hash": sha256_text(system_prompt, user_prompt, case["facsimile_sha256"] if variant.include_facsimile else ""),
                        }
                    )

        print(f"[experiment] {experiment_id}: {len(tasks)} execuções; DB={args.db}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(execute_task, task, args) for task in tasks]
            for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                result = future.result()
                store_run(out, result, args)
                print(
                    f"[{done}/{len(tasks)}] {result['case']['documento']}:{result['case']['pagina_num']} "
                    f"{result['variant_name']} #{result['repetition']} {result['status']} {result['latency_ms']}ms"
                )
        print(f"[done] experimento={experiment_id}")
        print_report(out, experiment_id, details=False)


def latest_experiment(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT id FROM experiments ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("Nenhum experimento encontrado")
    return str(row["id"])


def print_report(con: sqlite3.Connection, experiment_id: str, details: bool) -> str:
    exp = con.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
    if exp is None:
        raise ValueError(f"Experimento inexistente: {experiment_id}")
    lines = [
        f"# Benchmark de prompts — {exp['name']}",
        "",
        f"Experimento: `{experiment_id}`  ",
        f"Provider/modelo: `{exp['provider']}` / `{exp['model']}`",
        "",
        "| Variante | Runs | JSON válido | Score médio | Latência média | Saída estável |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    rows = con.execute(
        """
        SELECT variant_name, COUNT(*) runs,
               SUM(status='completed') valid,
               AVG(automatic_score) avg_score,
               AVG(latency_ms) avg_latency
        FROM runs WHERE experiment_id=? GROUP BY variant_name ORDER BY variant_name
        """,
        (experiment_id,),
    ).fetchall()
    for row in rows:
        stable = con.execute(
            """
            WITH grouped AS (
              SELECT case_id, COUNT(DISTINCT canonical_json) variants
              FROM runs WHERE experiment_id=? AND variant_name=? AND status='completed'
              GROUP BY case_id
            ) SELECT COALESCE(AVG(variants=1),0) FROM grouped
            """,
            (experiment_id, row["variant_name"]),
        ).fetchone()[0]
        valid_pct = 100.0 * int(row["valid"] or 0) / max(int(row["runs"]), 1)
        lines.append(
            f"| {row['variant_name']} | {row['runs']} | {valid_pct:.1f}% | "
            f"{float(row['avg_score'] or 0):.1f} | {float(row['avg_latency'] or 0):.0f} ms | {100*float(stable or 0):.1f}% |"
        )

    finding_rows = con.execute(
        "SELECT findings_json FROM runs WHERE experiment_id=? AND findings_json<>'[]'",
        (experiment_id,),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in finding_rows:
        for finding in json.loads(row[0]):
            counts[finding] = counts.get(finding, 0) + 1
    if counts:
        lines.extend(["", "## Alertas automáticos", ""])
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {name}: {count}")

    if details:
        lines.extend(["", "## Resultados", ""])
        detail_rows = con.execute(
            """
            SELECT c.documento,c.pagina_num,r.variant_name,r.repetition,r.status,
                   r.automatic_score,r.canonical_json,r.error_message
            FROM runs r JOIN cases c ON c.id=r.case_id
            WHERE r.experiment_id=?
            ORDER BY c.documento,c.pagina_num,r.variant_name,r.repetition
            """,
            (experiment_id,),
        ).fetchall()
        for row in detail_rows:
            lines.append(
                f"### {row['documento']}:{row['pagina_num']} — {row['variant_name']} #{row['repetition']}"
            )
            lines.append("")
            lines.append(f"Status: `{row['status']}`; score: `{row['automatic_score']}`")
            lines.append("")
            if row["canonical_json"]:
                lines.extend(["```json", json.dumps(json.loads(row["canonical_json"]), ensure_ascii=False, indent=2), "```", ""])
            elif row["error_message"]:
                lines.append(f"Erro: {row['error_message']}")

    report = "\n".join(lines) + "\n"
    print(report, end="")
    return report


def report_command(args: argparse.Namespace) -> None:
    with connect_playground(args.db) as con:
        experiment_id = args.experiment or latest_experiment(con)
        report = print_report(con, experiment_id, details=args.details)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"[report] salvo em {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Captura casos e executa a matriz de prompts")
    run.add_argument("--pages", nargs="+", required=True, metavar="DOC:PAGE")
    run.add_argument("--variants", default="all", help="Lista CSV ou 'all'")
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--jobs", type=int, default=3)
    run.add_argument("--provider", choices=["openai", "vllm", "ollama"], default="openai")
    run.add_argument("--model", default="gpt-5-mini")
    run.add_argument("--base-url", default="")
    run.add_argument("--api-key-env", default="OPENAI_API_KEY")
    run.add_argument("--reasoning-effort", default="high")
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--num-ctx", type=int, default=16384)
    run.add_argument("--name", default="")
    run.add_argument("--dry-run", action="store_true", help="Grava prompts sem chamar modelos")
    run.add_argument("--db", type=Path, default=DEFAULT_DB)
    run.add_argument("--resumos-db", type=Path, default=DEFAULT_RESUMOS_DB)
    run.add_argument("--indices-db", type=Path, default=DEFAULT_INDICES_DB)

    report = sub.add_parser("report", help="Resume um experimento armazenado")
    report.add_argument("--experiment", default="", help="ID; vazio usa o mais recente")
    report.add_argument("--details", action="store_true")
    report.add_argument("--output", type=Path)
    report.add_argument("--db", type=Path, default=DEFAULT_DB)

    variants = sub.add_parser("variants", help="Lista as variantes disponíveis")
    variants.set_defaults(list_variants=True)
    return parser


def validate_run_args(args: argparse.Namespace) -> None:
    if args.repetitions < 1:
        raise ValueError("--repetitions deve ser >= 1")
    if args.jobs < 1:
        raise ValueError("--jobs deve ser >= 1")
    for path in (args.resumos_db, args.indices_db):
        if not path.is_file():
            raise FileNotFoundError(path)


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "variants":
        for variant in VARIANTS.values():
            print(f"{variant.name}: {variant.description}")
        return
    if args.command == "run":
        validate_run_args(args)
        run_experiment(args)
        return
    report_command(args)


if __name__ == "__main__":
    main()
