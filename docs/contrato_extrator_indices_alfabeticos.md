# Contrato do extrator de índices alfabéticos

Este documento fecha o formato do novo extrator de índices alfabéticos, remissivos, onomásticos e bíblicos.

Ele usa como ponto de partida o extrator atual de índices em:

- [scripts/run_index_extraction.py](/homessddata/Projects/pdfocr/scripts/run_index_extraction.py:1)
- [.codex/skills/patristic-index-extractor/scripts/index_db.py](/homessddata/Projects/pdfocr/.codex/skills/patristic-index-extractor/scripts/index_db.py:1)

Mas o schema abaixo é separado, porque os casos reais observados em `PG`, `PL` e `PO` não cabem bem no modelo atual de `index_entries`.

Este contrato deve ser lido junto com:

- [docs/levantamento_indices_alfabeticos.md](/homessddata/Projects/pdfocr/docs/levantamento_indices_alfabeticos.md:1)
- [docs/normalizacao_indices_biblicos.md](/homessddata/Projects/pdfocr/docs/normalizacao_indices_biblicos.md:1)
- [docs/taxonomia_indices_po.md](/homessddata/Projects/pdfocr/docs/taxonomia_indices_po.md:188)

## 1. Decisão de modelagem

A decisão é:

- manter `volumes` como âncora principal, como no extrator atual
- manter um conceito de `sections`, porque um volume pode ter mais de um índice final
- não reaproveitar `index_entries` como tabela principal do novo fluxo
- introduzir `entries` com hierarquia opcional e `refs` como entidade própria

O problema central do schema atual é que ele pressupõe, na prática:

- uma entrada principal relativamente plana
- no máximo uma referência de página/coluna por linha
- um único `target_file` principal

Isso funciona para `ORDO RERUM` e `INDEX CAPITUM`, mas não para:

- `PG003`, com muitas referências por lema
- `PL143`, com hierarquia explícita acima do lema
- `PO025`, com índices de nomes, citações bíblicas, perícopes e concordância

Portanto, o novo schema deve ser:

- multi-seção
- multi-referência
- hierárquico de forma opcional
- conservador na normalização
- amigável para busca web

## 2. Princípios obrigatórios

O agente:

- não pode criar tipos novos fora das taxonomias definidas
- não pode colapsar várias referências em um único campo textual
- não pode substituir o literal OCR por forma normalizada
- não pode confundir `arquivo OCR`, `página impressa da entrada do índice` e `página citada pelo índice`

O schema precisa preservar separadamente:

- a seção editorial do índice
- a entrada lógica do índice
- a referência individual dentro da entrada
- a localização física plausível no volume

## 3. Reaproveitamento do extrator atual

Vale reaproveitar do extrator atual:

- a tabela `volumes`
- o conceito de `section_key`
- o padrão de `raw_json`
- o uso de `confidence`
- a ideia de importar um payload JSON canônico para SQLite

Não vale reaproveitar diretamente:

- `works`, como eixo obrigatório do novo fluxo
- `index_entries` com `page_ref_int` único
- `target_file` único por entrada como verdade suficiente

`work_key` continua útil como campo opcional de relação quando o índice final estiver claramente ligado a uma obra específica, mas não deve estruturar o schema inteiro.

## 4. Taxonomia mínima fechada

### 4.1 Tipos de seção

`section_kind` permitidos:

- `analytic_subject`
- `alphabetical_general`
- `onomastic_person`
- `onomastic_place`
- `onomastic_mixed`
- `author_index`
- `scripture_index`
- `pericope_index`
- `concordance_index`
- `foreign_terms`
- `ordo_rerum`
- `crosswalk_index`
- `editorial_closure`

Regras:

- `ordo_rerum` não é índice alfabético, mas pode coexistir no mesmo volume e precisa poder ser armazenado no mesmo banco
- `crosswalk_index` cobre casos como `INDEX ORATIONUM` com correspondência entre duas ordenações
- `editorial_closure` cobre `addenda/corrigenda` quando o scanner final captar esse material junto

### 4.2 Tipos de entrada

`entry_kind` permitidos:

- `lemma`
- `sublemma`
- `cross_reference`
- `editorial_note`
- `heading_group`
- `scripture_citation`
- `scripture_pericope`
- `concordance_item`

Regras:

- `heading_group` cobre letras e cabeçalhos internos como `A`, `B`, `SUMMI PONTIFICES`
- `editorial_note` não é lema e não deve ser promovido a `lemma`
- `cross_reference` cobre `vide`, `vid.`, `voir`, remissões explícitas

### 4.3 Tipos de referência

`ref_kind` permitidos:

- `editorial_page`
- `editorial_column`
- `editorial_page_column`
- `editorial_range`
- `editorial_page_line`
- `target_locator`
- `scripture`
- `parallel_locator`
- `unresolved`

Regras:

- `target_locator` cobre a localização no corpus OCR do ponto material provável
- `scripture` é reservado para referências bíblicas parseadas
- `parallel_locator` cobre concordâncias, tabelas paralelas e relações `novus/vetus ordo`

### 4.4 Tipos de node

`node_kind` permitidos:

- `letter_group`
- `heading_group`
- `rubric_group`
- `ordinal_group`

Regras:

- `letter_group` cobre letras de agrupamento como `A`, `B`, `C`
- `heading_group` cobre macrogrupos semânticos como `SUMMI PONTIFICES`
- `rubric_group` cobre rubricas editoriais internas que não são lemas
- `ordinal_group` cobre divisões numeradas como `I.`, `II.`, `III.`

### 4.5 Papéis de referência bíblica

`ref_role` permitidos em `alphabetical_scripture_refs`:

- `citation`
- `pericope`
- `concordance_component`

Regras:

- `citation` cobre citações bíblicas pontuais
- `pericope` cobre intervalos longos e leituras contínuas
- `concordance_component` cobre referências bíblicas anexas a tabelas de concordância ou rubricas paralelas

## 5. Schema SQLite fechado

Esta é a versão fechada do schema recomendada para implementação inicial.

Decisões explícitas desta versão:

- `work_key` permanece como campo opcional sem FK nesta primeira versão
- taxonomias documentais viram `CHECK` no banco para impedir drift de schema
- `alphabetical_scripture_refs` passa a carregar `ref_norm`, porque a camada bíblica exige `ref_raw` e `ref_norm`
- `alphabetical_runs.volume_id` referencia `alphabetical_volumes(volume_id)`
- a ordem editorial é protegida por constraints de unicidade por escopo

Este é o schema recomendado para o novo banco.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS alphabetical_volumes (
    volume_id TEXT PRIMARY KEY,
    collection TEXT NOT NULL CHECK (collection IN ('PG', 'PL', 'PO')),
    source_root TEXT NOT NULL,
    volume_label TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alphabetical_sections (
    section_key TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    work_key TEXT,
    section_order INTEGER,
    section_kind TEXT NOT NULL CHECK (
        section_kind IN (
            'analytic_subject',
            'alphabetical_general',
            'onomastic_person',
            'onomastic_place',
            'onomastic_mixed',
            'author_index',
            'scripture_index',
            'pericope_index',
            'concordance_index',
            'foreign_terms',
            'ordo_rerum',
            'crosswalk_index',
            'editorial_closure'
        )
    ),
    heading_raw TEXT NOT NULL,
    heading_norm TEXT,
    heading_letter TEXT,
    page_start INTEGER,
    page_end INTEGER,
    file_start TEXT,
    file_end TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (section_order IS NULL OR section_order >= 1),
    CHECK (page_start IS NOT NULL OR file_start IS NOT NULL),
    CHECK (page_end IS NOT NULL OR file_end IS NOT NULL),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_sections_volume_id
    ON alphabetical_sections(volume_id);
CREATE INDEX IF NOT EXISTS idx_alpha_sections_kind
    ON alphabetical_sections(section_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_sections_volume_order
    ON alphabetical_sections(volume_id, section_order)
    WHERE section_order IS NOT NULL;

CREATE TABLE IF NOT EXISTS alphabetical_nodes (
    node_key TEXT PRIMARY KEY,
    section_key TEXT NOT NULL REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE,
    parent_node_key TEXT REFERENCES alphabetical_nodes(node_key) ON DELETE CASCADE,
    node_order INTEGER NOT NULL,
    node_kind TEXT NOT NULL CHECK (
        node_kind IN (
            'letter_group',
            'heading_group',
            'rubric_group',
            'ordinal_group'
        )
    ),
    label_raw TEXT NOT NULL,
    label_norm TEXT,
    label_sort TEXT,
    node_level INTEGER NOT NULL,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (node_order >= 1),
    CHECK (node_level >= 1),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_nodes_section_key
    ON alphabetical_nodes(section_key);
CREATE INDEX IF NOT EXISTS idx_alpha_nodes_parent
    ON alphabetical_nodes(parent_node_key);
CREATE INDEX IF NOT EXISTS idx_alpha_nodes_kind
    ON alphabetical_nodes(node_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_nodes_section_order
    ON alphabetical_nodes(section_key, node_order);

CREATE TABLE IF NOT EXISTS alphabetical_entries (
    entry_key TEXT PRIMARY KEY,
    section_key TEXT NOT NULL REFERENCES alphabetical_sections(section_key) ON DELETE CASCADE,
    parent_node_key TEXT REFERENCES alphabetical_nodes(node_key) ON DELETE SET NULL,
    entry_order INTEGER NOT NULL,
    entry_kind TEXT NOT NULL CHECK (
        entry_kind IN (
            'lemma',
            'sublemma',
            'cross_reference',
            'editorial_note',
            'heading_group',
            'scripture_citation',
            'scripture_pericope',
            'concordance_item'
        )
    ),
    lemma_raw TEXT,
    lemma_display TEXT,
    lemma_norm TEXT,
    lemma_sort TEXT,
    entry_raw TEXT NOT NULL,
    context_raw TEXT,
    heading_letter TEXT,
    inferred_printed_page INTEGER,
    section_start_file TEXT,
    editorial_anchor_file TEXT,
    target_file_best TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (entry_order >= 1),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_section_key
    ON alphabetical_entries(section_key);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_parent_node
    ON alphabetical_entries(parent_node_key);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_kind
    ON alphabetical_entries(entry_kind);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_lemma_norm
    ON alphabetical_entries(lemma_norm);
CREATE INDEX IF NOT EXISTS idx_alpha_entries_lemma_sort
    ON alphabetical_entries(lemma_sort);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_entries_section_order
    ON alphabetical_entries(section_key, entry_order);

CREATE TABLE IF NOT EXISTS alphabetical_refs (
    ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT NOT NULL REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE,
    ref_order INTEGER NOT NULL,
    ref_kind TEXT NOT NULL CHECK (
        ref_kind IN (
            'editorial_page',
            'editorial_column',
            'editorial_page_column',
            'editorial_range',
            'editorial_page_line',
            'target_locator',
            'scripture',
            'parallel_locator',
            'unresolved'
        )
    ),
    ref_raw TEXT NOT NULL,
    page_ref_raw TEXT,
    page_ref_int INTEGER,
    page_ref_col TEXT,
    line_ref_raw TEXT,
    range_start_raw TEXT,
    range_end_raw TEXT,
    target_file TEXT,
    target_file_probability REAL,
    section_start_file TEXT,
    editorial_anchor_file TEXT,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (ref_order >= 1),
    CHECK (
        page_ref_raw IS NOT NULL
        OR target_file IS NOT NULL
        OR range_start_raw IS NOT NULL
        OR range_end_raw IS NOT NULL
    ),
    CHECK (
        target_file_probability IS NULL
        OR (target_file_probability >= 0.0 AND target_file_probability <= 1.0)
    ),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_entry_key
    ON alphabetical_refs(entry_key);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_page_ref_int
    ON alphabetical_refs(page_ref_int);
CREATE INDEX IF NOT EXISTS idx_alpha_refs_kind
    ON alphabetical_refs(ref_kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_refs_entry_order
    ON alphabetical_refs(entry_key, ref_order);

CREATE TABLE IF NOT EXISTS alphabetical_scripture_refs (
    scripture_ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT NOT NULL REFERENCES alphabetical_entries(entry_key) ON DELETE CASCADE,
    ref_order INTEGER NOT NULL,
    ref_role TEXT NOT NULL CHECK (
        ref_role IN (
            'citation',
            'pericope',
            'concordance_component'
        )
    ),
    ref_raw TEXT NOT NULL,
    ref_norm TEXT,
    book_raw TEXT,
    book_norm TEXT,
    chapter_start INTEGER,
    verse_start INTEGER,
    chapter_end INTEGER,
    verse_end INTEGER,
    is_range INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    raw_json TEXT NOT NULL,
    CHECK (ref_order >= 1),
    CHECK (is_range IN (0, 1)),
    CHECK (chapter_start IS NULL OR chapter_start >= 1),
    CHECK (verse_start IS NULL OR verse_start >= 1),
    CHECK (chapter_end IS NULL OR chapter_end >= 1),
    CHECK (verse_end IS NULL OR verse_end >= 1),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);
CREATE INDEX IF NOT EXISTS idx_alpha_scripture_entry_key
    ON alphabetical_scripture_refs(entry_key);
CREATE INDEX IF NOT EXISTS idx_alpha_scripture_book_norm
    ON alphabetical_scripture_refs(book_norm);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_scripture_entry_order_role
    ON alphabetical_scripture_refs(entry_key, ref_order, ref_role);

CREATE TABLE IF NOT EXISTS alphabetical_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES alphabetical_volumes(volume_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'failed', 'completed', 'imported', 'skipped')
    ),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    notes TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_volume_id
    ON alphabetical_runs(volume_id);
```

## 6. Por que essas tabelas existem

### 6.1 `alphabetical_sections`

Equivale conceitualmente a `index_sections` do extrator atual.

Ela continua sendo necessária porque:

- um volume pode ter vários índices finais
- cada índice final pode ter taxonomia diferente
- a UI web vai precisar agrupar por seção

### 6.2 `alphabetical_nodes`

Esta é a grande diferença estrutural.

Ela existe para representar hierarquia editorial acima da entrada:

- letras (`A`, `B`, `C`)
- macrogrupos (`ELENCHUS PERSONARUM`)
- subgrupos (`SUMMI PONTIFICES`)
- rubricas internas

`PL143` exige isso.

Sem `nodes`, o agente teria de:

- achatar a hierarquia
- ou inventar campos ad hoc

As duas coisas seriam ruins.

### 6.3 `alphabetical_entries`

Esta é a unidade principal de busca e de renderização.

Ela representa:

- o lema
- a linha lógica extraída do índice
- o texto OCR preservado
- os melhores anchors materiais conhecidos

`lemma_*` pode ser nulo em entradas que sejam:

- apenas remissão cruzada
- apenas nota editorial
- fragmentos OCR ainda não resolvidos

Importante:

- o schema aceita mais de uma `entry` para a mesma zona material do índice, desde que cada uma represente uma hipótese editorial distinta e auditável
- não existe restrição de unicidade em `lemma_raw`, `lemma_norm`, `lemma_sort` ou `entry_raw`
- portanto, ambiguidade explícita pode ser serializada tanto como uma única entrada com candidatos concorrentes quanto como duas entradas distintas, dependendo do tipo de dúvida

Regra operacional:

- se a dúvida for apenas material, sobre qual arquivo OCR melhor ancora a mesma entrada lógica, manter uma única `entry`
- se a dúvida for semântica/editorial, sobre onde começa ou termina uma entrada, se um fragmento pertence ao lema anterior ou ao seguinte, ou se duas leituras produzem estruturas diferentes de `refs`, emitir mais de uma `entry`
- quando mais de uma `entry` nascer da mesma zona OCR ambígua, registrar em `raw_json` que se trata de hipóteses concorrentes do mesmo trecho

### 6.4 `alphabetical_refs`

Cada entrada pode ter muitas referências.

Esta tabela existe para não comprimir:

- `515, 512, 519...`
- `674 b, c`
- `xii, 2-xiii, 10`
- `293`
- `vid. Rudolfum`

num único `page_ref_int`.

Esse é o principal gargalo do schema atual e precisa desaparecer.

### 6.5 `alphabetical_scripture_refs`

Índices bíblicos e concordâncias não devem ser reduzidos a `page_ref_raw`.

É preciso separar:

- a referência bíblica canônica
- a página editorial do volume onde ela é tratada

Por isso:

- a referência bíblica parseada vai para `alphabetical_scripture_refs`
- a remissão material no volume continua em `alphabetical_refs`

## 7. Campos mínimos obrigatórios por camada

### 7.1 Seção

Obrigatórios:

- `section_key`
- `volume_id`
- `section_kind`
- `heading_raw`
- um anchor inicial: `page_start` ou `file_start`
- um anchor final: `page_end` ou `file_end`

### 7.2 Node

Obrigatórios:

- `node_key`
- `section_key`
- `node_order`
- `node_kind`
- `label_raw`
- `node_level`

### 7.3 Entrada

Obrigatórios:

- `entry_key`
- `section_key`
- `entry_order`
- `entry_kind`
- `entry_raw`

Fortemente recomendados:

- `lemma_raw`
- `lemma_norm`
- `lemma_sort`
- `heading_letter`

### 7.4 Referência

Obrigatórios:

- `entry_key`
- `ref_order`
- `ref_kind`
- `ref_raw`

Ao menos um destes deve existir:

- `page_ref_raw`
- `target_file`
- `range_start_raw`
- `book_raw` via tabela bíblica

## 8. Payload JSON canônico

O extrator deve produzir um único payload canônico por volume, no mesmo espírito do extrator atual.

Top-level:

- `schema_version`
- `generated_at`
- `volume`
- `sections`
- `nodes`
- `entries`
- `refs`
- `scripture_refs`
- `coverage`
- `notes`

Regra adicional:

- o extrator deve evitar `entries` vazias sempre que houver line items recuperáveis
- se `sections` vier não vazia e `entries` vier vazia, isso só é aceito quando `coverage.entries_status` for `unrecoverable_ocr` ou `no_line_items`
- nesse caso excepcional, `coverage.entries_status_reason` deve explicar o motivo em texto não vazio
- nesse caso excepcional, `coverage.evidence_files` deve listar arquivos OCR concretos que sustentam a decisão

Formato:

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-16T00:00:00Z",
  "volume": {
    "volume_id": "PG003",
    "collection": "PG",
    "source_root": "teste/PG003/text",
    "volume_label": "PG003",
    "notes": []
  },
  "sections": [],
  "nodes": [],
  "entries": [],
  "refs": [],
  "scripture_refs": [],
  "coverage": {},
  "notes": []
}
```

### 8.1 Forma mínima de seção

```json
{
  "section_key": "PG003:alpha:analytic_subject:001",
  "volume_id": "PG003",
  "work_key": null,
  "section_order": 1,
  "section_kind": "analytic_subject",
  "heading_raw": "INDEX RERUM ET VERBORUM",
  "heading_norm": "index-rerum-et-verborum",
  "heading_letter": null,
  "page_start": null,
  "page_end": null,
  "file_start": "teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-591.txt",
  "file_end": "teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-595.txt",
  "confidence": 0.96,
  "raw_json": {}
}
```

### 8.2 Forma mínima de node

```json
{
  "node_key": "PL143:node:summi-pontifices:001",
  "section_key": "PL143:alpha:onomastic_person:001",
  "parent_node_key": "PL143:node:elencus-personarum:001",
  "node_order": 3,
  "node_kind": "heading_group",
  "label_raw": "SUMMI PONTIFICES",
  "label_norm": "summi pontifices",
  "label_sort": "summi pontifices",
  "node_level": 2,
  "confidence": 0.99,
  "raw_json": {}
}
```

### 8.3 Forma mínima de entrada

```json
{
  "entry_key": "PG003:entry:000001",
  "section_key": "PG003:alpha:analytic_subject:001",
  "parent_node_key": null,
  "entry_order": 1,
  "entry_kind": "lemma",
  "lemma_raw": "Deus",
  "lemma_display": "Deus",
  "lemma_norm": "deus",
  "lemma_sort": "deus",
  "entry_raw": "Deus... 515, 512, 519... ibid. ... 530 ... 571",
  "context_raw": null,
  "heading_letter": "D",
  "inferred_printed_page": null,
  "section_start_file": "teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-591.txt",
  "editorial_anchor_file": "teste/PG003/text/71f490b7-f7b5-498a-8e94-ef7e22d326e7-272.txt",
  "target_file_best": "teste/PG003/text/71f490b7-f7b5-498a-8e94-ef7e22d326e7-272.txt",
  "confidence": 0.83,
  "raw_json": {}
}
```

### 8.4 Forma mínima de referência

```json
{
  "entry_key": "PG003:entry:000001",
  "ref_order": 1,
  "ref_kind": "editorial_page",
  "ref_raw": "515",
  "page_ref_raw": "515",
  "page_ref_int": 515,
  "page_ref_col": null,
  "line_ref_raw": null,
  "range_start_raw": null,
  "range_end_raw": null,
  "target_file": null,
  "target_file_probability": null,
  "section_start_file": null,
  "editorial_anchor_file": null,
  "confidence": 0.92,
  "raw_json": {}
}
```

### 8.5 Forma mínima de referência bíblica

```json
{
  "entry_key": "PO025:entry:000187",
  "ref_order": 1,
  "ref_role": "citation",
  "ref_raw": "Gal. VI, 14",
  "ref_norm": "Gálatas 6,14",
  "book_raw": "Gal.",
  "book_norm": "Gálatas",
  "chapter_start": 6,
  "verse_start": 14,
  "chapter_end": null,
  "verse_end": null,
  "is_range": 0,
  "confidence": 0.97,
  "raw_json": {}
}
```

## 9. Regras de serialização para o agente

O agente deve obedecer a estas regras:

- não emitir árvores aninhadas diretamente dentro de `entries`
- usar listas planas com chaves explícitas (`section_key`, `parent_node_key`, `entry_key`)
- não inventar campos extras fora de `raw_json`
- colocar qualquer dado experimental, editorial ou residual em `raw_json`
- preservar sempre os literais `*_raw`

### 9.1 Papel do helper no fluxo

O helper não decide o JSON final.

O helper serve para expor:

- candidatos de localização material
- evidências textuais e paginais
- probabilidade relativa entre candidatos
- papel provável do candidato (`target_candidate`, `index_page_candidate`, `mixed_signal`)

O agente continua responsável por decidir:

- `section_kind`
- `entry_kind`
- hierarquia em `nodes`
- desdobramento de `refs`
- se há uma entrada só ou mais de uma hipótese concorrente

### 9.2 Evidências mínimas a preservar em `raw_json`

Quando o agente usar o helper para sustentar uma decisão material, deve preservar em `raw_json`, pelo menos quando existirem:

- `status` retornado pelo helper (`resolved`, `ambiguous`, `unresolved`)
- `candidate_role`
- `reason_summary`
- lista resumida de `candidates[]` com `file`, `probability` e principais `evidence`

Isso vale especialmente quando:

- `section_start_file`, `editorial_anchor_file` e `target_file_best` divergem
- o helper marcou o caso como `ambiguous`
- o agente escolheu uma hipótese menos provável por razão editorial explícita

### 9.3 Quando emitir uma entrada só

Emitir uma única `entry` quando:

- a entrada lógica do índice é a mesma e só variam os anchors materiais
- a disputa está entre `section_start_file`, `editorial_anchor_file` e `target_file_best`
- o helper oferece vários candidatos para a mesma referência, mas sem mudar a leitura editorial da entrada

Nesses casos:

- a `entry` permanece única
- a disputa material fica em `raw_json`
- as referências individuais podem carregar seus próprios anchors em `alphabetical_refs`

### 9.4 Quando emitir mais de uma entrada

Emitir mais de uma `entry` quando a ambiguidade mudar a montagem editorial do JSON, por exemplo:

- um fragmento pode pertencer ao lema anterior ou ao lema seguinte
- a mesma linha pode ser lida como `lemma` ou `cross_reference`
- há duas segmentações plausíveis da mesma zona OCR em subentradas diferentes
- a expansão de herança por travessão gera conjuntos de `refs` materialmente diferentes

Nesses casos:

- cada hipótese vira uma `entry_key` própria
- as hipóteses devem compartilhar em `raw_json` um identificador ou nota de agrupamento
- a incerteza não deve ser escondida por fusão precoce

Isso é importante porque:

- facilita import incremental
- facilita auditoria
- evita drift de schema entre volumes

## 10. Campos de busca web

Pensando na página web futura, estes são os campos relevantes:

- `section_kind`
- `heading_raw`
- `label_raw`
- `lemma_display`
- `lemma_norm`
- `lemma_sort`
- `entry_raw`
- `ref_raw`
- `book_norm`

Decisão importante:

- a web deve buscar principalmente em `lemma_norm` e `lemma_display`
- `entry_raw` e `ref_raw` entram como texto de apoio
- a ordenação A-Z deve usar `lemma_sort`

## 11. Campos que resolvem a ambiguidade material

O helper já mostrou que precisamos separar papéis materiais.

Portanto:

- `section_start_file` = arquivo onde a seção efetivamente começa
- `editorial_anchor_file` = arquivo onde o número editorial citado bate melhor
- `target_file_best` = melhor alvo único para navegação, quando existir

Esses campos não são equivalentes.

Eles existem porque casos como `PG036` e `PL198` mostram que:

- início material da seção
- âncora editorial
- melhor ponto de entrada para inspeção

podem divergir.

Procedimento recomendado:

1. o helper propõe candidatos e evidências
2. o agente decide se a disputa é apenas material ou se muda a estrutura editorial da entrada
3. se for apenas material, preencher uma única `entry` e preservar a disputa em `raw_json`
4. se mudar a estrutura editorial, emitir duas ou mais `entries` concorrentes, cada uma com seus `refs`
5. só preencher `target_file_best` como alvo único quando isso realmente ajudar a navegação; caso contrário, ele pode ficar nulo

## 12. Decisão final sobre o que herdar do schema antigo

Herdar:

- `volumes`
- `sections`
- `confidence`
- `raw_json`
- `runs`

Substituir:

- `index_entries` por `alphabetical_nodes`, `alphabetical_entries`, `alphabetical_refs`

Adicionar:

- `alphabetical_scripture_refs`

Não promover a peça central:

- `works`

`works` pode aparecer como relação opcional via `work_key`, mas o centro do schema novo deve ser:

`volume -> section -> node/entry -> refs`

## 13. Resposta curta

O formato que melhor encaixa no material coletado previamente não é uma variação pequena de `index_entries`.

É um schema novo, mas deliberadamente compatível com a filosofia do extrator atual:

- `alphabetical_volumes`
- `alphabetical_sections`
- `alphabetical_nodes`
- `alphabetical_entries`
- `alphabetical_refs`
- `alphabetical_scripture_refs`
- `alphabetical_runs`

Esse desenho cobre:

- `PG003`
- `PG011`
- `PG036`
- `PL143`
- `PL198`
- `PO025`

sem obrigar o agente a inventar estruturas on-the-fly.
