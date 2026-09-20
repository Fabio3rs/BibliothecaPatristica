#!/usr/bin/env python3
"""Teste stream direto com o mesmo prompt exato de keywords_serial para PG002/p630."""
import json
import sqlite3
import sys
import time
import urllib.request

DB = "data/patristica_resumos.db"
MODEL = "qwen3:8b"
BASE_URL = "http://localhost:11434"

SYSTEM_PROMPT = """\
Você é um especialista em Patrística (Patrologia Graeca e Patrologia Latina) \
e em catalogação bibliográfica.

Sua tarefa é extrair **keywords** (palavras-chave) do conteúdo fornecido.

Regras:
- Extraia entre 5 e 20 keywords, ordenadas por relevância (mais relevante primeiro).
- Inclua nomes próprios (autores, santos, personagens bíblicos), obras citadas, \
temas teológicos, conceitos filosóficos e termos técnicos.
- Mantenha termos em latim/grego quando forem nomes próprios ou termos técnicos \
consagrados (ex: "Epistola ad Corinthios", "homilia", "Trinitas").
- Traduza conceitos genéricos para português do Brasil.
- NÃO inclua palavras genéricas demais (ex: "texto", "página", "volume").
- NÃO invente keywords que não estejam no conteúdo.

Formato de resposta (siga rigorosamente):

Keywords:
1. keyword_um
2. keyword_dois
3. keyword_três
...

Categorias (agrupe as keywords acima):
- Pessoas: ...
- Obras: ...
- Temas: ...
- Termos técnicos: ...
"""

# Busca a página do DB
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
row = con.execute(
    "SELECT * FROM resumos WHERE documento='PG002' AND pagina_num=630"
).fetchone()
con.close()

if not row:
    print("Página não encontrada!")
    sys.exit(1)

row = dict(row)

def replace_linebreak(text: str) -> str:
    return text.replace("-\n", " ")

# Monta user prompt igual ao keywords_serial --source tudo
parts = []
parts.append(f"Documento: PG002  |  Página: {row['pagina_num']}\n")
parts.append("<texto_original>")
parts.append(replace_linebreak(row["pagina_texto"]))
parts.append("</texto_original>\n")
parts.append("<resumo_da_pagina>")
parts.append(row["resumo_pagina"])
parts.append("</resumo_da_pagina>\n")
parts.append("<resumo_global>")
parts.append(row["resumo_global"])
parts.append("</resumo_global>\n")
parts.append(
    "Extraia as keywords do conteúdo acima seguindo rigorosamente "
    "o formato de resposta especificado."
)
user_prompt = "\n".join(parts)

print(f"User prompt: {len(user_prompt)} chars")
print(f"Modelo: {MODEL}")
print(f"Stream: True")
print("=" * 60)

payload = {
    "model": MODEL,
    "stream": True,
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ],
    "options": {
        "temperature": 0.2,
        "num_ctx": 16384,
        "repeat_penalty": 2.0,
        "repeat_last_n": 100,
    },
    "think": False,
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    f"{BASE_URL}/api/chat",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)

t0 = time.time()
token_count = 0
first_token_time = None

try:
    with urllib.request.urlopen(req, timeout=300) as resp:
        for line in resp:
            chunk = json.loads(line.decode("utf-8"))

            # Mostra stats do prompt eval
            if chunk.get("prompt_eval_count"):
                elapsed = time.time() - t0
                print(f"\n[prompt eval: {chunk['prompt_eval_count']} tokens em {elapsed:.1f}s]")

            content = chunk.get("message", {}).get("content", "")
            if content:
                if first_token_time is None:
                    first_token_time = time.time()
                    ttft = first_token_time - t0
                    print(f"[TTFT: {ttft:.1f}s]\n")
                token_count += 1
                sys.stdout.write(content)
                sys.stdout.flush()

            if chunk.get("done"):
                elapsed = time.time() - t0
                eval_count = chunk.get("eval_count", 0)
                eval_duration = chunk.get("eval_duration", 0)
                prompt_eval_count = chunk.get("prompt_eval_count", 0)
                prompt_eval_duration = chunk.get("prompt_eval_duration", 0)

                tps = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0
                prompt_tps = prompt_eval_count / (prompt_eval_duration / 1e9) if prompt_eval_duration > 0 else 0

                print(f"\n\n{'=' * 60}")
                print(f"Tempo total: {elapsed:.1f}s")
                print(f"Prompt: {prompt_eval_count} tokens ({prompt_tps:.0f} tok/s)")
                print(f"Geração: {eval_count} tokens ({tps:.1f} tok/s)")
                print(f"Chunks recebidos: {token_count}")
                break

except KeyboardInterrupt:
    elapsed = time.time() - t0
    print(f"\n\nInterrompido após {elapsed:.1f}s  ({token_count} chunks)")
except Exception as e:
    elapsed = time.time() - t0
    print(f"\n\nErro após {elapsed:.1f}s: {e}")
