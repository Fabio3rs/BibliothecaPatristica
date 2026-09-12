# Piloto de embeddings do OCR

Data do levantamento: 2026-09-04.

> O baseline pagina-centrico deste documento foi preservado. A implementacao
> estrutural comparativa V0/V1/V2, com IR auditavel e KNN pagina-pagina, esta em
> `docs/experimento_knn_ocr_estrutural_2026-09-04.md`.

## Objetivo

Testar recuperacao semantica diretamente sobre o OCR de cerca de dez volumes,
sem misturar esses vetores com o banco de resumos. O piloto separa a etapa
auditavel de limpeza/segmentacao da chamada ao modelo de embeddings.

## O que ja existia para resumos

`tools/generate_resumo_embeddings.py` le `data/patristica_resumos.db` e grava
dois vetores por pagina no mesmo banco:

- `resumo_pagina_embedding`, a partir de `summary_page_clean` com fallback para
  `resumo_pagina`;
- `resumo_global_embedding`, a partir de `summary_global_clean` com fallback
  para `resumo_global`.

Em 2026-09-04, cada tabela continha 285.228 vetores
`qwen3-embedding:8b`, com 4.096 dimensoes `float32`. Cada tabela ocupava
4.673.175.552 bytes somente nos BLOBs. Os vetores UMAP reduzidos tinham dez
dimensoes e eram consumidos pelo KNN de paginas relacionadas.

Limites encontrados:

- o gerador legado nao aceita `ocr_clean_search` como fonte;
- os embeddings legados nao possuem hash do texto de entrada, apenas hash do
  vetor; uma alteracao posterior nos campos limpos pode nao provocar reembedding;
- `drop_original_embedding` e `drop_summary_embedding` existem, mas estavam
  zerados em todo o banco;
- o banco de resumos estava recebendo novas linhas durante a inspecao, por isso
  nao deve servir como snapshot do OCR atual.

O pipeline v2 e melhor nesse ponto: usa `embedding_source_hash` e invalida o
vetor quando `embedding_text` muda.

## Fonte e deduplicacao

O piloto le `teste/<VOLUME>/text`, porque correcoes manuais podem existir no
arquivo antes de serem refletidas em `ocr_versions.db`. Antes de ler o texto,
os arquivos sao agrupados por numero fisico da pagina.

Quando ha mais de um arquivo para o mesmo numero, vence o nome canonico
`<VOLUME>-NNN.txt`; os demais ficam registrados em
`pages.source_candidates_json`. Se nao houver um unico candidato nem um nome
canonico inequivoco, o script aborta em vez de escolher silenciosamente.

Essa regra e necessaria: na amostra, PL020 tinha 2.222 arquivos para 1.223
paginas fisicas e PL185 tinha 2.018 arquivos para 1.019 paginas. Em ambos os
casos havia 999 arquivos sobrepostos.

## Formato estruturado observado

Nos dez volumes inspecionados, as paginas atuais usam uma raiz `<pagina>` com
atributos como `estado="com_texto|vazio"` e, de forma nao uniforme,
`tipo="texto|capa_ou_guarda"`. O conteudo aparece sobretudo em `<bloco>`, com
`tipo`, `script` e `bbox`. Tambem existem tags `<cabecalho>` e `<rodape>`.

Os valores nao formam uma taxonomia perfeitamente controlada. Foram observados,
entre outros, `texto_principal`, `cabecalho`, `rodape`, `nota`,
`nota_marginal`, `aparato_critico` e variantes com erros de grafia. Em `script`
aparecem `latino`, `grego`, `misto`, `siriaco`, `cirilico` e variantes como
`latin`, `greco`, `greek` e `gregο`. Havia ainda 74 paginas XML recuperadas pelo
fallback tolerante a XML malformado na amostra.

O elemento `<notas>` diretamente sob `<pagina>` e comentario sobre a
transcricao e deve ser ignorado. Ja blocos classificados como `nota`,
`nota_marginal`, `aparato_critico` ou `rodape` nao podem ser descartados em
bloco: a amostra contem neles variantes textuais extensas e ate continuacoes
do corpo grego/latino.

## Filtro v1

O filtro do piloto:

1. parseia XML e texto legado com `tools.ocr_xml_utils`;
2. preserva latim, grego e outros scripts no corpo;
3. ignora o elemento externo `<notas>`, mas preserva notas/aparato substanciais;
4. remove boilerplate do Google, avisos de reconhecimento, selos, URLs,
   marcadores isolados e paginas vazias;
5. detecta cabecalhos ou rodapes curtos repetidos dentro de cada volume e os remove;
6. descarta paginas que ficam vazias ou muito curtas;
7. divide cada pagina em janelas de ate 3.200 caracteres, com 320 caracteres
   de sobreposicao.

Rotulos estruturais argumentativos como `RESPONSIO`, `OBIECTIO`, `QUAESTIO`,
`CONCLUSIO`, `ΑΠΟΚΡΙΣΙΣ` e `ΕΡΩΤΗΣΙΣ` sao preservados mesmo quando se repetem.
Eles carregam funcao discursiva, ao contrario de um cabecalho corrido de tomo,
autor ou obra.

Nao se usa a regra antiga que, em certas proporcoes de blocos, podia manter
somente `script={latino,misto}`. Para busca direta no OCR, perder o grego seria
um erro mais grave do que manter algum ruido adicional.

## Banco separado

O destino padrao e `data/patristica_ocr_embeddings.db`, ignorado pelo Git. As
tabelas principais sao:

- `volumes`: configuracao e estatisticas de preparacao;
- `pages`: fonte escolhida, hashes, contagens, descarte e motivos;
- `chunks`: locator, tamanho e hash do texto enviado ao modelo;
- `embeddings`: BLOB `float32`, modelo, dimensao e hash da entrada;
- `runs`: reservado para auditoria de execucoes.

O texto limpo e os chunks existem somente em memoria. O consultor reconstrói o
trecho a partir do arquivo-fonte e valida seu hash antes de exibi-lo. Uma
mudanca no texto ou na configuracao recria apenas os locators afetados e remove
seus embeddings por `ON DELETE CASCADE`. Repetir a execucao sem mudancas
preserva os vetores existentes.

## Dez volumes sugeridos

Para um primeiro teste de infraestrutura e ruido:

```text
PG001 PG024 PG031 PG084 PG116 PL001 PL020 PL021 PL124 PL185
```

O conjunto inclui PG/PL, volumes recem-corrigidos, dois formatos de nomes de
arquivo e os casos de sobreposicao PL020/PL185. Para uma avaliacao semantica
cega, convem depois substituir PL020 por um volume sem anomalia conhecida.

Auditoria integral com o filtro v1, sem DB e sem Ollama:

| Volume | paginas fisicas | XML malformado recuperado | paginas mantidas | chunks | fontes sobrepostas |
|---|---:|---:|---:|---:|---:|
| PG001 | 742 | 2 | 741 | 1.551 | 0 |
| PG024 | 648 | 0 | 632 | 1.269 | 0 |
| PG031 | 942 | 0 | 937 | 1.872 | 0 |
| PG084 | 658 | 0 | 647 | 1.483 | 0 |
| PG116 | 747 | 0 | 744 | 1.485 | 0 |
| PL001 | 693 | 3 | 683 | 1.585 | 0 |
| PL020 | 1.223 | 43 | 1.214 | 3.009 | 999 |
| PL021 | 615 | 21 | 607 | 1.630 | 0 |
| PL124 | 667 | 0 | 666 | 1.730 | 0 |
| PL185 | 1.019 | 5 | 1.010 | 2.281 | 999 |
| **Total** | **7.954** | **74** | **7.881** | **17.895** | **1.998** |

Com o modelo atual de 4.096 dimensoes, os BLOBs desses 17.895 chunks devem
ocupar aproximadamente 293.191.680 bytes (cerca de 280 MiB), antes do overhead
do SQLite. Isso e muito menor que duplicar texto e vetores de pagina no banco
de resumos.

## Execucao segura

Primeiro, somente calcular o que seria preparado:

```bash
.venv/bin/python tools/generate_ocr_embeddings.py \
  --volumes PG001 PG024 PG031 PG084 PG116 PL001 PL020 PL021 PL124 PL185 \
  --stage audit \
  --verbose
```

Depois de auditar as contagens, fazer um smoke limitado a 32 chunks. A
preparacao ocorre novamente em memoria e o banco recebe metadados, hashes e
vetores, mas nao o texto limpo:

```bash
.venv/bin/python tools/generate_ocr_embeddings.py \
  --volumes PG001 PG024 \
  --stage embed \
  --model qwen3-embedding:8b \
  --batch-size 16 \
  --limit-chunks 32
```

Completar os dez volumes removendo `--limit-chunks`.

## Consulta do piloto

`tools/query_ocr_embeddings.py` gera embedding de consulta com o mesmo
`Instruct:` usado no playground atual e calcula cosseno diretamente nos
vetores originais. O OCR documental continua sendo embutido sem instrucao: a
instrucao pertence ao lado da consulta, o que permite testar varias intencoes
contra os mesmos vetores. Nao se aplica UMAP antes de avaliar retrieval, pois a
reducao pode alterar a vizinhanca semantica.

```bash
.venv/bin/python tools/query_ocr_embeddings.py \
  "processao do Espirito Santo" \
  --volumes PG001 PG024 PG031 PG084 PG116 PL001 PL020 PL021 PL124 PL185 \
  --top-k 10
```

Para procurar sentido argumentativo, e nao apenas coincidencia tematica:

```bash
.venv/bin/python tools/query_ocr_embeddings.py \
  "como o autor refuta a objecao sobre a unidade divina" \
  --profile argumentative \
  --volumes PG001 PG024 PG031 PG084 PG116 \
  --top-k 10
```

Tambem e possivel testar uma formulacao propria sem regenerar o banco:

```bash
.venv/bin/python tools/query_ocr_embeddings.py \
  "consulta" \
  --instruction "Retrieve passages that contain a formal objection followed by its answer."
```

## Criterio de avaliacao

Montar de 20 a 30 consultas com resposta conhecida e marcar, para cada uma:

- pagina relevante apareceu em top-1, top-5 e top-10;
- resultado e corpo real, titulo/cabecalho ou ruido;
- latim/grego foi preservado;
- OCR encontrou passagem que o resumo omitiu;
- resumo encontrou tema que o OCR literal diluiu.

O resultado esperado nao e substituir os embeddings de resumos. OCR e resumo
sao sinais complementares: OCR melhora evidencia e termos raros; resumo melhora
temas abstratos e tolerancia ao idioma da consulta.
