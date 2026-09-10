# Volume Index Taxonomy

Sources:

- `../../../../docs/taxonomia_indices.md` for `PG` and `PL`
- `../../../../docs/taxonomia_indices_po.md` for `PO`

Choose the branch that matches `collection`.

## 0. Numbering Glossary

- `OCR file suffix` or `physical OCR file`: the numeric suffix in `...-NNN.txt`. It identifies the local OCR text file only.
- `Printed/internal/editorial page`: the page, folio, or column number printed in the source volume and cited by its indexes.
- `Scan/sheet reality`: the visual capture behind the OCR. One scan may contain two printed pages, facing pages, split columns, or other non-1:1 layouts.

Do not collapse these into one numbering system.

## 1. `PG` and `PL`

### 1.1 Beginning of the volume

Use this zone to find the editorial inventory of the whole tome.

Typical headings:

- `ELENCHUS`
- `AUCTORUM ET OPERUM ...`
- `ORDO RERUM` when it functions as a front-matter inventory

Typical signals:

- authors and works are grouped into blocks
- references point to columns or pages in the printed book
- the OCR file suffix is not the editorial page number

### 1.2 Beginning of a work

Use this zone to find the internal structure of a single work.

Typical headings:

- `INDEX CAPITUM`
- `INDEX CAPITUM LIBRI PRIMI`
- `INDEX CAPITUM SCRIPTURÆ SACRÆ`
- `PROLEGOMENA`
- `CAPUT`, `CAP.`, `LIBER`, `SECTIO`

Typical signals:

- chapter numbers are often Roman numerals
- each entry is a semantic unit, even if wrapped across OCR lines
- pagination usually points into the body text of the work

### 1.3 End of the volume

Use this zone to find closing indexes and editorial closure.

Typical headings:

- `ORDO RERUM`
- `INDEX ANALYTICUS`
- `INDEX RERUM ET VERBORUM`
- `INDEX GRÆCITATIS`

Typical signals:

- analytical or alphabetical ordering
- final editorial closure, sometimes with explicit `FINIS TOMI`
- may continue the same work list that started earlier in the volume

## 2. `PO`

### 2.1 Volume-level structures

Use these classes to identify the opening editorial structures of a tome.

Typical section classes:

- `volume_title`
- `volume_table`
- `fascicle_inventory`

Typical headings or signals:

- `TOMUS ...`
- `TABLE DES MATIÈRES`
- repeated `FASC. I`, `FASC. II`, etc.
- list of works, editors, and page ranges for the current tome

### 2.2 Work-level opening structures

Use these classes to identify the opening editorial structures of a fascicle or work.

Typical section classes:

- `work_front_matter`
- `work_internal_table`

Typical headings:

- `AVERTISSEMENT`
- `INTRODUCTION`
- `PRÉFACE`
- `PROLOGUE`
- `TABLE DES MATIÈRES`
- `TABLE DES MATIÈRES CONTENUES DANS CE LIVRE`

Typical signals:

- editorial discussion of manuscripts, versions, and translation
- chapter, part, or section table for a single work
- may appear in French, English, Latin, or another editorial language

### 2.3 Work-level closing indexes

Use these classes to identify the specialized closing indexes attached to one work or fascicle.

Typical section classes:

- `work_index_nominal`
- `work_index_scripture`
- `work_index_alphabetical`
- `work_index_analytic`

Typical headings:

- `TABLE DES NOMS PROPRES`
- `TABLE DES NOMS PROPRES SYRIAQUES`
- `INDEX DES NOMS PROPRES`
- `INDEX DES CITATIONS DES ÉCRITURES`
- `TABLE ALPHABÉTIQUE`
- `TABLE ALPHABÉTIQUE DES MATIÈRES`
- `TABLE ANALYTIQUE DES MATIÈRES`

Typical signals:

- alphabetical or thematic ordering
- references to pages, lines, notes, or sections
- entries keyed by names, biblical citations, or topics

### 2.4 Editorial closure and retrospective tables

Use these classes to identify final editorial closure or tables that do not belong to the current tome only.

Typical section classes:

- `editorial_closure`
- `retrospective_table`

Typical headings:

- `ADDENDA`
- `CORRIGENDA`
- `ADDENDA AND CORRIGENDA`
- `TABLE DES MATIÈRES` that explicitly lists several tomes

Typical signals:

- corrections and addenda
- cumulative inventory of several tomes
- repeated mentions of `Tome V`, `Tome VI`, etc. on one page
- `FASC.` entries that do not belong to the current volume

## 3. Numbering rules

- Keep OCR literals intact.
- Do not assume monotonic page numbering.
- Do not assume the OCR file suffix matches the printed/editorial page number.
- Do not derive a physical file match from printed numbering alone when string evidence is available.
- Printed/internal numbers can be wrong in OCR because of CER, worn type, faded ink, bleed-through, cropping, or damaged scans.
- Prefer direct title pages, author names, incipits, explicit work headings, and nearby body text over
  printed numbers when anchoring a physical OCR file.
- Treat exact and Levenshtein/fuzzy phrase matches as additive evidence. An occurrence inside an
  `ORDO`, `ELENCHUS`, catalogue, prefatory inventory, or closing index is not a work target.
- Compare a logical header assembled from all header blocks on the OCR file; page numbers may occur
  in the title block or in separate OCR/XML blocks.
- A similar logical header recurring on at least four physical files, with gaps of up to three
  missing or damaged headers, is strong evidence of a probable body range. It does not by itself
  identify the title-page start or exact ending.
- If a scan contains facing pages or two printed pages on one sheet, preserve the printed references as editorial data and locate the physical file separately.
- Adjacent works may share a physical scan or editorial page. Do not derive one work's end as the
  next work's start minus one, and do not synthesize a physical ending from editorial numbers alone.
- Pure external `Vide ... tom.` / `Voir ... tome ...` remissions have no local physical target.
- Treat `Ibid.` as a reference to the previous entry.
- Treat `col.` as column reference, not file number.
- When in doubt, store the raw text and lower the confidence.
- For `PO`, preserve bracket pagination and parallel page numbering exactly as printed.

## 4. Practical decision rule

Classify the section by the strongest heading evidence, not by the file suffix.

Priority order:

For `PG` and `PL`:

1. volume-front inventory (`ELENCHUS`, `AUCTORUM ET OPERUM`)
2. work-front chapter index (`INDEX CAPITUM`, `PROLEGOMENA`)
3. volume-end analytic index (`ORDO RERUM`, `INDEX ANALYTICUS`, `INDEX RERUM ET VERBORUM`, `INDEX GRÆCITATIS`)

For `PO`:

1. `volume_title`
2. `volume_table`
3. `fascicle_inventory`
4. `work_front_matter`
5. `work_internal_table`
6. `work_index_nominal`
7. `work_index_scripture`
8. `work_index_alphabetical`
9. `work_index_analytic`
10. `editorial_closure`
11. `retrospective_table`

## 5. Physical targets for structural entries

When an owned table enumerates chapters, books, parts, homilies, epistles, questions, or similar
units, the semantic chunk may inspect the whole current volume to find the corresponding body
headings. Follow `chapter-target-localization.md`.

- Use in-memory OCR normalization only for searching; preserve source literals.
- Split numbering restarts into independent book/part runs.
- Require title evidence and monotonic neighboring matches, not ordinal equality alone.
- Exclude the source index and other lists from candidate targets.
- Record structured evidence for resolved targets; leave ambiguous targets null.
