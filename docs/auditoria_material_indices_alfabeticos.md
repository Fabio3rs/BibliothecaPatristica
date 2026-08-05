# Auditoria do material já extraído dos índices alfabéticos

O auditor cruza três fontes sem modificar nenhuma delas:

1. o payload em `data/alphabetical_index_payloads/`;
2. `data/alphabetical_indices.db`;
3. o OCR atual do volume em `teste/<VOLUME>/text/`.

Ele serve para priorizar reimportações, localizar referências ainda sem alvo e
questionar alvos antigos quando a evidência textual atual aponta para outro
arquivo. O relatório é uma proposta de reparo, não uma mutação automática.

## Uso

Auditoria estrutural rápida, sem abrir o OCR:

```bash
.venv/bin/python scripts/audit_alphabetical_extracted_material.py \
  --volume-id PL143 --no-ocr
```

Auditoria de uma amostra distribuída de referências:

```bash
.venv/bin/python scripts/audit_alphabetical_extracted_material.py \
  --volume-id PL143 --volume-id PG066 --max-refs 50 --workers 1
```

`--max-refs 0` inspeciona todas as referências do volume e deve ser usado
explicitamente, pois pode ler muitos arquivos. Os relatórios são gravados em
`data/alphabetical_material_audits/`.

## Classes principais

- `locator_text_without_refs`: a linha parece conter localizador, mas nenhuma
  referência foi serializada;
- `target_file_missing`: o payload aponta para um arquivo inexistente;
- `target_inside_index_section`: o alvo recai no próprio índice;
- `*_missing_from_db` e `target_drift_payload_db`: divergência payload/DB;
- `fill_missing_target`: a barreira determinística encontrou um alvo para uma
  referência vazia;
- `replace_suspicious_target`: a evidência determinística converge para arquivo
  diferente do alvo antigo;
- `review_competing_target`: há candidato melhor, mas a evidência ainda não
  autoriza reparo automático;
- `source_text_unverified`: o texto extraído não foi reencontrado na evidência
  física declarada;
- `source_scope_mismatch`: o texto foi reencontrado literalmente em um vizinho
  da mesma família; o relatório sugere `extend_file_start` ou `extend_file_end`;
- `segmentation_suspect`: uma entrada pode ter engolido duas ou mais linhas.

Uma proposta determinística usa a mesma barreira do pipeline compacto: conteúdo
textual e localizador independentes, candidato único, confiança mínima e, quando
necessário, validação da paginação editorial pelos arquivos vizinhos. Em índices
onomásticos, variantes, números irmãos, nomes vizinhos e família documental são
evidências cruzadas; nenhuma delas, isoladamente, autoriza substituição.

O relatório v2 usa nomes espaciais explícitos:
`stored_target_ocr_file`, `db_stored_target_ocr_file`,
`helper_best_ocr_file` e `proposed_target_ocr_file`. Nos comparativos estruturais,
os campos equivalentes são `payload_target_ocr_file` e `db_target_ocr_file`.
Nenhum deles representa página editorial; páginas citadas usam o prefixo
`cited_editorial_`.
