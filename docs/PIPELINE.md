# Pipeline Técnico — Patrística Digital

*English abstract*: End-to-end pipeline that ingests PDFs, renders pages, runs OCR (Tesseract or LLM-based with deterministic gating), writes per-page summaries/keywords in PT-BR only, generates embeddings/clusters, and builds a custom Pagefind index (sharded) because the raw corpus exceeds GitHub Pages limits. Prompts and outputs are PT-BR only; English text here is reference. Manual review is limited due to volume, so treat all AI outputs as draft and validate against sources.

## Visão geral
- **Objetivo**: manter um fluxo reprodutível de ingestão → OCR → validação → resumos/keywords → embeddings/clustering → busca web estática.
- **Público**: operadores técnicos do projeto e colaboradores que rodem jobs localmente ou em CI.
- **Escopo**: scripts CLI (Python/Node/Bash) e artefatos em disco. Fora de escopo: detalhes de UI Astro além do build.

## Pré-requisitos rápidos
- Python 3.10+, Node 18+ (ESM/top-level await), Tesseract com traineddata lat/grc, Poppler (`pdftocairo`).
- `pip install -r requirements.txt`; para front-end: `cd web && npm install` (+ `npm install --save-dev pagefind`).
- Variáveis de ambiente: `OPENAI_API_KEY`, endpoints opcionais (`OPENAI_BASE_URL`, `OLLAMA_URL`).
- Hardware sugerido: OCR/LLM CPU multi-core; UMAP/HDBSCAN e Pagefind completo pedem >16–32 GB RAM; paralelismo alto em keywords causa tráfego/custo em provedores externos.

## Arquitetura de dados (diretórios/artefatos)
- Corpus OCR: `teste/P*/text/*` (~289.852 páginas, ~1B tokens). PDFs/imagens não são versionados.
- Intermediários: `ocr_out/`, `pages/` (imagens, XML, blocos), `data/tesseract.db` (cache OCR), `data/ocr_eval.db` (logs LLM judge).
- Resumos/keywords: `data/patristica_resumos.db`, `data/patristica_keywords.db`.
- Web build: `web/dist/` (Astro), `web/public/` shards (volumes/page_blocks/keywords), índice Pagefind custom em `web/public/pagefind/`.
- Logs: `logs/keywords_parallel`, `logs/keywords_parallel/*.log`, e saídas de avaliação em `data/ocr_eval.db`.

## Ingestão e OCR (`main2.py`)
- **Conversão**: PDF → PNG via `pdf2image`/`pdftocairo` (cache em `out/<pdf>/images`), texto em `out/<pdf>/text`.
- **Defaults**: `--dpi 300`, `--lang lat` (ajuste para fra+lat+grc+ell+syr), `--procs 12`, `--omp-threads 2` (limita OpenMP), `OMP_THREAD_LIMIT=4`.
- **Provedor VLM**: `--algorithm {ollama,openai}`, com `ollama` como padrão; modelos padrão `DEFAULT_LLM_MODEL=qwen3.5:397b-cloud` (Ollama) ou `gpt-5` (OpenAI). Tesseract não é um backend final: ele funciona como leitura auxiliar para verificação/reconciliação ou, com `--tesseract-cache-only`, para aquecer o cache. `--ollama-url`, `--openai-base-url`, `--openai-api-key` permitem overrides.
- **Flags úteis**: `--first/--last` (faixa), `--concat` (agrega em `texto_extraido.txt`), `--refresh-pages` (lista para reextrair), `--maxtasksperchild`, `--chunksize`, `--procs`.
- **Gating/validação**:
  - `--verify`: compara `.txt` existentes com Tesseract; `--verify-fix`: reprocessa falhas.
  - `--verify-judge-llm`: executa LLM judge só quando heurísticas detectam risco; `--judge-force` força LLM em todas.
  - Heurísticas: detecção de ruído/layout, sobreposição token/bloco LLM×Tesseract, CASOS_PERDIDOS (lista de páginas irrecuperáveis), classificação visual (vazia/capa/texto).
  - Avaliações gravadas em `data/ocr_eval.db` (campos: volume, página, provider, modelo, prompt_version, status, xml_raw/parse_result, duração).
- **Avisos**: custo de LLM, risco de alucinação ou omissão, ausência de revisão humana integral. Sempre validar contra o PDF original para uso acadêmico.

<a id="ocr-three-way"></a>

### OCR com VLM em duas passagens e reconciliação tripla

No contexto deste projeto, **VLM** (*Vision-Language Model*) significa uma LLM
multimodal capaz de ler a imagem da página. Não confundir com **vLLM**, o software
de serving/inferência de modelos. O nome descritivo adotado aqui é **VLM em duas
passagens com reconciliação tripla de OCR**: a VLM pode examinar a imagem duas
vezes e, na passagem corretiva, reconcilia três entradas — fac-símile, texto do
Tesseract e XML produzido pela primeira passagem da VLM.

Isso não é *one-shot*, *two-shot* ou *three-shot prompting*. Na terminologia de
prompting, *shot* normalmente conta demonstrações ou exemplos de entrada/saída
fornecidos no contexto. Aqui não há duas ou três demonstrações: há uma imagem
fonte e duas leituras candidatas da mesma página. No contexto efetivamente
processado pela segunda chamada, isso corresponde a duas evidências textuais mais
os tokens visuais do fac-símile. A expressão **duas passagens** descreve a
cronologia da VLM; **reconciliação tripla** descreve as evidências presentes na
segunda passagem.

> **Invariante do fluxo:** o fac-símile nunca é retirado do contexto. Tesseract,
> cada passagem da VLM e o LLM judge recebem/processam a imagem da página. Na
> segunda passagem, os textos anteriores são evidências suplementares anexadas à
> imagem; eles nunca a substituem. Não existe, neste fluxo de OCR, uma etapa de
> correção por VLM baseada somente nos dois textos.

```text
                         mesma imagem da página
                         ┌──────────┴──────────┐
                         │                     │
                [A] Tesseract sozinho   [B] VLM — passagem 1
                  texto de referência     XML inicial
                         │                     │
                         └──────────┬──────────┘
                                    │
                         comparação determinística
                      (XML, tokens, cobertura e ruído)
                                    │
                         aprovada ───┴─── reprovada
                            │                 │
                       mantém XML      [C] VLM — passagem 2
                                      imagem novamente
                                      + texto Tesseract
                                      + XML da VLM inicial
                                              │
                                       XML corrigido final
```

As etapas são independentes e têm papéis diferentes:

1. **Tesseract sozinho — testemunha textual independente.** O fac-símile passa pelo
   deskew/pré-processamento e pelo Tesseract, sem receber texto produzido pela
   VLM. O resultado é texto simples, armazenado em `data/tesseract.db`. Ele é
   especialmente útil para detectar termos latinos omitidos ou inventados, mas
   pode perder colunas, diacríticos, layout e scripts complexos.
2. **VLM sozinha, passagem 1 — transcrição visual estruturada.** A VLM recebe apenas
   o fac-símile e as instruções de transcrição — “sozinha” significa sem rascunho
   do Tesseract ou de outra VLM, nunca sem imagem. Ela produz XML com estado da
   página, blocos, tipo, script e `bbox`. Como não vê o Tesseract, seu primeiro
   resultado é uma leitura independente, sem ancoragem nos erros do OCR
   tradicional.
3. **Gating determinístico.** `--verify` valida o XML e confronta sua leitura com
   o Tesseract por sobreposição/recall de tokens, discrepância de quantidade,
   marcadores de ilegibilidade, classificação visual do fac-símile e heurísticas
   de ruído. Uma página aprovada termina aqui: não há segunda chamada à VLM.
4. **Reconciliação tripla, passagem 2 da VLM.** Com `--verify-fix`, somente as
   páginas reprovadas voltam à VLM. Ela recebe obrigatoriamente o **fac-símile
   outra vez**, junto com o texto independente do Tesseract e o XML produzido na
   passagem 1. O prompt manda usar os dois textos apenas como rascunhos e tratar a
   imagem como fonte de verdade. Essa etapa pode recuperar omissões apontadas pelo
   Tesseract sem copiar automaticamente seus erros, além de corrigir estrutura,
   ordem de colunas e gutter.

Reconciliação tripla não significa votar cegamente entre dois OCRs nem concatenar
suas saídas. A imagem continua sendo a evidência primária nas duas chamadas
visuais; a segunda passagem é uma revisão informada por divergências. Isso reduz
erros não correlacionados: o Tesseract tende a falhar em layout e scripts difíceis,
enquanto a VLM pode alucinar, normalizar ou omitir conteúdo que visualmente parece
pouco saliente.

#### Base empírica e limites metodológicos

Esta pipeline foi desenvolvida e ajustada **empiricamente**. A escolha das etapas,
dos prompts e dos limiares de gating veio da inspeção visual exploratória de
páginas escolhidas informalmente em diferentes pontos do corpus, sem desenho
formal de amostragem. Os textos produzidos foram comparados com seus respectivos
fac-símiles para observar padrões recorrentes de omissão, alucinação, mistura de
colunas, gutter e scripts complexos.

Ela **não** foi derivada de uma avaliação matemática formal de BCER, nem de um
benchmark sistemático com *ground truth* alinhado caractere a caractere. Portanto,
os limiares atuais devem ser entendidos como heurísticas operacionais calibradas
para este corpus, e não como estimativas estatísticas de erro ou prova de
superioridade sobre outra engine. Qualquer afirmação quantitativa de qualidade
exigiria montar uma amostra anotada e representativa por coleção, idioma, script,
layout e estado material, calcular BCER/CER por etapa e informar intervalos de
confiança e protocolo de amostragem.

Na implementação, a ordem cronológica mais comum é passagem 1 da VLM → Tesseract
na verificação → passagem 2 nas falhas. O diagrama mostra a independência lógica
das duas primeiras leituras, não uma obrigação de executar Tesseract antes da
VLM. Se os textos normalizados forem idênticos, o segundo prompt omite o XML
inicial por ser redundante. `--do-not-reprocess-compare` também desativa a dupla
evidência e faz uma nova passagem visual sem os rascunhos anteriores.

Exemplo operacional em duas passagens:

```bash
# Passagem 1: gera o XML inicial usando a VLM
python main2.py input.pdf --out ocr_out \
  --algorithm openai --llm-model gpt-5 \
  --lang fra+lat+grc+ell+syr

# Verifica com Tesseract e aplica a passagem 2 somente às páginas reprovadas
python main2.py input.pdf --out ocr_out \
  --algorithm openai --llm-model gpt-5 \
  --lang fra+lat+grc+ell+syr --verify-fix
```

`--algorithm` escolhe apenas o provedor VLM (`ollama`, o padrão, ou `openai`). O
Tesseract participa internamente da verificação e da reconciliação, mas não pode
mais ser selecionado como backend que grava o resultado final. A operação
`--tesseract-cache-only` continua disponível para preparar sua leitura auxiliar.

O **LLM judge** é uma camada diferente. `--verify-judge-llm` envia à VLM o
fac-símile junto com o XML existente e pede que ela classifique fidelidade e
usabilidade. Avaliações `baixa` ou `descartar`, em qualquer um dos dois eixos,
encaminham a página para a mesma pipeline de reprocessamento; `--judge-force`
apenas força o julgamento em todas as páginas. O judge decide se deve haver nova
tentativa, enquanto a passagem 2 é a tentativa de transcrição corretiva
propriamente dita. Mesmo no judge, o modelo nunca avalia o texto sem acesso à
imagem.

Para auditoria, cada resultado fica associado à engine, modelo, prompt, hash da
imagem, motivo do reprocessamento e duração em `data/ocr_versions.db`. A versão
corrigida substitui o `.txt` corrente, mas o histórico permite comparar a passagem
1, o Tesseract intermediário e a passagem 2.

<a id="three-way-ocr-english"></a>

### English: two-pass VLM with three-way OCR reconciliation

In this project, **VLM** means *Vision-Language Model*: an LLM that can inspect a
page image. It should not be confused with **vLLM**, the model serving/inference
software. The descriptive name used here is **two-pass VLM with three-way OCR
reconciliation**: the VLM may inspect the facsimile twice, and its corrective
pass reconciles three inputs — the facsimile, Tesseract text, and XML from the
first VLM pass.

This is not *one-shot*, *two-shot*, or *three-shot prompting*. In prompting
terminology, a *shot* normally counts input/output demonstrations included in the
context. This workflow supplies no set of two or three demonstrations; it supplies
one source image and two candidate readings of that same page. In the context
actually processed by the corrective call, these are two textual pieces of
evidence plus the facsimile's visual tokens. **Two-pass** describes the VLM
chronology, while **three-way reconciliation** describes the evidence available
during the second pass.

> **Pipeline invariant:** the facsimile is never removed from the context.
> Tesseract, every VLM pass, and the LLM judge receive or process the page image.
> Text produced in earlier stages is supplementary evidence attached to that
> image; it never replaces the image. The corrective VLM stage is therefore
> never a text-only comparison between two OCR drafts.

```text
                          same page facsimile
                         ┌──────────┴──────────┐
                         │                     │
                 [A] Tesseract alone    [B] VLM — pass 1
                  reference text          initial XML
                         │                     │
                         └──────────┬──────────┘
                                    │
                         deterministic comparison
                       (XML, tokens, coverage, noise,
                          and visual classification)
                                    │
                          accepted ──┴── rejected
                             │               │
                         keep XML      [C] VLM — pass 2
                                      facsimile again
                                      + Tesseract text
                                      + pass-1 VLM XML
                                              │
                                      corrected final XML
```

The stages have distinct responsibilities:

1. **Tesseract alone — independent textual witness.** Tesseract processes the
   facsimile after deskew/preprocessing and receives no VLM-generated draft. Its
   plain-text result is cached in `data/tesseract.db`. It is useful for exposing
   omitted or invented Latin terms, although it may lose columns, diacritics,
   layout, and complex scripts.
2. **VLM alone, pass 1 — structured visual transcription.** The VLM receives the
   facsimile and transcription instructions, but no Tesseract or previous VLM
   draft. Here “alone” means independent of other OCR text, not without the
   image. It produces XML containing page state, blocks, type, script, and
   normalized `bbox` coordinates.
3. **Deterministic gating.** `--verify` validates the XML and compares it with
   Tesseract using token overlap/recall, token-count discrepancy, illegibility
   markers, noise heuristics, and visual classification of the facsimile. An
   accepted page keeps the pass-1 XML and does not incur another VLM call.
4. **Three-way reconciliation, VLM pass 2.** With `--verify-fix`, rejected pages
   return to the VLM. The request always contains the facsimile again, plus the
   independent Tesseract text and the XML from pass 1. The prompt treats both
   texts as drafts and the image as ground truth. This lets the VLM recover
   possible omissions without blindly copying Tesseract errors, while also
   revisiting layout, column order, and gutter markers.

Three-way reconciliation is not majority voting and does not concatenate OCR
outputs. Both VLM calls remain grounded in the facsimile. The second call uses
disagreements as diagnostic evidence because the two engines tend to fail
differently: Tesseract is brittle around layout and difficult scripts, while a
VLM may hallucinate, normalize, or omit visually inconspicuous content.

#### Empirical basis and methodological limits

This pipeline was developed and tuned **empirically**. Its stages, prompts, and
gating thresholds came from exploratory visual inspection of pages informally
selected from different parts of the corpus, without a formal sampling design.
Generated text was compared against the corresponding facsimiles to identify
recurring omissions, hallucinations, merged columns, gutter errors, and failures
on complex scripts.

It was **not** derived from a formal mathematical BCER evaluation or a systematic
benchmark against character-aligned ground truth. The current thresholds should
therefore be treated as operational heuristics calibrated for this corpus, not
as statistical error estimates or proof that one OCR engine is superior to
another. Quantitative quality claims would require a representative annotated
sample stratified by collection, language, script, layout, and physical page
condition, followed by per-stage BCER/CER measurements with a documented sampling
protocol and confidence intervals.

The usual chronological order is VLM pass 1 → Tesseract during verification →
VLM pass 2 for failed pages. The first two readings are logically independent,
even when they are not executed in the order shown by the forked diagram. If the
normalized drafts are identical, the second prompt omits the redundant pass-1
XML. `--do-not-reprocess-compare` explicitly disables the two-draft correction
and performs another image-grounded pass without attaching the earlier drafts.

Example using two passes over the command-line pipeline:

```bash
# Pass 1: initial image-grounded VLM XML
python main2.py input.pdf --out ocr_out \
  --algorithm openai --llm-model gpt-5 \
  --lang fra+lat+grc+ell+syr

# Tesseract verification; pass 2 only for rejected pages
python main2.py input.pdf --out ocr_out \
  --algorithm openai --llm-model gpt-5 \
  --lang fra+lat+grc+ell+syr --verify-fix
```

`--algorithm` now selects only the VLM provider (`ollama`, the default, or
`openai`). Tesseract participates internally in verification and reconciliation,
but it can no longer be selected as the backend that writes the final result.
The `--tesseract-cache-only` operation remains available to prepare its auxiliary
reading.

The **LLM judge** is separate from the three-way corrective transcription. With
`--verify-judge-llm`, the judge receives both the facsimile and the current XML,
then rates fidelity and usability. A `baixa` (low) or `descartar` (discard) value
on either axis routes the page back to the reprocessing pipeline. `--judge-force`
forces this image-grounded assessment for every page. The judge decides whether
another attempt is needed; VLM pass 2 performs the corrective transcription.

For provenance, `data/ocr_versions.db` records the image hash, engine, model,
prompt, reprocessing reason, duration, and each OCR result. The corrected `.txt`
becomes current, while the version history preserves VLM pass 1, the intermediate
Tesseract witness, and VLM pass 2 for later comparison.

## Resumos e keywords
- **Resumos**: gerados a partir do OCR por script do projeto (salvos em `data/patristica_resumos.db`, tabela `resumos`).
- **Keywords — modo serial** (`keywords_serial.py`):
  - Fontes (`--source`): `resumo_pagina`, `resumo_global`, `pagina_texto`, `resumo+pagina`, `tudo` (default).
  - LLM: `--provider {ollama,openai}`, `--model`, `--reasoning-effort`, `--num-ctx`, timeouts separados.
  - Validação: `keyword_integrity` (NLTK/CLTK), `--verify` / `--verify-fix`, `--llm-judge`, `--no-skip-noise` (processa páginas marcadas como ruído).
  - Operação: DRY RUN por padrão (stdout); `--write` grava em `data/patristica_keywords.db` (schema `keywords`, índices em doc/página/source/modelo). `--preview-llm-judge` roda o judge mas não grava.
- **Keywords — modo paralelo** (`keywords_parallel.sh`):
  - Orquestra volumes em paralelo (default `--jobs 40`, provider OpenAI gpt-5-mini, reasoning minimal).
  - Filtros: `--pattern "PL*"`, `--source`, `--rerun-bad` (reprocessa páginas problemáticas), `--no-skip-noise`, `--verify/--verify-fix`, `--dry-run`.
  - Logs em `logs/keywords_parallel/<volume>.log`.
- **Avisos**: prompts/outputs somente em PT-BR; revisão humana limitada; valide keywords antes de uso editorial.

## Extração de índices internos (`scripts/run_index_extraction.py`)
- **Objetivo**: extrair a estrutura editorial dos índices que aparecem dentro de cada volume PG/PL, separando índice do começo do tomo, índices de abertura de obra e índices finais do volume.
- **Driver**: `scripts/run_index_extraction.py` executa um ciclo por volume, chama o prescan, monta o prompt do Codex, coleta o JSON final e importa o resultado no SQLite.
- **Prescan e prompt**:
  - `scan_volume.py` localiza páginas candidatas e títulos recorrentes em `teste/<VOLUME>/text/*`.
  - `build_volume_prompt.py` gera o envelope com blocos `TASK`, `VOLUME`, `PRESCAN`, `WORK INSTRUCTIONS`, `TODO`, `OUTPUT FILE` e `FINAL RESPONSE`.
  - O prompt manda preservar literais OCR e tratar a amostra inicial como mapa, não como verdade final.
- **Artefatos**:
  - `data/index_payloads/<VOLUME>_prescan.json`
  - `data/index_payloads/<VOLUME>_prompt.txt` quando `--dry-run`
  - `data/index_payloads/<VOLUME>_last_message.txt`
  - `data/index_payloads/<VOLUME>_indices.json`
  - logs persistentes em `data/index_logs/*`
- **Banco de dados**: o importador grava em `data/patristic_indices.db`, com tabelas `volumes`, `works`, `index_sections`, `index_entries` e `runs`.
- **Contrato de saída**:
  - payload JSON com top-level `volume`, `works`, `sections`, `notes`
  - chaves estáveis para `work_key` e `section_key`
  - preservação de `raw_json` em todos os níveis para auditoria posterior
- **Taxonomia**:
  - front-matter do volume: `ELENCHUS`, `AUCTORUM ET OPERUM`, `ORDO RERUM` quando funciona como inventário do tomo
  - índice de abertura de obra: `INDEX CAPITUM`, `PROLEGOMENA`, `CAPUT`, `LIBER`, `SECTIO`
  - índice final do volume: `ORDO RERUM`, `INDEX ANALYTICUS`, `INDEX RERUM ET VERBORUM`, `INDEX GRÆCITATIS`
  - referência editorial em vez de numeração do arquivo OCR: o sufixo `*.txt` é apenas identificação técnica
- **Flags úteis**:
  - seleção: `--volume-id`, `--all-volumes`, `--blob`, `--limit`
  - execução: `--codex-bin`, `--model`, `--use-json`, `--replace`, `--skip-done`, `--fresh-extraction`, `--import-existing`
  - operação: `--output-dir`, `--log-dir`, `--keep-temp`, `--dry-run`, `--verbose`
  - `--fresh-extraction` é obrigatório para reextrações motivadas por fragmentos ou payloads
    semanticamente defeituosos: ignora o conteúdo do JSON anterior e não reutiliza chunks
    completos, mas preserva o payload antigo até o novo passar por validação e importação
- **Tradução e análise linguística**:
  - A implementação fica isolada em `scripts/index_translation/` (persistência/cache, provedor,
    ferramentas lexicais e worker/setup do CLTK).
  - `--translate` traduz, após a importação, as strings semânticas ainda pendentes para
    `en,fr,it,pt-br`; configure `OPENAI_API_KEY` e opcionalmente `OPENAI_MODEL`.
  - `--translation-only` faz backfill diretamente de `patristic_indices.db`, sem OCR, extração,
    validação ou nova importação. Exemplo: `python scripts/run_index_extraction.py --volume-id
    PL001 --translation-only --db data/patristic_indices.db`.
  - Traduções são cacheadas globalmente por string/idioma. Lemma, UPOS, morfologia e dependências
    CLTK são cacheados por string e reaproveitados entre idiomas e volumes.
  - O CLTK 1.5 deve usar uma venv Python 3.12 isolada: `python3.12 -m venv .venv-cltk`, instalar
    `requirements-cltk.txt` e então executar `.venv-cltk/bin/python
    scripts/index_translation/setup_cltk.py --languages lat,grc`. A pipeline nunca baixa modelos
    sozinha.
  - Se CLTK, seus modelos ou o script da língua não estiverem disponíveis, a tradução continua
    sem a anotação linguística. Use `--no-translation-cltk` para desativá-la explicitamente.
- **Avisos**:
  - o prescan é apenas ponto de partida; o volume precisa ser conferido em OCR antes do fechamento
  - em PG/PL, o pré-filtro também reconhece `INCIPIUNT CAPITULA`, `TITULI CAPITUM` e
    listas estruturais densas sem título explícito; seções de capítulos só avançam para o scan
    seguinte quando esse scan ainda apresenta evidência estrutural de lista
  - números, colunas e referências internas devem ser mantidos literalmente quando houver dúvida
  - a importação substitui por volume quando `--replace` é usado; sem isso, o fluxo preserva o que já existe
  - `--import-existing --replace` valida o payload canônico já existente, executa os gates de consumo de fragments e evidência, e substitui somente esse volume no SQLite sem rodar prescan, chunks ou Codex novamente
  - a documentação canônica da classificação editorial continua em `docs/taxonomia_indices.md`
  - para o desenho ainda em aberto de um fluxo separado para índices alfabéticos, remissivos, onomásticos e bíblicos, ver `docs/levantamento_indices_alfabeticos.md`

## Extração de índices alfabéticos e remissivos (`scripts/run_alphabetical_index_extraction.py`)
- **Objetivo**: extrair payloads JSON para índices alfabéticos, analíticos, onomásticos, remissivos, bíblicos e de concordância em `PG`, `PL` e `PO`, usando o helper de localização material para apoiar a decisão do agente.
- **Driver**: `scripts/run_alphabetical_index_extraction.py` executa um ciclo por volume, consome páginas pré-filtradas quando existirem, monta o prompt do Codex para `$alphabetical-index-extractor`, coleta o JSON final, valida o payload e importa automaticamente o resultado no SQLite alfabético.
- **Entradas**:
  - JSON externo de páginas filtradas por volume via `--filtered-pages-json` ou `--filtered-pages-dir`
  - fallback heurístico interno quando não houver JSON externo; o script avisa quando isso acontecer
  - payload anterior opcional via `--previous-result-json` ou `--previous-result-dir`
- **Prompt e helper**:
  - o envelope segue `.codex/skills/alphabetical-index-extractor/references/prompt-contract.md`
  - o agente recebe `FILTERED PAGES`, `PREVIOUS RESULT`, `HELPER PATHS`, `OUTPUT FILE`
  - o helper material é `scripts/index_target_locator.py`, com request/response JSON temporários por volume
- **Artefatos**:
  - `data/alphabetical_index_payloads/<VOLUME>_filtered_pages.json`
  - `data/alphabetical_index_payloads/<VOLUME>_prompt.txt` quando `--dry-run`
  - `data/alphabetical_index_payloads/<VOLUME>_helper_request.json`
  - `data/alphabetical_index_payloads/<VOLUME>_helper_output.json`
  - `data/alphabetical_index_payloads/<VOLUME>_last_message.txt`
  - `data/alphabetical_index_payloads/<VOLUME>_alphabetical_indices.json`
  - logs persistentes em `data/alphabetical_index_logs/*`
- **Banco de dados**:
  - `scripts/init_alphabetical_index_db.py` inicializa `data/alphabetical_indices.db`
  - `scripts/import_alphabetical_index_json.py` importa um payload JSON canônico para o banco
  - o schema usa `alphabetical_volumes`, `alphabetical_sections`, `alphabetical_nodes`, `alphabetical_entries`, `alphabetical_refs`, `alphabetical_scripture_refs` e `alphabetical_runs`
- **Contrato de saída**:
  - payload JSON com top-level `schema_version`, `generated_at`, `volume`, `sections`, `nodes`, `entries`, `refs`, `scripture_refs`, `coverage`, `notes`
  - os aliases legados `section_start_file`, `editorial_anchor_file` e `target_file_best`
    permanecem distintos; no banco v8 use `index_section_start_ocr_file`,
    `index_entry_source_ocr_file` e `resolved_target_ocr_file`
  - o helper não decide o payload final; sua evidência deve ser preservada em `raw_json`
- **Flags úteis**:
  - seleção: `--volume-id`, `--all-volumes`, `--blob`, `--limit`
  - entradas externas: `--filtered-pages-json`, `--filtered-pages-dir`, `--previous-result-json`, `--previous-result-dir`
  - execução: `--codex-bin`, `--model`, `--use-json`, `--replace`, `--skip-done`, `--db`
  - operação: `--output-dir`, `--log-dir`, `--keep-temp`, `--dry-run`, `--verbose`
- **Avisos**:
  - o fallback interno de páginas filtradas é heurístico e mais fraco que um JSON externo curado
  - headings detectados automaticamente são apenas candidatos; a decisão editorial continua com o agente
  - `--skip-done` olha o status `imported` em `alphabetical_runs`, não apenas a presença do JSON no `output-dir`
  - `--replace` sobrescreve o payload de saída e substitui as linhas do mesmo volume no banco alfabético
  - a estratégia operacional de chunking, intermediários, `todo.json` e builders está em `docs/estrategia_pipeline_extracao_indices_alfabeticos.md`
  - o fluxo canônico está documentado em `docs/levantamento_indices_alfabeticos.md`, `docs/contrato_extrator_indices_alfabeticos.md` e `docs/normalizacao_indices_biblicos.md`

## Embeddings e clustering (`hdbscan_embedding.py`)
- Entrada: tabela `keyword_embedding` (BLOB float32) em `data/patristica_keywords.db` (pode filtrar por `--model`).
- Pipeline: UMAP (`--umap-components`, `--umap-neighbors`) → HDBSCAN (`--min-cluster-size`, `--min-samples`, `--cluster-selection-epsilon`).
- Saídas: recria `keyword_clusters`, `keyword_cluster_meta`; atualiza `keywords.hdbscan_group_id` (NULL para noise -1).
- Recursos: `--chunksize` (default 50k) para equilibrar RAM/tempo; usar máquinas com RAM adequada.

## Reparo determinístico de capítulos e outras divisões

- Dry-run por volume: `python scripts/repair_structural_targets.py --volume PL020 --workers 20 --report /tmp/PL020_structural_targets.json`.
- Apply protegido por hash e backup: `python scripts/repair_structural_targets.py --apply-report /tmp/PL020_structural_targets.json --backup-dir /tmp/PL020_structural_targets_backup --reimport-db data/patristic_indices.db`.
- Validação pós-apply: `python scripts/validate_structural_target_apply.py --report /tmp/PL020_structural_targets.json --db data/patristic_indices.db`.
- Varreduras interrompidas podem continuar com `--resume`; transações interrompidas são recuperadas com `--recover-only`. `--workers` é um orçamento híbrido: volumes pesados usam pool interno e volumes pequenos usam um pool persistente entre volumes.
- Scan e matching usam processos; a agregação retorna à ordem estável e a escrita final permanece determinística sob lock.
- Apply/importador usam lock cooperativo, journal recuperável, `fsync` e promoção atômica do SQLite; resultados negativos permanecem no relatório sidecar.
- O contrato, formatos cobertos, estados de evidência e limites estão em `docs/pipeline_reparo_alvos_estruturais.md`.
- Rode esse reparo depois da extração/validação do payload e antes de materializar os shards que leem `patristic_indices.db`.

## Geração dos shards do site
1) Dicionário canônico: `python tools/export_keywords_dicts.py --db data/patristica_keywords.db --out web/public/dict --min-count 1` (ajuste `--include-noise`/`--top` conforme necessidade).
2) Renderização das páginas/shards: `python tools/render_publication_from_shards.py --index data/shards/enrichment/index.json --out web/public --db data/patristica_keywords.db --keywords-json web/public/dict/keywords.json --resumos-db data/patristica_resumos.db --related-topk 7 --page-block-size 50`.
   - Cada bloco `meta/<volume>-pages-*.json.gz` grava uma única `raw_base_url`. Por página, `raw.file` contém somente o nome do `.txt`; `raw.url` fica reservado a overrides incomuns.
   - O tamanho 50 coincide com a granularidade usada pela busca e evita baixar metadados de páginas que não serão mostradas.
3) Export dos índices do banco: `python tools/export_indices_from_db.py --db data/patristic_indices.db --out web/public/indices`.
4) Build Astro + índice Pagefind custom (seção abaixo). O `npm run build` em `web/` chama `npm run build:indices` antes do build principal para garantir que `/indices` receba os JSONs atualizados.

## Busca web (Pagefind custom por shards)
- Por que custom: o índice completo excede limites do GitHub Pages; o Pagefind padrão em `dist/` não é usado diretamente.
- Script: `node tools/build_pagefind_from_shards.mjs --public web/public --out web/public/pagefind --base /BibliothecaPatristica --min-count 1 --concurrency 4`.
  - Inputs: `web/public/volumes.json`, shards em `web/public/page_blocks/*`, dicionário `web/public/dict/keywords.json` (keyword_id→label/count/grupo).
  - Para cada volume: lê blocos, monta `content` com `summary_page` + labels de keywords filtradas por `min-count`, addCustomRecord com URL `viewer?doc=<vid>&page=<p>` (filtros collection/volume, language `pt`).
  - Output: limpa `outDir`, grava índice em `web/public/pagefind/` via Pagefind API (`createIndex`, `writeFiles`), fecha com `close()`.
- Build completo: `cd web && npm run build && node ../tools/build_pagefind_from_shards.mjs --public dist --out dist/pagefind --base /BibliothecaPatristica` (ajuste `--base` se trocar path de publicação), publicar `dist/` + `pagefind/` juntos.

## Operação e monitoração (happy path)
1) OCR: `python main2.py input.pdf --out ocr_out --lang lat --algorithm ollama` (ou `--algorithm openai`; acrescente `--verify-judge-llm` para gating).
2) Resumos: rodar pipeline de resumos (ex.: `python resumo_serial.py --volume-dir teste/PL001 --provider openai --model gpt-5-mini`).
3) Keywords: `./keywords_parallel.sh --pattern "PL%" --provider openai --model gpt-5-mini --jobs 20 --write` (ajuste flags/verify conforme necessidade).
4) Embeddings/clusters: `python hdbscan_embedding.py --db data/patristica_keywords.db --model qwen3-embedding:8b --chunksize 50000 --umap-components 5 --min-cluster-size 50`.
5) Web: `cd web && npm run build && node ../tools/build_pagefind_from_shards.mjs --public dist --out dist/pagefind --base /BibliothecaPatristica`.
6) Publicação: enviar `dist/` + `dist/pagefind/` (ou `web/public/pagefind/`) ao GitHub Pages.
- Logs/checagens: `logs/keywords_parallel/*.log`; `data/ocr_eval.db` para decisões do judge; smoke tests com PDFs pequenos; pytest sugeridos `test_limpeza_ocr.py`, `test_process_index.py`.

## Avisos e boas práticas
- **IA**: risco de alucinação, omissão e viés; sempre conferir no PDF original antes de uso acadêmico ou editorial.
- **Cobertura**: corpus parcialmente processado; ausência no índice não prova ausência no texto.
- **Custo e privacidade**: LLMs externos geram custo e podem trafegar dados sensíveis; avalie antes de enviar conteúdo.
- **Escala**: evitar rodar sobre todo `teste/` sem filtros; usar subconjuntos (`--pattern`, `--first/--last`).
- **Segurança**: chaves em variáveis de ambiente; não versionar PDFs/imagens; limpar dados sensíveis de logs se necessário.

## QA e testes rápidos
- Smoke pytest: `pytest test_limpeza_ocr.py test_process_index.py test_stream_pg002_630.py -q` (raiz).
- Keywords: `pytest test_keywords_clean.py test_keyword_integrity.py -q`.
- Regex/citações: `pytest test_scripture_ref_variants.py test_regex.py -q`.
- Perf rápido: `python -m cProfile -o /tmp/kw_profile.prof keywords_serial.py --verify --doc PG001 --limit 500`.

## Apêndice (schemas e flags)
- `data/patristica_resumos.db`: tabela `resumos(documento, pagina_num, resumo_pagina, resumo_global, modelo, criado_em, keywords_json, ...)`.
- `data/patristica_keywords.db`: `keywords(id, documento, pagina_num, source, keywords_json, keywords_raw, modelo, prompt_hash, criado_em, hdbscan_group_id)` + `keyword_embedding(keyword_id, model, embedding BLOB)` + `keyword_clusters`, `keyword_cluster_meta` (sobrescritos a cada run).
- `data/ocr_eval.db`: registros do judge com XML bruto, parse e status.
- CLIs chave:
  - `main2.py --algorithm ... --verify-judge-llm --judge-force --verify/--verify-fix --refresh-pages --concat`.
  - `keywords_serial.py --source ... --provider ... --model ... --write --verify/--verify-fix --llm-judge --preview-llm-judge --no-skip-noise --timeout --num-ctx --rerun-bad --openai-url/--ollama-url`.
  - `keywords_parallel.sh --jobs --pattern --source --provider --model --verify/--verify-fix --rerun-bad --no-skip-noise --dry-run --log-dir`.
  - `hdbscan_embedding.py --db --model --chunksize --umap-components --umap-neighbors --min-cluster-size --min-samples --cluster-selection-epsilon`.
  - `build_pagefind_from_shards.mjs --public --out --base --min-count --concurrency`.
  - Auxiliares: `tools/ingest_keywords_from_resumos.py` (backfill/parse em SQL), `tools/export_keywords_dicts.py` (dict para web), `tools/generate_keyword_embeddings.py` e `tools/generate_resumo_embeddings.py` (embeddings), `tools/fill_canon_keywords.py` (nomes canônicos), `tools/preview_related_pages.py` (relação de páginas via embeddings).

## Tabela rápida de ferramentas (`tools/`)
- **OCR / validação**: `rerun_bad_evals.py` (reprocessa páginas com avaliações ruins, filtros de data/provedor/modelo), `classify_scan_kind.py` (capa/vazia/texto), `bbox_playground.py` (anotar boxes).
- **Resumos**: `backfill_clean_resumos.py --limit 1000 --batch-size 200` (preenche campos limpos/flags em `resumos`).
- **Keywords & judge**: `ingest_keywords_from_resumos.py` (carrega keywords do DB de resumos), `generate_keyword_embeddings.py --db data/patristica_keywords.db --batch-size 512`, `cluster_keywords.py` (embedding faltante + HDBSCAN), `fill_canon_keywords.py` (nomes canônicos), `preview_related_pages.py --db data/patristica_resumos.db --kind page --queries DOC:PAGE`, perf/bench: `bench_keywords.py`, `profile_keywords.py`.
- **Resumos embeddings**: `hdbscan_resumo_embeddings.py` (+ utils em `resumo_embedding_utils.py`) para embeddings/clusters dos resumos.
- **Export/publicação**: `export_enrichment_shards.py --all --no-text` (gera `data/shards/enrichment/*.ndjson` + `index.json`), `export_keywords_dicts.py --db ... --out web/public/dict --top 80 --min-count 1` (dicts), `render_publication_from_shards.py --index data/shards/enrichment/index.json --out web/public --db data/patristica_keywords.db --keywords-json web/public/dict/keywords.json --resumos-db data/patristica_resumos.db --related-topk 7 --page-block-size 50`, `build_dict_bundle.py` (all.json), `build_pagefind_from_shards.mjs --public web/public --out web/public/pagefind --base /BibliothecaPatristica`.
- **Playground/diagnóstico**: `opencv_playground.py` (cadeias OpenCV), `color_band_compare.py` (deltaE borda/centro), `read_poribp.py` (lex PorIBP), `bbox_playground.py` (visual de layout).

*Limitação de idioma*: prompts, resumos e keywords são mantidos somente em PT-BR; este README em inglês é apenas referência.
