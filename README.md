# Projeto Patrística Digital

> [!WARNING]
> Pipelines Python utilizam LLMs tanto no OCR (gating/juiz combinando Tesseract + LLM) quanto em resumos/keywords. Resultados podem conter alucinações, omissões ou vieses; valide sempre no texto original antes de uso acadêmico/editorial.

> [!WARNING]
> O corpus é massivo (~1B tokens). Evite rodar jobs em todo `teste/` sem filtros; use `--first/--last` (OCR) e `--pattern`/`--limit` (keywords) para não gerar custo excessivo ou travamentos.

Este repositório mantém o corpus OCR da *Patrologia* (Graeca, Latina, Orientalis) e scripts de processamento. Escolha o idioma:
This repository maintains the OCR corpus of the *Patrology* (Graeca, Latina, Orientalis) and processing scripts. Choose your language:

- Versão completa em português (PT-BR): [README.pt-BR.md](README.pt-BR.md)
- Full version in English: [README.en.md](README.en.md)
- Pipeline técnico detalhado: [docs/PIPELINE.md](docs/PIPELINE.md)

Notas rápidas
- A maior parte dos prompts, resumos e keywords foi escrita originalmente em PT-BR; não há versão em inglês para estes no momento.
- Os textos de OCR já estão versionados em `teste/` (páginas `P*/text/*`).
- A limpeza/melhoria do OCR segue em progresso; PDFs/imagens originais não são versionados.

Quick notes
- Most prompts, summaries, and keywords were originally written in PT-BR; the English version is a reference translation.
- The OCR texts are already versioned in `teste/` (pages `P*/text/*`).
