"""
assistente_patristico.py – Assistente interativo de Patrística usando smolagents + Ollama.

Combina:
  • Consulta ao DB de resumos seriais (patristica_resumos.db)
  • Busca FTS no DB de textos (patristica_text.db)
  • Leitura de arquivos brutos estilo head/tail/grep/slice
  • Ferramentas do pipeline existente (listagem de volumes, páginas, chunks)

Uso:
    python3.11 assistente_patristico.py
    python3.11 assistente_patristico.py --model qwen3:30b
    python3.11 assistente_patristico.py --model deepseek-v3.2:cloud
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# smolagents
# ---------------------------------------------------------------------------
from smolagents import CodeAgent, LiteLLMModel, tool

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUMOS_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_TEXT_DB = PROJECT_ROOT / "data" / "patristica_text.db"
DEFAULT_ROOT = PROJECT_ROOT / "teste"
DEFAULT_MODEL = "qwen3:30b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _json_out(payload: Dict[str, Any], max_chars: int = 16000) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _as_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


# ===================================================================
# TOOLS – DB de Resumos (patristica_resumos.db)
# ===================================================================

@tool
def resumo_listar_documentos(
    db: str = str(DEFAULT_RESUMOS_DB),
) -> str:
    """
    Lista todos os documentos (volumes) que possuem resumos no banco de resumos.
    Retorna nome do documento, total de páginas processadas e data do último resumo.
    NÃO altere o parâmetro db a menos que tenha certeza de outro caminho.

    Args:
        db: Caminho do banco de resumos SQLite. Default já aponta para o DB correto.
    """
    p = _as_path(db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})
    con = _connect(p)
    rows = con.execute(
        """
        SELECT documento,
               COUNT(*) AS total_paginas,
               MIN(pagina_num) AS pag_min,
               MAX(pagina_num) AS pag_max,
               MAX(criado_em) AS ultimo_resumo
        FROM resumos
        GROUP BY documento
        ORDER BY documento
        """
    ).fetchall()
    con.close()
    return _json_out({
        "ok": True,
        "count": len(rows),
        "documentos": [dict(r) for r in rows],
    })


@tool
def resumo_buscar(
    query: str,
    documento: str = "",
    campo: str = "resumo_pagina",
    limit: int = 10,
    db: str = str(DEFAULT_RESUMOS_DB),
) -> str:
    """
    Busca texto nos resumos do banco de resumos (LIKE %query%).
    Pode filtrar por documento e campo específico.

    Args:
        query: Texto a buscar (case-insensitive).
        documento: Filtrar por nome do documento (ex: 'PL001'). Vazio = todos.
        campo: Campo onde buscar: 'resumo_pagina', 'resumo_global' ou 'pagina_texto'.
        limit: Máximo de resultados.
        db: Caminho do banco de resumos SQLite.
    """
    p = _as_path(db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    allowed = {"resumo_pagina", "resumo_global", "pagina_texto"}
    if campo not in allowed:
        return _json_out({"ok": False, "error": f"campo inválido, use: {allowed}"})

    con = _connect(p)
    sql = f"SELECT documento, pagina_num, pagina_file, {campo} AS trecho FROM resumos WHERE {campo} LIKE ?"
    params: list = [f"%{query}%"]
    if documento:
        sql += " AND documento = ?"
        params.append(documento)
    sql += f" ORDER BY documento, pagina_num LIMIT ?"
    params.append(max(1, limit))

    rows = con.execute(sql, params).fetchall()
    con.close()

    results = []
    for r in rows:
        trecho = str(r["trecho"])
        # Destaca contexto ao redor do match
        idx = trecho.lower().find(query.lower())
        if idx >= 0:
            start = max(0, idx - 150)
            end = min(len(trecho), idx + len(query) + 150)
            snippet = trecho[start:end]
        else:
            snippet = trecho[:300]
        results.append({
            "documento": r["documento"],
            "pagina_num": r["pagina_num"],
            "pagina_file": r["pagina_file"],
            "snippet": snippet,
        })

    return _json_out({"ok": True, "count": len(results), "results": results})


@tool
def resumo_obter_pagina(
    documento: str,
    pagina_num: int,
    db: str = str(DEFAULT_RESUMOS_DB),
) -> str:
    """
    Obtém o resumo completo de uma página específica de um documento.
    Retorna o texto original da página, o resumo da página e o resumo global naquele ponto.

    Args:
        documento: Nome do documento (ex: 'PL001').
        pagina_num: Número da página.
        db: Caminho do banco de resumos SQLite.
    """
    p = _as_path(db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    con = _connect(p)
    row = con.execute(
        """SELECT documento, pagina_num, pagina_file, pagina_texto,
                  resumo_pagina, resumo_global, modelo, criado_em
           FROM resumos WHERE documento = ? AND pagina_num = ?""",
        (documento, pagina_num),
    ).fetchone()
    con.close()
    if not row:
        return _json_out({"ok": False, "error": "não encontrado"})
    return _json_out({"ok": True, "resumo": dict(row)})


@tool
def resumo_obter_global(
    documento: str,
    db: str = str(DEFAULT_RESUMOS_DB),
) -> str:
    """
    Obtém o resumo global mais recente (última página processada) de um documento.
    Esse é o overview acumulado de todo o documento até o final.

    Args:
        documento: Nome do documento (ex: 'PL001').
        db: Caminho do banco de resumos SQLite.
    """
    p = _as_path(db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    con = _connect(p)
    row = con.execute(
        """SELECT documento, pagina_num, resumo_global, modelo, criado_em
           FROM resumos WHERE documento = ?
           ORDER BY pagina_num DESC LIMIT 1""",
        (documento,),
    ).fetchone()
    con.close()
    if not row:
        return _json_out({"ok": False, "error": "documento não encontrado"})
    return _json_out({"ok": True, "resumo_final": dict(row)})


@tool
def resumo_intervalo(
    documento: str,
    pag_inicio: int,
    pag_fim: int,
    campo: str = "resumo_pagina",
    db: str = str(DEFAULT_RESUMOS_DB),
) -> str:
    """
    Obtém resumos de um intervalo de páginas de um documento.
    Útil para ver a evolução do conteúdo entre duas páginas.

    Args:
        documento: Nome do documento (ex: 'PL001').
        pag_inicio: Página inicial (inclusive).
        pag_fim: Página final (inclusive).
        campo: 'resumo_pagina' ou 'resumo_global'.
        db: Caminho do banco de resumos SQLite.
    """
    p = _as_path(db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    allowed = {"resumo_pagina", "resumo_global"}
    if campo not in allowed:
        return _json_out({"ok": False, "error": f"campo inválido, use: {allowed}"})

    con = _connect(p)
    rows = con.execute(
        f"""SELECT pagina_num, pagina_file, {campo} AS texto
            FROM resumos
            WHERE documento = ? AND pagina_num BETWEEN ? AND ?
            ORDER BY pagina_num""",
        (documento, pag_inicio, pag_fim),
    ).fetchall()
    con.close()
    return _json_out({
        "ok": True,
        "documento": documento,
        "count": len(rows),
        "paginas": [dict(r) for r in rows],
    })


# ===================================================================
# TOOLS – Leitura de arquivos brutos (head / tail / grep / slice)
# ===================================================================

@tool
def arquivo_head(
    path: str,
    linhas: int = 30,
) -> str:
    """
    Lê as primeiras N linhas de um arquivo de texto (como head).

    Args:
        path: Caminho do arquivo.
        linhas: Número de linhas a retornar.
    """
    p = _as_path(path)
    if not p.exists():
        return _json_out({"ok": False, "error": f"arquivo não encontrado: {p}"})
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return _json_out({"ok": False, "error": str(e)})
    selected = all_lines[:max(1, linhas)]
    return _json_out({
        "ok": True,
        "path": str(p),
        "total_linhas": len(all_lines),
        "retornadas": len(selected),
        "texto": "\n".join(selected),
    })


@tool
def arquivo_tail(
    path: str,
    linhas: int = 30,
) -> str:
    """
    Lê as últimas N linhas de um arquivo de texto (como tail).

    Args:
        path: Caminho do arquivo.
        linhas: Número de linhas a retornar.
    """
    p = _as_path(path)
    if not p.exists():
        return _json_out({"ok": False, "error": f"arquivo não encontrado: {p}"})
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return _json_out({"ok": False, "error": str(e)})
    n = max(1, linhas)
    selected = all_lines[-n:]
    return _json_out({
        "ok": True,
        "path": str(p),
        "total_linhas": len(all_lines),
        "retornadas": len(selected),
        "texto": "\n".join(selected),
    })


@tool
def arquivo_slice(
    path: str,
    linha_inicio: int = 1,
    linha_fim: int = 50,
) -> str:
    """
    Lê um trecho de um arquivo por intervalo de linhas (1-indexed, inclusivo).
    Como sed -n 'Xp,Yp'.

    Args:
        path: Caminho do arquivo.
        linha_inicio: Linha inicial (1-based, inclusivo).
        linha_fim: Linha final (1-based, inclusivo).
    """
    p = _as_path(path)
    if not p.exists():
        return _json_out({"ok": False, "error": f"arquivo não encontrado: {p}"})
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return _json_out({"ok": False, "error": str(e)})
    start = max(0, linha_inicio - 1)
    end = max(start, linha_fim)
    selected = all_lines[start:end]
    return _json_out({
        "ok": True,
        "path": str(p),
        "total_linhas": len(all_lines),
        "intervalo": f"{linha_inicio}-{linha_fim}",
        "retornadas": len(selected),
        "texto": "\n".join(selected),
    })


@tool
def arquivo_grep(
    pattern: str,
    path: str = "",
    directory: str = "",
    glob_pattern: str = "*.txt",
    case_sensitive: bool = False,
    context_lines: int = 2,
    limit: int = 20,
) -> str:
    """
    Busca um padrão em arquivo(s) de texto, retornando linhas com contexto.
    Pode buscar em um arquivo específico ou recursivamente num diretório.

    Args:
        pattern: Texto ou regex a buscar.
        path: Caminho de um arquivo específico (prioridade sobre directory).
        directory: Diretório para busca recursiva se path não for dado.
        glob_pattern: Filtro de arquivos na busca recursiva.
        case_sensitive: Se True, diferencia maiúsculas/minúsculas.
        context_lines: Linhas de contexto acima/abaixo de cada match.
        limit: Máximo de matches retornados.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        # Se não é regex válido, trata como literal
        regex = re.compile(re.escape(pattern), flags)

    results: List[Dict[str, Any]] = []

    def _search_file(fp: Path):
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return
        for i, line in enumerate(lines):
            if regex.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet = "\n".join(lines[start:end])
                results.append({
                    "file": str(fp),
                    "line_num": i + 1,
                    "snippet": snippet,
                })
                if len(results) >= limit:
                    return

    if path:
        fp = _as_path(path)
        if fp.exists() and fp.is_file():
            _search_file(fp)
    elif directory:
        dp = _as_path(directory)
        if dp.exists() and dp.is_dir():
            for fp in sorted(dp.rglob(glob_pattern)):
                if fp.is_file():
                    _search_file(fp)
                    if len(results) >= limit:
                        break
    else:
        return _json_out({"ok": False, "error": "forneça 'path' ou 'directory'"})

    return _json_out({"ok": True, "pattern": pattern, "count": len(results), "results": results})


@tool
def arquivo_listar(
    directory: str,
    glob_pattern: str = "*.txt",
    limit: int = 100,
) -> str:
    """
    Lista arquivos num diretório com tamanho e nome.

    Args:
        directory: Diretório a listar.
        glob_pattern: Filtro de nomes (ex: '*.txt').
        limit: Máximo de arquivos retornados.
    """
    dp = _as_path(directory)
    if not dp.exists():
        return _json_out({"ok": False, "error": f"diretório não encontrado: {dp}"})
    files = []
    for p in sorted(dp.glob(glob_pattern)):
        if p.is_file():
            files.append({"name": p.name, "path": str(p), "size": p.stat().st_size})
            if len(files) >= limit:
                break
    return _json_out({"ok": True, "directory": str(dp), "count": len(files), "files": files})


# ===================================================================
# TOOLS – Busca FTS no text DB existente
# ===================================================================

@tool
def fts_buscar(
    query: str,
    unit: str = "page",
    volume_id: str = "",
    series: str = "",
    limit: int = 15,
    text_db: str = str(DEFAULT_TEXT_DB),
) -> str:
    """
    Busca full-text (FTS5) no banco de textos OCR da Patrística.
    Suporta busca em chunks ou páginas.

    Args:
        query: Consulta FTS (ex: 'Tertullianus apologeticum').
        unit: 'chunk' ou 'page'.
        volume_id: Filtro por volume (ex: 'PL001'). Vazio = todos.
        series: Filtro por série (ex: 'PL'). Vazio = todas.
        limit: Máximo de resultados.
        text_db: Caminho do banco de textos.
    """
    p = _as_path(text_db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    if unit not in {"chunk", "page"}:
        return _json_out({"ok": False, "error": "unit deve ser 'chunk' ou 'page'"})

    con = _connect(p)
    try:
        if unit == "chunk":
            sql = """
                SELECT c.chunk_id AS item_id, c.volume_id,
                       bm25(chunks_fts) AS score,
                       snippet(chunks_fts, 0, '[', ']', ' ... ', 24) AS snippet
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
            """
        else:
            sql = """
                SELECT p.page_id AS item_id, p.volume_id,
                       bm25(pages_fts) AS score,
                       snippet(pages_fts, 0, '[', ']', ' ... ', 24) AS snippet
                FROM pages_fts
                JOIN pages p ON p.id = pages_fts.rowid
                WHERE pages_fts MATCH ?
            """
        params: list = [query]
        if volume_id:
            sql += f" AND {'c' if unit == 'chunk' else 'p'}.volume_id = ?"
            params.append(volume_id)
        if series:
            sql += f" AND substr({'c' if unit == 'chunk' else 'p'}.volume_id, 1, 2) = ?"
            params.append(series.upper())
        sql += " ORDER BY score LIMIT ?"
        params.append(max(1, limit))
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    return _json_out({
        "ok": True,
        "query": query,
        "unit": unit,
        "count": len(rows),
        "results": [dict(r) for r in rows],
    })


@tool
def fts_listar_volumes(
    series: str = "",
    limit: int = 50,
    text_db: str = str(DEFAULT_TEXT_DB),
) -> str:
    """
    Lista volumes disponíveis no banco de textos OCR.

    Args:
        series: Filtro por série (PG, PL, PO). Vazio = todas.
        limit: Máximo de volumes.
        text_db: Caminho do banco de textos.
    """
    p = _as_path(text_db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    con = _connect(p)
    sql = "SELECT volume_id, series, volume_number, volume_suffix FROM volumes"
    params: list = []
    if series:
        sql += " WHERE series = ?"
        params.append(series.upper().strip())
    sql += " ORDER BY series, volume_number, volume_suffix LIMIT ?"
    params.append(max(1, limit))
    rows = con.execute(sql, params).fetchall()
    con.close()
    return _json_out({"ok": True, "count": len(rows), "volumes": [dict(r) for r in rows]})


@tool
def fts_obter_pagina(
    page_id: str,
    max_chars: int = 8000,
    text_db: str = str(DEFAULT_TEXT_DB),
) -> str:
    """
    Obtém o texto OCR normalizado de uma página pelo seu page_id.

    Args:
        page_id: Identificador da página (ex: 'PL001::079134e2-...-394').
        max_chars: Máximo de caracteres do texto a retornar.
        text_db: Caminho do banco de textos.
    """
    p = _as_path(text_db)
    if not p.exists():
        return _json_out({"ok": False, "error": f"DB não encontrado: {p}"})

    con = _connect(p)
    row = con.execute(
        """SELECT page_id, volume_id, page_num, file_path, text_norm, char_count
           FROM pages WHERE page_id = ?""",
        (page_id,),
    ).fetchone()
    con.close()
    if not row:
        return _json_out({"ok": False, "error": f"página não encontrada: {page_id}"})
    text = str(row["text_norm"] or "")
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncado]"
    return _json_out({"ok": True, "page": {**dict(row), "text_norm": text}})


# ===================================================================
# Montagem do agente
# ===================================================================

SYSTEM_PROMPT = f"""\
Você é um assistente especializado em Patrística (Patrologia Graeca e Patrologia Latina).
Você tem acesso a ferramentas para consultar bancos de dados de resumos seriais e textos OCR
dos volumes da Patrística, além de ler e buscar em arquivos de texto brutos.

IMPORTANTE – Caminhos padrão (NÃO altere ao chamar as ferramentas, use sempre os defaults):
- Banco de resumos: {DEFAULT_RESUMOS_DB}
- Banco de textos OCR: {DEFAULT_TEXT_DB}
- Diretório raiz dos volumes: {DEFAULT_ROOT}

Regras:
1. Responda sempre em português do Brasil.
2. Cite fontes: indique documento, página, trecho quando possível.
3. Quando não souber, diga explicitamente e sugira como investigar.
4. Use as ferramentas disponíveis para fundamentar suas respostas.
5. Para visão geral de um documento, use `resumo_obter_global`.
6. Para buscar conteúdo específico, combine `resumo_buscar` com `fts_buscar`.
7. Para ler arquivos brutos, use `arquivo_head`, `arquivo_tail`, `arquivo_grep`, `arquivo_slice`.
8. NUNCA invente caminhos de arquivos. Use os defaults ou descubra com `arquivo_listar`.
9. Ao chamar ferramentas, NÃO passe os argumentos db, text_db, etc. — use os valores padrão.

Ferramentas disponíveis (resumo):
- resumo_listar_documentos: lista volumes com resumos processados
- resumo_buscar: busca texto nos resumos (LIKE)
- resumo_obter_pagina: obtém resumo completo de uma página
- resumo_obter_global: obtém overview final do documento
- resumo_intervalo: obtém resumos de intervalo de páginas
- fts_buscar: busca full-text nos textos OCR
- fts_listar_volumes: lista volumes no DB de textos
- fts_obter_pagina: obtém texto OCR de uma página
- arquivo_head/tail/slice/grep/listar: leitura de arquivos brutos
"""


def build_agent(model_name: str, ollama_url: str) -> CodeAgent:
    """Constrói o CodeAgent com todas as tools."""
    model_id = model_name if model_name.startswith("ollama/") else f"ollama/{model_name}"
    model = LiteLLMModel(
        model_id=model_id,
        api_base=ollama_url,
    )

    tools = [
        # DB de resumos
        resumo_listar_documentos,
        resumo_buscar,
        resumo_obter_pagina,
        resumo_obter_global,
        resumo_intervalo,
        # Arquivos brutos
        arquivo_head,
        arquivo_tail,
        arquivo_slice,
        arquivo_grep,
        arquivo_listar,
        # FTS / text DB
        fts_buscar,
        fts_listar_volumes,
        fts_obter_pagina,
    ]

    return CodeAgent(
        model=model,
        tools=tools,
        instructions=SYSTEM_PROMPT,
        max_steps=15,
        verbosity_level=1,
        additional_authorized_imports=["json", "re"],
    )


# ===================================================================
# CLI + Loop interativo
# ===================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Assistente Patrístico interativo (smolagents + Ollama).",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Modelo Ollama (default: {DEFAULT_MODEL}).",
    )
    p.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help=f"URL do Ollama (default: {DEFAULT_OLLAMA_URL}).",
    )
    p.add_argument(
        "--query", "-q",
        default=None,
        help="Pergunta única (modo não-interativo). Se omitido, entra no modo interativo.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    print("=" * 60)
    print("  Assistente Patrístico  (smolagents + Ollama)")
    print(f"  Modelo: {args.model}")
    print(f"  Ollama: {args.ollama_url}")
    print("=" * 60)

    agent = build_agent(model_name=args.model, ollama_url=args.ollama_url)

    # Modo single-query
    if args.query:
        result = agent.run(args.query)
        print("\n" + str(result))
        return

    # Modo interativo
    print("\nDigite sua pergunta (ou 'sair' para encerrar):\n")
    while True:
        try:
            user_input = input("🏛️  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAté logo!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"sair", "exit", "quit", "q"}:
            print("Até logo!")
            break

        try:
            result = agent.run(user_input)
            print(f"\n{result}\n")
        except Exception as exc:
            print(f"\n❌ Erro: {exc}\n")


if __name__ == "__main__":
    main()
