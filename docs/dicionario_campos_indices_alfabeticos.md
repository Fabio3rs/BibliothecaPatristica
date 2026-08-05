# Dicionário de campos dos índices alfabéticos

Versão: 1 — banco SQLite v8

Este documento é normativo para nomes e significados espaciais. Ele existe para impedir que um
número editorial, um arquivo OCR e a referência impressa no índice sejam tratados como se fossem
a mesma coisa.

## 1. Vocabulário espacial

### Arquivo OCR

Um arquivo OCR é um `.txt` localizado sob `alphabetical_volumes.source_root`. Ele é uma unidade
física de armazenamento, não uma página editorial. Um arquivo pode conter uma página, duas páginas
enfrentadas, parte de uma página ou blocos de regiões diferentes.

Campos canônicos que apontam para essa unidade usam `ocr_file` ou `ocr_file_path` no nome.
O sufixo numérico do nome do arquivo é somente uma sequência física e nunca prova paginação
editorial.

### Página editorial do índice

É o número impresso na página que contém a própria seção ou entrada do índice. Quando inferido,
ele vem de cabeçalhos, páginas vizinhas e sequência editorial. Não é a página citada pelo índice.

Campos canônicos usam o prefixo `index_editorial_page_`.

### Página editorial citada

É o locator impresso na entrada do índice, por exemplo `515` em `Deus ... 515`. Ela aponta para
material no corpo da obra, mas ainda não identifica por si só um arquivo OCR.

Campos canônicos usam o prefixo `cited_editorial_`.

### Alvo OCR resolvido

É o arquivo OCR que a etapa de localização considerou conter o material indicado pela referência
editorial citada. Ele só é navegável quando `locator_status='resolved'` e possui evidência
independente suficiente.

Campos canônicos usam `resolved_target_ocr_file`.

### Source span

É o trecho de OCR de onde a seção ou entrada do índice foi extraída. Seus limites de linha são
inclusivos. O source span aponta para o texto do índice, não para o texto corporal citado.

## 2. Campos canônicos do banco v8

### `alphabetical_sections`

| Campo | Significado |
| --- | --- |
| `index_editorial_page_start` / `index_editorial_page_end` | Limites editoriais impressos da própria seção de índice, quando conhecidos. |
| `index_ocr_file_start` / `index_ocr_file_end` | Primeiro e último arquivo OCR que contêm material possuído da seção. Não são números de página. |
| `pipeline_owner` | Pipeline responsável pelo conteúdo: `alphabetical` ou `general`. |
| `alphabetical_role` | `owned_section` para conteúdo extraído ou `stop_boundary` para fronteira. Novas fronteiras ficam em `alphabetical_boundaries`, não como seção. |
| `material_reference_mode` | Relação da seção com o corpo: `remissive`, `parallel`, `source_only` ou `boundary_only`. |
| `scripture_mode` | Função bíblica ortogonal à forma editorial da seção. |
| `taxonomy_source` | `explicit` quando as quatro dimensões vieram do payload; `legacy_inferred` quando a migração precisou inferi-las. |

### `alphabetical_entries`

| Campo | Significado |
| --- | --- |
| `index_editorial_page_estimate` | Página editorial estimada na qual a entrada do índice foi impressa. Não é a página citada pela entrada. |
| `index_section_start_ocr_file` | Arquivo OCR onde começa a seção proprietária. É contexto de navegação, não o alvo da referência. |
| `index_entry_source_ocr_file` | Arquivo OCR que contém o texto-fonte da entrada do índice. O `source_span` é mais preciso e deve ser preferido. |
| `resolved_target_ocr_file` | Melhor alvo corporal derivado das referências resolvidas da entrada. É cache de navegação, não evidência primária. |

### `alphabetical_refs`

Cada linha é uma ocorrência material impressa. Os extremos abaixo são tipados separadamente para
que uma linha nunca seja confundida com uma página.

| Campo | Significado |
| --- | --- |
| `cited_editorial_page_start_raw` | Literal OCR do número editorial inicial citado. |
| `cited_editorial_page_start_number` | Interpretação inteira conservadora do número inicial. |
| `cited_editorial_page_end_raw` / `cited_editorial_page_end_number` | Extremo editorial final de um intervalo. Em intervalo de linhas na mesma página, repete a página inicial. |
| `cited_editorial_column_raw` | Coluna editorial impressa, preservada literalmente. |
| `cited_editorial_line_raw` | Notação completa de linha, preservada literalmente. |
| `cited_editorial_line_start_raw` / `cited_editorial_line_start_number` | Extremo inicial da linha citada. |
| `cited_editorial_line_end_raw` / `cited_editorial_line_end_number` | Extremo final da linha citada. |
| `cited_location_parse_status` | `parsed`, `partial`, `unparsed` ou `not_applicable`. Mede somente a estrutura do locator impresso; não mede se o alvo OCR foi encontrado. |
| `resolved_target_ocr_file` | Arquivo OCR corporal escolhido pelo localizador. |
| `target_ocr_file_candidate_score` | Escore relativo do candidato produzido pelo localizador. Não é probabilidade calibrada nem substitui `locator_status`. |
| `locator_status` | Estado da decisão material: `resolved`, `ambiguous`, `unresolved` ou `unverified`. |

Exemplos:

- `10,2-3`: página inicial 10, página final 10, linha inicial 2, linha final 3;
- `69_13-70_1`: página 69 linha 13 até página 70 linha 1;
- `12-15`: página inicial 12 e página final 15, sem linha;
- `12 seqq.`: literal preservado e parse parcial; o fim não é inventado.

### Proveniência e evidência

`alphabetical_source_spans` armazena `ocr_file_path`, linhas inclusivas, tipo de bloco e hash do
texto. `alphabetical_entry_source_spans` associa um ou mais spans a uma entrada.

`alphabetical_locator_evidence` materializa evidências antes escondidas em
`raw_json.compact_locator.evidence`. `evidence_ocr_file_path` é arquivo OCR;
`observed_editorial_page_number` é o número editorial observado nesse arquivo. Esses dois campos
nunca são intercambiáveis.

`alphabetical_boundaries` guarda `ORDO RERUM`, fechamento editorial, errata e outras fronteiras
não possuídas. Uma fronteira não produz entradas alfabéticas.

## 3. Aliases legados

Os campos abaixo permanecem no banco somente para builders, payloads e consultas históricas. Novo
código deve usar o campo canônico.

| Legado | Canônico | Observação |
| --- | --- | --- |
| `sections.page_start/page_end` | `index_editorial_page_start/end` | Página impressa da seção de índice. |
| `sections.file_start/file_end` | `index_ocr_file_start/end` | Caminho de arquivo OCR. |
| `entries.inferred_printed_page` | `index_editorial_page_estimate` | Página da entrada do índice, não página citada. |
| `entries.section_start_file` | `index_section_start_ocr_file` | Arquivo OCR. |
| `entries.editorial_anchor_file` | `index_entry_source_ocr_file` | O nome legado é ambíguo; não usar em código novo. |
| `entries.target_file_best` | `resolved_target_ocr_file` | Cache derivado. |
| `refs.page_ref_raw/page_ref_int` | `cited_editorial_page_start_raw/number` | Número editorial citado. |
| `refs.page_ref_col` | `cited_editorial_column_raw` | Coluna editorial. |
| `refs.line_ref_raw` | `cited_editorial_line_raw` | Literal composto; os extremos tipados são preferíveis. |
| `refs.range_start_raw/range_end_raw` | extremos `cited_editorial_*` | O legado é ambíguo e não deve receber números de linha. |
| `refs.target_file` | `resolved_target_ocr_file` | Caminho de arquivo OCR corporal. |
| `refs.target_file_probability` | `target_ocr_file_candidate_score` | Escore não calibrado. |

Os aliases não devem ser usados para criar novas regras. Durante a transição, o importador
preenche os campos canônicos e preserva os literais legados para reprodutibilidade.

## 4. Regras para consultas e validadores

1. Nunca compare sufixo de arquivo a página editorial como se fossem a mesma escala.
2. Para localizar conteúdo, combine página citada, texto do lema/passagem, escopo de obra ou
   fascículo, sequência vizinha e cabeçalho OCR.
3. Uma decisão `resolved` é sobre um `resolved_target_ocr_file`; `cited_location_parse_status`
   apenas descreve a qualidade do parse impresso.
4. Use source spans para detectar entrada fora da seção, seção vazia, cobertura residual e
   duplicação OCR.
5. Preserve todos os campos `*_raw`; correções de CER pertencem aos campos derivados e à evidência.

## 5. Contrato operacional de localização v2

Os artefatos `locator_items.json` e `analysis_locator_items.json` usam
`locator_format_version=2`. Cada item mantém os aliases planos durante a migração, mas a passagem
entre etapas deve ler primeiro `locator_contract`, dividido em cinco grupos:

| Grupo | Função |
| --- | --- |
| `ownership` | Identidade imutável `(entry_key, ref_order)` e vínculo bíblico eventual. |
| `index_source` | Arquivos OCR que contêm a seção e a entrada do índice. Nunca são o alvo corporal. |
| `cited_location` | Página, coluna, linha ou locator interno exatamente como citado pelo índice. |
| `search_hints` | Lema, passagem bíblica e nomes dos campos compactos usados para busca. |
| `target_resolution` | Estado pendente, candidatos e, somente depois da validação, alvo OCR resolvido. |

`excluded_index_intervals` lista todos os intervalos OCR das seções semânticas conhecidas. O
localizador deve excluí-los de buscas corporais, inclusive quando a referência não tem página
editorial. Nos shards a lista comum é elevada ao envelope para evitar repetição volumosa.

Para `target_locator` sem página impressa, `resolved` pode ser produzido mecanicamente somente com
o conjunto independente completo: título/abreviação de obra no corpo, número interno do locator no
mesmo candidato e confirmação da família de obra por cabeçalhos. Similaridade textual isolada ou
um número isolado continuam ambíguos.

O banco de análise expõe `analysis_occurrences_canonical`, uma view que renomeia os campos
operacionais segundo este dicionário sem remover as colunas legadas.

## 6. Checkpoints e dependências

`ocr_source_snapshot.json` registra, para cada `.txt`, caminho relativo, tamanho, `mtime_ns` e
SHA-256 do conteúdo. O fingerprint raiz participa dos checkpoints semânticos e de localização.
Hashes individuais são reutilizados quando tamanho e `mtime_ns` não mudaram.

Artefatos mecânicos checkpointáveis possuem um sidecar `*.checkpoint.json` com:

- `stage` e `status=complete`;
- `input_fingerprint` e dependências explícitas;
- caminho e SHA-256 da saída;
- resumo de contagens.

Uma etapa só reutiliza o artefato quando fingerprint de entrada, caminho e hash da saída conferem.
Resultados do agente são adicionalmente armazenados por ocorrência em `locator_cache/`, usando um
fingerprint do item. Assim, inserir ou remover uma referência não invalida resultados independentes
apenas porque a numeração posicional dos shards mudou.
