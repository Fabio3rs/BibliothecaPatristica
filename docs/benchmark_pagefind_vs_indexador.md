# Pagefind versus PatrologiaIndexer

Benchmark offline, sem navegador ou servidor HTTP, executado em 2026-08-09. O
driver fornece a ambos os indexadores o mesmo `content` enriquecido usado pela
pipeline web. Pagefind também recebe os metadados e filtros que sua interface
oferece; o indexador custom guarda apenas nome, URL e postings, pois a página de
resultados não precisa de excertos.

## Configuração

- 25 volumes estratificados: 9 PG, 8 PL e 8 PO
- 17.514 páginas e 48.425.919 bytes de conteúdo pesquisável
- Pagefind 1.4.0, chunking interno padrão da biblioteca
- PatrologiaIndexer: `max_terms_per_shard=5000`,
  `max_bytes_per_shard=307200`, `documents_per_shard=5000` e
  `min_importance=2.01`
- keywords: `min_count=1`, igual à pipeline Pagefind
- ingestão custom em batches de 250 documentos via RPC JSONL em stdin/stdout

## Resultado

| Métrica | Pagefind | PatrologiaIndexer | Pagefind / custom |
| --- | ---: | ---: | ---: |
| Tempo total de indexação e escrita | 33,85 s | 6,23 s | 5,43× |
| Tamanho lógico | 53.469.101 B | 8.476.042 B | 6,31× |
| Espaço alocado | 98.062.336 B | 8.704.000 B | 11,27× |
| Arquivos | 18.053 | 109 | 165,62× |

O custom gerou 104 shards de postings e 4 shards de documentos para 105.479
termos distintos. O limite de aproximadamente 300 KiB dominou antes do teto de
5.000 termos, portanto este último não produziu shards grandes nessa amostra.

No Pagefind, os 17.514 arquivos de fragmentos ocuparam 29.744.104 bytes lógicos
(55,6% do índice) e 73.183.232 bytes alocados (74,6%). O código do Pagefind 1.4.0
sempre serializa um fragmento por registro; não há opção pública estável nessa
versão para manter postings e URLs sem os dados usados para excertos.

Uma extrapolação estritamente linear para 288.570 páginas indicaria cerca de
881 MB para Pagefind e 140 MB para o custom. Ela é apenas uma verificação de
ordem de grandeza: `min_importance` depende da frequência global dos termos e o
corpus completo pode alterar a proporção. O Pagefind de produção já existente,
com 288.570 fragmentos distribuídos em 17 índices, mede aproximadamente 860 MB
lógicos e 1,5 GB alocados, próximo da projeção para Pagefind.

## Reprodução

O comando padrão usa somente PG001, PL001 e PO002. Para um lote maior, passe a
lista explícita de volumes:

```sh
node tools/benchmark_pagefind_vs_indexador.mjs \
  --volumes PG001,PL001,PO002
```

O resultado completo é salvo como `report.json` sob um diretório temporário
impresso no início da execução. O driver mostra progresso a cada 5.000 páginas
e aceita opções explícitas para todos os limites de shard e importância.

Este benchmark mede indexação, escrita e tamanho. Relevância deve ser avaliada
separadamente com um conjunto de consultas e julgamentos; Pagefind aplica
stemming, enquanto o custom atualmente faz normalização de caixa/acentos e
busca por tokens, sem stemming.
