# Auditoria de volumes duplicados e mal nomeados

> **Estado posterior:** a substituição dos cinco volumes com fonte incorreta e
> o novo OCR foram concluídos em 4 de setembro de 2026. Esta auditoria preserva
> o diagnóstico e o estado observado em 2 de setembro. A execução, as
> validações e os derivados ainda pendentes estão registrados em
> `plano_expurgo_reprocessamento_volumes_defeituosos_2026-09-02.md`, na seção
> “Atualização operacional: resolução na camada OCR”.

Data da auditoria: 2 de setembro de 2026.

## Objetivo

Esta auditoria foi criada para identificar, dentro de `teste/`:

- volumes digitais duplicados;
- pastas cujo nome não corresponde ao tomo impresso;
- digitalizações diferentes do mesmo tomo;
- pastas que contêm mais de um tomo em grupos sucessivos de páginas;
- falsos positivos causados pelo OCR ou pelo frontispício padronizado de Migne.

O fac-símile foi adotado como evidência final. As pontuações automáticas servem
somente para ordenar a fila de revisão e não autorizam apagar, renomear ou
substituir arquivos.

## Como a auditoria evoluiu

### 1. Amostra inicial por pasta

O primeiro levantamento examinou as dez primeiras páginas físicas de cada uma
das 407 pastas encontradas no corpus. Para cada pasta, o processo:

1. escolheu um único OCR por número de página, preferindo o arquivo canônico;
2. aplicou `clean_ocr_text_optimized`, de `scripts/limpeza_ocr.py`, para remover
   XML, notas e ruído que acrescentariam tokens sem valor;
3. normalizou Unicode, ligaturas e caixa;
4. procurou declarações como `Patrologiae Graecae Tomus ...` e
   `Patrologiae Latinae Tomus ...`;
5. calculou similaridade com 70% de cosseno TF-IDF e 30% de Jaccard sobre
   sequências de quatro palavras;
6. calculou SHA-256 dos fac-símiles dos pares candidatos;
7. produziu planilhas e `diffs` ordenados por similaridade.

Essa fase encontrou 83 pares candidatos. Apenas quatro grupos passaram pelos
critérios de agrupamento de maior confiança. O resultado foi útil para triagem,
mas insuficiente para detectar uma troca ocorrida no meio de uma pasta.

Script:

```text
tools/audit_volume_similarity.py
```

Comando de reprodução:

```bash
python tools/audit_volume_similarity.py \
  --corpus teste \
  --pages 10 \
  --output-dir data/volume_similarity_audit
```

### 2. Auditoria por grupos de páginas

A inspeção de `PL020` mostrou que a unidade correta de análise não poderia ser
somente a pasta. Ela começa como tomo XX e apresenta uma nova folha de rosto do
tomo XXI na página digital 612.

Foi então criado um segundo auditor, que:

- lê todas as páginas dos volumes selecionados;
- forma blocos consecutivos de dez páginas;
- registra declarações explícitas encontradas em cada bloco;
- propaga a identidade do último `Tomus` confirmado para os blocos seguintes;
- consolida blocos consecutivos em segmentos bibliográficos;
- calcula hashes do texto normalizado e das sequências de fac-símiles;
- relata grupos exatamente duplicados entre pastas diferentes.

Menções isoladas a `Patrologiae Latinae`, `Patrologiae Graecae` ou `Pars` não
alteram a identidade propagada. Essa proteção foi necessária porque referências
bibliográficas no corpo do texto estavam criando falsos cortes. Somente uma nova
declaração de `Tomus` abre um possível segmento.

Script:

```text
tools/audit_page_groups.py
```

Exemplo:

```bash
python tools/audit_page_groups.py \
  --corpus teste \
  --volumes PL020 \
  --group-size 10 \
  --output-dir data/page_group_audit_pl020
```

## Resultados confirmados no fac-símile

### Duplicatas digitais exatas

| Pastas | Resultado |
|---|---|
| `PG024` e `PL024` | Os 51 blocos de imagens, páginas 1–509, são idênticos. O conteúdo é PL XXIV; portanto, `PG024` está errado. |
| `PL115` e `PL115-1` | Os 74 blocos de imagens, páginas 1–738, são idênticos. |
| `PL116` e `PL116-1` | Os 55 blocos de imagens, páginas 1–545, são idênticos. |

Os resultados acima comparam toda a extensão disponível dos pares, não apenas
as dez primeiras páginas.

### Mesmo tomo em digitalizações diferentes

| Pastas | Resultado |
|---|---|
| `PL123` e `PL124` | Ambas as folhas de rosto anunciam PL CXXIII. São digitalizações diferentes; `PL124` está mal nomeado. |
| `PG106` e `PG116` | Ambas as folhas de rosto anunciam PG CVI. São digitalizações diferentes; `PG116` está mal nomeado. |

### Tomos incorretamente colocados

| Pasta | Esperado pelo nome | Impresso no fac-símile |
|---|---|---|
| `PG024` | PG XXIV | PL XXIV |
| `PG031` | PG XXXI | PG XXI |
| `PG084` | PG LXXXIV | PG LXXIV |
| `PG116` | PG CXVI | PG CVI |
| `PL124` | PL CXXIV | PL CXXIII |

### Pasta com mais de um tomo

`PL020` possui dois segmentos confirmados:

| Segmento digital | Identidade inferida | Evidência |
|---|---|---|
| páginas 1–610 | PL XX | folha de rosto na página 6 |
| bloco 611–620 até a página 1223 | PL XXI | nova folha de rosto na página 612 |

O começo do segundo segmento é apresentado como bloco 611–620 porque a unidade
da auditoria tem dez páginas. A declaração explícita está na página 612.

### Caso legítimo de partes separadas

`PG007.01` e `PG007.02` tiveram similaridade alta, mas o fac-símile declara,
respectivamente, `Pars Prior` e `Pars Secunda` do tomo VII. Não são duplicatas.

## Checagem dos tomos anteriores e posteriores

Foram examinadas visualmente as folhas de rosto e percorridos todos os blocos
internos dos vizinhos imediatos dos volumes defeituosos. Essa etapa abrangeu
1.295 grupos de páginas em 18 pastas.

| Alvo | Anterior | Alvo encontrado | Posterior | Conclusão |
|---|---|---|---|---|
| `PG024` | `PG023` = PG XXIII | PL XXIV | `PG025` = PG XXV | erro isolado |
| `PG031` | `PG030` = PG XXX | PG XXI | `PG032` = PG XXXII | erro isolado |
| `PG084` | `PG083` = PG LXXXIII | PG LXXIV | `PG085` = PG LXXXV | erro isolado |
| `PG116` | `PG115` = PG CXV | PG CVI | `PG117` = PG CXVII | erro isolado |
| `PL020` | `PL019` = PL XIX | PL XX seguido de PL XXI | `PL021` = PL XXI | somente `PL020` é misto |
| `PL124` | `PL123` = PL CXXIII | PL CXXIII | `PL125` = PL CXXV | erro isolado |

Não há sinal de deslocamento sequencial: os tomos anteriores e posteriores
permanecem corretos do começo ao fim. A única transição interna adicional foi a
de `PL020`.

## Falsos positivos importantes

### Algarismos romanos lidos incorretamente

O OCR gerou alertas falsos para:

- `PL057`: imprimiu LVII, OCR detectou XVII;
- `PL058`: imprimiu LVIII, OCR detectou LVII;
- `PL087`: imprimiu LXXXVII, OCR detectou LXXVII;
- `PL093`: imprimiu XCIII, OCR detectou XCI;
- `PL094`: imprimiu XCIV, OCR detectou XLIV;
- `PL181`: imprimiu CLXXXI, OCR detectou CLXXI;
- `PL214`: imprimiu CCXIV, OCR também produziu a leitura espúria 264.

Esses casos demonstram por que divergência de título detectada por OCR deve ser
tratada como alerta, nunca como decisão final.

### Frontispícios padronizados

Pares como `PG136/PG143`, `PG122/PG143`, `PG060/PG063` e `PG154/PG155`
receberam pontuações altas porque os frontispícios de Migne repetem quase todo o
texto editorial. A inspeção visual confirmou tomos distintos. Esses pares devem
ser classificados como `distinct_tomes_shared_front_matter`.

### Patrologia Orientalis

Alguns pares PO aparecem por similaridade textual, mas não há fac-símiles locais
nas pastas examinadas. Permanecem inconclusivos e não devem provocar qualquer
ação até que as imagens sejam obtidas.

## PDFs novos para substituição

### Obrigatórios

1. Patrologia Graeca, tomo XXIV, para substituir `PG024`.
2. Patrologia Graeca, tomo XXXI, para substituir `PG031`.
3. Patrologia Graeca, tomo LXXXIV, para substituir `PG084`.
4. Patrologia Graeca, tomo CXVI, para substituir `PG116`.
5. Patrologia Latina, tomo CXXIV, para substituir `PL124`.

Os cinco PDFs acima foram posteriormente encontrados em
`/homessddata/patristica/`. Foram extraídas as dez primeiras páginas de cada um,
executado Tesseract com o modelo `migne` e realizada conferência visual das
folhas de rosto. Todos tiveram série e tomo aprovados:

| PDF novo | Total de páginas | Página PDF conferida | Título impresso |
|---|---:|---:|---|
| `PG024.pdf` | 648 | 7 | Patrologiae Graecae Tomus XXIV |
| `PG031.pdf` | 942 | 1 | Patrologiae Graecae Tomus XXXI |
| `PG084.pdf` | 658 | 7 | Patrologiae Graecae Tomus LXXXIV |
| `PG116.pdf` | 747 | 1 | Patrologiae Graecae Tomus CXVI |
| `PL124.pdf` | 667 | 1 | Patrologiae Tomus CXXIV, série latina |

Essa aprovação cobria a identidade inicial dos arquivos. Na data desta etapa
ainda não havia ocorrido cópia, substituição de pastas nem OCR integral dos
novos PDFs. Esse estado histórico foi superado pela execução documentada na
atualização operacional de 4 de setembro de 2026.

### `PL020`: não adquirir substituto neste momento

`PL020` não representa omissão de dados. O tomo XX está presente e o problema é
a duplicação adicional do tomo XXI a partir da página digital 612. Por isso,
`PL020` foi retirado da lista de aquisição e deve receber tratamento separado.

O procedimento auditável preparado posteriormente para separar as páginas
612–1223 e limpar somente os derivados está documentado em
[`recorte_pl020_tomo_xxi_2026-09-04.md`](recorte_pl020_tomo_xxi_2026-09-04.md).

Em uma etapa posterior, deve-se comparar a qualidade material do trecho XXI
incorporado em `PL020` com a digitalização independente de `PL021`. Essa análise
poderá considerar nitidez, resolução, páginas ausentes, cortes, manchas, ordem,
qualidade do OCR e legibilidade geral. Até essa comparação, os dois testemunhos
devem ser preservados sem recorte ou substituição.

Também não é necessário baixar novamente `PL115` ou `PL116`: nesses casos
existem pastas redundantes, mas uma cópia íntegra já está presente.

Antes de incorporar qualquer PDF novo, deve-se conferir:

1. série e tomo impressos na folha de rosto;
2. autores ou obras anunciados;
3. começo e fim da digitalização;
4. ordem e quantidade das páginas;
5. hashes e similaridade contra o corpus existente;
6. possíveis mudanças de `Tomus` no interior do arquivo.

## Arquivos produzidos

| Arquivo | Conteúdo |
|---|---|
| `data/volume_similarity_audit/volume_inventory.csv` | Inventário das 407 pastas e títulos detectados nas dez primeiras páginas. |
| `data/volume_similarity_audit/similarity_groups.csv` | Pares candidatos, pontuações, grupos e motivos de revisão. |
| `data/volume_similarity_audit/diffs/` | Diferenças textuais normalizadas dos pares candidatos. |
| `data/volume_similarity_audit/manual_facsimile_review.csv` | Decisões tomadas após inspeção visual. |
| `data/volume_similarity_audit/neighbor_facsimile_review.csv` | Conferência dos volumes anteriores e posteriores. |
| `data/volume_similarity_audit/pdfs_to_acquire.csv` | Lista operacional dos PDFs novos necessários. |
| `data/volume_similarity_audit/new_pdf_initial_review.csv` | OCR e conferência visual das páginas iniciais dos cinco PDFs novos. |
| `data/page_group_audit_pl020/` | Blocos e segmentos internos de `PL020`. |
| `data/page_group_audit_duplicates/` | Grupos exatamente duplicados nos pares confirmados. |
| `data/page_group_audit_misnamed/` | Segmentação dos volumes mal nomeados examinados. |
| `data/page_group_audit_neighbors/` | Auditoria completa dos 18 volumes da checagem de vizinhança. |

## Testes

Foram adicionados testes para:

- escolha do OCR canônico por página;
- limpeza de XML e notas antes da tokenização;
- detecção de série, tomo e partes;
- criação de grupos de similaridade e `diffs`;
- mudança de tomo no meio de uma pasta;
- propagação da identidade pelos blocos seguintes;
- prevenção de falsos segmentos causados por referências a `Pars` ou a outra
  série sem nova declaração de `Tomus`;
- detecção de grupos exatamente duplicados em pastas diferentes.

Comando executado:

```bash
/usr/bin/python3.11 -m pytest -q \
  tests/test_audit_page_groups.py \
  tests/test_audit_volume_similarity.py \
  tests/test_limpeza_ocr_xml.py
```

Resultado: 15 testes aprovados.

## Limitações e próximos passos

- A comparação inicial de similaridade cobriu todas as 407 pastas, mas somente
  as dez primeiras páginas de cada uma.
- A auditoria completa por blocos foi executada nos candidatos e vizinhos, não
  em todo o corpus de aproximadamente 288 mil arquivos de texto.
- SHA-256 comprova igualdade digital, mas não encontra sozinho digitalizações
  diferentes do mesmo exemplar.
- Uma transição sem folha de rosto, ou cuja declaração `Tomus` esteja ilegível
  no OCR, ainda pode passar despercebida.
- Os candidatos sem fac-símile local precisam de validação externa.
- O trecho do tomo XXI contido em `PL020` ainda precisa ser comparado, página a
  página ou por amostragem representativa, com o `PL021` independente antes de
  qualquer decisão de retenção, recorte ou preferência de fonte.

O próximo passo recomendado é executar `audit_page_groups.py` sobre todo o
corpus, armazenar o relatório e submeter os novos alertas de maior confiança à
mesma adjudicação manual documentada nesta auditoria.
