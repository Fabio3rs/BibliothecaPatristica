# Review e endurecimento das pipelines de índices OCR

## Escopo

Este review cobre:

- `scripts/run_index_extraction.py`: índices gerais/estruturais, do início físico para o fim;
- `scripts/run_alphabetical_index_extraction.py`: índices alfabéticos/analíticos, do fim físico
  para o início;
- `scripts/run_index_extraction_chunks.py`: execução semântica dividir-e-conquistar;
- localização determinística, estimação de páginas editoriais, montagem de fragmentos,
  verificação contra OCR e importação.

O princípio central é separar descoberta mecânica de leitura editorial:

1. scripts determinísticos localizam regiões, ordenam arquivos, estimam cobertura e produzem
   evidência;
2. agentes leem e segmentam o conteúdo editorial real;
3. scripts determinísticos validam escopo, montagem, proveniência e cobertura;
4. números impressos nunca são tratados como identificadores de arquivo.

## Correções aplicadas

### Separação das duas pipelines

- Os perfis `general` e `alphabetical` usam famílias de cabeçalhos distintas.
- O perfil geral semeia a janela física inicial; o alfabético semeia a janela final.
- Cabeçalhos partidos entre blocos OCR, como `ELENCHUS` seguido de
  `AUCTORUM ET OPERUM...`, são reconhecidos pela leitura agregada do cabeçalho.
- Um artefato externo tipado para a pipeline errada é rejeitado.
- Um artefato legado sem `profile` não é reutilizado pela pipeline geral, pois pode conter a
  antiga mistura de candidatos alfabéticos.

### Ordem física correta

- Arquivos UUID são ordenados pelo sufixo físico numérico, por `page_sort_key`, e não
  lexicograficamente.
- A direção de investigação não altera a regra de propriedade: uma entrada entre páginas pertence
  ao chunk que contém o arquivo físico onde ela começa, em ordem crescente de scans.

### CER de paginação editorial

- Página editorial, sufixo físico do arquivo e referência citada são três sistemas separados.
- O estimator reduz a confiança de inferências formadas somente por vizinhos.
- O locator aceita distância OCR de um dígito em referências editoriais, inclusive substituição
  e perda de dígito.
- Sinais numéricos correlacionados são limitados: cabeçalho, estimador, vizinhos e posição física
  não podem contar repetidamente como provas independentes.
- Evidência textual exata no OCR recebe precedência sobre um número editorial possivelmente
  corrompido.
- Evidência exclusivamente numérica produz `ambiguous`, mesmo quando a probabilidade calculada
  parece alta. O agente deve folhear e pesquisar o volume.

### Liberdade de investigação do agente

O workplan define propriedade e emissão, não uma prisão de leitura. O prompt manda:

- começar nos arquivos próprios e de contexto;
- abrir páginas adicionais e pesquisar qualquer parte do mesmo `source_root` quando CER,
  paginação danificada, limite incerto ou seção truncada exigirem;
- registrar em `raw_json` as páginas e buscas extras materialmente relevantes;
- emitir apenas entradas pertencentes ao chunk.

Isso permite investigar além da janela sem criar duplicação entre chunks.

### Quebras de palavra OCR

- O leitor preferencial é `scripts/read_ocr_page_text.py --view xml --show-source`.
- Ele recompõe quebras prováveis dentro do mesmo bloco, por exemplo
  `pala-\nvra` em `palavra`.
- Hífens lexicais duvidosos devem ser conferidos no texto bruto.
- Um hífen no fim de um arquivo físico só é removido se o começo do scan seguinte provar a
  continuação.
- `scripts/pipeline_index_extraction/fix_linebreak_hyphens.py` é documentado no prompt como
  recuperação pós-verificação, nunca como evidência nem como normalização cega.

### Retomada segura

- Cada chunk contém fingerprint do contrato e dos arquivos OCR próprios/de sobreposição/de
  contexto.
- Um chunk completo só é reaproveitado se:
  - o identificador ainda existe;
  - o fingerprint é idêntico;
  - o fragmento de saída ainda existe.
- Mudança no OCR ou no contrato invalida o checkpoint.
- Um fragmento marcado como completo é revalidado antes de ser ignorado por `--skip-complete`.

### Montagem e cobertura

- Fragmentos completos são unidos determinísticamente em `assembled_fragments.json`.
- Todo entry fragmentado recebe `entry_key` estável.
- Chaves de obras, seções, nós, entradas e referências são conferidas depois da montagem final.
- A pipeline falha se o payload final omitir um objeto estável produzido por um chunk completo.
- Entradas sem fonte própria usam a extensão física da seção como evidência.
- Uma amostra configurável é comparada ao OCR por match normalizado exato/cross-page/fuzzy.
- Seções list-bearing vazias exigem motivo, status de recuperação e arquivos de evidência.

### Execução dividir-e-conquistar

- Cada chunk roda em contexto Codex efêmero; JSON em disco é o estado persistente.
- `--chunk-workers N` permite executar chunks independentes em paralelo. O padrão permanece `1`
  para evitar concorrência inesperada em volumes com helpers manuais compartilhados.
- A atualização do manifest ocorre no processo coordenador, após validar cada fragmento.

### Importação

- A pipeline geral agora encaminha explicitamente `--db` ao importador, respeitando o banco
  escolhido na CLI.
- Verificação de fragmentos e evidência ocorre antes da importação.

## Smoke tests em volumes reais

Em `PG003`:

- o perfil geral localizou `ELENCHUS AUCTORUM ET OPERUM...` no arquivo físico 004;
- o perfil alfabético localizou `INDEX RERUM ET VERBORUM` e `ORDO RERUM` nos arquivos
  físicos finais 588–596;
- a estimação nos arquivos físicos 271–273 recuperou as duplas editoriais
  541/542, 543/544 e 545/546;
- num teste de CER 543→643, a frase corporal exata levou o locator ao arquivo físico 272,
  em vez da página numericamente coincidente.

Em `PL129`:

- o perfil geral localizou `ELENCHUS AUCTORUM ET OPERUM...` no arquivo físico 008;
- o perfil alfabético localizou material nos arquivos 696–698 e 722–728;
- o scanner confirmou a cauda física correta em 732, eliminando a antiga ordenação UUID
  lexicográfica.

## Comandos de validação

```bash
PYTHONPYCACHEPREFIX=/tmp/pdfocr_pycache pytest -q \
  tests/test_audit_index_page_boundary_sample.py \
  tests/test_audit_index_scan_sample.py \
  tests/test_build_filtered_pages_profiles.py \
  tests/test_check_index_target_locator_examples.py \
  tests/test_editorial_page_estimator.py \
  tests/test_estimate_editorial_pages_cli.py \
  tests/test_import_alphabetical_index_json.py \
  tests/test_import_index_numbering.py \
  tests/test_index_chunk_driver.py \
  tests/test_index_entry_estimator.py \
  tests/test_index_fragment_assembly.py \
  tests/test_index_page_boundaries.py \
  tests/test_index_payload_evidence.py \
  tests/test_index_target_locator.py \
  tests/test_index_target_locator_cli.py \
  tests/test_index_workplan.py \
  tests/test_index_workplan_resume.py \
  tests/test_run_alphabetical_index_extraction.py \
  tests/test_run_index_extraction.py \
  tests/test_run_index_extraction_chunks.py
```

O locator continua deliberadamente conservador: quando só há números ou quando candidatos textuais
competem, a saída correta é ambiguidade acompanhada de evidência para inspeção do agente, não uma
resolução determinística artificial.
