# Plano de Feature: Versionamento de OCR

**Data:** 2026-04-14  
**Repositório:** BibliothecaPatristica  
**Branch alvo:** codex  

---

## 1. Motivação

O pipeline atual (`main2.py`) produz resultados de OCR que são salvos como arquivos `.txt` no diretório `text/` de cada volume. Não há controle sobre:

- **Qual engine/modelo** produziu o texto (tesseract, ollama/qwen, gemini, openai/gpt-5 etc.)
- **Qual versão do prompt** foi usada (`PROMPT`, `PROMPT_VERIFY_TESSERACT`, `PROMPT_VERIFY_LLM_VS_TESSERACT`)
- **Quando** o texto foi gerado (apenas data do arquivo no filesystem)
- **Histórico de revisões** — sobrescrever o `.txt` apaga o rastro anterior
- **Diffs** entre versões — impossível saber o que mudou entre dois reprocessamentos

A `evaluation_db.py` registra avaliações *pós-OCR* com `created_at`, mas não versiona o **texto em si** nem rastreia as revisões.

---

## 2. Objetivo

Criar um módulo **`ocr_versions_db.py`** e o SQLite **`data/ocr_versions.db`** que:

1. Armazene **cada versão do texto OCR completo** de cada página inline no banco, com metadados completos.
2. Armazene **os prompts completos** (system e user) inline, com SHA256 para comparação rápida de igualdade.
3. Use **SHA256 de texto e imagem** para deduplicação e detecção rápida de mudança sem ler o conteúdo.
4. Permita **consultar o histórico** de uma página — quem gerou, quando, qual resultado, qual prompt exato.
5. Exponha **diffs calculados dinamicamente** sob demanda (sem persistir), a partir dos textos originais armazenados.
6. Integre-se de forma **não-intrusiva** ao `main2.py` sem quebrar o fluxo existente.

> **Decisão de design:** diffs **não são persistidos** no banco. Os textos originais completos são armazenados e o diff é calculado dinamicamente quando necessário. Isso é mais seguro (sem risco de diff desatualizado), mais simples (sem tabela extra) e ainda permite qualquer tipo de comparação futura (unified, token, semântica) sem migração de schema.

---

## 3. Esquema SQLite proposto

```sql
-- Catálogo de combinações engine+modelo+prompt
-- Cada combinação única recebe um ID; prompts completos são guardados aqui,
-- não repetidos em cada ocr_result.
CREATE TABLE IF NOT EXISTS ocr_engine_versions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    engine                TEXT NOT NULL,    -- "tesseract" | "ollama" | "openai" | "gemini"
    model                 TEXT,             -- ex: "qwen3.5:397b-cloud", "gpt-5", "gemini-2.0-flash-lite", "lat"
    prompt_key            TEXT,             -- nome simbólico: "PROMPT", "PROMPT_VERIFY_TESSERACT", etc.
    system_prompt_hash    TEXT NOT NULL,    -- SHA256 hex completo do system prompt
    system_prompt_content TEXT NOT NULL,    -- conteúdo completo do system prompt (inline)
    user_prompt_hash      TEXT NOT NULL DEFAULT '', -- SHA256 hex do user prompt ('' se sem user prompt)
    user_prompt_content   TEXT NOT NULL DEFAULT '', -- conteúdo completo do user prompt ('' se vazio)
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Unicidade lógica: mesma combinação engine+model+prompts = mesmo registro
CREATE UNIQUE INDEX IF NOT EXISTS uq_engine_version
    ON ocr_engine_versions(engine, COALESCE(model, ''), system_prompt_hash, user_prompt_hash);

-- Cada execução de OCR para uma página/imagem
CREATE TABLE IF NOT EXISTS ocr_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id           TEXT NOT NULL,      -- ex: "PO009"
    page_num            INTEGER NOT NULL,   -- número da página
    image_path          TEXT NOT NULL,      -- path relativo à raiz do projeto
    image_hash          TEXT NOT NULL,      -- SHA256 hex do arquivo de imagem (detecta troca de imagem)
    engine_version_id   INTEGER NOT NULL REFERENCES ocr_engine_versions(id),
    text_content        TEXT NOT NULL,      -- texto/XML completo produzido (inline, original)
    text_hash           TEXT NOT NULL,      -- SHA256 hex do text_content (comparação rápida sem ler o texto)
    is_current          INTEGER NOT NULL DEFAULT 1, -- 1 = versão ativa; 0 = histórica
    reprocess_reason    TEXT,               -- "initial" | "verify_failed" | "judge_force" | "judge_failed" | "manual" | "legacy_unknown"
    meta_json           TEXT,               -- JSON com metadados de origem específicos do modo:
                                            -- NULL para OCR inicial LLM direto
                                            -- {"reprocess_mode":"verify_tesseract","tesseract_text_hash":"...","tesseract_lang":"..."}
                                            -- {"reprocess_mode":"verify_llm_vs_tesseract","tesseract_text_hash":"...","prior_llm_result_id":42,"prior_llm_text_hash":"..."}
                                            -- {"reprocess_mode":"tesseract_direct","tesseract_lang":"...","tesseract_config":"..."}
    duration_ms         REAL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ocr_results_vol_page
    ON ocr_results(volume_id, page_num);
CREATE INDEX IF NOT EXISTS idx_ocr_results_image_path
    ON ocr_results(image_path);
CREATE INDEX IF NOT EXISTS idx_ocr_results_is_current
    ON ocr_results(is_current);
-- Índice para deduplicação rápida por hash (sem ler text_content)
CREATE INDEX IF NOT EXISTS idx_ocr_results_text_hash
    ON ocr_results(text_hash);
CREATE INDEX IF NOT EXISTS idx_ocr_results_image_hash
    ON ocr_results(image_hash);
```

> **Sem tabela `ocr_diffs`:** diffs são calculados dinamicamente a partir dos `text_content` armazenados. O custo de armazenar o texto completo por versão é aceitável (textos XML de ~2–20 KB por página) e elimina toda complexidade de manutenção de diffs potencialmente desatualizados.

### Diagrama de relações

```
ocr_engine_versions (1) ──< ocr_results (N)
```

Simples. Diffs são derivados sob demanda via `compute_diff(result_a, result_b)`.

---

## 4. API pública do módulo `ocr_versions_db.py`

```python
import hashlib, difflib, json, sqlite3
from pathlib import Path

# --- Utilitários de hash (base de toda deduplicação) ---

def sha256_str(text: str) -> str:
    """SHA256 hex de uma string UTF-8. Usado para text_content e prompts."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def sha256_file(path: Path) -> str:
    """SHA256 hex do conteúdo binário de um arquivo. Usado para image_hash."""
    return hashlib.sha256(path.read_bytes()).hexdigest()

# --- Conexão ---

def connect_versions_db(path: Path) -> sqlite3.Connection: ...
def init_versions_schema(con: sqlite3.Connection) -> None: ...
def open_versions_db() -> sqlite3.Connection:
    """Abre e inicializa; chamada DENTRO do worker, após o fork."""

# --- Registro de engine/modelo/prompt ---

def get_or_create_engine_version(
    con: sqlite3.Connection,
    *,
    engine: str,                # "tesseract" | "ollama" | "openai" | "gemini"
    model: str | None,          # nome do modelo; None para Tesseract puro
    prompt_key: str,            # nome simbólico
    system_prompt: str,         # conteúdo completo (armazenado inline + SHA256)
    user_prompt: str = "",      # conteúdo completo do user prompt (armazenado inline + SHA256)
) -> int:
    # Calcula system_prompt_hash = sha256_str(system_prompt)
    # Calcula user_prompt_hash   = sha256_str(user_prompt)  (ou '' se vazio)
    # Faz SELECT pelo índice único (engine, model, system_prompt_hash, user_prompt_hash)
    # Se não existir → INSERT com os conteúdos completos
    # Retorna id
    ...

# --- Registro de resultado OCR ---

def record_ocr_result(
    con: sqlite3.Connection,
    *,
    volume_id: str,
    page_num: int,
    image_path: Path,
    engine_version_id: int,
    text_content: str,
    reprocess_reason: str | None = None,   # "initial"|"verify_failed"|"judge_force"|"judge_failed"|"manual"
    duration_ms: float | None = None,
    meta_json: str | None = None,          # JSON com metadados de origem (ver §5.2 do plano)
) -> int:
    # 1. image_hash = sha256_file(image_path)
    # 2. text_hash  = sha256_str(text_content)
    # 3. Idempotência: SELECT WHERE image_hash=? AND engine_version_id=? AND text_hash=?
    #    → se existir, retorna o id existente sem modificar nada
    # 4. BEGIN IMMEDIATE
    # 5. UPDATE ocr_results SET is_current=0, updated_at=... WHERE volume_id=? AND page_num=?
    # 6. INSERT novo com is_current=1, meta_json=meta_json
    # 7. COMMIT
    # Retorna novo id
    ...

# --- Consultas ---

def get_current_result(
    con: sqlite3.Connection,
    volume_id: str,
    page_num: int,
) -> sqlite3.Row | None:
    # SELECT * FROM ocr_results WHERE volume_id=? AND page_num=? AND is_current=1
    ...

def get_history(
    con: sqlite3.Connection,
    volume_id: str,
    page_num: int,
) -> list[sqlite3.Row]:
    # SELECT * FROM ocr_results WHERE volume_id=? AND page_num=?
    # ORDER BY created_at DESC
    # Retorna todos os text_content originais para análise/diff posterior
    ...

def get_engine_version(
    con: sqlite3.Connection,
    engine_version_id: int,
) -> sqlite3.Row | None: ...

# --- Diff dinâmico (não persiste, calculado sob demanda) ---

def compute_diff(
    result_a: sqlite3.Row,
    result_b: sqlite3.Row,
    mode: str = "unified",   # "unified" | "stat_only"
) -> dict:
    """
    Calcula diff entre dois ocr_results a partir dos text_content armazenados.
    Nunca persiste — chame quando precisar comparar.

    Retorna:
    {
        "from_id": int,
        "to_id": int,
        "text_equal": bool,      # text_hash igual → diff vazio garantido sem calcular
        "unified": str,          # diff -u; vazio se textos iguais
        "stat": {
            "insertions": int,   # tokens novos em b não presentes em a
            "deletions": int,    # tokens de a ausentes em b
            "old_len": int,
            "new_len": int,
            "changed_chars": int,
        }
    }
    """
    # Atalho: se text_hash igual, retorna imediatamente sem calcular diff
    if result_a["text_hash"] == result_b["text_hash"]:
        return {"from_id": result_a["id"], "to_id": result_b["id"],
                "text_equal": True, "unified": "", "stat": {}}
    ...

def compute_diff_chain(
    con: sqlite3.Connection,
    volume_id: str,
    page_num: int,
) -> list[dict]:
    """
    Retorna lista de diffs entre versões consecutivas (mais antiga→mais recente).
    Usa text_hash para pular pares com texto idêntico em O(1).
    """
    history = get_history(con, volume_id, page_num)   # DESC → reversed para ASC
    history = list(reversed(history))
    return [
        compute_diff(history[i], history[i+1])
        for i in range(len(history) - 1)
    ]

# --- Utilitários de manutenção ---

def revert_to(
    con: sqlite3.Connection,
    result_id: int,
    txt_dir: Path,
) -> None:
    """Restaura .txt para uma versão anterior e marca is_current=1 nessa versão."""
    ...

def print_history(
    volume_id: str,
    page_num: int,
    db_path: Path = Path("data/ocr_versions.db"),
) -> None:
    """Imprime histórico legível com engine, model, prompt_key, datas e SHA256."""
    ...
```

---

## 5. Integração com `main2.py`

### 5.1 Os três modos de `_ocr_one` e o que cada um produz

Antes de definir o ponto de integração, é necessário mapear exatamente o que acontece em cada modo — porque o registro de versão precisa refletir fielmente o contexto de cada execução:

| Modo | Condição em `_ocr_one` | O que a LLM recebe | `prompt_key` | `reprocess_reason` |
|------|----------------------|-------------------|--------------|-------------------|
| **OCR inicial** | `algorithm in LLM` + `reprocess=False` | Apenas a imagem | `"PROMPT"` | `"initial"` |
| **OCR inicial Tesseract** | `algorithm == "tesseract"` | — (OCR direto) | `"tesseract_direct"` | `"initial"` |
| **Reprocess — só Tesseract** | `reprocess=True` + textos iguais após limpeza | Imagem + rascunho Tesseract | `"PROMPT_VERIFY_TESSERACT"` | `"reprocess"` (insuficiente — ver §5.3) |
| **Reprocess — Tesseract + LLM anterior** | `reprocess=True` + textos divergem | Imagem + rascunho Tesseract + OCR anterior da LLM | `"PROMPT_VERIFY_LLM_VS_TESSERACT"` | `"reprocess"` (insuficiente — ver §5.3) |

> **Gap identificado:** o plano original usava `reprocess_reason="reprocess"` como string genérica para os dois sub-modos de reprocessamento. Eles são fundamentalmente diferentes — um usa só Tesseract como âncora, o outro usa Tesseract + o resultado anterior da LLM como contexto. Isso precisa estar rastreado.

### 5.2 Campo `meta_json` em `ocr_results` — metadados de origem do reprocessamento

Para o modo `reprocess`, a LLM recebe **insumos adicionais** (textos de outras engines) que influenciam diretamente o resultado. Esses insumos devem ser rastreados. A solução é um campo `meta_json TEXT` opcional em `ocr_results`:

```sql
-- Adicionado à tabela ocr_results:
meta_json TEXT   -- JSON com metadados de origem específicos do modo de execução
```

Exemplos de conteúdo por modo:

```jsonc
// Modo: OCR inicial (LLM direto, sem rascunhos)
// meta_json = NULL

// Modo: Reprocess com só Tesseract como âncora
{
  "reprocess_mode": "verify_tesseract",
  "tesseract_text_hash": "sha256...",   // hash do rascunho Tesseract enviado
  "tesseract_lang": "fra+lat+grc+ell+syr"
}

// Modo: Reprocess com Tesseract + LLM anterior
{
  "reprocess_mode": "verify_llm_vs_tesseract",
  "tesseract_text_hash": "sha256...",   // hash do rascunho Tesseract enviado
  "tesseract_lang": "fra+lat+grc+ell+syr",
  "prior_llm_result_id": 42,            // id do ocr_result anterior que foi enviado como contexto
  "prior_llm_text_hash": "sha256..."    // hash do texto anterior enviado (redundante mas conveniente)
}

// Modo: Tesseract direto (sem LLM)
{
  "reprocess_mode": "tesseract_direct",
  "tesseract_lang": "lat",
  "tesseract_config": "--psm 3 --oem 1"
}
```

Isso responde a qualquer pergunta futura do tipo: *"este resultado foi influenciado por qual versão anterior?"* ou *"qual Tesseract exato foi enviado como rascunho?"* — sem precisar reconstruir o contexto a partir de timestamps.

### 5.3 `reprocess_reason` com valores granulares

O campo `reprocess_reason` passa de `bool` para `str` propagada desde o chamador até `_ocr_one`. Valores definidos:

| Valor | Origem |
|-------|--------|
| `"initial"` | Primeira execução, sem `.txt` existente |
| `"verify_failed"` | `verify_page()` retornou `False` → reprocessado por `--verify-fix` |
| `"judge_failed"` | LLM judge retornou fidelidade/usabilidade baixa → reprocessado no loop de `judge_all_parallel` |
| `"judge_force"` | Flag `--judge-force` foi passada explicitamente |
| `"manual"` | Reprocessado por operação manual (ex: `--refresh-pages`) |
| `"legacy_unknown"` | Reservado para backfill de `.txt` existentes sem histórico |

### 5.4 Ponto de integração em `_ocr_one`

A integração acontece **depois** que `page_txt_path.write_text(txt)` é chamado. O `system_prompt_usado` e `user_prompt_usado` já são variáveis locais em `_ocr_one` nos dois sub-modos de reprocess (`prompt_llm_judge` e `user_prompt`):

```python
# NOVO: determinar meta_json de acordo com o modo
if reprocess and algorithm in {"ollama", "openai", "gemini"}:
    prior_result = get_current_result(versions_con, volume_id, page_num)  # antes do INSERT
    if prompt_key == "PROMPT_VERIFY_TESSERACT":
        meta = {
            "reprocess_mode": "verify_tesseract",
            "tesseract_text_hash": sha256_str(tesseractres),
            "tesseract_lang": lang,
        }
    else:  # PROMPT_VERIFY_LLM_VS_TESSERACT
        meta = {
            "reprocess_mode": "verify_llm_vs_tesseract",
            "tesseract_text_hash": sha256_str(tesseractres),
            "tesseract_lang": lang,
            "prior_llm_result_id": prior_result["id"] if prior_result else None,
            "prior_llm_text_hash": prior_result["text_hash"] if prior_result else None,
        }
elif algorithm == "tesseract":
    meta = {
        "reprocess_mode": "tesseract_direct",
        "tesseract_lang": lang,
        "tesseract_config": "--psm 3 --oem 1",
    }
else:
    meta = None

# NOVO: registrar versão no banco (nunca propaga exceção para fora)
try:
    versions_con = open_versions_db()
    ev_id = get_or_create_engine_version(
        versions_con,
        engine=algorithm,
        model=llm_model,
        prompt_key=prompt_key,             # determinado no bloco if/else do modo
        system_prompt=system_prompt_usado,
        user_prompt=user_prompt_template,  # template cru, não o preenchido
    )
    record_ocr_result(
        versions_con,
        volume_id=infer_volume_id(img_path) or "",
        page_num=parse_page_num_from_filename(img_path) or 0,
        image_path=img_path,
        engine_version_id=ev_id,
        text_content=txt,
        reprocess_reason=reprocess_reason,  # string propagada pelo chamador
        duration_ms=(time.time() - start_total) * 1000,
        meta_json=json.dumps(meta) if meta else None,
    )
except Exception as ver_err:
    print(f"[VERSIONS] {img_path.name} — falha ao registrar versão: {ver_err}")
```

### 5.5 Prompt key: rastreamento simbólico (atualizado)

| Cenário em `_ocr_one` | `prompt_key` | `reprocess_reason` |
|----------------------|--------------|-------------------|
| LLM sem rascunho (inicial) | `"PROMPT"` | `"initial"` |
| Tesseract direto (inicial) | `"tesseract_direct"` | `"initial"` |
| Reprocess só com Tesseract | `"PROMPT_VERIFY_TESSERACT"` | propagado pelo chamador |
| Reprocess Tesseract + LLM anterior | `"PROMPT_VERIFY_LLM_VS_TESSERACT"` | propagado pelo chamador |

O `system_prompt_hash` (SHA256) garante que mudanças no texto dos prompts gerem nova `engine_version_id` automaticamente, independentemente do `prompt_key` simbólico.

---

## 6. SHA256 e Estratégia de Deduplicação

### 6.1 Onde cada hash é calculado

| Campo | O que hasheia | Para que serve |
|-------|--------------|----------------|
| `ocr_results.image_hash` | `SHA256(bytes do arquivo de imagem)` | Detecta troca de imagem no disco sem comparar pixels; invalida cache se imagem regenerada |
| `ocr_results.text_hash` | `SHA256(text_content.encode("utf-8"))` | Deduplicação rápida: antes de inserir, verifica se o texto mudou sem ler o conteúdo; atalho no `compute_diff` |
| `ocr_engine_versions.system_prompt_hash` | `SHA256(system_prompt.encode("utf-8"))` | Detecta mudança no prompt mesmo que o `prompt_key` simbólico não mude; garante nova `engine_version` quando o texto do prompt evolui |
| `ocr_engine_versions.user_prompt_hash` | `SHA256(user_prompt.encode("utf-8"))` ou `''` | Idem para user prompt (templates preenchidos como `PROMPT_VERIFY_TESSERACT` têm user prompt variável por página — usar o template cru, não o preenchido) |

> **Atenção para `user_prompt_hash`:** nos prompts com `{tesseract_text}` e `{llm_ocr}`, o hash deve ser calculado sobre o **template** (string com `{...}`), não sobre a versão preenchida com o texto da página. O conteúdo preenchido fica apenas no `text_content` do resultado.

### 6.2 Fluxo de deduplicação em `record_ocr_result`

```
1. sha256_file(image_path)        → image_hash
2. sha256_str(text_content)       → text_hash
3. SELECT id FROM ocr_results
   WHERE image_hash = ?
     AND engine_version_id = ?
     AND text_hash = ?
   → Encontrou? Retorna id existente. FIM (idempotente).
4. BEGIN IMMEDIATE
5. UPDATE is_current=0 para (volume_id, page_num)
6. INSERT nova versão com is_current=1
7. COMMIT
```

### 6.3 Diff dinâmico com atalho de hash

```python
def compute_diff(result_a, result_b, mode="unified") -> dict:
    # Atalho O(1): hashes iguais → textos idênticos → diff vazio garantido
    if result_a["text_hash"] == result_b["text_hash"]:
        return {"text_equal": True, "unified": "", "stat": {}}

    # Só agora lê os text_content (potencialmente grandes)
    old_lines = result_a["text_content"].splitlines(keepends=True)
    new_lines = result_b["text_content"].splitlines(keepends=True)
    unified = "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"v{result_a['id']} ({result_a['created_at']})",
        tofile=f"v{result_b['id']} ({result_b['created_at']})",
    ))
    ...
```

---

## 7. Resiliência Concorrente, Cancelamento e Retomada

Esta é a seção mais crítica para o contexto do projeto: o pipeline roda com `multiprocessing.Pool(fork)`, processos podem ser mortos a qualquer momento (SIGKILL, timeout, OOM), e o pipeline **deve ser retomável de qualquer ponto sem corrupção e sem perder resultados já salvos**.

### 7.1 Garantias do SQLite em WAL

| Propriedade | Como garantir |
|-------------|--------------|
| Múltiplos leitores simultâneos | WAL permite N leitores + 1 escritor sem bloqueio |
| Escritas concorrentes de N workers | `PRAGMA busy_timeout = 30000` — cada writer espera até 30s pela lock de escrita antes de falhar |
| Crash no meio de uma transação | WAL + transações atômicas — rollback automático; o banco nunca fica num estado parcial |
| Fork herda conexão aberta | **Proibido** — conexão DEVE ser aberta dentro do worker (após o fork), nunca herdada |

```python
# connect_versions_db — padrão canônico do projeto
def connect_versions_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0)   # timeout Python-level (antes dos PRAGMAs)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")   # timeout SQLite-level (dentro de operações)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA synchronous = NORMAL")   # seguro com WAL; mais rápido que FULL
    return con

def open_versions_db() -> sqlite3.Connection:
    """Chamada DENTRO do worker, após o fork."""
    con = connect_versions_db(Path("data/ocr_versions.db"))
    init_versions_schema(con)
    return con
```

> **`PRAGMA synchronous = NORMAL`** — com WAL é seguro: garante durabilidade no checkpoint, não em cada commit. Oferece ~2–3× mais throughput que `FULL` sem risco real de corrupção nos padrões de uso do projeto.

### 7.2 Cada escrita em transação explícita com try/except

O `_ocr_one` já tem um `try/except` externo que captura qualquer falha e retorna `""`. A integração com o banco de versões **não deve propagar exceções** — uma falha de escrita no banco de versões não pode derrubar o OCR da página:

```python
# Dentro de _ocr_one, após page_txt_path.write_text(txt):
try:
    _record_version_safe(
        img_path=img_path,
        txt=txt,
        algorithm=algorithm,
        llm_model=llm_model,
        system_prompt=system_prompt_usado,
        prompt_key=prompt_key,
        reprocess_reason=reprocess_reason,
        duration_ms=(time.time() - start_total) * 1000,
    )
except Exception as ver_err:
    # Falha no versionamento nunca mata o worker
    print(f"[VERSIONS] {img_path.name} — falha ao registrar versão: {ver_err}")
```

A função `_record_version_safe` abre sua própria conexão, usa `with con:` (autocommit + rollback automático), e fecha ao terminar.

### 7.3 Transação `BEGIN IMMEDIATE` para a sequência "desativar + inserir"

A operação `record_ocr_result` envolve 2 passos que precisam ser atômicos:

1. `UPDATE ocr_results SET is_current=0` para a página
2. `INSERT` do novo resultado com `is_current=1`

Se o processo morrer entre os passos 1 e 2, a página ficaria sem `is_current=1`. Por isso, usa-se `BEGIN IMMEDIATE` (que adquire lock de escrita imediatamente, evitando deadlock com outros writers em espera):

```python
def record_ocr_result(con, *, ...) -> int:
    image_hash = sha256_file(image_path)
    text_hash  = sha256_str(text_content)

    # Idempotência: mesma imagem + mesmo engine + mesmo texto → não duplica
    existing = con.execute(
        "SELECT id FROM ocr_results WHERE image_hash=? AND engine_version_id=? AND text_hash=?",
        (image_hash, engine_version_id, text_hash),
    ).fetchone()
    if existing:
        return existing["id"]

    # Transação atômica: desativar anterior + inserir novo
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "UPDATE ocr_results SET is_current=0, updated_at=datetime('now') WHERE volume_id=? AND page_num=?",
            (volume_id, page_num),
        )
        cur = con.execute(
            """INSERT INTO ocr_results
               (volume_id, page_num, image_path, image_hash, engine_version_id,
                text_content, text_hash, is_current, reprocess_reason, duration_ms)
               VALUES (?,?,?,?,?,?,?,1,?,?)""",
            (volume_id, page_num, str(image_path), image_hash, engine_version_id,
             text_content, text_hash, reprocess_reason, duration_ms),
        )
        new_id = cur.lastrowid
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return new_id
```

> **Sem INSERT de diff aqui:** a transação é mínima — apenas 1 UPDATE + 1 INSERT. Diffs são calculados dinamicamente via `compute_diff()` fora de qualquer transação, quando o consumidor precisar.

### 7.4 Retomada após cancelamento (idempotência de re-run)

O design garante que re-executar o pipeline sobre páginas já processadas é seguro:

| Cenário de cancelamento | Comportamento no re-run |
|------------------------|------------------------|
| Processo morto **antes** do `write_text` | `.txt` não existe → OCR refeito normalmente |
| Processo morto **depois** do `write_text`, **antes** do `record_ocr_result` | `.txt` existe → `_ocr_one` usa o `.txt` salvo (skip); próximo re-run com `--reprocess` registra o texto atual |
| Processo morto **no meio** do `record_ocr_result` (entre UPDATE e INSERT) | `BEGIN IMMEDIATE` → rollback automático; banco fica com versão anterior ainda `is_current=1`; próximo re-run registra normalmente |
| Banco com `is_current=0` sem nenhum `is_current=1` | Só possível por bug externo; `get_current_result` retorna `None` → tratado como "sem versão registrada" |
| Banco corrompido por OOM/power-off | WAL + `PRAGMA synchronous=NORMAL` → rollback do frame não commitado no próximo `OPEN` |

### 7.5 Tolerância a resumo de qualquer item OCR

O pipeline de resumos/agentes consome os `.txt` via `patristica_pipeline/ingest.py`. O design do `ocr_versions.db` é intencional em **não bloquear o ingest**:

- A coluna `ocr_result_id` na tabela `pages` (item 15.2 do plano) é **nullable** (`INTEGER` sem `NOT NULL`) — o ingest pode rodar sem o banco de versões e simplesmente deixar `ocr_result_id = NULL`
- O `ingest` pode resumir **a partir de qualquer versão** — basta consultar `get_current_result(volume_id, page_num)` para obter o texto que corresponde ao `is_current=1`, ou ignorar o banco de versões completamente e usar o `.txt` do filesystem como hoje
- Não há lock compartilhado entre `ocr_versions.db` e `patristica_text.db` — são bancos independentes; o ingest nunca bloqueia o OCR e vice-versa

### 7.6 `PRAGMA synchronous` — tabela de trade-offs

| Valor | Durabilidade | Throughput | Recomendação |
|-------|-------------|-----------|--------------|
| `FULL` | Máxima (fsync em cada commit) | Baixo | Não necessário com WAL |
| `NORMAL` | Alta (fsync no checkpoint) | **2–3× FULL** | ✅ **Usar aqui** |
| `OFF` | Nenhuma (crash pode corromper) | Máximo | ❌ Proibido |

Com WAL + `NORMAL`, a única janela de perda é um crash do SO **durante o checkpoint** (que acontece em background a cada ~1000 páginas). Nesse caso perde-se no máximo o último checkpoint, não páginas individuais.

---

## 8. Utilitários de consulta (CLI / exploração)

Adicionar ao módulo `ocr_versions_db.py` funções de conveniência:

```python
def print_history(volume_id: str, page_num: int, db_path: Path = Path("data/ocr_versions.db")) -> None:
    """Imprime histórico de versões de uma página."""
    ...

def export_diff(
    from_result_id: int,
    to_result_id: int,
    db_path: Path = Path("data/ocr_versions.db"),
) -> str:
    """Retorna o diff unified entre dois resultados."""
    ...

def revert_to(
    result_id: int,
    txt_dir: Path,
    db_path: Path = Path("data/ocr_versions.db"),
) -> None:
    """Restaura o arquivo .txt para uma versão anterior e atualiza is_current."""
    ...
```

---

## 9. Scripts de migração (backfill e re-ingestão)

### 9.1 Backfill de `.txt` existentes — `scripts/backfill_versions.py`

Para popular o banco com os `.txt` já existentes em disco sem saber exatamente qual engine os gerou:

```python
# scripts/backfill_versions.py
# Lê todos os *.txt em teste/**/text/ e os registra com engine="unknown"
# image_hash é calculado a partir da imagem correspondente (se existir)
# prompt_key = "legacy_unknown"
# Usa os mesmos algoritmos de parsing de main2.py (parse_page_num, infer_volume_id)
```

**Implementado e validado:** 693 páginas do PL001 importadas em 1.3s, idempotente no re-run.

### 9.2 Re-ingestão do `tesseract_cache` — `scripts/reingest_tesseract_cache.py`

O `tesseract.db` (tabela `tesseract_cache`) guarda resultados OCR intermediários do Tesseract
que são usados nos fluxos de reprocess/comparação da LLM, mas nunca foram versionados.
O plano original não previa essa fonte de dados.

**Gap identificado:** quando `_ocr_one(reprocess=True)` executa, ele busca o `tesseractres` do cache
e o envia como rascunho para a LLM. Esse texto intermediário influencia diretamente o resultado final,
mas não ficava rastreado no `ocr_versions.db`. Sem essa re-ingestão:

- Diffs entre "o que o Tesseract produziu" e "o que a LLM refinou" seriam impossíveis
- O campo `meta_json.tesseract_text_hash` não teria correspondência no banco de versões
- A cadeia de proveniência ficaria incompleta

```python
# scripts/reingest_tesseract_cache.py
# Lê todas as entradas do tesseract_cache (image_hash, lang, result, imgpath)
# Registra cada uma como engine="tesseract", model=lang, prompt_key="tesseract_direct"
# image_hash já está no cache (evita re-leitura do arquivo de imagem)
# Resolve volume/page_num via parsing do imgpath (mesmos algoritmos de main2.py)
```

**Implementado e validado:** 48.874 entradas importadas, zero erros, 100% mapeável.

---

## 10. Arquivos a criar/modificar

| Arquivo | Ação | Descrição |
|---|---|---|
| `ocr_versions_db.py` | **Criado ✅** | Módulo principal da feature |
| `data/ocr_versions.db` | **Auto-criado ✅** na 1ª execução | SQLite de versões (308 MB com dados reais) |
| `main2.py` | **Modificar** (P1) | Integrar `record_ocr_result` em `_ocr_one` |
| `scripts/backfill_versions.py` | **Criado ✅** | Migração de `.txt` existentes |
| `scripts/reingest_tesseract_cache.py` | **Criado ✅** | Re-ingestão de resultados do `tesseract_cache` |
| `tests/test_ocr_versions_db.py` | **Criado ✅** | 37 testes unitários (hash, engine, record, diff, concorrência, revert) |

---

## 11. Testes unitários planejados

```python
# tests/test_ocr_versions_db.py

# --- Testes de hash ---

def test_sha256_str_is_deterministic():
    # Mesmo texto → mesmo hash sempre

def test_sha256_str_differs_for_different_texts():
    # Textos diferentes → hashes diferentes

def test_sha256_file_matches_content_hash():
    # sha256_file(path) == sha256_str(path.read_text())  para arquivo texto

# --- Testes de engine_version ---

def test_create_engine_version_idempotent():
    # Mesma combinação engine+model+prompts → mesmo id em chamadas repetidas

def test_engine_version_armazena_prompt_completo():
    # system_prompt_content e user_prompt_content ficam gravados completos no banco

def test_engine_version_novo_quando_prompt_muda():
    # Mesmo engine+model mas system_prompt diferente → novo id

def test_engine_version_hash_usa_template_nao_preenchido():
    # user_prompt com {placeholder} → hash calculado sobre o template, não o valor renderizado

# --- Testes de record_ocr_result ---

def test_record_ocr_result_marks_previous_as_not_current():
    # Após 2 chamadas para a mesma página, apenas a última é is_current=1

def test_idempotency_same_image_engine_text_hash():
    # image_hash + engine_version_id + text_hash iguais → retorna id existente sem duplicar

def test_text_content_stored_verbatim():
    # text_content recuperado do banco é byte-for-byte igual ao inserido

def test_get_history_returns_all_versions_ordered():
    # Histórico retorna todas as versões em ordem DESC por created_at

def test_get_current_returns_only_is_current_one():
    # Após 3 versões, get_current retorna apenas a mais recente

# --- Testes de diff dinâmico ---

def test_compute_diff_equal_texts_uses_hash_shortcut():
    # Quando text_hash é igual → text_equal=True e unified='' sem chamar difflib

def test_compute_diff_produces_valid_unified_diff():
    # unified diff aplicável via patch para reconstruir o texto novo a partir do antigo

def test_compute_diff_stat_counts_tokens_correctly():
    # insertions + deletions correspondem aos tokens únicos de cada versão

def test_compute_diff_chain_two_versions():
    # 3 versões → chain tem 2 entradas; chain[0] vai de v1→v2, chain[1] vai de v2→v3

def test_compute_diff_chain_skips_identical_consecutive():
    # Se v2.text_hash == v3.text_hash → diff correspondente tem text_equal=True

# --- Testes de resiliência concorrente ---

def test_concurrent_writes_same_page_no_duplicate_is_current():
    # N threads escrevendo para a mesma (volume_id, page_num) simultaneamente
    # → ao final, exatamente 1 registro tem is_current=1

def test_record_is_safe_after_simulated_mid_write_crash():
    # Simula interrupção entre UPDATE is_current=0 e INSERT novo resultado
    # → banco deve estar em estado consistente (versão anterior ainda is_current=1)

# --- Testes de retomada ---

def test_rerun_after_txt_written_but_no_db_record():
    # .txt existe no disco, mas ocr_versions não tem registro → record_ocr_result insere normalmente

def test_multiple_reruns_produce_growing_history():
    # 3 chamadas com textos diferentes → get_history retorna 3 versões

# --- Testes de manutenção ---

def test_revert_to_updates_is_current_and_restores_txt():
    # Após revert_to(old_id, txt_dir), a versão antiga tem is_current=1 e o .txt foi restaurado

def test_ingest_runs_without_versions_db():
    # ingest_ocr_to_text_db funciona com ocr_result_id=NULL quando db não existe

def test_ingest_populates_ocr_result_id_when_db_exists():
    # Quando db existe e tem is_current=1 para a página, ingest preenche ocr_result_id
```

---

## 12. Fluxo de dados completo (após implementação)

```
PDF
 └─► pages_to_images()
      └─► _ocr_one(img_path, ...)
           ├─► LLM/Tesseract → txt
           ├─► page_txt_path.write_text(txt)      ← comportamento atual (mantido)
           └─► [try] _record_version_safe(...)     ← NOVO (falha aqui não mata o worker)
                ├─► sha256_file(img_path)          → image_hash
                ├─► sha256_str(txt)                → text_hash
                ├─► get_or_create_engine_version() → engine_version_id
                │    ├─► sha256_str(system_prompt) → system_prompt_hash
                │    └─► sha256_str(user_prompt)   → user_prompt_hash
                └─► BEGIN IMMEDIATE
                     ├─► UPDATE is_current=0 (versões anteriores da página)
                     ├─► INSERT ocr_results (is_current=1, text_content inline)
                     └─► COMMIT

Diff (sob demanda, fora do pipeline):
 └─► compute_diff(result_a, result_b)
      ├─► text_hash igual? → retorna {text_equal: True} em O(1)
      └─► difflib.unified_diff(a.text_content, b.text_content)
```

---

## 13. Prioridade de implementação

1. **[P0]** Criar `ocr_versions_db.py` com esquema + `record_ocr_result` + `get_or_create_engine_version`
2. **[P0]** Testes unitários básicos (idempotência, is_current, diffs) ✅
3. **[P1]** Integração em `_ocr_one` no `main2.py` ✅
4. **[P1]** Integração em `ocr_images_to_text` (path legado sequencial) ✅
5. **[P1]** Alterar `reprocess: bool` → `reprocess_reason: str | None` + propagar nos callers ✅
6. **[P1]** Versionar resultado Tesseract intermediário no reprocess (`tesseract_intermediate`) ✅
7. **[P2]** Script de backfill `scripts/backfill_versions.py` ✅
8. **[P2]** Script de re-ingestão `scripts/reingest_tesseract_cache.py` ✅
9. **[P3]** Utilitários de consulta/revert na CLI

---

## 14. Decisões confirmadas

| # | Questão | Decisão |
|---|---------|---------|
| 1 | Guardar `text_content` inline no SQLite ou só o hash + apontar para o `.txt`? | ✅ **Inline** — portável, sem dependência de FS, diff direto |
| 2 | Banco separado ou unificar com `ocr_eval.db`? | ✅ **Separado** (`ocr_versions.db`) — separation of concerns |
| 3 | Script de backfill para `.txt` existentes? | ✅ **Sim** — `scripts/backfill_versions.py` |

---

## 15. Pontos cegos identificados (após análise completa do projeto)

### 15.1 `patristica_pipeline/ingest.py` — segundo consumidor dos `.txt`

O módulo `ingest_ocr_to_text_db()` em `patristica_pipeline/ingest.py` lê os `.txt` do filesystem e os indexa em `data/patristica_text.db` (tabelas `pages` + `chunks` com FTS5). Ele **não sabe** se o texto que está lendo é a versão mais recente ou uma versão sobrescrita.

**Gap:** quando o `ingest` roda após um reprocessamento, ele silenciosamente substitui o texto indexado sem registrar que houve mudança.

**Solução complementar:** o `ingest` deve, opcionalmente, consultar `ocr_versions.db` para pegar o `ocr_result_id` da versão corrente e armazená-lo nas tabelas `pages` como coluna `ocr_result_id INTEGER REFERENCES ...` (FK para o banco de versões). Isso fecha o elo entre o texto indexado e sua proveniência.

### 15.2 `patristica_pipeline/db.py` — tabela `pages` sem coluna de proveniência

A tabela `pages` em `patristica_text.db` tem `text_raw` e `text_norm`, mas **nenhuma referência** à engine ou modelo que gerou o texto. Se o mesmo volume for reingestionado com um OCR de outra engine, não há como distinguir.

**Solução:** adicionar coluna `ocr_result_id INTEGER` opcional à tabela `pages` (populada pelo backfill e pelo ingest futuro).

### 15.3 ~~Dois `write_text` distintos em `main2.py`~~ ✅ RESOLVIDO

Ambos os pontos de escrita (L2024 em `_ocr_one` e L1773 em `ocr_images_to_text`) agora registram versão no `ocr_versions.db`.

### 15.4 ~~`reprocess_reason` precisa de granularidade maior~~ ✅ RESOLVIDO

O parâmetro `reprocess: bool` foi substituído por `reprocess_reason: str | None` em `_ocr_one` e `ocr_images_to_text_parallel`. Os callers no `main()` agora propagam strings descritivas:
- `"judge_reprocess"` — reprocessado após falha no LLM judge
- `"verify_failed"` — reprocessado após `verify_page` falhar
- `"tesseract_intermediate"` — Tesseract cache registrado como versão intermediária durante reprocess
- `None` — execução inicial (sem reprocessamento)

### 15.5 `image_hash` detecta substituição de imagem — mas não da engine Tesseract

O campo `image_hash` (sha256 da imagem) garante que, se a imagem mudar, o resultado é tratado como nova versão. Porém, o `tesseract_cache` usa o mesmo hash (`image_hash + lang`) como chave de cache. Se a imagem não mudar mas o **modelo Tesseract traineddata mudar** (ex.: `hye-calfa-n.traineddata` que existe na raiz do projeto), o cache ficará obsoleto.

**Solução:** adicionar `traineddata_hash` como campo opcional em `ocr_engine_versions` para versões Tesseract — ou ao menos documentar que o `tesseract_cache` deve ser purgado manualmente ao trocar traineddata.

---

## 16. Tabela de rastreabilidade completa (após implementação)

| Onde | O que rastreia | Rastreia proveniência OCR? |
|------|----------------|---------------------------|
| `.txt` no filesystem | Conteúdo atual da página | ❌ Nenhuma |
| `data/tesseract.db` | Cache de resultados Tesseract por `image_hash+lang` | Parcial (lang, não model) |
| `data/ocr_eval.db` | Julgamentos de qualidade (fidelidade/usabilidade) | Parcial (provider, model) |
| `data/patristica_text.db` | Texto indexado para busca e pipeline de resumos | ❌ Nenhuma |
| `data/ocr_versions.db` ← **NOVO** | **Todas as versões de OCR com engine, model, prompt, diff, timestamps** | ✅ Completa |

A lacuna crítica é a ausência de elo entre `patristica_text.db` (que é consumido pelo pipeline de resumos/agentes) e a proveniência do OCR. O `ocr_versions.db` fecha essa lacuna para o pipeline de OCR, mas a integração com `ingest.py` (item 15.1) é necessária para fechar a cadeia completa até os resumos.
