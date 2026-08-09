# Bibliotheca Patristica OCR Corpus and Structured Text Format

## Purpose and provenance

Bibliotheca Patristica is an open digital corpus built from public-domain historical patristic material. Most of its pages come from Jacques-Paul Migne's nineteenth-century *Patrologia Graeca* (PG) and *Patrologia Latina* (PL). The *Patrologia Orientalis* (PO) is a later, independent collection and includes additional Eastern Christian languages and scripts.

Migne's collections are historically important compilations, not modern critical editions. The OCR is intended for discovery, reading, and computational research, but quotations and scholarly conclusions should be checked against page images and, where available, newer critical editions.

The repository versions project-generated OCR text and derived metadata, not source PDFs or page images. The underlying historical works and editions are treated as public-domain source material. Conditions attached to third-party scans or reproductions can vary by provider and jurisdiction.

## Corpus organization

OCR pages are stored as individual UTF-8 files:

```text
teste/<VOLUME>/text/<UUID>-<OCR_PAGE>.txt
```

Examples of volume identifiers are `PG001`, `PL129`, and `PO025`. The numeric suffix in a filename is the project's technical OCR-page locator. It must not automatically be interpreted as the printed page, folio, column number, or an edition-level citation.

The `.txt` extension is a transport and repository convention. Current files usually contain a project-specific XML dialect. They are not TEI, ALTO, hOCR, or generic plain-text documents. Older files can still contain unstructured plain text, and consumers must support both forms.

## Multimodal OCR pipeline

The corpus is produced with a multimodal pipeline rather than a single OCR engine:

- multilingual Tesseract models for Latin, Greek, and other scripts;
- the project-specific [`migne-tesseract`](https://github.com/Fabio3rs/migne-tesseract) model, trained for the typography and recurring problems of Migne's editions;
- modern vision-language models for transcription and page-layout interpretation;
- LLM-assisted comparison, classification, and correction;
- heuristic gating, token- and block-level agreement checks, and selective reruns for uncertain pages.

The page image is the ground truth. Tesseract output and model-generated transcriptions are candidates or aids: neither is accepted merely because a model produced it. The pipeline attempts to detect missing blocks, hallucinated text, script errors, blank or cover pages, and damaged multi-column reading order, but it cannot eliminate all errors.

## Structured page contract

A structured page has a `<pagina>` root. Its required `estado` attribute is normally `com_texto` or `vazio`. The optional `tipo` attribute can classify pages such as `capa_ou_guarda`, `texto`, or `gravura`.

Textual regions are represented by `<bloco>` elements. The current transcription contract recognizes these `tipo` values:

- `cabecalho`
- `texto_principal`
- `aparato_critico`
- `rodape`
- `nota`
- `nota_marginal`
- `outro`

The principal `script` vocabulary is:

- `latino`
- `grego`
- `copta`
- `siriaco`
- `cirilico`
- `ethiopico`
- `armenio`
- `arabe`
- `hebraico`
- `georgiano`
- `ingles`
- `frances`
- `misto`
- `desconhecido`

Names in this vocabulary remain in Portuguese because they are machine-facing values already used throughout the corpus and software. Consumers should preserve unfamiliar values rather than reject a page: older pipelines and collection-specific exports can extend the vocabulary.

Each block can carry `bbox="x1,y1,x2,y2"`, using page-relative coordinates on a normalized 0–1000 scale. A final `<notas>` element can record technical transcription comments or relevant corrections; it is not part of the literal page text shown in continuous reading.

A blank page can be represented compactly:

```xml
<pagina estado="vazio" tipo="capa_ou_guarda" />
```

A typical text page follows this shape:

```xml
<pagina estado="com_texto" tipo="texto">
  <bloco tipo="cabecalho" script="latino" bbox="200,20,800,50">
    OPERIS TITULUS
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="20,70,480,980">
    Literal transcription of the left column.
  </bloco>
  <bloco tipo="texto_principal" script="grego" bbox="510,70,970,980">
    Πιστὴ μεταγραφὴ τῆς δεξιᾶς στήλης.
  </bloco>
  <notas>Technical transcription note.</notas>
</pagina>
```

## Fidelity and reading order

The XML stores literal transcription, not translation or silent modernization. Models are instructed not to invent missing content and to use `[ilegivel]` only for individual unreadable words. Headers, main text, critical apparatus, footers, notes, and marginal identifiers should remain distinct blocks.

Block order is also the canonical continuous-reading order. For a conventional two-column page, the transcription records the full left column before the full right column. Full-width headers and footers remain at the point they occupy in the reading sequence. Central gutter identifiers such as `A`, `B`, `C`, and `D` are separate `nota_marginal` blocks rather than being inserted into column text.

Consumers must not reconstruct reading order by sorting coordinates alone. Bounding boxes support layout visualization; the XML element order carries the intended linear sequence.

## Reading representations

The web viewer exposes three interpretations of the same OCR file:

1. **Continuous reading** parses `<bloco>` elements and displays their text in source order. It retains block and script distinctions, applies appropriate language and right-to-left metadata where known, and omits XML syntax and the technical `<notas>` element.
2. **Transcription code** displays the stored `.txt` content, including XML tags and attributes, for inspection and reuse.
3. **Page layout** places blocks according to their normalized bounding boxes. If usable coordinates are unavailable, the viewer falls back to the structured continuous representation.

Continuous reading improves legibility but does not correct the transcription. It remains a presentation of OCR output.

## Parsing and cleaning for derived data

The canonical Python parser treats a file beginning with `<pagina` as structured OCR and otherwise follows the legacy plain-text path. Well-formed XML is parsed normally; malformed structured files use a conservative regular-expression fallback so a single damaged page does not stop corpus processing.

For visible derived text, the parser:

- normalizes Unicode to NFC;
- normalizes line endings and non-breaking space variants;
- removes embedded tags from block content;
- joins alphabetic words split by a hyphen at a line break;
- collapses horizontal whitespace while preserving meaningful line separation;
- preserves legacy plain text as a synthetic `texto_principal` block.

The parser exposes `header_text`, `body_text`, `footer_text`, `notes_text`, and `all_text`. These are analytical projections grouped by semantic role. They are not identical to the viewer's continuous representation, which follows the original block sequence. Top-level `<notas>` contains technical commentary and is ignored as page content; blocks whose `tipo` is `nota` or `nota_marginal` remain available as textual notes.

Downstream summaries, keywords, embeddings, related-page signals, Pagefind records, and editorial indexes are derived from OCR and can inherit its errors. Most generated summaries, keywords, and supporting metadata are in Brazilian Portuguese even when the interface is English or Italian. The OCR itself remains in the source language whenever possible.

## Hugging Face dataset export

The [Bibliotheca Patristica dataset on Hugging Face](https://huggingface.co/datasets/FabioRS/BibliothecaPatristica) is a distribution layer rather than the canonical repository layout. Its current release packages the PO corpus as Parquet and exposes the structured transcription in `xml_raw` together with:

- `volume` and `pagina` as technical page locators;
- `original_path` as the corresponding path under `teste/`;
- `fidelidade` and `usabilidade` for pages evaluated by the LLM judge;
- `idiomas` for detected script labels when recorded by that evaluation;
- `decision` to distinguish deterministic agreement from LLM-judged pages;
- `status` for the resulting processing state.

`deterministic_pass` means that Tesseract and VLM candidates reached the configured agreement threshold; it does not mean human verification. Likewise, `alta` or `media` are automated judge assessments, not scholarly review. Consult the dataset card for release-specific coverage, counts, filtering, and licensing instead of assuming that every GitHub page appears in a given Parquet release.

## Code and examples

- [OCR producer and transcription prompts](https://github.com/Fabio3rs/BibliothecaPatristica/blob/codex/main2.py)
- [Migne-specific Tesseract model](https://github.com/Fabio3rs/migne-tesseract)
- [Web viewer consumer](https://github.com/Fabio3rs/BibliothecaPatristica/blob/codex/web/src/components/pages/ViewerPage.astro)
- [Representative structured OCR page](https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste/PL129/text/258c2257-1b0b-4bfe-ab60-4913f43cd7dc-169.txt)
- [Hugging Face dataset export](https://huggingface.co/datasets/FabioRS/BibliothecaPatristica)

## Licensing and reuse

Project source code is licensed under MIT. Project-generated OCR, summaries, keywords, and page metadata in this repository are released under CC0 1.0 Universal. The Hugging Face dataset card currently declares CC BY 4.0 for that packaged release. Third-party models, language data, and scan providers retain their own terms. See [LICENSE-NOTICE.md](../LICENSE-NOTICE.md) for the repository's concise licensing statement and consult each external distribution's own metadata before reuse.
