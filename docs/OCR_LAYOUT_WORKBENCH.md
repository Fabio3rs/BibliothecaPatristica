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
