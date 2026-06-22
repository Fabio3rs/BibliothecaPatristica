import requests

DEFAULT_MODEL = "qwen3-embedding:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/embed"

url = "http://localhost:11434/api/embed"


def get_detailed_instruct(task_description: str, query: str) -> str:
    return f"Instruct: {task_description}\nQuery: {query}"


# Instruct: Retrieve passages relevant to a keyword from patristic and theological texts. The keyword may be a person, work, theological theme, philosophical concept, or technical term.
# Query: <KEYWORD>

INSTRUCT = "Retrieve passages relevant to a keyword from patristic and theological texts. The keyword may be a person, work, theological theme, philosophical concept, or technical term."


def build_instruct_query(keyword_query: str) -> str:
    return get_detailed_instruct(
        task_description=INSTRUCT,
        query=keyword_query,
    )


# # Exemplo de uso fazendo batch
# payload = {
#     "model": "qwen3-embedding:8b",
#     "input": [
#         build_instruct_query("Athanasius"),
#         build_instruct_query("Patrologia Latina"),
#         build_instruct_query("homoousios"),
#     ],
# }

# resp = requests.post(url, json=payload, timeout=300)
# resp.raise_for_status()

# data = resp.json()
# embeddings = data["embeddings"]

# print(len(embeddings))  # número de vetores
# print(len(embeddings[0]))  # dimensão


def embed_queries(
    keywords: list[str],
    model: str = DEFAULT_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> list[list[float]]:
    payload = {
        "model": model,
        "input": [build_instruct_query(k) for k in keywords],
    }
    resp = requests.post(ollama_url, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    return data["embeddings"]


def embed_documents(
    texts: list[str],
    model: str = DEFAULT_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> list[list[float]]:
    """
    Gera embeddings para documentos/chunks de texto (sem instruct).
    Retorna lista de vetores float.
    """
    payload = {
        "model": model,
        "input": texts,
    }
    resp = requests.post(ollama_url, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings")
    if embeddings is None:
        raise RuntimeError("Resposta de embedding sem campo 'embeddings'")
    return embeddings


"""
time python embedding_playground.py 
3
4096

real	0m0,279s
user	0m0,092s
sys	0m0,018s


Está **correto**. Bem correto, inclusive.

Seu código já validou os três pontos que importam:

* a chamada local ao Ollama via `POST /api/embed` está certa; a documentação oficial usa esse endpoint para embeddings. ([Ollama][1])
* o formato `Instruct: ...\nQuery: ...` para a **query** está alinhado com o uso recomendado da família Qwen3-Embedding. ([Hugging Face][2])
* o vetor com tamanho **4096** bate com a dimensão máxima oficial do **Qwen3-Embedding-8B**. ([Hugging Face][3])

Então o veredito é: **sim, esse é um jeito correto de chamar o Ollama em localhost com `python requests` para esse modelo**.

Eu só faria alguns ajustes pequenos, do tipo “evitar futuras maldades estatísticas”.

## 1. Seu helper está bom; eu só deixaria mais explícito

```python
import requests

OLLAMA_URL = "http://localhost:11434/api/embed"
MODEL = "qwen3-embedding:8b"

INSTRUCT = (
    "Retrieve passages relevant to a keyword from patristic and theological texts. "
    "The keyword may be a person, work, theological theme, philosophical concept, or technical term."
)

def build_instruct_query(keyword: str) -> str:
    return f"Instruct: {INSTRUCT}\nQuery: {keyword}"

def embed_queries(keywords: list[str]) -> list[list[float]]:
    payload = {
        "model": MODEL,
        "input": [build_instruct_query(k) for k in keywords],
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    return data["embeddings"]
```

## 2. Para documentos, eu **não** usaria o instruct

Para indexar páginas/chunks do corpus, eu mandaria o texto puro:

```python
def embed_documents(texts: list[str]) -> list[list[float]]:
    payload = {
        "model": MODEL,
        "input": texts,
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["embeddings"]
```

Esse arranjo “query com instruct, documento sem instruct” é o que você quer para retrieval com esse modelo. ([Hugging Face][2])

## 3. O tempo está muito bom

`3 embeddings -> 0.279s` localmente está bem decente para teste rápido. Nada de reclamar para os deuses da inferência hoje.

## 4. Cuidado com um detalhe específico do 8B

O model card oficial diz que o 8B **suporta dimensões configuráveis de 32 a 4096**. ([Hugging Face][3])

Mas há discussões públicas relatando que, em alguns setups, ele acaba sempre retornando **4096**. ([Hugging Face][4])

Então, no seu ambiente atual, assuma pragmaticamente:

**você está recebendo 4096 e pronto.**
Não modele o banco esperando que a dimensão menor funcione até você testar isso explicitamente no Ollama.

## 5. Para gravar no SQLite, o próximo passo bonito é empacotar em float32

Ollama te devolve uma lista Python de floats. Para banco, eu converteria para `float32` e gravaria como `BLOB`.

Exemplo:

```python
import struct

def floats_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)
```

Ou com NumPy, que costuma ser mais limpo para volume maior:

```python
import numpy as np

def floats_to_blob(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()
```

E na tabela, eu guardaria pelo menos:

* `keyword_text`
* `embedding BLOB`
* `embedding_dim`
* `embedding_model`
* `prompt_version`

## 6. Eu adicionaria um check defensivo

```python
embeddings = embed_queries(["Athanasius", "Patrologia Latina", "homoousios"])

for i, vec in enumerate(embeddings):
    if len(vec) != 4096:
        raise ValueError(f"Embedding {i} veio com dimensão inesperada: {len(vec)}")
```

Esse tipo de paranoia saudável evita corrupção silenciosa no banco.

## Em resumo

Seu código está no trilho certo.
A forma correta, para o seu caso, é exatamente essa:

* `POST http://localhost:11434/api/embed`
* `model: "qwen3-embedding:8b"`
* `input: [...]`
* query formatada como
  `Instruct: ...\nQuery: ...`
* documentos indexados sem instruct
* assumir **4096 dims** até prova em contrário

A próxima peça do quebra-cabeça é empacotar isso em BLOB e popular o SQLite sem transformar o corpus patrístico numa sopa binária triste.

[1]: https://docs.ollama.com/capabilities/embeddings?utm_source=chatgpt.com "Embeddings"
[2]: https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/9782d53e3666059d2a989a8868adccebf465d5f0/README.md?utm_source=chatgpt.com "README.md · Qwen/Qwen3-Embedding-8B at ..."
[3]: https://huggingface.co/Qwen/Qwen3-Embedding-8B?utm_source=chatgpt.com "Qwen/Qwen3-Embedding-8B"
[4]: https://huggingface.co/Qwen/Qwen3-Embedding-8B/discussions?utm_source=chatgpt.com "Qwen/Qwen3-Embedding-8B · Discussions"

"""
