# Reparação de índices: páginas e arquivos OCR

Data da revisão: 2026-08-23

## Objetivo

Este guia descreve como corrigir âncoras de obras e seções já extraídas sem
rodar novamente o agente de geração dos índices. O fluxo serve para casos em
que `start_page`, `end_page`, `start_file` ou `end_file` estão nulos, truncados
ou apontam para uma página de índice em vez do corpo material.

A fonte editorial é `data/index_payloads/<VOLUME>_indices.json`. O SQLite
`data/patristic_indices.db` é derivado desses payloads, e
`web/public/indices` mais `web/public/indexador/indices` são derivados do
SQLite. Corrigir somente o banco ou somente o diretório público cria uma
divergência que reaparece no próximo rebuild.

## Princípios de segurança

- Trabalhar por volume e fazer backup do banco e dos payloads afetados antes
  da primeira gravação.
- Fazer primeiro um dry-run. `--apply` só deve ser usado depois da revisão dos
  candidatos.
- Nunca interpretar o sufixo físico do `.txt` como página editorial. Por
  exemplo, `PL020-759.txt` pode conter a coluna 295.
- Não completar dígitos por heurística. Em PL020 foram confirmados casos como
  `29→295` e `33→335`, mas cada um foi validado no OCR e na imagem.
- Um match literal forte pode ser apenas uma ocorrência no `Elenchus`, `Ordo`
  ou índice final. Confirmar que o arquivo contém o título ou início do corpo.
- Páginas mistas existem: a mesma imagem pode terminar uma obra e iniciar um
  índice. A classificação do arquivo inteiro é apenas um sinal.
- Remissões como `Vide supra`, `Vide tomum...` ou `col. ...` podem apontar para
  outro volume ou para material anterior. Não inventar uma âncora local.
- Não rodar o agente LLM para uma correção determinística já comprovada no OCR.

## 1. Auditoria

Para um volume:

```bash
python scripts/reconcile_work_index_anchors.py \
  --volume-id PL020 \
  --workers 1 \
  --report-json /tmp/PL020-anchor-audit.json
```

Para vários volumes, repetir `--volume-id` ou usar uma lista separada por
vírgulas. Uma auditoria global pode usar até 20 workers nesta máquina:

```bash
python scripts/reconcile_work_index_anchors.py \
  --all-volumes \
  --workers 20 \
  --report-json /tmp/work-anchor-audit.json
```

Razões que merecem triagem prioritária:

- `missing_start_file`: existe obra sem link físico inicial;
- `candidate_editorial_page_not_supported`: a página declarada não foi
  sustentada pelo OCR;
- `no_non_index_candidate_with_title_evidence`: o índice traz uma descrição
  bibliográfica longa, mas o corpo usa um título mais curto;
- `start_file_points_to_index_source`: pode ser erro, peça editorial legítima
  ou página mista;
- `end_overlap`: revisar o limite compartilhado com a obra seguinte.

O reconciliador normaliza caixa, diacríticos e ligaturas (`æ/ae`, `œ/oe`) e
aceita um núcleo material parecido com a descrição bibliográfica. Isso amplia
os candidatos, mas não substitui a inspeção.

## 2. Localização manual no OCR

Começar com o volume, nunca com o corpus completo:

```bash
rg -n -i 'epistol.{0,8}virgin|villecourt' teste/PG001/text
```

Comparar o candidato com arquivos vizinhos e observar:

- cabeçalho com as duas colunas editoriais;
- folha de rosto, título, incipit ou prefácio da obra;
- fim da obra anterior e começo da seguinte;
- palavras como `INDEX`, `ELENCHUS`, `ORDO`, `TABULA`, `Vide supra`;
- OCR de números confundindo `1/I/l`, `5/S`, `8/B` ou omitindo algarismos.

O número editorial deve vir do conteúdo impresso/OCR e da sequência dos
vizinhos. O número terminal do nome do arquivo serve somente para ordenar e
parear os artefatos físicos.

### Hierarquia de evidência para o início material

1. O fac-símile mostra um número editorial legível e, na mesma coluna, o
   título, incipit ou começo material da obra.
2. Se o número estiver cortado ou ilegível, aceitar a sequência somente quando
   `N-1` fecha ou ainda contém a obra X, `N` abre inequivocamente a obra Y e
   `N+1` continua Y. Registrar que a paginação foi inferida pela sequência, não
   lida visualmente.
3. Cabeçalho repetido, match numérico ou estimador editorial isolado não
   bastam. Eles podem apontar para continuação, índice ou placa remissiva.

O fallback automático correspondente é conservador: exige candidato material,
match exato ou fuzzy do título, começo de sequência com pelo menos três
ocorrências e suporte do estimador à página já declarada. Ele não reescreve
automaticamente uma página declarada que o candidato não sustenta; correções
desse tipo exigem a inspeção manual acima.

## 3. Imagem pareada

O reconciliador devolve `image_file` nos candidatos quando encontra uma imagem
irmã inequívoca. Também é possível resolver o caminho diretamente:

```bash
python .codex/skills/alphabetical-index-extractor/scripts/locate-ocr-page-image.py \
  --ocr-file teste/PG001/text/7fa490d2-837f-49cc-b89b-e5df30fac772-175.txt \
  --pretty
```

A mesma regra está em
`patristica_pipeline.index_target_locator.resolve_paired_page_image()`. Ela usa
o sufixo físico apenas para parear `text/` e `images/`; não infere a página
editorial. Se retornar `ambiguous` ou `missing`, não escolher uma imagem por
suposição.

Abrir a imagem quando a dúvida depender de layout, ligatura, algarismo, título
ornamental ou limite compartilhado. Registrar a evidência visual material no
payload quando ela decidir o reparo.

## 4. Edição canônica do payload

Obras usam:

```json
{
  "start_page": 349,
  "end_page": 508,
  "start_file": "/homessddata/Projects/pdfocr/teste/PG001/text/...-175.txt",
  "end_file": "/homessddata/Projects/pdfocr/teste/PG001/text/...-254.txt"
}
```

Seções usam nomes diferentes:

```json
{
  "page_start": 1455,
  "page_end": 1456,
  "file_start": "/homessddata/Projects/pdfocr/teste/PL154/text/...-732.txt",
  "file_end": "/homessddata/Projects/pdfocr/teste/PL154/text/...-732.txt"
}
```

Uma decisão manual deve ficar dentro de `raw_json`:

```json
"manual_anchor_review": {
  "status": "resolved",
  "method": "direct OCR and paired-image inspection",
  "evidence": "Work heading and opening verified at editorial column 295."
}
```

Use `resolved_unlocated` quando a revisão comprovar que o item é apenas uma
remissão externa ou não possui corpo local. Nesse caso, deixe `start_page` e
`start_file` nulos e explique em `evidence` a coluna da placa remissiva e o
destino citado. Os estados `resolved` e
`resolved_unlocated` impedem que uma auditoria posterior proponha novamente o
mesmo falso positivo.

Para lotes revisados, prefira um manifesto versionado e a aplicação por chave:

```bash
python scripts/apply_work_anchor_repair_manifest.py \
  --manifest data/index_repairs/manual_work_anchor_repairs_2026-08-23.json \
  --report-json /tmp/manual-anchor-dry-run.json

python scripts/apply_work_anchor_repair_manifest.py \
  --manifest data/index_repairs/manual_work_anchor_repairs_2026-08-23.json \
  --apply \
  --report-json /tmp/manual-anchor-apply.json
```

O aplicador valida `work_key`, volume, estado, existência do arquivo e
confinamento ao `source_root`. Ele só altera `start_page`, `start_file` e
`raw_json.manual_anchor_review`; autores, títulos, traduções, limites finais,
seções e entradas permanecem intactos.

Depois da edição:

```bash
jq empty data/index_payloads/PL020_indices.json
```

## 5. Importação controlada no SQLite

Importar somente os volumes editados:

```bash
python scripts/import_index_json.py \
  --db data/patristic_indices.db \
  --input data/index_payloads/PL020_indices.json \
  --replace
```

`--replace` substitui as linhas do volume, por isso o payload inteiro deve ser
válido e revisado. Confirmar o banco em modo somente leitura:

```bash
sqlite3 -readonly data/patristic_indices.db 'PRAGMA integrity_check;'
sqlite3 -readonly data/patristic_indices.db \
  "SELECT work_key,start_page,start_file FROM works WHERE volume_id='PL020';"
```

Executar a auditoria de integridade materializada:

```bash
python scripts/audit_index_integrity.py \
  --db data/patristic_indices.db \
  --payload-dir data/index_payloads \
  --report-json /tmp/index-integrity.json
```

## 6. Exportação pública

O export completo dos shards JSON é:

```bash
python tools/export_indices_from_db.py \
  --db data/patristic_indices.db \
  --out web/public/indices \
  --workers 20
```

Para poucos volumes, é mais rápido regravar somente os shards afetados e
substituir suas linhas no manifesto completo:

```bash
python tools/export_indices_from_db.py \
  --db data/patristic_indices.db \
  --out web/public/indices \
  --volume PL020 \
  --volume PL163 \
  --merge-manifest \
  --progress-every 1 \
  --workers 2
```

O export informa a auditoria das relações de tradução, o progresso e o tempo.
Shards e manifesto são substituídos atomicamente; uma interrupção preserva o
último arquivo completo. `--compresslevel` permite trocar tamanho por tempo,
mas o artefato publicado continua usando o padrão 9.

Cada worker abre sua própria conexão SQLite somente leitura. Nunca compartilhar
uma conexão entre processos. O manifesto só é substituído depois que todos os
volumes terminam. Nesta máquina, uma exportação focal de 30 volumes caiu de
40,5 s com um worker para 7,3 s com 20 workers, produzindo conteúdo idêntico.
O padrão detecta a afinidade de CPU e usa no máximo 20 processos; `--workers 1`
mantém o modo serial. Para poucos volumes, o script reduz automaticamente o
número efetivo de workers à quantidade de volumes.

Depois, reconstruir o índice customizado de índices, que não usa Pagefind:

```bash
cd web
npm run build:indices-indexador
```

Validar o manifesto, os shards afetados e uma busca de fumaça antes de
publicar. Alterar `web/public/indices` sem reconstruir
`web/public/indexador/indices` deixa a navegação e a pesquisa em estados
diferentes.

## 7. Protocolo para revisão com subagentes

Para investigação manual paralela, dividir por faixas de volumes e limitar cada
subagente a poucos itens. Cada resposta deve trazer:

- `work_key` e título;
- arquivo inicial/final proposto;
- página editorial inicial/final;
- trecho de OCR que sustenta a decisão;
- caminho da imagem realmente aberta, quando existir;
- nível de confiança e alternativas rejeitadas;
- indicação explícita de remissão externa.

Os subagentes não devem editar payload, banco ou export. Um revisor central
consolida as evidências, elimina conflitos, aplica as mudanças e executa a
auditoria final. Essa separação evita gravações concorrentes e torna cada
decisão rastreável.

## 8. Resultado da campanha de 2026-08-23

A classe prioritária `start_page` conhecida com `start_file` ausente foi
esgotada nos payloads canônicos:

| Resultado da revisão | Itens |
| --- | ---: |
| início material confirmado | 131 |
| remissão, duplicata entre tomos ou início material ausente | 21 |
| total revisado nesta campanha | 152 |
| página conhecida com arquivo ausente após o reparo | 0 |

Estado global dos 4.772 registros de obra após a aplicação:

| Estado | Itens |
| --- | ---: |
| página e arquivo conhecidos | 4.558 |
| arquivo conhecido, página editorial ausente | 131 |
| página e arquivo ausentes | 83 |
| página conhecida, arquivo ausente | 0 |

Os dois manifestos auditáveis são:

- `data/index_repairs/manual_work_anchor_repairs_2026-08-23.json`;
- `data/index_repairs/manual_work_anchor_repairs_remaining_2026-08-23.json`.

Foram reimportados somente 21 volumes. Antes e depois, `index_strings`
permaneceu com 200.404 linhas e `index_translations` com 801.616; os hashes
SHA-256 das duas tabelas também permaneceram idênticos. O export focal paralelo
terminou em 3,5 s, preservou o manifesto de 407 volumes e o índice customizado
foi reconstruído com 170.671 documentos e 259.736 termos.

## 9. Checklist final

- payloads válidos com `jq empty`;
- nenhum reparo baseado apenas no sufixo físico;
- OCR e imagem inspecionados nos casos ambíguos;
- remissões externas marcadas como `resolved_unlocated`;
- dry-run sem tentativa de reverter decisões manuais;
- payloads reimportados com `--replace`;
- `PRAGMA integrity_check` igual a `ok`;
- teste focado do reconciliador aprovado;
- shards JSON exportados;
- índice customizado reconstruído e validado.
