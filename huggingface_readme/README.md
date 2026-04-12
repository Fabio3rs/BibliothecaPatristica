---
language:
- la
- grc
- fr
- syc
- hy
- cop
- ar
- en
license: cc-by-4.0
tags:
- patristics
- ocr
- historical-documents
- latin
- multilingual
- document-understanding
- xml
- layout-analysis
pretty_name: Bibliotheca Patristica
---

# Bibliotheca Patristica

Structured OCR corpus of the **Patrologia Orientalis** (PO), with Patrologia Graeca (PG) and Patrologia Latina (PL) forthcoming.

The Patrologia Orientalis contains patristic texts in Oriental languages — Syriac, Armenian, Coptic, Arabic, Ethiopic — alongside Greek and Latin, making it one of the most linguistically diverse collections of early Christian literature.

**Source code (GitHub):** https://github.com/Fabio3rs/BibliothecaPatristica  
**Dataset owner on Hugging Face:** FabioRS

## Dataset Summary

| | |
|---|---|
| **Version** | 1.0 (Patrologia Orientalis) |
| **Volumes** | 20 (PO002, PO003, PO006–PO019, PO021–PO023, PO025) |
| **Pages** | 16,096 |
| **Scripts** | Latin, Greek, Syriac, Armenian, Coptic, Arabic, French, English (less frequent), mixed |
| **License** | CC BY 4.0 |

## OCR Pipeline

Transcription uses a **multistage pipeline**:

1. **Tesseract** — baseline OCR with Latin/Greek language packs, adaptive binarization (CLAHE + adaptive threshold), run in parallel per page
2. **LLM transcription** — vision model (qwen3.5:397b-cloud through ollama) produces structured XML with typed blocks, bounding boxes, and script identification
3. **Deterministic similarity gate** — token overlap between Tesseract and LLM outputs is computed; pages with sufficient agreement are marked `deterministic_pass` without further evaluation
4. **LLM judge** — pages that fail the gate are evaluated by a second LLM pass comparing the image against the transcription, producing `fidelidade` (fidelity) and `usabilidade` (usability) scores

Quality assurance uses a two-tier approach: a deterministic similarity gate comparing Tesseract and LLM token overlap (covering ~98.9% of pages), with LLM judge evaluation reserved for pages where the gate detects significant disagreement. This avoids the prohibitive cost of vision-based evaluation at scale while concentrating review where it matters most.

## Quality Distribution

| Criterion | Value | Pages |
|---|---|---|
| `decision` | `deterministic_pass` | 15,934 |
| `decision` | `llm_judged` | 162 |
| `fidelidade` | `alta` | 104 |
| `fidelidade` | `media` | 58 |

> Pages with `decision = deterministic_pass` have `fidelidade = null` (no LLM judge was invoked).
> Pages with `decision = llm_judged` always have a `fidelidade` value (`alta` or `media`); pages
> judged `baixa` or `descartar` are excluded from the published dataset.

## Schema

| Column | Type | Description |
|---|---|---|
| `volume` | string | Volume ID (e.g. `PO009`) |
| `pagina` | int32 | Page number within volume |
| `xml_raw` | string | Structured XML transcription (see below) |
| `original_path` | string | Path of the per-page TXT inside this repo (`teste/POxxx/text/*.txt`) |
| `fidelidade` | string or null | `alta` / `media` / `null` (null = deterministic pass, no judge invoked) |
| `usabilidade` | string or null | `alta` / `media` / `null` (null = deterministic pass, no judge invoked) |
| `idiomas` | list[string] | Scripts detected on the page (populated only for `llm_judged` pages) |
| `decision` | string | `deterministic_pass` / `llm_judged` |
| `status` | string | `deterministic_pass` / `parse_ok` |

## XML Structure

Each page is transcribed as structured XML with typed blocks, script identification, and bounding boxes (scale 0–1000):

```xml
<pagina estado="com_texto">
  <bloco tipo="cabecalho" script="latino" bbox="60,20,940,90">
    1007 ORIGENIS 1008
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="60,90,490,930">
    tendimus errasse Celsum, cum dixit perspicuum
    esse, qui innatae proclivitati ad peccandum ...
  </bloco>
  <bloco tipo="texto_principal" script="greco" bbox="560,90,940,930">
    Α γορῶνων τῶν Μουσών. Οἱ μὲν οὖν καθ' ἡμᾶς ...
  </bloco>
  <bloco tipo="aparato_critico" script="latino" bbox="60,840,940,920">
    (71) Vel Babylonium intellige ...
  </bloco>
  <bloco tipo="rodape" script="latino" bbox="60,930,940,990">
    Digitized by Google
  </bloco>
  <notas>Two-column layout: left in Latin, right in Greek.</notas>
</pagina>
```

## A Note on Portuguese Metadata

This dataset was produced by a Brazilian developer using a pipeline
written in Portuguese. As a result, some metadata fields and XML tags
use Portuguese terms. The following glossary maps them to English:

### Quality fields (`fidelidade` / `usabilidade`)

| Portuguese | English | Present in dataset |
|---|---|---|
| `alta` | high | ✅ yes |
| `media` | medium | ✅ yes |
| `baixa` | low | ❌ excluded before publish |
| `descartar` | discard | ❌ excluded before publish |
| `null` | no judge invoked (deterministic pass) | ✅ yes |

### XML block types (`tipo=`)

| Portuguese | English |
|---|---|
| `cabecalho` | page header |
| `texto_principal` | main text body |
| `aparato_critico` | critical apparatus |
| `rodape` | footer |
| `nota` | footnote |
| `nota_marginal` | marginal note |
| `outro` | other |

### XML page states (`estado=`)

| Portuguese | English |
|---|---|
| `com_texto` | contains text |
| `vazio` | blank / empty page |

### XML scripts (`script=`)

| Portuguese | English |
|---|---|
| `latino` | Latin |
| `grego` | Greek |
| `siriaco` | Syriac |
| `armenio` | Armenian |
| `copta` | Coptic |
| `arabe` | Arabic |
| `ethiopico` | Ethiopic |
| `cirilico` | Cyrillic |
| `georgiano` | Georgian |
| `hebraico` | Hebrew |
| `ingles` | English |
| `frances` | French |
| `misto` | mixed scripts |
| `desconhecido` | unknown |

**Allowed block types:** `cabecalho`, `texto_principal`, `aparato_critico`, `rodape`, `nota`, `nota_marginal`, `outro`

**Allowed scripts:** `latino`, `grego`, `copta`, `siriaco`, `cirilico`, `ethiopico`, `armenio`, `arabe`, `hebraico`, `georgiano`, `ingles`, `frances`, `misto`, `desconhecido`

## Usage

```python
from datasets import load_dataset

ds = load_dataset("FabioRS/BibliothecaPatristica", data_files="data/PO.parquet")

# filter by quality (llm-judged pages only)
high_quality = ds["train"].filter(
    lambda x: x["fidelidade"] == "alta"
)

# include deterministic-pass pages (fidelidade is null for those)
high_quality_with_det = ds["train"].filter(
    lambda x: x["fidelidade"] in ("alta",) or x["decision"] == "deterministic_pass"
)

# filter by script (only populated for llm_judged pages)
syriac_pages = ds["train"].filter(
    lambda x: "siriaco" in (x["idiomas"] or [])
)
```

### Reproducing OCR on a small slice

To re-run OCR on specific pages directly from the original PDFs (e.g., pages 507–508 of volume PO011) with the full verify/judge flow:

```bash
python main2.py \
  'PO011.pdf' \
  --algorithm openai \
  --out teste \
  --lang fra+lat+grc+ell+syr+hye-calfa-n+ara+heb+eth \
  --verify-judge-llm \
  --first 507 --last 508 \
  --judge-force
```

"--lang" is for specifying the languages to be used in the Tesseract OCR process.

Other volumes can be processed in batch via `patristicaoriental.sh`, which calls `main2.py` over all `PO*.pdf` with deterministic gating + LLM judge.

## Glossary (Portuguese field & tag names)

- `fidelidade`: fidelity of the transcription — `alta` (high), `media` (medium), `null` (deterministic pass, no judge invoked). Values `baixa` and `descartar` exist in the pipeline but are excluded from the published dataset.
- `usabilidade`: usability for downstream tasks — same labels as `fidelidade`.
- `idiomas`: list of scripts detected on the page (populated only for `llm_judged` pages).
- Block types: `cabecalho` (header), `texto_principal` (main text), `aparato_critico` (critical apparatus), `rodape` (footer), `nota` (note), `nota_marginal` (marginal note), `outro` (other).
- Script labels: `latino` (Latin), `grego` (Greek), `copta` (Coptic), `siriaco` (Syriac), `cirilico` (Cyrillic), `ethiopico` (Ethiopic), `armenio` (Armenian), `arabe` (Arabic), `hebraico` (Hebrew), `georgiano` (Georgian), `ingles` (English), `frances` (French), `misto` (mixed), `desconhecido` (unknown).
- `decision`: `deterministic_pass` (overlap gate OK) or `llm_judged` (sent to judge).
- `status`: `deterministic_pass` or `parse_ok`.

## Limitations & Quality Notes

> **This corpus has not been reviewed by human experts.**

- Pages marked `deterministic_pass` had strong Tesseract/LLM agreement but were not manually verified
- Pages marked `fidelidade: alta` or `media` passed automated LLM judge evaluation only
- Oriental scripts (Syriac, Armenian, Coptic, Arabic) were evaluated for structural presence and approximate extent, not character-level accuracy
- Greek diacritics and ligatures may contain errors even on otherwise well-transcribed pages
- Known unrecoverable pages are excluded from the dataset

Recommended use: LLM training, semantic search, metadata extraction, and document understanding research. **Not recommended as a substitute for critical editions without manual verification.**

## How to Contribute

If you find transcription errors or want to contribute human-verified corrections, please open an issue or PR on the [GitHub repository](https://github.com/Fabio3rs/BibliothecaPatristica).

## Versions

| Version | Contents | Status |
|---|---|---|
| v1.0 | Patrologia Orientalis (PO) — 20 volumes | ✅ Released |
| v2.0 | + Patrologia Graeca (PG) | 🔄 In progress |
| v3.0 | + Patrologia Latina (PL) | 📋 Planned |

## Citation

If you use this dataset in your research, please cite:

```bibtex
@dataset{bibliotheca_patristica_2026,
  author    = {FabioRS},
  title     = {Bibliotheca Patristica},
  year      = {2026},
  publisher = {Hugging Face},
  url       = {https://huggingface.co/datasets/FabioRS/BibliothecaPatristica},
  note      = {Multistage OCR corpus of the Patrologia Orientalis.
               Not human-reviewed. CC BY 4.0.}
}
```

## Related Work

- [BibliothecaPatristica site](https://github.com/Fabio3rs/BibliothecaPatristica) — searchable interface over the corpus using PageFind
- [latin-codec](https://github.com/Fabio3rs/latin-codec) — empirical research on Latin as a compression/specification format for LLM contexts
