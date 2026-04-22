"""
External lexical lookup helpers: Whitaker for Latin, Ollama JSON prompt for others.
Fail-open: on any error, returns found=False.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List
import urllib.error
import urllib.request

# Local import moved inside lookup_term to avoid circular dependency


WHITAKER_ENABLED = os.getenv("KW_ENABLE_WHITAKER", "1").strip() not in {"0", "false", "False"}
OLLAMA_ENABLED = os.getenv("KW_ENABLE_OLLAMA_LOOKUP", "1").strip() not in {"0", "false", "False"}

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
OLLAMA_TIMEOUT = float(os.getenv("KW_OLLAMA_TIMEOUT", "60"))

DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

LOOKUP_PROVIDER = os.getenv("KW_LOOKUP_PROVIDER", "ollama").strip().lower()
LOOKUP_MODEL = os.getenv("KW_LOOKUP_MODEL")
LOOKUP_BASE_URL = os.getenv("KW_LOOKUP_BASE_URL")

DEFAULT_MODEL = LOOKUP_MODEL or (DEFAULT_OPENAI_MODEL if LOOKUP_PROVIDER == "openai" else OLLAMA_MODEL)
DEFAULT_BASE_URL = LOOKUP_BASE_URL or (DEFAULT_OPENAI_BASE_URL if LOOKUP_PROVIDER == "openai" else OLLAMA_URL)

DEFAULT_TIMEOUT_OLLAMA = int(OLLAMA_TIMEOUT)
DEFAULT_TIMEOUT_OPENAI = int(os.getenv("KW_OPENAI_TIMEOUT", "300"))

DEFAULT_NUM_CTX = int(os.getenv("KW_LOOKUP_NUM_CTX", "4096"))
MAX_TOKENS_DEFAULT = 6 * 1024

DEFAULT_TOP_P = 0.8
DEFAULT_TEMPERATURE = 0.1
TOP_P_DEFAULT = DEFAULT_TOP_P
TEMPERATURE_DEFAULT = DEFAULT_TEMPERATURE

DEFAULT_REASONING_EFFORT = os.getenv("KW_LOOKUP_REASONING", "medium")
DEFAULT_API_KEY_ENV = os.getenv("KW_LOOKUP_API_KEY_ENV", "OPENAI_API_KEY")

LLM_ENABLED_ENV = os.getenv("KW_ENABLE_LOOKUP_LLM")
LLM_ENABLED = (
    LLM_ENABLED_ENV.strip() not in {"0", "false", "False"}
    if LLM_ENABLED_ENV is not None
    else OLLAMA_ENABLED
)

# Prompt from PoC (short version)
OLLAMA_SYSTEM = """
Você vai receber uma palavra ou expressão em português, latim, grego, inglês, francês, hebraico, aramaico, sírio, armênio ou etíope do contexto Patrístico/bíblico.
Objetivo: identificar a língua e se a original está incorreta (erros OCR ou grafia em geral).

Saída APENAS JSON UTF-8:
{
  "palavras": [
    {
      "palavra": "exemplo",
      "correcao": "exemplo",
      "lingua": "português",
      "notas": "Nota sobre a palavra exemplo. Opcional, pode ser vazio"
    }
    ... outras palavras se existir ...
  ]
}
"""


@dataclass
class LookupCandidate:
    palavra: str = ""
    correcao: str = ""
    lingua: str = "unknown"
    notas: str = ""


@dataclass
class LookupResult:
    found: bool
    source: Optional[str] = None  # "whitaker" | "ollama" | "openai"
    lemma: Optional[str] = None
    language: str = "unknown"
    normalized: str = ""
    notes: str = ""
    corrected: str = ""
    candidates: List[LookupCandidate] = field(default_factory=list)


_lookup_cache: Dict[Tuple[str, Optional[str]], LookupResult] = {}


def _normalize_for_match(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(cleaned.split())


def _pick_best_palavra(term: str, palavras: list[dict]) -> Optional[dict]:
    """Choose the best entry from LLM JSON output for a (possibly multiword) term."""
    term_norm = _normalize_for_match(term)
    best = None
    best_score = -1
    for p in palavras:
        candidate = p.get("correcao") or p.get("palavra") or ""
        cand_norm = _normalize_for_match(candidate)
        score = 0
        if cand_norm == term_norm:
            score += 100
        if term_norm and cand_norm and term_norm in cand_norm:
            score += 30
        if term_norm and cand_norm and cand_norm in term_norm:
            score += 20
        term_tokens = set(term_norm.split())
        cand_tokens = set(cand_norm.split())
        if term_tokens and cand_tokens:
            score += 5 * len(term_tokens & cand_tokens)
        score += len(cand_norm)
        if score > best_score:
            best = p
            best_score = score
    return best


def _maybe_join_phrase(term: str, palavras: list[dict]) -> Optional[LookupResult]:
    """If LLM devolveu uma lista tokenizada, tenta remontar a frase inteira."""
    pieces_raw: list[str] = []
    pieces_norm: list[str] = []
    langs: list[str] = []
    for p in palavras:
        raw = (p.get("correcao") or p.get("palavra") or "").strip()
        if not raw:
            continue
        pieces_raw.append(raw)
        pieces_norm.append(_normalize_for_match(raw))
        lang = (p.get("lingua") or "unknown").strip()
        if lang:
            langs.append(lang)

    if len(pieces_raw) < 2:
        return None

    term_norm = _normalize_for_match(term)
    joined_raw = " ".join(pieces_raw)
    joined_norm = _normalize_for_match(joined_raw)

    if not joined_norm:
        return None

    term_tokens = set(term_norm.split())
    joined_tokens = set(joined_norm.split())
    if not term_tokens or not joined_tokens:
        return None

    overlap = len(term_tokens & joined_tokens) / max(len(term_tokens), 1)
    if overlap < 0.5:
        # Não parece a mesma expressão
        return None

    # Escolhe a língua mais frequente
    majority_lang = "unknown"
    if langs:
        from collections import Counter

        majority_lang = Counter(langs).most_common(1)[0][0]

    return LookupResult(
        found=True,
        source=LOOKUP_PROVIDER,
        lemma=joined_raw,
        language=majority_lang,
        normalized=term,
        corrected=joined_raw,
        notes="",
        candidates=[LookupCandidate(palavra=raw, correcao=raw, lingua=majority_lang, notas="") for raw in pieces_raw],
    )


def lookup_term(term: str, language_hint: Optional[str] = None) -> LookupResult:
    # Late import to avoid circular dependency
    from keyword_integrity import detect_script_flags, clean_keyword_token
    key = (term, language_hint)
    if key in _lookup_cache:
        return _lookup_cache[key]

    cleaned = clean_keyword_token(term)
    flags = detect_script_flags(cleaned)
    result = LookupResult(found=False, language="unknown", normalized=cleaned)

    try:
        # Latin script → try Whitaker first
        if flags.get("has_latin") and WHITAKER_ENABLED:
            r = _lookup_whitaker(cleaned)
            if r.found:
                result = r
        # If not found or non-Latin, try LLM provider (Ollama/OpenAI)
        if not result.found and LLM_ENABLED:
            r = _lookup_llm(cleaned)
            if r.found:
                result = r
    except Exception:
        result = LookupResult(found=False, language="unknown", normalized=cleaned)

    _lookup_cache[key] = result
    return result


def _lookup_whitaker(term: str) -> LookupResult:
    cmd = ["tools/run_whitaker.sh", term]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False
        )
    except Exception:
        return LookupResult(found=False, language="unknown", normalized=term)

    out = proc.stdout.strip()
    if "UNKNOWN" in out:
        return LookupResult(found=False, language="unknown", normalized=term)

    # parse first non-empty line
    first_line = None
    for line in out.splitlines():
        line = line.strip()
        if line:
            first_line = line
            break
    if not first_line:
        return LookupResult(found=False, language="unknown", normalized=term)

    lemma = first_line.split()[0]
    return LookupResult(
        found=True,
        source="whitaker",
        lemma=lemma,
        language="lat",
        normalized=term,
        corrected=lemma,
        candidates=[LookupCandidate(palavra=term, correcao=lemma, lingua="lat", notas="")],
    )


def openai_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = DEFAULT_OPENAI_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT_OPENAI,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    json_schema: dict | None = None,
    top_p: float = TOP_P_DEFAULT,
    temperature: float = TEMPERATURE_DEFAULT,
) -> str:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Variável de ambiente {api_key_env} não definida.")
    url = f"{base_url}/chat/completions"

    response_format = {"type": "json_object"}

    if json_schema:
        response_format = {"type": "json_schema", "json_schema": json_schema}

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

    if "gpt-5" not in model:
        payload["temperature"] = temperature
        payload["top_p"] = top_p
        payload["max_tokens"] = MAX_TOKENS_DEFAULT
    else:
        payload["verbosity"] = "low"
        payload["service_tier"] = "flex"

    data = json.dumps(payload).encode("utf-8")
    with open("debug.json", "w") as f:
        f.write(data.decode("utf-8"))

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
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"OpenAI HTTP {exc.code}: {detail}")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        print(f"OpenAI falhou: {exc}")
        raise RuntimeError(f"OpenAI falhou: {exc}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI retornou sem choices")
    content = choices[0].get("message", {}).get("content", "")

    print(f"OpenAI response: {content}")
    return content.strip()


def ollama_chat(
    prompt_system: str,
    prompt_user: str,
    model: str,
    base_url: str = OLLAMA_URL,
    timeout: int = DEFAULT_TIMEOUT_OLLAMA,
    num_ctx: int = DEFAULT_NUM_CTX,
    think: bool = False,
    top_p: float = TOP_P_DEFAULT,
    temperature: float = TEMPERATURE_DEFAULT,
) -> str:
    url = f"{base_url}/api/chat"
    payload: dict = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_ctx": num_ctx,
        },
    }
    if not think:
        payload["think"] = False

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"Ollama HTTP {exc.code}: {detail}")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        print(f"Ollama falhou: {exc}")
        raise RuntimeError(f"Ollama falhou: {exc}") from exc

    content = (body.get("message") or {}).get("content", "")
    print(f"Ollama response: {content}")
    return content.strip()


def llm_chat(
    prompt_system: str,
    prompt_user: str,
    *,
    provider: str = LOOKUP_PROVIDER,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT_OLLAMA,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    num_ctx: int = DEFAULT_NUM_CTX,
    think: bool = False,
    json_schema: dict | None = None,
    top_p: float = TOP_P_DEFAULT,
    temperature: float = TEMPERATURE_DEFAULT,
) -> str:
    print(f"LLM Chat - Provider: {provider}, Model: {model}, Base URL: {base_url}, Timeout: {timeout}s, Reasoning Effort: {reasoning_effort}, Num Ctx: {num_ctx}, Think: {think}, Top_p: {top_p}, Temperature: {temperature}")
    if provider == "openai":
        return openai_chat(
            prompt_system=prompt_system,
            prompt_user=prompt_user,
            model=model,
            base_url=base_url,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            api_key_env=api_key_env,
            json_schema=json_schema,
            top_p=top_p,
            temperature=temperature,
        )
    return ollama_chat(
        prompt_system=prompt_system,
        prompt_user=prompt_user,
        model=model,
        base_url=base_url,
        timeout=timeout,
        num_ctx=num_ctx,
        think=think,
        top_p=top_p,
        temperature=temperature,
    )


def _lookup_llm(term: str) -> LookupResult:
    provider = LOOKUP_PROVIDER
    model = DEFAULT_MODEL
    base_url = DEFAULT_BASE_URL
    timeout = DEFAULT_TIMEOUT_OPENAI if provider == "openai" else DEFAULT_TIMEOUT_OLLAMA

    try:
        content = llm_chat(
            prompt_system=OLLAMA_SYSTEM,
            prompt_user=term,
            provider=provider,
            model=model,
            base_url=base_url,
            timeout=timeout,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            api_key_env=DEFAULT_API_KEY_ENV,
            num_ctx=DEFAULT_NUM_CTX,
            top_p=TOP_P_DEFAULT,
            temperature=DEFAULT_TEMPERATURE,
        )
    except Exception:
        return LookupResult(found=False, language="unknown", normalized=term)

    try:
        parsed = json.loads(content)
    except Exception:
        return LookupResult(found=False, language="unknown", normalized=term)

    palavras = parsed.get("palavras") or []
    candidates = [
        LookupCandidate(
            palavra=p.get("palavra", ""),
            correcao=p.get("correcao", ""),
            lingua=p.get("lingua", "unknown"),
            notas=p.get("notas", ""),
        )
        for p in palavras
    ] if palavras else []
    if not candidates:
        return LookupResult(found=False, language="unknown", normalized=term)

    # Se lista veio tokenizada, tenta remontar a frase inteira
    phrase_result = _maybe_join_phrase(term, palavras)
    if phrase_result:
        return phrase_result

    best = _pick_best_palavra(term, palavras)
    if not best:
        return LookupResult(found=False, language="unknown", normalized=term, candidates=candidates)

    corrected = best.get("correcao") or best.get("palavra") or term
    language = best.get("lingua") or "unknown"
    notes = best.get("notas") or ""
    return LookupResult(
        found=True,
        source=provider,
        lemma=corrected,
        language=language,
        normalized=term,
        corrected=corrected,
        notes=notes,
        candidates=candidates,
    )


def _lookup_ollama(term: str) -> LookupResult:
    # Mantido por compatibilidade; agora usa _lookup_llm com provider configurado.
    return _lookup_llm(term)
