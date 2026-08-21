# Patristic Digital Project (EN)

Digitizing, enriching, and publishing the *Patrologia Graeca*, *Patrologia Latina*, and *Patrologia Orientalis* collections. The project turns their pages into searchable OCR text, summaries, keywords, and indexes for scholarly, theological, and devotional use.

## Explore the collection

**[Open BibliothecaPatristica](https://fabio3rs.github.io/BibliothecaPatristica/)**

The published static website provides:

- search by author, work, topic, and Latin or Greek terms;
- navigation across the PG, PL, and PO collections, volumes, and indexes;
- Portuguese summaries and keywords alongside the OCR text;
- a Portuguese, English, and Italian interface.

Original PDFs and images are **not** versioned; only OCR text and its derived data are kept in this repository.

## Where to find the OCR texts

The actual digitized text is versioned under [`teste/`](teste/). Each collection and volume stores its page files in directories such as `teste/PG001/text/`, `teste/PL001/text/`, and `teste/PO002/text/`. The website is the search and navigation layer built on top of this corpus.

## Jacques-Paul Migne and the collections

The abbreviations **PL** and **PG** refer to the *Patrologia Latina* and *Patrologia Graeca*, assembled and published in the nineteenth century by the French priest, printer, and editor [Jacques-Paul Migne (1800–1875)](https://catalogue.bnf.fr/ark:/12148/cb119160622). His publishing project circulated writings by the Church Fathers and other ancient and medieval Christian authors on an unprecedented scale: the PL appeared between 1844 and 1855, with later index volumes, while the PG comprises 161 volumes published between 1857 and 1866.

Migne did not author these texts or produce critical editions in the modern sense. He compiled and reprinted editions available in his time. Although the collections have textual limitations and should be checked against newer critical editions when available, they remain fundamental historical references and, in some cases, preserve texts for which no other complete edition is readily accessible.

The **Patrologia Orientalis (PO)** was not part of Migne's work. It is a later, independent collection founded in Paris by René Graffin and François Nau to publish texts from Eastern Christian traditions, often in their original languages with translations. It historically complements the Greek and Latin patrologies.

Sources: [Bibliothèque Interuniversitaire de la Sorbonne — PL](https://www.bis-sorbonne.fr/patrologia-latina), [PG](https://www.bis-sorbonne.fr/patrologia-graeca), and [Persée — René Graffin](https://www.persee.fr/authority/175035).

> Provenance note: prompts, summaries, and keywords are PT-BR only (no English release yet); this README is just a reference translation. OCR texts are already versioned under `teste/`, and cleaning/normalization of those OCRs remains a work in progress.

## Scope and data volume
- OCR corpus versioned at `teste/P*/text/*`: ~289,852 page files (~1B raw tokens).
- PDFs/images stay out of the repo because of size and copyright.

## Key scripts
- `main2.py` — page-by-page multimodal OCR using multilingual Tesseract, the custom [`migne-tesseract`](https://github.com/Fabio3rs/migne-tesseract) model, VLM/LLM stages, OpenCV, and OMP-limited parallelism.
- `resumo_serial.py` — generates per-page summaries/keywords from `.txt` (default Ollama; OpenAI optional).
- `download/` — see `download/README.md` for fetching PDFs (`download.py`) and checking missing ones (`checkfaltantes.py`).
- `web/` — Astro static website with Pagefind search and multilingual navigation.
- `requirements.txt` — minimal deps: numpy, opencv-python, Pillow, pytesseract, pdf2image.

## Quick start
- OCR: `python main2.py <file.pdf> --lang lat` (adjust language; DPI/threshold tuned internally).
- Summaries: `python resumo_serial.py --volume-dir teste/PL001` (Ollama). OpenAI: `--provider openai --model gpt-5-mini`.
- Download: follow `download/README.md`.
- Website: `cd web && npm install && npm run build`; for local development, run `npm run dev`.

## Two-pass VLM with three-way OCR reconciliation

The multimodal flow is not a simple choice between Tesseract and an LLM. First,
the VLM (*Vision-Language Model*, or vision-capable LLM) transcribes the facsimile
without seeing auxiliary OCR. Logically in parallel, Tesseract produces an
independent textual reading of the same image. XML, token overlap, coverage,
noise, and visual checks compare both outputs. If the page fails validation, the
VLM must receive the facsimile again, now alongside the Tesseract text and its
previous XML, and produces a corrective transcription. There are therefore two
visual passes; the Tesseract reading is the independent witness between them.
The image is never replaced by the text drafts, including in the LLM judge.
This is not *two-shot* or *three-shot prompting*: those terms count demonstrations
in a prompt, whereas this workflow reconciles three pieces of evidence from the
same page — facsimile, Tesseract text, and previous VLM XML — during its second
VLM pass.

This design and its thresholds were obtained empirically through exploratory
inspection of pages informally selected from different parts of the corpus,
without a formal sampling design, and visual comparison of generated text with
each facsimile. No mathematical BCER evaluation or character-aligned ground-truth
benchmark was performed; the thresholds are operational heuristics rather than
statistical quality estimates.

The second pass is selective and runs through `--verify-fix`. `--algorithm`
selects the VLM provider (`ollama`, the default, or `openai`); Tesseract is an
auxiliary stage rather than a final backend. The LLM judge is a separate layer:
it rates fidelity/usability and routes `baixa` (low) or `descartar` (discard)
assessments to reprocessing. See the diagram, commands, and exceptions in the
[full English technical explanation](docs/PIPELINE.md#three-way-ocr-english).

## Related documentation
- Two-pass VLM with three-way OCR reconciliation: `docs/PIPELINE.md#three-way-ocr-english`
- OCR corpus and structured text format: `docs/CORPUS_OCR_FORMAT.md`
- Semantic unification paper (English): `docs/LLM_unification_paper_en.md`
- Versão em Português / Portuguese version: `docs/LLM_unification_paper.md`

## Prereqs
- Python 3.10+, Tesseract (lat/grc), Poppler (`pdftocairo`).
- `pip install -r requirements.txt`.
- OpenAI: `OPENAI_API_KEY` set. Ollama: local service (e.g., `qwen3:30b`).
- Linguistic data (keyword checker):
  - NLTK: `python - <<'PY'\nimport nltk\nfor pkg in ['punkt','stopwords','floresta','words','omw-1.4']:\n    nltk.download(pkg)\nPY`
  - CLTK: `cltk download lat grc syr hye ara hbo gez`

## Smoke test
- OCR: run `python main2.py` on a small PDF and inspect generated `.txt`.
- Summaries: `python resumo_serial.py --volume-dir <dir>` and confirm writes to `data/patristica_resumos.db`.
- Download: follow `download/README.md` and check `PL/PG/PO###.pdf` names.

## Published data
- Full OCR is available in `teste/` (pages under `P*/text/*`).
- `ocr_out/` and `pages/` hold intermediates; `data/` keeps enriched indexes.
- PT-BR summaries/keywords live in the repo artifacts (no English variant yet); OCR cleanup is still in progress.

## Status / Roadmap
- OCR stable; summaries/keywords running.
- Astro + Pagefind website published on GitHub Pages.
- Ongoing: OCR cleaning/normalization and gap fixes.
- Broader editorial and alphabetical indexes and search refinements are ongoing.

## AI, coverage, and human review
- **Incomplete coverage**: not all pages/volumes are processed yet.
- **OCR errors**: noise, diacritics, and multi-column layouts can degrade text and summaries/keywords.
- **False negatives**: absence in search ≠ absence in corpus — check the original.
- **False positives**: suggested keywords may misrepresent the text — validate before use.
- **Automatic interpretation**: models can add anachronisms or simplifications; metadata logs model/provider/date.

## Note on *Patrologia Orientalis*
These volumes mix multiple scripts (French, Syriac, Greek, etc.) with dense scholarly apparatus, making traditional OCR brittle. This project uses a multimodal model for initial transcription/layout; Tesseract is reserved for targeted regions. `main2.py` enforces XML integrity checks, visual classification (blank/cover/text), token- and block-level overlap with Tesseract, and selective reruns to curb hallucinations and omissions. Always verify against the source, especially for scholarly use.

## Biblical citations
Follows Portuguese convention: comma between chapter and verse (e.g., `John 3,16`), hyphen for ranges (`Matthew 5,3-12`), semicolon for multiples (`John 3,16; 4,1-3; 5,24`).
