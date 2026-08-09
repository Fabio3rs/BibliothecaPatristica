# Exportação de treinamento em bundles LSTMF

## Visão geral

`export_training.py` possui dois modos:

- `--mode lines`, padrão compatível com o comportamento antigo, grava um PNG e
  um `.gt.txt` por linha;
- `--mode bundles` lê imagens e consensos do SQLite, compõe páginas TIFF com
  várias linhas e publica um `.lstmf` com várias amostras internas.

O modo bundles não altera o banco nem cria PNG/GT por linha. A conexão SQLite é
aberta em modo somente leitura e os BLOBs são buscados em lotes apenas depois da
seleção dos metadados.

## Consenso ponderado e múltiplos runs

A unidade de agrupamento é sempre:

```text
(line_id, text_hash, text_content)
```

Antes de pontuar, os votos são deduplicados por:

```text
(line_id, text_hash, text_content, model)
```

Assim, várias execuções (`run_id`) do mesmo modelo repetindo a mesma saída
contam como um voto. Se o mesmo `model` aparecer por providers diferentes,
continua contando uma vez e recebe peso 2 quando ao menos um desses providers é
`tesseract`. Um modelo pode aparecer em grupos diferentes quando mudou
de texto entre runs; se mais de um grupo atingir o limiar, o programa escolhe o
de maior pontuação e registra `consensus_conflict` em `warnings.jsonl`.

Pesos padrão:

```text
provider = tesseract  -> 2 pontos por modelo distinto
outros providers      -> 1 ponto por modelo distinto
aceitação              -> 3 pontos ou mais
```

O limiar pode ser alterado com `--min-consensus-score`. Todo o histórico de
`line_versions` participa; `is_current` não é usado como filtro.

### Prioridade da revisão manual

Um `lines.reviewed_text` não vazio é ground truth autoritativo e participa
mesmo quando a linha não alcança o limiar de consenso. A revisão substitui o
texto escolhido em `line_versions`; linhas com `status = rejected` são
excluídas. Texto manual inválido segundo os mesmos filtros de sanidade é
rejeitado explicitamente, sem fallback silencioso para o consenso.

O manifesto por linha registra `text_source`, `review_status`,
`consensus_overridden` e `consensus_text`. `decisions.jsonl` registra também
alternativas não escolhidas, rejeições, amostras adiadas e a associação ao
bundle publicado. Para amostras aceitas, `shuffle_index` guarda a posição após
o embaralhamento e `bundle_position` a posição dentro do bundle.

## Filtros de sanidade

Antes de uma linha entrar no bundle, o texto é normalizado para NFC e rejeitado
quando contém:

- CR, LF ou tab;
- controles, formatadores invisíveis, surrogates, private-use ou codepoints não
  atribuídos;
- `U+FFFD`, escapes Unicode literais ou noncharacters;
- combinantes degenerados, clusters excessivamente grandes ou repetição longa
  do mesmo caractere;
- pouquíssimas letras ou proporção anormal de pontuação/símbolos;
- apenas dígitos.

Sem `--filter-unicharset`, letras devem pertencer aos scripts Latin ou Greek.
Com a opção abaixo, o unicharset fornecido passa a ser a autoridade para os
scripts permitidos:

```bash
--filter-unicharset /homessddata/Projects/pdfocr/unicharset
```

O loader entende o formato textual do Tesseract, converte a entrada `NULL` em
espaço e aceita unidades multicodepoint. A linha só passa quando sua transcrição
inteira pode ser segmentada nas unidades do arquivo. `Digitized by Google` é
permitido intencionalmente por representar tipografia real presente no corpus.

## Execução

Instale as dependências adicionais:

```bash
pip install -r requirements.txt
```

Canário de 30 linhas, forçando várias páginas TIFF:

```bash
python export_training.py \
  --mode bundles \
  --db ocr.db \
  --out /tmp/migne-bundle-canary \
  --bundle-size 30 \
  --limit 30 \
  --page-height 512 \
  --lang migne \
  --tessdata-dir /homessddata/Projects/pdfocr \
  --filter-unicharset /homessddata/Projects/pdfocr/unicharset
```

Exportação com o tamanho padrão de 5.000 linhas, igual ao alvo da pipeline
sintética:

```bash
python export_training.py \
  --mode bundles \
  --db ocr.db \
  --out training_bundles \
  --lang migne \
  --tessdata-dir /homessddata/Projects/pdfocr \
  --filter-unicharset /homessddata/Projects/pdfocr/unicharset
```

Para usar os modelos vanilla Latin e Greek:

```bash
python export_training.py \
  --mode bundles \
  --db ocr.db \
  --out training_bundles_vanilla \
  --lang lat+grc \
  --tessdata-dir /homessddata/Projects/tesstrain/langdata_lstm-main \
  --filter-unicharset /homessddata/Projects/pdfocr/unicharset
```

O padrão é PSM 6, TIFF grayscale Deflate a 300 DPI, páginas `2048x4096`,
margens de 32 pixels e gap de 16 pixels. Linhas largas recebem uma página
dedicada sem redimensionamento.

Depois da seleção e de `--limit`, os candidatos são embaralhados antes da
busca dos BLOBs e da composição das páginas. Isso impede que o ranking de
consenso ou `line_id` determine a composição dos bundles. O shuffle é
reproduzível e usa `--seed 0` por padrão. Ele é intencionalmente anterior ao
shuffle que o próprio Tesseract aplica ao `DocumentData` já recortado.

Para preservar somente as imagens multipágina após a validação, acrescente:

```bash
--keep-tiff
```

Os arquivos serão gravados como `debug_tiff/real_000000.tif`. Para preservar
também BOX, log e demais intermediários, use `--keep-intermediates`. Se as duas
opções forem usadas, o TIFF de debug será copiado e o original continuará no
diretório de intermediários.

O resto que não completa um bundle é salvo em `deferred.jsonl`. Para retomar
uma execução interrompida, repita os mesmos argumentos e acrescente `--resume`.
Bundles existentes só são reutilizados quando configuração, código exportador,
entradas, pixels, manifesto e checksum do `.lstmf` conferem.

## Artefatos e validação

Uma execução bem-sucedida produz:

```text
bundles/real_000000.lstmf
bundles/real_000000.json
all.list
all-gt
manifest.jsonl
warnings.jsonl
decisions.jsonl
deferred.jsonl
run.json
```

`tools/lstmf_info.cpp` é compilado automaticamente contra o `libtesseract.a`
da árvore configurada. Antes da publicação, ele confirma a quantidade de
amostras e o multiconjunto exato das transcrições, pois o Tesseract embaralha a
ordem interna do `DocumentData`.

Testes focados:

```bash
pytest -q \
  tests/test_export_training.py \
  tests/test_normalize_ocr_db_nfc.py \
  tests/test_infer_nfc_storage.py \
  tests/test_review_nfc_storage.py
python -m py_compile \
  export_training.py \
  infer.py \
  review.py \
  scripts/normalize_ocr_db_nfc.py
git diff --check -- \
  export_training.py \
  infer.py \
  review.py \
  scripts/normalize_ocr_db_nfc.py \
  docs/EXPORT_TRAINING_BUNDLES.md \
  tools/lstmf_info.cpp \
  tests/test_export_training.py \
  tests/test_normalize_ocr_db_nfc.py \
  tests/test_infer_nfc_storage.py \
  tests/test_review_nfc_storage.py
```

Validação de consumo de um canário com o modelo Migne:

```bash
/homessddata/Projects/tesstrain/tesseract/build/bin/lstmeval \
  --model /homessddata/Projects/pdfocr/migne.traineddata \
  --eval_listfile /tmp/migne-bundle-canary/all.list
```

O resultado deve informar `Loaded N/N lines`. Consulte `run.json` para o estado
`COMPLETE`, contagens, filtros e hashes usados; intermediários de falhas são
preservados em `failed/`.

## Correção NFC do banco existente

Novas gravações da inferência e da revisão são normalizadas antes de serem
persistidas. Para corrigir registros históricos, primeiro execute a auditoria
somente leitura:

```bash
python scripts/normalize_ocr_db_nfc.py --db ocr.db
```

Depois de fazer backup do SQLite e interromper escritores concorrentes, aplique
a correção:

```bash
python scripts/normalize_ocr_db_nfc.py --db ocr.db --apply
```

O utilitário processa em lotes, recalcula `line_versions.text_hash`, atualiza os
campos textuais de `lines` e consolida votos duplicados que se tornem idênticos
após NFC. A operação é idempotente e pode ser retomada.
