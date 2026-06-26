#!/usr/bin/env python3
"""
Small helper to hit a local Ollama model (default: qwen3.5:9b) with custom
system and user prompts.

Usage examples:
    python ollama_prompt_test.py --system "You are concise." --user "Hello"
    python ollama_prompt_test.py --model qwen3.5:9b --user "Explique a graça"

If a prompt is omitted, you will be prompted for it interactively.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import os
import sqlite3
from typing import Iterable, Callable, Dict, List, Set, Optional
import concurrent.futures
import threading
import random
import time
import hashlib
import logging
from pathlib import Path
import multiprocessing as mp

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUMOS_DB = PROJECT_ROOT / "data" / "patristica_resumos.db"
DEFAULT_KEYWORDS_DB = PROJECT_ROOT / "data" / "patristica_keywords.db"
DEFAULT_MODEL = "qwen3:30b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_OLLAMA = 1000
DEFAULT_TIMEOUT_OPENAI = 1000

DEFAULT_NUM_CTX = 16384
TOKEN_RESERVE_OUTPUT = 1024  # keywords precisam de menos output
TOKEN_RESERVE_SYSTEM = 400
CHARS_PER_TOKEN = 3.8
RECOMMENDED_MIN_KEYWORDS = 5
RECOMMENDED_MAX_KEYWORDS = 20
MAX_KEYWORDS_LENGTH = 180
OUTLIER_LOW_RATIO = 0.4
OUTLIER_HIGH_RATIO = 2.0

MAX_TOKENS_DEFAULT = 6 * 1024

# Integridade via serviço HTTP (opcional)
INTEGRITY_HTTP_URL = os.getenv("KW_INTEGRITY_HTTP_URL", "").strip()
INTEGRITY_HTTP_TIMEOUT = float(os.getenv("KW_INTEGRITY_HTTP_TIMEOUT", "60.0"))

DEFAULT_NORM_DB = PROJECT_ROOT / "data" / "patristica_normalizations.db"


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def open_norm_db(path: str) -> sqlite3.Connection:
    """Abre/Cria o DB de normalizações e garante a tabela."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS normalizations (
            id INTEGER PRIMARY KEY,
            original TEXT NOT NULL,
            canonical TEXT NOT NULL,
            group_id INTEGER,
            model TEXT,
            backend TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(original, group_id)
        )
        """
    )
    conn.commit()
    return conn


def save_normalizations(conn: sqlite3.Connection, mapping: Dict[str, str], group_id: Optional[int] = None, model: Optional[str] = None, backend: Optional[str] = None) -> int:
    """Salva o dicionário original->canonical na tabela de normalizações.

    Return: número de registros inseridos/atualizados.
    """
    import datetime

    now = datetime.datetime.utcnow().isoformat()
    cur = conn.cursor()
    count = 0
    for orig, canon in mapping.items():
        try:
            cur.execute(
                "INSERT OR REPLACE INTO normalizations (original, canonical, group_id, model, backend, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (orig, canon, group_id, model, backend, now),
            )
            count += 1
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to save normalization %s -> %s: %s", orig, canon, e)
    conn.commit()
    return count


def get_existing_normalizations(conn: sqlite3.Connection, group_id: Optional[int]) -> Set[str]:
    """Retorna o conjunto de 'original' já normalizados para um group_id (ou vazio se None)."""
    if group_id is None:
        return set()
    cur = conn.cursor()
    cur.execute("SELECT original FROM normalizations WHERE group_id = ?", (group_id,))
    rows = cur.fetchall()
    return set([str(r[0]) for r in rows])


def query_all_keywords_from_group_id(conn: sqlite3.Connection, hdbscan_group_id: int) -> list[dict]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM keywords WHERE hdbscan_group_id = ?",
        (hdbscan_group_id,)
    )
    rows = cursor.fetchall()
    return rows
    # return [dict(row) for row in rows]


DEFAULT_TOP_P = 0.8
DEFAULT_TEMPERATURE = 0.01


DEFAULT_SYSTEM_CURTO = """
Você vai receber uma lista de keywords patrísticas/bíblicas semanticamente parecidas, sua resposta deverá ser de acordo com as regras.

REGRAS:
Fundir palavras que significam a mesma coisa em um único significado canônico para indexação de acadêmicos em português do Brasil se não tiver tradução podem ser mantidas no original (latim, grego, etc.).
Apenas o que deve ser alterado deve estar presente na saída, o que estiver correto não deve ser incluído.
Caso sejam citações bíblicas, use o nome do livro por extenso, mantendo os capítulos e versículos no formato católico tradicional. (ex: use 'João 1,1' e não 'João 1, 1')"
Não alterar número de capítulos ou versículos.
Proibido usar descrições como 'Termo não identificado', 'Lixo' ou 'Erro'. Se não entender o sentido, não faça alterações.

A ordem de saída deve ser no sentido:
original: Como está na mensagem do usuário
change_to: Nome adequado as regras

Só retorne o que deve ser alterado.

Formato de saída de exemplo JSON UTF-8:
{
  "results": [
    {
      "change_to": "keyword I",
      "original": "kw. I"
    }
  ]
}
"""


def openai_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: int = DEFAULT_TIMEOUT_OPENAI,
    reasoning_effort: str = "high",
    api_key_env: str = "OPENAI_API_KEY",
    json_schema: dict | None = None,
    top_p: float = DEFAULT_TOP_P,
    temperature: float = DEFAULT_TEMPERATURE,
) -> str:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Variável de ambiente {api_key_env} não definida.")
    url = f"{base_url}/chat/completions"

    response_format = {"type": "json_object"}

    # if json_schema:
    #     response_format = {"type": "json_schema", "json_schema": json_schema}

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
        "response_format": response_format,
    }
    if reasoning_effort and len(reasoning_effort) > 0:
        payload["reasoning_effort"] = reasoning_effort

    if not "gpt-5" in model:
        # Model não é gpt-5, portanto deve suportar temperatura
        payload["temperature"] = temperature
        payload["top_p"] = top_p
        payload["max_tokens"] = MAX_TOKENS_DEFAULT
    else:
        payload["verbosity"] = "low"
        payload["service_tier"] = "flex"

    data = json.dumps(payload).encode("utf-8")
    with open("debug.json", "w") as f:
        f.write(data.decode("utf-8"))
    # Alguns provedores (ex.: Cerebras via Cloudflare) bloqueiam user-agents
    # padrão do urllib. Enviamos um UA explícito e cabeçalho Accept para
    # evitar falsos positivos de WAF.
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": os.getenv("OPENAI_USER_AGENT", "curl/8.5.0"),
    }

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            jsstr = resp.read().decode("utf-8")

            try:
                body = json.loads(jsstr)
            except json.JSONDecodeError as exc:
                print(jsstr)
                raise RuntimeError(f"OpenAI JSONDecodeError: {exc}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(detail, exc)
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI falhou: {exc}") from exc
    print(body)
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI retornou sem choices")
    content = choices[0].get("message", {}).get("content", "")
    return content.strip()



def call_ollama(model: str, system_prompt: str, user_prompt: str, base_url: str, num_ctx: int|None = None) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "top_p": DEFAULT_TOP_P,
            "temperature": DEFAULT_TEMPERATURE,
        },
    }

    if num_ctx is not None:
        payload["options"]["num_ctx"] = num_ctx

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "")



def build_keywords_prompt(keywords: set[str]) -> str:
    keywords = list(keywords)
    keywords.sort()

    lista = "\n".join(f"{kw}" for kw in keywords)

    result = "<keywords>\n"
    result += lista
    result += "\n</keywords>\n\n"

    result += "Agora, liste o que deve ser feito com as keywords segundo as instruções de sistema."

    return result


def chunked(iterable: Iterable[str], size: int) -> List[List[str]]:
    it = list(iterable)
    return [it[i : i + size] for i in range(0, len(it), size)]


def parse_llm_response(raw: str) -> List[Dict[str, str]]:
    """Tenta extrair JSON da resposta do LLM e retorna a lista em 'results'.

    Retorna lista vazia se não encontrar/parsear JSON válido.
    """
    raw = raw.strip()
    if not raw:
        return []
    # tentar carregar direto
    try:
        obj = json.loads(raw)
    except Exception:
        # tentar extrair o primeiro objeto JSON encontrado
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            obj = json.loads(raw[start : end + 1])
        except Exception:
            return []

    if isinstance(obj, dict) and "results" in obj and isinstance(obj["results"], list):
        parsed = []
        for item in obj["results"]:
            if not isinstance(item, dict):
                continue
            original = item.get("original")
            change_to = item.get("change_to")
            if original is None:
                continue
            parsed.append({"original": str(original), "change_to": (None if change_to is None else str(change_to))})
        return parsed
    return []


def default_llm_fn(batch: Iterable[str], model: str, system_prompt: str, base_url: str, num_ctx: Optional[int] = None) -> List[Dict[str, str]]:
    """Adapter que chama `call_ollama` e parseia a resposta em lista de mudanças.

    Retorna lista de dicts {'original':..., 'change_to':...}.
    """
    prompt = build_keywords_prompt(set(batch))
    raw = call_ollama(model, system_prompt, prompt, base_url, num_ctx=num_ctx)
    return parse_llm_response(raw)


def tournament_reduce(
    keywords: Iterable[str],
    llm_fn: Callable[[Iterable[str]], List[Dict[str, Optional[str]]]],
    batch_size: int = 50,
    max_rounds: int = 10,
    workers: int = 4,
    stop_when_unchanged: bool = True,
) -> Dict[str, str]:
    """Reduz um conjunto de keywords usando um torneio em rodadas.

    - `keywords`: iterável de keywords originais.
    - `llm_fn`: função que recebe um batch (iterable) e retorna lista de dicts {original, change_to}.
    - Retorna um dicionário mapeando keyword_original -> canonical.
    """
    logging.getLogger(__name__).info("Starting tournament_reduce with %d keywords", len(list(keywords)))
    # estado inicial
    originals = list(dict.fromkeys([str(k) for k in keywords]))
    if not originals:
        return {}

    # mapeamento incremental original -> current canonical (pode apontar para outra chave)
    mapping: Dict[str, str] = {k: k for k in originals}

    current_set: Set[str] = set(originals)

    for rnd in range(1, max_rounds + 1):
        batches = chunked(current_set, batch_size)
        new_canonicals: Set[str] = set()

        # executar batches em paralelo controlado
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(llm_fn, batch): tuple(batch) for batch in batches}
            for fut in concurrent.futures.as_completed(futures):
                batch = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    logging.getLogger(__name__).warning("LLM batch failed for %s: %s", batch, e)
                    continue
                # res esperado: lista de {'original':..., 'change_to':...}
                for item in res:
                    orig = item.get("original")
                    change_to = item.get("change_to")
                    if orig is None:
                        continue
                    if change_to and change_to.strip():
                        mapping[str(orig)] = str(change_to)
        # construir novo conjunto de vencedores seguindo mapeamento transitivo
        for k in list(current_set):
            # seguir cadeia até estabilizar
            seen = set()
            cur = mapping.get(k, k)
            while cur in mapping and cur not in seen:
                seen.add(cur)
                nxt = mapping.get(cur, cur)
                if nxt == cur:
                    break
                cur = nxt
            mapping[k] = cur
            new_canonicals.add(cur)

        logging.getLogger(__name__).info("Round %d: %d -> %d", rnd, len(current_set), len(new_canonicals))

        if stop_when_unchanged and len(new_canonicals) >= len(current_set):
            break
        if new_canonicals == current_set:
            break
        current_set = new_canonicals

    # garantir que todo original aponte para canonical final (seguir cadeia)
    final_map: Dict[str, str] = {}
    for orig in originals:
        cur = mapping.get(orig, orig)
        seen = set()
        while True:
            if cur in seen:
                break
            seen.add(cur)
            nxt = mapping.get(cur, cur)
            if nxt == cur:
                break
            cur = nxt
        final_map[orig] = cur

    return final_map


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick LLM chat call / tournament")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model name (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--system", help="System prompt text", default=DEFAULT_SYSTEM_CURTO
    )
    parser.add_argument("--user", help="User prompt text")
    parser.add_argument(
        "--url",
        default=os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL),
        help="Base URL for Ollama (default from OLLAMA_URL)",
    )
    parser.add_argument(
        "--openai-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="Base URL for OpenAI-compatible API",
    )
    parser.add_argument("--backend", choices=["ollama", "openai"], default="ollama", help="Which backend to use for LLM calls")
    parser.add_argument("--group", help="HDBSCAN group ID")
    parser.add_argument("--db", help="Path to SQLite database", default=str(DEFAULT_KEYWORDS_DB))
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size per LLM call")
    parser.add_argument("--max-rounds", type=int, default=10, help="Max rounds for tournament_reduce")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers for batches")
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="Number of context tokens (num_ctx) for Ollama")
    parser.add_argument("--norm-db", help="Path to normalizations DB", default=str(DEFAULT_NORM_DB))
    parser.add_argument("--apply", action="store_true", help="If set, save mapping to normalizations DB")
    parser.add_argument("--retries", type=int, default=3, help="Number of retries for LLM calls")
    parser.add_argument("--backoff-base", type=float, default=0.5, help="Base backoff seconds (exponential)")
    parser.add_argument("--jitter-max", type=float, default=0.5, help="Max jitter seconds added to backoff")
    parser.add_argument("--qps", type=float, default=0.0, help="Max queries per second (0 = unlimited)")


    args = parser.parse_args()

    system_prompt = args.system or DEFAULT_SYSTEM_CURTO.strip()
    group_id = args.group or input("HDBSCAN group ID: ").strip()
    db_path = args.db or input("Path to SQLite database: ").strip()

    conn = open_db(db_path)
    try:
        gid = int(group_id)
    except Exception:
        print(f"Invalid HDBSCAN group ID: {group_id}", file=sys.stderr)
        sys.exit(1)

    keywords = query_all_keywords_from_group_id(conn, gid)

    keyword_original: set[str] = set([str(k["keyword_original"]) for k in keywords])

    # construir prompt de amostra (para exibição) e executar o torneio
    prompt = build_keywords_prompt(keyword_original)
    print(prompt)

    # abrir norm_db e filtrar keywords já normalizadas (se existirem)
    try:
        norm_conn = open_norm_db(args.norm_db)
        existing = get_existing_normalizations(norm_conn, gid)
        if existing:
            before = len(keyword_original)
            keyword_original = set(keyword_original) - existing
            after = len(keyword_original)
            print(f"Filtered {before-after} keywords already normalized for group {gid}")
    except Exception:
        # se falhar, continuar sem filtro
        existing = set()

    # fábrica de wrappers com cache, retries/backoff+jitter e rate-limiting
    def make_llm_wrapper(backend: str, model: str, system_prompt: str, ollama_url: str, openai_url: str, num_ctx: int, retries: int, backoff_base: float, jitter_max: float, qps: float):
        cache: Dict[str, List[Dict[str, Optional[str]]]] = {}
        cache_lock = threading.Lock()
        rate_lock = threading.Lock()
        last_call_time = {"t": 0.0}

        def build_cache_key(batch: Iterable[str]) -> str:
            lst = sorted([str(x) for x in batch])
            key_str = json.dumps({"batch": lst, "backend": backend, "model": model, "system": system_prompt}, ensure_ascii=False)
            return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

        def wrapper(batch: Iterable[str]) -> List[Dict[str, Optional[str]]]:
            key = build_cache_key(batch)
            with cache_lock:
                if key in cache:
                    return cache[key]

            # rate limiting (simple spacing across threads)
            if qps and qps > 0.0:
                min_interval = 1.0 / float(qps)
                with rate_lock:
                    now = time.time()
                    elapsed = now - last_call_time["t"]
                    if elapsed < min_interval:
                        to_sleep = min_interval - elapsed
                        time.sleep(to_sleep)
                    last_call_time["t"] = time.time()

            attempt = 0
            while True:
                try:
                    if backend == "ollama":
                        res = default_llm_fn(batch, model, system_prompt, ollama_url, num_ctx=num_ctx)
                    else:
                        prompt = build_keywords_prompt(set(batch))
                        raw = openai_chat(system_prompt, prompt, model, base_url=openai_url, timeout=DEFAULT_TIMEOUT_OPENAI, top_p=DEFAULT_TOP_P, temperature=DEFAULT_TEMPERATURE)
                        res = parse_llm_response(raw)

                    # cache and return
                    with cache_lock:
                        cache[key] = res
                    return res

                except Exception as exc:
                    attempt += 1
                    if attempt > retries:
                        logging.getLogger(__name__).warning("LLM call failed after %d attempts: %s", attempt - 1, exc)
                        return []
                    backoff = backoff_base * (2 ** (attempt - 1))
                    jitter = random.uniform(0, jitter_max)
                    sleep_for = backoff + jitter
                    logging.getLogger(__name__).info("LLM call error, retrying in %.2fs (attempt %d): %s", sleep_for, attempt, exc)
                    time.sleep(sleep_for)

        return wrapper

    llm_wrapper = make_llm_wrapper(args.backend, args.model, system_prompt, args.url, args.openai_url, args.num_ctx, args.retries, args.backoff_base, args.jitter_max, args.qps)

    # executar torneio sem atualizar o DB — retornará mapping original -> canonical
    mapping = tournament_reduce(keyword_original, llm_wrapper, batch_size=args.batch_size, max_rounds=args.max_rounds, workers=args.workers)

    print("\n=== Tournament mapping (original -> canonical) ===\n")
    print(json.dumps(mapping, ensure_ascii=False, indent=2))

    if args.apply:
        try:
            norm_conn = open_norm_db(args.norm_db)
            saved = save_normalizations(norm_conn, mapping, group_id=gid, model=args.model, backend=args.backend)
            print(f"Saved {saved} normalizations to {args.norm_db}")
        except Exception as e:
            print(f"Failed to save normalizations: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
