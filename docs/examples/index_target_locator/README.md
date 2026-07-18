# Exemplos de validação manual do `index_target_locator`

Estes arquivos são exemplos reais de validação manual para o helper:

- [pg003_manual_validation.json](/homessddata/Projects/pdfocr/docs/examples/index_target_locator/pg003_manual_validation.json:1)
- [pg036_manual_validation.json](/homessddata/Projects/pdfocr/docs/examples/index_target_locator/pg036_manual_validation.json:1)
- [pl143_manual_validation.json](/homessddata/Projects/pdfocr/docs/examples/index_target_locator/pl143_manual_validation.json:1)
- [pl198_manual_validation.json](/homessddata/Projects/pdfocr/docs/examples/index_target_locator/pl198_manual_validation.json:1)
- [po025_manual_validation.json](/homessddata/Projects/pdfocr/docs/examples/index_target_locator/po025_manual_validation.json:1)

Eles ficam separados de `data/` e do pipeline real de importação.

## Uso

Exemplo:

```bash
python scripts/index_target_locator.py \
  --input docs/examples/index_target_locator/pg003_manual_validation.json \
  --pretty
```

## Convenção destes exemplos

- `entries[]` segue o contrato do helper.
- `expected_manual` existe apenas para validação humana.
- o helper ignora `expected_manual`.
- `expected_manual.section_start_file` aponta para o melhor arquivo de início da seção ou peça.
- `expected_manual.editorial_anchor_file` aponta para o melhor arquivo onde a âncora editorial do índice cai com mais precisão.
- `expected_manual.target_file` continua aceito como fallback legado para ambos os papéis.
- `expected_manual.accepted_section_start_targets` e `expected_manual.accepted_editorial_anchor_targets` podem listar mais de um arquivo aceitável em casos fronteiriços.
- `expected_manual.reason` resume por que o caso foi escolhido.

## Critério de validação manual

Para cada arquivo:

1. Rodar o helper.
2. Ver se o `best_candidate.file` coincide com o alvo esperado de início de seção e/ou com a âncora editorial.
3. Se falhar, comparar `candidates[]`, `probability`, `candidate_role` e `evidence`.
4. Ajustar heurísticas.
5. Rodar de novo.
6. Preservar o caso como regressão conhecida.

## Observação

Os casos aqui foram escolhidos para serem relativamente verificáveis por leitura direta do OCR.

Eles não esgotam a complexidade real do corpus.

## Lacuna atual

`PO022` foi inspecionado, mas ainda não entrou como exemplo novo neste diretório porque a convenção editorial de paginação observada ali ainda está menos transparente do que em `PG` e `PL`. Se esse volume for promovido a gabarito manual, convém antes registrar em documentação como os números do índice se relacionam com a paginação física e com os marcadores internos da edição.
