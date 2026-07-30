# Pipeline “dividir para analisar”

O extrator alfabético possui um modo operacional por etapas. Ele mantém trabalho
parcial em `data/alphabetical_analysis.db` e só escreve no banco canônico durante
`assemble`.

## Etapas

```bash
python scripts/run_alphabetical_index_extraction.py discover --volume-id PL001
python scripts/run_alphabetical_index_extraction.py extract --volume-id PL001
python scripts/run_alphabetical_index_extraction.py locate --volume-id PL001
python scripts/run_alphabetical_index_extraction.py verify --volume-id PL001
python scripts/run_alphabetical_index_extraction.py assemble --volume-id PL001
```

`run` executa as cinco etapas em sequência. Cada uma também aceita seleção por
`--all-volumes`, `--blob` e `--limit`.

- `discover` usa o prefilter determinístico como pista, entrega a topologia ao
  agente e registra no banco segmentos `owned`, `boundary`, `context` e
  `uncertain`, inclusive fronteiras no meio de um arquivo.
- `extract` consome o manifesto segmentado, produz fragmentos semânticos v2 com
  `source_span`/coverage e importa entradas e ocorrências no banco operacional.
- `locate` usa paginação editorial, o banco determinístico de citações e
  fallbacks por ocorrência.
- `verify` envia somente pendências e ambiguidades aos agentes.
- `assemble` valida, escreve o payload canônico e o importa em
  `alphabetical_indices.db`.

O default é retomar fingerprints completos. Use `--force` para regenerar a
etapa selecionada e marcar etapas posteriores como obsoletas.

## Modelo operacional

Uma linha lógica do índice permanece uma `analysis_entries`. Cada número
editorial impresso vira uma `analysis_occurrences`. Portanto:

```text
Matth. I, 1 — 120, 125, 130
```

produz uma entrada e três ocorrências. Um intervalo impresso como `120-135`
permanece uma ocorrência de intervalo e não é expandido.

O agente pode ler arquivos vizinhos como contexto, mas deve atribuir a entrada
ao `source_span` onde começa seu primeiro bloco ou linha recuperável. O raw OCR
continua preservado. Prompt, contrato interpretativo, glossário e schemas fazem
parte do fingerprint; checkpoints antigos continuam legíveis, mas novas
execuções escrevem os artefatos v2.

As referências compartilhadas são:

- `docs/contrato_interpretacao_indices.md`
- `docs/glossario_notacao_editorial_indices.md`
- `data/editorial_notation_glossary.json`
- `schemas/alphabetical_discovery_manifest.schema.json`
- `schemas/alphabetical_semantic_fragment.schema.json`

## Inspeção e sementes

```bash
python scripts/run_alphabetical_index_extraction.py status --volume-id PL001
python scripts/run_alphabetical_index_extraction.py review \
  --volume-id PL001 --status ambiguous
python scripts/run_alphabetical_index_extraction.py review \
  --text '"Matthaeus"'
python scripts/run_alphabetical_index_extraction.py export-seeds \
  --kind scripture --output data/pl001_scripture_seeds.json
python scripts/run_alphabetical_index_extraction.py export-seeds \
  --kind name --output data/pl001_name_seeds.json
```

O FTS5 cobre lema, entrada, contexto e referência bíblica. As sementes de nomes
são um contrato para um scanner corporal futuro; esta versão não cria um novo
banco persistente de ocorrências onomásticas.

## Shim bíblico

`--scripture-db` seleciona o banco determinístico. O helper só é considerado
atual quando o volume, detector, perfil, conjunto de arquivos, tamanhos e mtimes
coincidem com o corpus. A consulta restringe primeiro a página editorial e,
quando disponível, o fascículo PO; livro, capítulo e versículo confirmam o
conteúdo depois.

Uma ocorrência é resolvida automaticamente apenas quando há um único candidato
com evidência editorial e bíblica independentes. Ausência ou conflito continua
para `verify`. Hits dentro do próprio índice são excluídos.
