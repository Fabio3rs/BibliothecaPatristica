# Experimentos A/B com hints compactos do Tesseract

Implementação: `offline_ocr/migne_hints.py`, `offline_ocr/ab_experiment.py` e
`scripts/playgrounds/ocr_ab.py`. Preparação e avaliação são offline. **Somente o
subcomando `run` chama modelos**, quando executado manualmente. Nenhuma chamada
de inferência foi feita na validação desta implementação.

O experimento conserva imagem, prompt de sistema, eventual XML anterior, modelo
e parâmetros entre variantes. Varia a representação do mesmo TSV. Não importa os
playgrounds antigos, que têm execução de inferência no nível do módulo.
Não modifica `main2.py`, seus prompts, caches ou os arquivos de texto do corpus.

## Correção única usando o Tesseract em cache

`scripts/playgrounds/ocr_repair_cached.py` prepara uma chamada com o fac-símile,
o XML histórico e o **TXT exato do cache de `main2.py`**, sem reconstruí-lo de TSV.
Usa o mesmo transporte HTTP e registro de respostas do A/B, com uma única variante.
O prompt compacto pede a conferência dos dois rascunhos na imagem, incluindo
inícios/fins de colunas e continuação das notas, e devolução da página completa.
Não informa ao modelo as palavras usadas como alvos na investigação.

A preparação já disponível em `.work/ocr_diff_v2/repair_pg001_638_cached` contém:

- PG001:638, XML **809912**, modelo de origem **gpt-5-mini**;
- cache **migne:adaptive_soft:2.0:31:4**, 5.627 caracteres;
- `draft.xml`, `tesseract_cached.txt`, `system.txt`, `cached_text.txt` e
  `experiment.json`, com IDs, origem e hashes dos insumos.

Com `OPENAI_API_KEY` definido, execute manualmente:

```bash
.venv/bin/python scripts/playgrounds/ocr_repair_cached.py run \
  --experiment .work/ocr_diff_v2/repair_pg001_638_cached \
  --out .work/ocr_diff_v2/repair_pg001_638_gpt54mini_r3 \
  --model gpt-5.4-mini \
  --reasoning-effort medium
```

O padrão é `https://api.openai.com/v1/chat/completions` com `image_detail=original`.
Esse nível é suportado pelo modelo e indicado para OCR na
[documentação oficial de visão da OpenAI](https://developers.openai.com/api/docs/guides/images-vision#choose-an-image-detail-level).
O playground de correção envia **`reasoning_effort="medium"` por padrão**, como
`main2.py`. O valor efetivo fica em `run.json` e no resumo impresso. Pode ser
alterado por `--parameters arquivo.json`; `--reasoning-effort` prevalece sobre
esse arquivo. Não envia temperatura por padrão. Os níveis suportados pelo
modelo estão na [documentação oficial do GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

As execuções históricas `r1` e `r2` omitiram esse parâmetro e registraram zero
tokens de raciocínio. A correção do padrão não altera os prompts ou insumos
congelados: pode reutilizar o mesmo experimento e comparar a nova execução com
essas respostas. O prompt deste playground continua sendo a adaptação compacta
descrita acima, não uma reprodução integral da chamada de `main2.py`.

É **uma chamada, sem retries automáticos**. A saída inclui
`r001_cached_text.xml`, resposta integral `.response.json`, uso/duração/motivo
de término em `.result.json`, `changes.diff`, `inspection.json` e `events.jsonl`.
A inspeção valida o XML e registra alterações; não certifica a transcrição.
Falhas, XML inválido ou término incompleto produzem código de saída diferente de zero.
Para repetir, use outro diretório em `--out`.

Para preparar outro snapshot offline da mesma página:

```bash
.venv/bin/python scripts/playgrounds/ocr_repair_cached.py prepare \
  --version-id 809912 \
  --out .work/ocr_diff_v2/repair_pg001_638_cached_r2
```

Sem `--version-id`, escolhe a versão não Tesseract mais recente da mesma página
e imagem. `--expect-model` é `gpt-5-mini` por padrão e recusa outro modelo de
origem. Imagem, volume, página e bancos também podem ser informados por argumentos.
Os dois bancos são abertos somente para leitura; cache ausente causa erro,
sem executar OCR. As execuções usam o snapshot preparado e rejeitam alterações
nos arquivos congelados ou na imagem. Não atualiza o banco nem exporta ao corpus.

## Variantes e tamanho

| Variante | Conteúdo | Finalidade |
|---|---|---|
| `raw_text` | Palavras na ordem das linhas do TSV, sem coordenadas | Controle; não é necessariamente byte a byte o `.txt` do Tesseract |
| `ordered_text` | Mesmo texto reorganizado por blocos físicos, sem coordenadas | Separar o efeito da reordenação do efeito dos hints |
| `line_tsv` | ID do bloco/linha, caixa e texto por linha | Referência com mais detalhes espaciais |
| `block_tsv` | ID, caixa uma vez por bloco, texto com `\n` | Menor custo de coordenadas |
| `micro_xml` | `<b box="..." hint="text|heading|margin">texto</b>` | Estrutura próxima da saída desejada |

Todas as caixas são `x1,y1,x2,y2` em coordenadas **da página**, normalizadas
entre 0 e 1000. O texto é escapado no XML; no TSV por bloco, `\\` representa uma
barra invertida e `\n` uma quebra de linha. Confiança, IDs originais, evidência
de cada limite e densidade ficam em `layout.json`, não no prompt.

Na PG001:638, os hints têm respectivamente 5.607, 5.615, 8.158, 5.895 e 6.031
caracteres. O TSV por bloco adiciona 5,1% e o micro XML 7,6% ao controle textual.
O TSV por bloco ocupa 12,6% dos bytes do TSV completo. São medidas de tamanho,
**não contagens de tokens do modelo**. O sistema comum tem 1.021 caracteres;
o XML anterior, quando incluído, tem o mesmo custo em todas as variantes.

## Heurística física

O agrupamento usa caixas das palavras, sem depender dos blocos/linhas estimados
pelo Tesseract. Reúne palavras por altura, busca corredores verticais vazios em
janelas locais e usa hífens nos limites como evidência adicional. Corredores
estreitos persistentes em pelo menos seis linhas ajudam a detectar três colunas.
Não força uma divisão central quando faltam evidências.

As regiões podem ter uma, duas ou três colunas. Títulos centrados/maiúsculos e
palavras atravessando um limite sugerem linhas que abrangem a largura. Marcadores
A/B/C/D junto ao vão podem formar blocos marginais. Nenhuma palavra é descartada
por baixa confiança; cada palavra TSV não vazia aparece exatamente uma vez.
Não há correção ortográfica ou união de palavras hifenizadas nessa preparação.

Controles reais inspecionados no fac-símile:

- **PG001:638:** conserva 939/939 palavras; o bloco direito começa com `ditor
  exigit...` e contém `et adulteros et homicidas...` na segunda linha.
- **PG001:018:** conserva 778/778 palavras; reconhece uma coluna no topo, duas
  no meio e três no trecho inferior. A primeira versão perdia o segundo vão;
  a evidência de corredores estreitos persistentes corrigiu esse caso.
- **PG001:129:** conserva 945/945 palavras; identifica duas colunas e regiões
  de títulos/aparato. O Tesseract também pode não reconhecer conteúdo da imagem;
  conservar seu TSV não significa transcrever toda a página.

Não é uma segmentação adjudicada: trechos curtos e transições ainda podem ser
agrupados com a região vizinha, e dois títulos simultâneos podem virar um único
bloco abrangente. Na PG001:018 há transições fragmentadas e trechos de três colunas
ainda descritos como duas. Isso fica visível nas caixas, bandas e evidências.
As instruções pedem ao modelo para conferir os hints na imagem. Não há evidência
de melhoria da LLM até executar e conferir o A/B.

Os três scripts `layout_analysis*.py` foram consultados, em especial a busca de
gaps por faixas do `(1)`. O módulo novo aproveita a ideia de evidência local,
sem importar esses scripts nem seus ajustes globais para duas colunas.

## Preparar offline

Use TSV da **imagem original**. Uma imagem com borda, rotação ou redimensionamento
exige transformar suas coordenadas antes de usar caixas da imagem original.
A validação rejeita dimensões incompatíveis; dimensões iguais não provam que
o TSV veio da imagem correta. Hashes registram os insumos escolhidos.

```bash
mkdir -p .work/ocr_diff_v2/ab_inputs
OMP_THREAD_LIMIT=2 tesseract teste/PG001/images/PG001-638.png \
  .work/ocr_diff_v2/ab_inputs/PG001-638 -l migne --psm 3 --oem 1 tsv

.venv/bin/python scripts/playgrounds/ocr_ab.py prepare \
  --image teste/PG001/images/PG001-638.png \
  --tsv .work/ocr_diff_v2/ab_inputs/PG001-638.tsv \
  --draft teste/PG001/text/ecf7a6f6-2b76-444e-bcc4-5d19ab58b452-638.txt \
  --out .work/ocr_diff_v2/ab_pg001_638_manual
```

Sem `--draft`, testa transcrição da imagem com hints; com `--draft`, testa correção
do mesmo XML anterior em todas as variantes. `--formats raw_text block_tsv
micro_xml` reduz o conjunto. O diretório de saída precisa ser novo.

Artefatos já preparados nesta sessão:

- `.work/ocr_diff_v2/ab_pg001_638_v2/`: correção, cinco variantes.
- `.work/ocr_diff_v2/ab_pg001_018/`: transcrição, cinco variantes e layout variável.

Cada diretório contém `experiment.json`, `system.txt`, um `.txt` por variante,
`reference.tsv`, `layout.json` e, quando usado, `draft.xml`. Nenhum contém
resultado de inferência. O manifesto registra hashes, formatos e tamanhos.

## Executar manualmente no outro terminal

Exemplo com o Ollama; defina `OCR_AB_MODEL` com o modelo que pretende testar:

```bash
.venv/bin/python scripts/playgrounds/ocr_ab.py run \
  --experiment .work/ocr_diff_v2/ab_pg001_638_v2 \
  --out .work/ocr_diff_v2/ab_pg001_638_results \
  --provider ollama --base-url http://127.0.0.1:11434 \
  --model "$OCR_AB_MODEL" --repeats 3
```

Para um endpoint compatível com chat/completions, use `--provider openai-like`,
`--base-url "$OCR_AB_BASE_URL"` (raiz da API incluindo `/v1`, quando aplicável) e
`--api-key-env OPENAI_API_KEY` se exigir chave. `--image-detail high` ou outro
valor aceito pelo endpoint é opcional e idêntico em todas as variantes.

`--parameters arquivo.json` acrescenta os mesmos campos a todas as chamadas:
no Ollama, por exemplo, `{"options":{"temperature":0.1,"num_ctx":16384}}`;
em um servidor compatível que aceite esses campos, `{"temperature":0.1}`.
Escolha parâmetros suportados pelo modelo/servidor. Não há adivinhação de
parâmetros por nome de modelo nem alteração automática após erro.

Cinco variantes × três repetições fazem **15 chamadas**, sem retries automáticos.
`--order-seed` controla apenas a ordem aleatória das variantes em cada repetição;
não configura a seed do modelo. Execuções adicionais usam novos diretórios.
O runner salva configuração, cronologia, hash do payload, resposta integral,
texto, duração, uso e motivo de término quando fornecidos pelo servidor.
Cabeçalhos de autorização não são gravados. Inputs alterados após a preparação
impedem o início da execução. Falhas ficam registradas e não ganham chamadas extras.

## Avaliar offline

```bash
.venv/bin/python scripts/playgrounds/ocr_ab.py evaluate \
  --experiment .work/ocr_diff_v2/ab_pg001_638_v2 \
  --results .work/ocr_diff_v2/ab_pg001_638_results \
  --targets adulteros homicidas
```

Produz `evaluation.txt`, `evaluation.json` e alinhamentos detalhados em
`evaluation/`. Valida XML, estado, tipos, scripts e caixas; respostas inválidas,
falhas e trabalhos pendentes não entram nas médias de qualidade. Registra
presença dos alvos anotados e compara as quatro dimensões do diff v2: palavras
confiáveis da referência sem par, pares incertos da referência, palavras extras
do candidato e pares incertos do candidato. Alvos devem ser palavras individuais.

As diferenças são pareadas por repetição contra `raw_text`; valores negativos
significam menos divergências com o Tesseract. **Não significam automaticamente
melhor transcrição.** Não há vencedor automático, teste de significância ou
estimativa de qualidade no corpus. Confira as regiões do fac-símile, inclusive
se o formato fez o modelo copiar um erro do Tesseract. Compare também
`ordered_text` contra `block_tsv` para separar reordenação de coordenadas.

Desde `ocr-hints-evaluation-v2`, o relatório apresenta dois perfis separados:

- `spatial_profile` / campo JSON `profile`: preserva a avaliação anterior, com
  caixas produzidas pelo modelo e confiança Tesseract ≥60 para a referência.
- `content_profile`: ignora as caixas da resposta e compara todos os tokens
  lexicais com os textos dos blocos do `layout.json` congelado na preparação.
  Mantém limites entre blocos e normaliza hifenização dentro deles. Não é uma
  verdade de referência independente: a ordem continua sendo uma hipótese.

Os perfis têm tokenização/denominadores diferentes e não são intercambiáveis.
O segundo serve para diagnosticar texto presente que o alinhamento espacial
não localizou. Os alinhamentos adicionais ficam em `*.content-alignment.json`.
Diferenças pareadas de conteúdo ficam em `content_delta_vs_raw_text`.

O diagnóstico sinaliza uma possível confusão espacial quando há pelo menos
20 tokens sem par no perfil espacial e mais de três vezes o número do perfil
textual. Também sinaliza concordância quase literal com o Tesseract quando
igualdades exatas/normalizadas cobrem ≥98% de uma referência com ≥20 tokens.
São gatilhos de inspeção, não testes de qualidade nem prova de cópia incorreta.

## Primeiro ensaio real: PG001:638, GPT-5.4-mini

Execução manual do usuário, uma repetição por formato, imagem `original` e XML
anterior idêntico entre variantes. As cinco chamadas terminaram com `stop`;
a resposta identifica `gpt-5.4-mini-2026-03-17`, com zero tokens de reasoning
reportados. Foram 73.398 tokens de entrada e 10.108 de saída, somando 83.506.
Esses números são uso reportado pela API, não estimativas por caracteres.
Não houve nova inferência na auditoria.

| Variante | Sem par no perfil espacial | Sem par no perfil textual | Igualdades exatas/normalizadas com referência textual |
|---|---:|---:|---:|
| `raw_text` | 45 | 21 | 793/878 |
| `ordered_text` | 395 | 11 | 816/878 |
| `line_tsv` | 6 | 0 | 874/878 |
| `block_tsv` | 2 | 0 | 877/878 |
| `micro_xml` | 2 | 0 | 877/878 |

Conferência do fac-símile revelou três pontos que impedem usar essa tabela
como ranking de qualidade:

1. `ordered_text` colocou 765 palavras, incluindo quase as duas colunas,
   num bloco com caixa `225,95,635,905`; a outra caixa principal contém apenas
   as 32 palavras finais. **395 não são 395 omissões confirmadas.** O texto
   está em grande parte presente, mas sua localização e segmentação falharam.
2. `micro_xml` e `block_tsv` reproduzem praticamente o Tesseract. O cabeçalho
   impresso tem **1275 / 1276** e `S. CLEMENTIS I ROM. PONT. OPERA DUBIA.`;
   as respostas preservam **1375 / 1246** e fragmentos corrompidos como
   `CLEME chis 1 OM. PONT. OPRA` (micro XML muda `CLEME` para `CLEMEN`).
   A contagem baixa mede concordância com uma referência errada nesses pontos.
   `micro_xml` emite apenas A como `nota_marginal`, embora B/C/D também sejam
   visíveis; os formatos TSV acrescentam B/C/D e são penalizados como extras
   no diagnóstico textual porque o TSV não os representou corretamente.
3. `raw_text` contém `adulteros` e `homicidas`, porém ainda omite a linha
   `ditor exigit etiam legem impleri. Et Petrus: Ergo`. A presença das duas
   palavras não demonstra que a omissão original foi resolvida por inteiro.

`raw_text` e `ordered_text` corrigem a leitura do título, apesar de pontuarem
pior contra o Tesseract. Os três formatos com coordenadas preservam a primeira
linha da coluna direita e aderem mais às caixas dos hints; isso é um sinal útil
para layout/cobertura, mas não demonstra ganho global de fidelidade. Precisamos
avaliar separadamente cobertura, correção de leituras OCR e localização,
incluindo alvos negativos anotados na imagem e repetições em outras páginas.
Essa limitação também vale para rankings de versões históricas baseados
somente em concordância com Tesseract.

Resultados locais: `.work/ocr_diff_v2/ab_pg001_638_gpt54mini_r1/`.
`evaluation_v1.json` e `evaluation_v1.txt` preservam o relatório anterior;
`evaluation.json` e `evaluation.txt` incluem os novos diagnósticos. As respostas,
prompts e configuração da execução foram preservados. Fixtures pequenas em
`tests/fixtures/ocr_ab_pg001_638/` registram as cinco respostas, hashes, uso e
anotações manuais; a projeção de layout contém apenas IDs, caixas e textos.

Validação da revisão: 21 testes de `test_ocr_ab.py` passaram, incluindo os cinco
novos controles. Na suíte conjunta, 179 passaram e o teste preexistente
`TestConcurrency::test_concurrent_writes_exactly_one_is_current` falhou com
`database is locked`; a reexecução isolada passou (1/1). Nem `ocr_versions_db.py`
nem esse teste foram alterados. Compilação, whitespace e hashes dos prompts e
respostas originais passaram. Essa ocorrência intermitente de concorrência
permanece registrada, sem mudança no banco de produção.

## Revalidar sem modelos

```bash
.venv/bin/python -m pytest -q tests/test_migne_hints.py tests/test_ocr_ab.py
```

Os testes usam TSVs reais, layouts sintéticos e respostas históricas injetadas
por um cliente falso, com HTTP bloqueado. A diferença entre respostas desse
cliente é uma fixture deliberada, não resultado A/B de modelos. Há controles
de uma/duas/três colunas, mudanças de layout, hífens, baixa confiança, conservação
das palavras, igualdade dos insumos entre variantes, respostas inválidas,
interrupções, hashes, credenciais e tamanhos dos prompts. As imagens não são
versionadas. A migração do cache TXT de produção para TSV continua numa segunda etapa.

Validação conjunta em 2026-11-02: **175 testes passaram em 23,05 s**, incluindo
`test_ocr_diff_v2`, `test_ocr_version_selection`, `test_migne_hints`, `test_ocr_ab`,
`test_offline_ocr`, `test_ocr_versions_db`, `test_main2_tesseract_direct`,
`test_main2_vlm_provider` e `test_main2_judge_routing`. Compilação dos módulos/CLIs
e verificação de whitespace passaram. O SHA256 do texto PG001:638 permaneceu
`7852ff176921e7d109a9fce0a3c52c25b5876228e3149263c803c8873b8fba65`.
