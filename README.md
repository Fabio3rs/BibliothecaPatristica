# Projeto Patrística Digital

Digitalização e processamento das coleções *Patrologia Graeca*, *Latina* e *Orientalis* para gerar texto OCR, resumos e keywords em português. Os PDFs/imagens originais **não** são versionados; apenas os `.txt` de OCR são mantidos. Próximos passos incluem busca FTS5 e RAG/agent.

## Scripts essenciais
- `main2.py` — OCR página a página com Tesseract + OpenCV (lat/grc), paralelismo com limites OMP.
- `resumo_serial.py` — Gera resumos/keywords por página a partir dos `.txt` (Ollama padrão ou OpenAI).
- `download/` — Ver `download/README.md` para baixar PDFs (`download.py`) e checar faltantes (`checkfaltantes.py`).
- `requirements.txt` — Dependências mínimas: numpy, opencv-python, Pillow, pytesseract, pdf2image.

## Como rodar (rápido)
- OCR: `python main2.py <arquivo.pdf> --lang lat` (ajuste idioma conforme série; configs internas já setam DPI/threshold).
- Resumo: `python resumo_serial.py --volume-dir teste/PL001` (Ollama). Para OpenAI: `--provider openai --model gpt-5-mini`.
- Download: siga o guia em `download/README.md`.

## Pré-requisitos rápidos
- Python 3.10+, Tesseract (com traineddata lat/grc) e Poppler (`pdftocairo`).
- Pip do `requirements.txt`.
- Para OpenAI: `OPENAI_API_KEY` definido. Para Ollama: serviço local com modelo (ex.: `qwen3:30b`).

## Smoke test
- OCR: rodar `python main2.py` em um PDF pequeno e conferir `.txt` gerado.
- Resumo: `python resumo_serial.py --volume-dir <dir>` e verificar escrita em `data/patristica_resumos.db`.
- Download: executar os passos do README em `download/` e checar nomes `PL/PG/PO###.pdf`.

## Licenças
- Código-fonte sob MIT (`LICENSE`).
- Textos gerados pelo projeto (OCR, resumos, keywords, metadados) dedicados ao domínio público via CC0 1.0 (`LICENSE-CC0`).
- Os PDFs/imagens originais **não** ficam no repositório; direitos podem variar por país/fonte. Baixe por sua conta e risco.
- Dependências externas (Tesseract, OpenCV, Ollama/OpenAI etc.) mantêm suas próprias licenças.

## Estado/Roadmap
OCR estável; resumos/keywords em produção. Próximo foco: indexação FTS5 + agente de RAG bibliotecário.
