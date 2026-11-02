# OCR versions com layout: oficina offline

A finalidade é encontrar e corrigir palavras que escaparam ao `main2.py`, refinar caixas LLM quando a evidência permitir e comparar novamente. O módulo `offline_ocr` é um complemento independente de `ocr_versions_db.py`: importa versões em modo somente leitura ou arquivos explícitos e grava em `.ocr-workbench/layouts.sqlite3`. Não altera `is_current`, imagens, textos do corpus, bancos de produção ou o site Astro.

## Executar

Python 3.10+, Tesseract e os modelos necessários devem estar instalados. Dependências Python específicas: `python -m pip install -r offline_ocr/requirements.txt`.

```bash
# Uma página; a imagem é usada sem redimensionamento, rotação ou recorte.
python -m offline_ocr ingest \
  --image teste/PG013/images/PG013-308.png \
  --text teste/PG013/text/PG013-308.txt \
  --lang migne --psm 3 --bbox-tolerance 25

# Importar uma versão histórica, incluindo engine/prompts e hashes.
python -m offline_ocr ingest \
  --image CAMINHO/IMAGEM.png \
  --versions-db data/ocr_versions.db --result-id 123

# TSV já existente, obrigatoriamente nas coordenadas da imagem original.
python -m offline_ocr ingest --image IMAGEM.png --text OCR.txt --tsv IMAGEM.tsv

python -m offline_ocr serve --port 8765
# Abrir http://127.0.0.1:8765

python -m offline_ocr list
python -m offline_ocr export CHAVE --output .ocr-workbench/comparacao.json
python -m offline_ocr export CHAVE --format xml --output .ocr-workbench/corrigido.txt
python -m offline_ocr export CHAVE --format png --output .ocr-workbench/overlay.png
```

`--db CAMINHO` é opção global, antes do subcomando. `--keep-hyphens` cria comparação alternativa sem unir hifens; `--tessdata-dir` seleciona os modelos. PSM e OEM são configuráveis e integram a identidade do cache. Não existe varredura automática de `teste/`.

Lote explícito (caminhos relativos ao diretório de execução), em JSONL:

```json
{"image_path":"teste/PG013/images/PG013-308.png","text_path":"teste/PG013/text/PG013-308.txt","lang":"migne"}
{"image_path":"teste/PG013/images/PG013-941.png","text_path":"teste/PG013/text/PG013-941.txt","tolerance":25}
```

```bash
python -m offline_ocr batch .ocr-workbench/sample.jsonl --workers 2
```

O manifesto é lido em streaming e a fila tem no máximo duas tarefas por processo. Cada processo abre a própria conexão SQLite; WAL, transações curtas e chaves únicas protegem a deduplicação. Tesseract usa `OMP_THREAD_LIMIT=1` por padrão no lote, evitando multiplicar threads por processo. Falhas são reportadas por página; o lote termina com código 1 se houver falha. Use poucos processos inicialmente: modelos Tesseract e imagens têm custo de memória significativo.

## Revisão no navegador

1. Escolha uma comparação; o filtro de páginas aceita prefixo (ex.: `PG013-941`) e a lista é paginada em 100 versões. Ligue/desligue blocos LLM, aproximações por texto, linhas, palavras Tesseract, divergências e ajustes. Opacidade e zoom são independentes.
2. Clique numa palavra do texto corrido, numa divergência ou numa caixa para ver os fragmentos, offsets, confiança Tesseract, diferenças de caracteres e relações candidatas.
3. Em **Ajustar palavras e bbox**, edite a palavra/trecho ou preencha com o texto Tesseract. **Usar texto Tesseract** apenas preenche o campo: é necessário salvar e conferir a imagem.
4. Para caixas, escolha o bloco; use coordenadas 0–1000, uma aproximação baseada em âncoras ou desenhe na imagem. A sugestão não é aplicada automaticamente. Os campos numéricos também permitem operar sem arrastar.
5. Salve. Revisões são cumulativas e imutáveis, com pai, nota e horário; restaurar gera outra revisão. Uma aba com versão antiga recebe conflito em vez de sobrescrever outra revisão.
6. **Recomparar ajustes em nova versão** usa o XML ajustado e o mesmo artefato Tesseract, preserva a relação com a revisão anterior e recalcula as relações e os identificadores. Os IDs são sempre locais à respectiva versão.
7. Exporte JSON completo, XML ajustado ou, opcionalmente, PNG com as camadas selecionadas. PNG acrescenta legenda e registro de ajustes abaixo da imagem. Nenhum raster é criado na ingestão.

A aplicação serve somente rotas explícitas e só escuta em `127.0.0.1`. Não permite navegar pelo filesystem. POSTs exigem JSON e origem local. Não é um servidor para exposição à internet.

## Modelo de evidências e idempotência

- `artifacts`: chave SHA256, tipo (`llm`/`tesseract`), payload e data. Fonte bruta é preservada: XML/texto e TSV.
- `comparisons`: chave SHA256, imagem/hash, referências aos dois artefatos, alinhamento e data. Não existe estado global `is_current` compartilhado com produção.
- `revisions`: comparação, pai, palavras/caixas revisadas, nota e data. Revisões não reescrevem artefatos ou alinhamentos anteriores.
- Índices: PKs das chaves, consultas por hash da imagem, artefato LLM/Tesseract, prefixo do nome, ordem de criação e histórico `(comparison_key,id)`. Consulta de uma página carrega seus artefatos, não o corpus. Palavras e relações estão no JSON por página; não há índice lexical global de palavras nesta versão.

Identidade Tesseract: SHA256 da imagem, versão do executável, SHA256 de cada `.traineddata`, idioma, PSM, OEM, pré-processamento e versão do parser. TSV importado tem hash próprio e proveniência de importação, sem inventar versão do reconhecedor. Identidade LLM: imagem, texto, parser e proveniência. Identidade da comparação: artefatos, versões de normalização/alinhamento e parâmetros. Mudar o XML ou o algoritmo reaproveita a extração Tesseract; mudar imagem/modelo/configuração invalida seu cache.

O JSON usa caixas `[x1,y1,x2,y2]` na escala 0–1000; Tesseract também preserva `bbox_px` da imagem original. A transformação da extração integrada é identidade. Coordenadas LLM são **alegações da fonte**, não coordenadas confirmadas de palavras.

Cada token corrido possui `id`, `text`, `flow_span` (intervalo semiaberto de caracteres Unicode, não bytes), `block_id`, transformação e `pieces`. Cada fragmento LLM preserva intervalos no XML original, inclusive entidades e tags intermediárias; cada fragmento Tesseract preserva palavra, bbox e confiança. `ver-\nbum` pode tornar-se `verbum` com dois fragmentos e duas caixas. Não se substituem duas caixas por um retângulo que cubra o espaço entre elas.

As correções são patches nos intervalos originais; entidades XML são escapadas. Ao corrigir uma palavra unida, o novo texto fica no primeiro fragmento e os demais fragmentos são removidos, mantendo o markup e as quebras originais. O diff automático continua acessível; a nova comparação passa a ter seus próprios offsets.

## Comparação orientada a OCR

A comparação usa duas fontes de evidência, sem dicionário de palavras válidas:

- Geometria: sobreposição com a caixa original, tolerância configurável (25 unidades = 2,5% da largura/altura) e envelope das palavras que ancoram um bloco.
- Texto: coincidências únicas, evidência aproximada perto do bloco e ordem de palavras dentro da região.

Três ou mais âncoras textuais distintas permitem sugerir um envelope. O JSON mantém caixa original, envelope, IDs das âncoras e diferença entre suas bordas. Candidatos próximos em pontuação e correspondências distantes da caixa original são incertos. Não há calibração estatística: pontuação e similaridade **não são probabilidades**.

O diff mantém sequências exatas como âncoras e resolve trechos ruidosos por programação dinâmica com inserção, remoção, substituição e divisão/junção de até três tokens. Custos usam Damerau–Levenshtein (inclui transposição), equivalências tipográficas e algumas confusões OCR: ligaturas, `ſ/s`, diacríticos, `rn/m`, `cl/d`, `1/l`, `0/o`. Essas equivalências apenas reduzem custos: **não tornam uma palavra diferente igual, nem corrigem silenciosamente a grafia**. A sequência de caracteres e o diff estrito permanecem disponíveis.

Regiões sem âncoras que excedam 250 mil células de DP são marcadas `unresolved`, evitando consumo quadrático descontrolado. Estados: `equal`, `normalized_equal`, `replace`, `split_merge`, `llm_only`, `tesseract_only`, `unresolved`. Todos os tokens permanecem representados, mesmo sem correspondência. O alinhador é substituível e versionado: não se afirma que estes pesos sejam ótimos para todos os layouts da Patrologia.

Referências técnicas: [formato TSV oficial do Tesseract](https://github.com/tesseract-ocr/tessdoc/blob/main/Command-Line-Usage.md) e [Damerau–Levenshtein no RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/Usage/distance/DamerauLevenshtein.html).

## Materiais existentes analisados

- `main2.py`: prompts `PROMPT` e verificação; blocos `tipo/script/bbox`, ordem de colunas, notas marginais A–D; `<notas>` é comentário, não transcrição.
- `ocr_versions_db.py` e `docs/VERSIONING_PLAN.md`: proveniência e hashes das versões.
- `tools/ocr_overlay.py`: hierarquia TSV e visualização de palavras/linhas.
- `layout_analysis.py`: heurísticas especializadas em duas colunas. Não aplicadas globalmente: capa, índices, notas e páginas multilíngues não obedecem sempre a essa hipótese.
- `tools/tesseract_compare.py`, `tools/tesseract_playground.py`: configuração/pré-processamento existentes; rotação/borda exigiriam transformação geométrica explícita.
- `scripts/limpeza_ocr.py` e testes XML: união de hifens e extração de blocos. A limpeza voltada à busca não foi reutilizada diretamente porque filtra conteúdo/idiomas e perde offsets.
- Amostras reais: `teste/PG013/{images,text}/PG013-308.*` e `PG013-941.*`, incluindo texto corrido, hifenização, blocos sobrepostos e aparato misto.

## Pitfalls e limites atuais

| Risco | Tratamento / limite |
|---|---|
| Bbox LLM deslocado, sobreposto ou inventado | Tolerância, âncoras e candidatos; caixas inválidas ficam sem localização. Sugestões requerem conferência. |
| Envelope de âncoras incompleto | Pode excluir bordas sem palavras reconhecidas ou incluir um outlier; nunca aplicado automaticamente. |
| Palavras repetidas, citações e colunas paralelas | Evidência textual sozinha não basta; ambiguidades ficam marcadas. Não há garantia de alinhamento semântico. |
| Ordem Tesseract mistura colunas/marginais | Blocos LLM ajudam a separar regiões, mas a ordem interna segue Tesseract; pode exigir outra configuração PSM ou revisão. |
| Hífen legítimo no fim da linha | Junção é candidata, reversível, limitada ao mesmo bloco (e parágrafo Tesseract); `--keep-hyphens` permite comparar. Não se une entre páginas ou blocos. |
| Bbox de palavra LLM | É inferido pelas relações Tesseract; não há localização física confirmada para `llm_only`. Ajuste manual de bbox nesta versão é por bloco. |
| Erro em ambos os OCRs | Concordância não prova correção. A imagem permanece referência; não se usa confiança Tesseract como probabilidade. |
| Escritas raras/RTL/tabelas | Dados e scripts preservados, mas qualidade depende dos modelos e da ordem do Tesseract. Não há segmentador especializado validado para todos esses casos. |
| TSV externo de imagem transformada | Dimensões/bounds são validados; transformação que mantenha dimensões (deskew, recorte compensado) não pode ser detectada automaticamente. Use TSV da imagem exata. |
| XML malformado | Parser recupera blocos e avisa; exportar ajustes não repara a estrutura XML. Confira antes de promover a produção. |
| Alteração de imagem depois da ingestão | Viewer/raster recusam hash divergente; ingestão verifica alteração durante extração. |
| Duas abas editando | Revisão-base e transação evitam perda silenciosa; a segunda recebe HTTP 409. |
| Escala do corpus | JSONs por página armazenam evidência detalhada e podem ocupar muito espaço. Dimensione armazenamento e processos em amostras antes do lote completo. |
| Qualidade dos pesos | Testes de contrato e duas páginas reais não substituem benchmark anotado por especialistas; há falsos positivos e falsos negativos. |

## Validação

```bash
python -m pytest tests/test_offline_ocr.py tests/test_ocr_versions_db.py -q
node --check offline_ocr/static/app.js
python -m compileall -q offline_ocr
```

Frontend estático servido pelo módulo Python, sem etapa de bundling ou dependência do build Astro. Evidências do navegador ficam em `.ocr-workbench/evidence/`, fora do versionamento. O arquivo `tests/test_offline_ocr.py` cobre offsets, entidades XML, Unicode, hifens, palavras inválidas, inserções, split/merge, bboxes aproximados, cabeçalhos, concorrência, revisões, exportação e índices.

### Resultado da validação de implementação (2026-09-27)

- 75 testes passaram: `tests/test_offline_ocr.py` + `tests/test_ocr_versions_db.py`.
- `node --check offline_ocr/static/app.js`, `python -m compileall -q offline_ocr` e `git diff --check` passaram. Não há bundler/lint dedicado nesta aplicação independente.
- Manifesto de duas páginas reais (`PG013-308`, `PG013-941`) processado com dois workers. Repetição retornou `reused=true` e `tesseract_cached=true` para ambas.
- Navegador em `http://127.0.0.1:8765`: 1920×1080 e 375×812, sem overflow horizontal, erros de console ou falhas de rede relevantes.
- Exercitados: filtro de páginas, seleção de diferenças, salvamento/restauração de palavra e bbox, histórico, aproximação e desenho de caixa, nova comparação, respostas XML/PNG e exports CLI JSON/XML/PNG.
- Screenshots: `.ocr-workbench/evidence/desktop.png`, `mobile.png`, `desktop-edit.png`; logs `console-final.txt`, `network-final.txt`; amostras em `sample-results.json`.
- Um teste concorrente preexistente revelou transação aberta após colisão no cadastro de engine. `ocr_versions_db.get_or_create_engine_version` agora faz rollback antes da releitura; regressão determinística adicionada. Nenhum banco de produção foi migrado ou alterado.
- Corrigida a associação indevida de um número de cabeçalho ao envelope aproximado do corpo do texto; caso coberto por regressão. Correspondência textual distante é marcada incerta.

## Diff v2 experimental e investigação de PG001:638 (2026-11-02)

Implementação independente: `offline_ocr/diff_v2.py`. O alinhador v1 da oficina e o
verificador de `main2.py` continuam com seu comportamento anterior. A v2 produz
relações entre ocorrências, variantes OCR, substituições incertas, junções/divisões,
trechos reordenados e candidatos a omissão com contexto, offsets e caixas. Não emite
aprovação automática nem corrige transcrições. A CLI fica em
`tools/compare_ocr_alignment.py` e o benchmark em `tools/benchmark_ocr_alignment.py`.

### Origem da regressão

Consultas somente leitura, limitadas a `volume_id='PG001' AND page_num=638`,
recuperaram cinco registros em `data/ocr_versions.db`:

| ID | Engine/modelo | Registro `created_at` | Trecho conhecido |
|---|---|---|---|
| 44725 | Tesseract `lat+grc`, legado | 2026-04-14 17:14:55 | Contém `adulteros` e `homicidas`, com ruído ao redor |
| 50266 | Origem desconhecida, legado XML | 2026-04-14 17:51:25 | Contém o trecho |
| 357449 | Ollama `qwen3.5:cloud` | 2026-04-15 21:28:43 | Contém as duas linhas e a continuação da nota inferior |
| 809912 | OpenAI `gpt-5-mini` | 2026-06-26 01:44:44 | Omite o início da coluna direita e duplica parte da frase seguinte |
| 853769 | Tesseract `migne`, cache | 2026-07-18 05:20:51 | Contém o trecho |

Datas de registros legados não necessariamente são datas da geração original.
Todos os registros têm o mesmo hash de imagem
`504f7abd1986eea57e89cf388f55512fd5eb454468f1b7d18831b2c902e9ed26`.
O arquivo `teste/PG001/text/ecf7a6f6-2b76-444e-bcc4-5d19ab58b452-638.txt`
corresponde integralmente ao registro 809912, SHA256
`7852ff176921e7d109a9fce0a3c52c25b5876228e3149263c803c8873b8fba65`.
Seu motivo é `verify_failed_do_not_reprocess_compare`, com `prompt_key= PROMPT`
e `meta_json` nulo. No código atual, esse motivo seleciona a geração direta,
sem o prompt de comparação. O banco não permite reconstruir todas as condições
da chamada histórica nem atribuir a causa interna do erro ao modelo.

Conferência do fac-símile `teste/PG001/images/PG001-638.png`, coluna impressa 1276:

```text
ditor exigit etiam legem impleri. Et Petrus: Ergo
et adulteros et homicidas salvat, si cognoverint
```

`ditor` continua `con-` no fim da coluna esquerda. O XML atual começa a coluna
direita em `eum, bonos autem...`, repete parte dessa frase e também perde a
continuação inferior que começa em `lium: Creator mundi corpora condidit...`.
A versão 357449 e o Tesseract preservam essa continuação. A anotação de referência
dos testes adjudica especificamente `adulteros` e `homicidas`; não trata todas as
divergências da página como erros confirmados.

O comparador antigo retorna sobreposição **0,889163** e recall **0,829885** contra o
cache `migne`, apesar da omissão. Seus conjuntos não preservam multiplicidade;
seu segmentador compara blocos XML e parágrafos Tesseract pela posição ordinal.
Esses números não equivalem a uma reconstrução da decisão histórica de aceitação.

### Tesseract e rejeição

Tesseract local 5.3.4, `migne`, OEM 1, PSM 3. A execução do pré-processamento atual
de `main2.py` (`adaptive_soft`, com borda de 50 px) reproduziu exatamente o cache
853769: 5.627 caracteres, SHA256
`953e77cc87fd98776c037289bc5c554b4f10e5ef5418f28317aa7c25c094cb1d`.
Adicionar `-c tessedit_zero_kelvin_rejection=1 -c textord_no_rejects=1` produziu o
mesmo texto e hash. Nesta amostra, a omissão conhecida não é do Tesseract.
Existe também um cache antigo `lat+grc:adaptive_soft:2.0:31:4` degradado, de 467
caracteres; ele não é a referência `migne` usada neste benchmark.

O TSV de teste foi extraído da imagem original, sem borda nem transformação.
Tem 2.480 × 3.509 px; o texto bruto dessa execução tem 5.630 caracteres e diferenças
menores em relação ao cache. O manifesto em `tests/fixtures/ocr_diff_v2/manifest.json`
registra comando, hashes dos artefatos e do modelo. As fixtures incluem apenas
texto/XML/TSV; imagens e bancos não são versionados.

### Algoritmo e limites

Reutiliza o parser com offsets, custos de caracteres e propostas de regiões do
revisor offline; preserva a implementação v1. A v2 acrescenta:

- Tokens Unicode de todos os comprimentos, incluindo grego, números e repetições;
  pontuação e metadados XML ficam fora do escopo lexical.
- Âncoras sequenciais, alinhamento por programação dinâmica limitada e distinção
  entre variantes tipográficas, possíveis erros OCR e substituições incertas.
  Similaridade ortográfica não estabelece equivalência semântica.
- Junções/divisões de até três tokens, restritas a vizinhança física quando há TSV;
  hifenização só é candidata dentro do mesmo bloco, com proveniência preservada.
- Recuperação de sequências exatas reordenadas, com verificação de unicidade e
  consumo único de cada token. Gaps opostos adjacentes permanecem divergência incerta.
- Proteção contra envelopes textuais que invadem outra coluna: uma palavra contida
  majoritariamente numa única caixa original não é desviada por uma coincidência
  lexical distante. A substituição de região fica registrada como incerta.

Com TSV, a v2 detecta a omissão atual e não a aponta na versão anterior. Em 24
remoções controladas de 1, 2, 5 e 10 palavras distribuídas nas duas colunas,
**108/108 tokens de referência avaliáveis** foram detectados, sem novos candidatos
extras além dos já existentes na comparação de controle. Os seis controles com
uma letra corrompida continuam pareados, sem virar omissões. São controles
construídos na mesma página, não uma estimativa de precisão/recall no corpus.

O desenvolvimento desses controles encontrou e corrigiu um caso em que remover
`Deo` da esquerda desviava sua referência para a direita, além de uma junção indevida
entre palavras distantes. Essas regressões têm testes próprios.

**Ainda não integrar automaticamente em `main2.py`.** O cache plano mistura colunas:
nesse modo, `adulteros` e `homicidas` ainda podem ser pareados com palavras de outra
região como substituições incertas. A CLI declara `text_order_only` e avisa sobre
essa limitação. Mesmo com TSV, há divergências não adjudicadas no cabeçalho/aparato;
ausência numa fonte pode ser ruído, ordem de leitura ou omissão da outra fonte.

A integração precisa primeiro de TSV com identidade de cache adequada
(imagem/modelo/configuração/pré-processamento) e coordenadas consistentes, além de
amostras anotadas de outros layouts/volumes para calibrar uma política de reprocessamento.
O TSV de uma imagem com borda/rotação não pode ser associado diretamente às caixas
normalizadas da imagem original. A v2 mantém `automatic_acceptance=false`.

### Reproduzir

```bash
# Sem Tesseract, modelos, bancos ou acesso ao corpus: fixtures pequenas.
.venv/bin/python tools/benchmark_ocr_alignment.py \
  --output .work/ocr_diff_v2/benchmark.json

# Gerar nova referência espacial a partir da imagem original.
mkdir -p .work/ocr_diff_v2/PG001_638
OMP_THREAD_LIMIT=2 tesseract teste/PG001/images/PG001-638.png \
  .work/ocr_diff_v2/PG001_638/migne_raw -l migne --psm 3 --oem 1 txt tsv

.venv/bin/python tools/compare_ocr_alignment.py \
  --candidate teste/PG001/text/ecf7a6f6-2b76-444e-bcc4-5d19ab58b452-638.txt \
  --tsv .work/ocr_diff_v2/PG001_638/migne_raw.tsv \
  --image teste/PG001/images/PG001-638.png \
  --output .work/ocr_diff_v2/PG001_638/current_v2.json

.venv/bin/python -m pytest -q \
  tests/test_ocr_diff_v2.py tests/test_offline_ocr.py tests/test_ocr_versions_db.py \
  tests/test_main2_tesseract_direct.py tests/test_main2_vlm_provider.py \
  tests/test_main2_judge_routing.py
```

Para texto sem coordenadas, substitua `--tsv ... --image ...` por `--reference OCR.txt`.
O JSON completo contém `links`, `omission_candidates`, `addition_candidates`,
`coverage`, `region_evidence`, hashes e avisos; stdout apresenta apenas o resumo.
O benchmark verifica hashes de fixtures e informa separadamente o acerto nos alvos,
as mutações controladas e os impedimentos à integração.

Evidências locais: `.work/ocr_diff_v2/PG001_638/history.json`,
`cache_metadata.json`, `current_v2.json`, `previous_v2.json`,
`.work/ocr_diff_v2/investigation.json` e `.work/ocr_diff_v2/benchmark.json`.
Nenhum texto do corpus, histórico de produção ou registro `is_current` foi alterado.

## Seleção manual de versões históricas

`tools/select_ocr_versions.py` usa o diff v2 espacial e consulta o banco em modo
somente leitura. `scan` produz propostas e um relatório; `apply` exporta as
recomendações de um lote já conferido; `rollback` restaura seus backups.
Nenhum desses comandos altera `is_current` no banco. Não há integração com
`main2.py` nem execução agendada.

A referência TSV tem cache próprio, calculado da imagem original com `migne`.
A identidade inclui hash da imagem, versão do Tesseract, hashes dos modelos,
idioma, PSM/OEM e flags de rejeição. O cache registra o hash do TSV.
Só concorrem XMLs não vazios com hash de texto íntegro e mesma imagem;
resultados Tesseract servem como referência, não como transcrição exportável.

Na política v2 são comparadas quatro dimensões em **dois perfis**, sem reduzi-las
a uma nota única: referência sem par, referência incerta, candidato sem par e
candidato incerto. O perfil espacial usa confiança Tesseract ≥60 e as caixas da
resposta; o textual usa todas as palavras na ordem física estimada do TSV,
independentemente dessas caixas. Seus denominadores/tokenizações diferem.
Uma recomendação precisa dominar todas as alternativas distintas nos dois
perfis, sem piorar qualquer dimensão. Também precisa melhorar o conteúdo em
pelo menos três tokens frente ao filesystem: corrigir apenas caixas não basta.
Empates, trocas entre dimensões, referência insuficiente, imagens ausentes,
caminhos ambíguos e edições locais fora do histórico exigem revisão. Variantes
OCR pareadas não são contadas como omissões. O resultado é um ranking heurístico,
não a afirmação de que uma versão esteja inteiramente correta.

Concordância quase literal com o Tesseract (≥98%, pelo menos 20 tokens), confusão
entre perfis espacial/textual e bboxes ausentes/inválidas também impedem exportar
uma recomendação nova. Esses gatilhos são conservadores: podem reter uma cópia
correta para revisão, pois concordância com OCR não estabelece fidelidade à imagem.
Os diagnósticos são compartilhados com o A/B em `offline_ocr/quality.py`.

Na PG001:638, a primeira política (v1) recomendou **357449**. Perfis espaciais observados:

| Versão | Referência sem par | Referência incerta | Candidato sem par | Candidato incerto |
|---|---:|---:|---:|---:|
| 50266 | 74 | 15 | 44 | 22 |
| 357449 | 5 | 1 | 11 | 9 |
| 809912, arquivo atual | 80 | 19 | 42 | 26 |

As categorias de referência dessa tabela usam confiança Tesseract ≥60. Os
alinhamentos completos preservam também os tokens com confiança menor.
Nenhuma proposta foi aplicada ao corpus nesta sessão.

**A revisão v2 mudou essa decisão para `review_required`.** 809912 é superada nos
dois perfis, mas 50266 e 357449 têm vantagens diferentes: no perfil textual,
357449 tem 6 tokens de referência sem par contra 40 da 50266, porém 8 tokens
do candidato sem par contra 5. A política não declara uma vencedora entre elas.
O relatório apresenta `undecided_ids=[50266,357449]` e `dominated_ids=[809912]`.
Relatório atual: `.work/ocr_diff_v2/selection_pg001_638_review_v2/report.txt`.

```bash
.venv/bin/python tools/select_ocr_versions.py scan \
  --volumes PG001 --pages 638 \
  --run-dir .work/ocr_diff_v2/selection_manual_638

.venv/bin/python tools/select_ocr_versions.py report \
  --run-dir .work/ocr_diff_v2/selection_manual_638

# Após conferir o relatório: exportação manual explícita.
.venv/bin/python tools/select_ocr_versions.py apply \
  --run-dir .work/ocr_diff_v2/selection_manual_638

.venv/bin/python tools/select_ocr_versions.py rollback \
  --run-dir .work/ocr_diff_v2/selection_manual_638
```

O relatório histórico da v1 está em
`.work/ocr_diff_v2/selection_pg001_638_v2/report.txt`. Cada lote novo possui
`manifest.json`, `proposals.jsonl`, `events.jsonl`, `report.txt` e evidências por
versão, além de TSVs congelados em `references/` e alinhamentos textuais separados.
`apply` acrescenta `backups/`, motivos, IDs e hashes de cada escrita.
O plano, imagens, histórico (incluindo hashes recalculados dos textos e identidade
das engines), arquivos atuais e ambas as evidências são revalidados antes
da escrita; exportação é atômica por arquivo, com backup e log de intenção.
Não é uma transação atômica de todo o lote. Interrupções após uma escrita podem
ser recuperadas pelos eventos de intenção. `rollback` recusa sobrescrever
uma edição posterior. Aplicações e restaurações repetidas são idempotentes.

A revisão também corrige quatro problemas operacionais encontrados no código:

- A presença de `<bloco/>` era suficiente para um XML concorrer. Agora a página
  precisa ser `com_texto`, ter blocos não vazios, tipos/scripts conhecidos e
  estrutura suportada. Caixas inválidas ficam elegíveis para diagnóstico e
  refinamento, mas impedem uma nova recomendação exportável.
- Lotes diferentes só travavam seus próprios logs. Agora `apply` e `rollback`
  compartilham um lock no diretório do corpus, sem criar arquivo nele. É um lock
  cooperativo entre essas CLIs; não coordena editores ou pipelines externos.
- `apply` confere política completa, decisão recalculada, perfis derivados das
  evidências e TSV congelado. Alterar o conteúdo de uma versão não selecionada
  sem atualizar seu hash também invalida o plano.
- Uma interrupção no fim do JSONL podia impedir restauração. O fragmento final
  incompleto é preservado em `journal-tail-*.bin`, removido da sequência de
  eventos e sua recuperação é registrada; corrupção no meio do log é recusada.

Planos da política v1 continuam disponíveis em `report` e `rollback`; `apply`
exige um novo `scan` com a política atual (`policy_changed_rescan_required`).

O escopo é explícito: `--volumes PG001 PG002` ou `--all-volumes`, e no máximo
25 páginas por lote por padrão. `--pages 637-639,650` restringe páginas;
`--limit` muda o tamanho do lote. O manifesto/relatório informa `next_after`
quando há mais páginas: passe esse valor em `--after PG001:638` no próximo
`scan`, com novo `--run-dir` e mesmo escopo. A ordem é por volume/página.
Isso permite percorrer o banco manualmente sem carregar/processar todo o
corpus de uma vez. Erros de páginas ficam no log e o cursor avança;
reexecute essas páginas por `--pages` quando corrigir a causa.
`--no-rejects` ativa as duas flags de rejeição em uma referência independente.

```bash
.venv/bin/python -m pytest -q tests/test_ocr_version_selection.py
```

Os testes usam banco e filesystem temporários, cobrindo ranking, empates,
aplicação/restauração, interrupção, hashes, fontes alteradas, flags/cache,
paginação e preservação de edições locais. O fluxo CLI foi escolhido para esta
etapa; não há mudanças na UI do revisor.

### Refinamento experimental: bboxes e trechos complementares

`refine` trabalha sobre um `scan` v2 e uma **versão base explícita**. Cria um novo
diretório fora do corpus, com `base.xml`, `draft.xml`, `review.json`, `review.txt`
e `events.jsonl`. Não escreve no banco, não muda `is_current` e não exporta o
rascunho para `teste/`. Não usa LLM.

```bash
.venv/bin/python tools/select_ocr_versions.py refine \
  --run-dir .work/ocr_diff_v2/selection_pg001_638_review_v2 \
  --page PG001:638 --version-id 809912 \
  --out .work/ocr_diff_v2/refine_manual_638 \
  --repair-bboxes --merge-candidates
```

`--repair-bboxes` usa sequências de três tokens únicas na página e no candidato,
com pelo menos 12 tokens de suporte e cobertura ≥85% do bloco. As âncoras precisam
concordar, preservar ordem e pertencer a uma única região física estimada. A nova
caixa envolve as palavras TSV pareadas; **somente o valor do atributo bbox muda**.
Textos, notas e demais bytes são preservados. Marcadores isolados, regiões pouco
cobertas, citações repetidas e blocos que misturam colunas provocam abstenção.
Isso não resolve segmentação nem confirma limites de tinta fora do TSV.

`--merge-candidates` identifica gaps de pelo menos três tokens e busca trechos
contíguos em outras versões elegíveis da mesma imagem, com correspondência
exata/normalizada/tipográfica à referência. Registra versão, bloco, offsets,
texto original do doador e contexto. **Não insere esses trechos em `draft.xml`.**
Votos entre versões não são evidências independentes, e uma divergência pode ser
correção do OCR, ordem de leitura ou uma omissão. Por isso, a fusão textual completa
continua exigindo conferência; esta opção automatiza a busca de complementos.

Na PG001:638, a versão base 809912 gerou uma proposta de bbox para o bloco direito,
de `510,90,960,820` para `543,107,903,821`, e três candidatos a complemento:
as linhas iniciais da direita, recuperadas integralmente de 357449, e dois trechos
da nota inferior. A caixa não recupera o texto ausente. O XML `ordered_text`
do A/B, que misturou quase duas colunas, é um controle negativo: o refinador recusa
trocar essa região por uma caixa gigante que esconderia a falha de segmentação.

```bash
.venv/bin/python -m pytest -q \
  tests/test_ocr_version_selection.py tests/test_ocr_refinement.py tests/test_ocr_ab.py
```

Validação desta revisão: **201 testes passaram em 51,60 s** na suíte conjunta
de diff, seleção, refinamento, hints, A/B, revisor offline, histórico e integrações
existentes do `main2.py`. Os oito testes de refinamento/exportação do rascunho
também passaram após o acréscimo do relatório textual. Compilação e whitespace
passaram. Nenhuma inferência nem exportação para o corpus foi executada.
Evidência do refinamento real:
`.work/ocr_diff_v2/refine_pg001_638_review_v2/review.txt` e `review.json`.

## A/B de formatos compactos

Preparação offline, heurísticas de uma/duas/três colunas e execução manual estão
descritas em [OCR_AB_HINTS.md](OCR_AB_HINTS.md). O experimento reaproveita o diff v2
para avaliar respostas, mantendo a integração com a pipeline de produção separada.
