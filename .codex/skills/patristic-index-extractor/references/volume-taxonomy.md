# Volume Index Taxonomy

Source: the repository note in `../../../../docs/taxonomia_indices.md`.

## 1. Begining of the volume

Use this zone to find the editorial inventory of the whole tome.

Typical headings:

- `ELENCHUS`
- `AUCTORUM ET OPERUM ...`
- `ORDO RERUM` when it functions as a front-matter inventory

Typical signals:

- authors and works are grouped into blocks
- references point to columns or pages in the printed book
- the OCR file suffix is not the editorial page number

## 2. Beginning of a work

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

## 3. End of the volume

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

## 4. Numbering rules

- Keep OCR literals intact.
- Do not assume monotonic page numbering.
- Treat `Ibid.` as a reference to the previous entry.
- Treat `col.` as column reference, not file number.
- When in doubt, store the raw text and lower the confidence.

## 5. Practical decision rule

Classify the section by the strongest heading evidence, not by the file suffix.

Priority order:

1. volume-front inventory (`ELENCHUS`, `AUCTORUM ET OPERUM`)
2. work-front chapter index (`INDEX CAPITUM`, `PROLEGOMENA`)
3. volume-end analytic index (`ORDO RERUM`, `INDEX ANALYTICUS`, `INDEX RERUM ET VERBORUM`, `INDEX GRÆCITATIS`)
