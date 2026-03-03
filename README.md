# Projeto Patrística Digital

Digitalização e processamento das coleções *Patrologia Graeca*, *Latina* e *Orientalis* para gerar texto OCR, resumos e keywords em português. Os PDFs/imagens originais **não** são versionados; apenas os `.txt` de OCR são mantidos. Próximos passos incluem busca FTS5 e RAG/agent.

## Escopo e volume de dados
- Corpus OCR versionado em `teste/P*/text/*`: ~289.852 arquivos de página.
- Volume total estimado: perto de **1 bilhão de tokens** de texto bruto.

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

## Aviso sobre IA, cobertura e revisão humana
Este projeto usa IA (LLMs) para acelerar a catalogação do corpus patrístico e facilitar a localização rápida de temas. Isso traz ganhos de velocidade e alcance, mas também limitações:

- **Cobertura incompleta**: nem todo o corpus está processado; páginas ou volumes podem faltar.
- **Erros de OCR**: páginas com ruído, diacríticos ou colunas podem gerar texto defeituoso e influenciar resumos/keywords.
- **Falsos negativos**: se a busca não retorna um tema, ele pode existir — confira o original.
- **Falsos positivos**: uma keyword sugerida pode não refletir fielmente o texto; não use sem validação.
- **Interpretação automática**: modelos podem introduzir anacronismos, vieses ou simplificações teológicas.

Portanto, todo resultado deve ser verificado no texto original e, para uso acadêmico, revisado por especialistas antes de citação ou análise. Registramos data, modelo e provedor de IA nos metadados para transparência.
