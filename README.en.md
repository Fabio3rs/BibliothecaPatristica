# Patristic Digital Project (EN)

Digitizing and processing the *Patrologia Graeca*, *Latina*, and *Orientalis* collections to produce OCR text, per-page summaries, and keywords. Original PDFs/images are **not** versioned; only OCR `.txt` files are kept. Next steps: FTS5 search and a RAG/agent librarian.

> Provenance note: prompts, summaries, and keywords are PT-BR only (no English release yet); this README is just a reference translation. OCR texts are already versioned under `teste/`, and cleaning/normalization of those OCRs remains a work in progress.

## Scope and data volume
- OCR corpus versioned at `teste/P*/text/*`: ~289,852 page files (~1B raw tokens).
- PDFs/images stay out of the repo because of size and copyright.

## Key scripts
- `main2.py` — page-by-page OCR with Tesseract + OpenCV (lat/grc), OMP-limited parallelism.
- `resumo_serial.py` — generates per-page summaries/keywords from `.txt` (default Ollama; OpenAI optional).
- `download/` — see `download/README.md` for fetching PDFs (`download.py`) and checking missing ones (`checkfaltantes.py`).
- `requirements.txt` — minimal deps: numpy, opencv-python, Pillow, pytesseract, pdf2image.

## Quick start
- OCR: `python main2.py <file.pdf> --lang lat` (adjust language; DPI/threshold tuned internally).
- Summaries: `python resumo_serial.py --volume-dir teste/PL001` (Ollama). OpenAI: `--provider openai --model gpt-5-mini`.
- Download: follow `download/README.md`.

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
- Ongoing: OCR cleaning/normalization and gap fixes.
- Next: FTS5 index + librarian-style RAG agent.

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
