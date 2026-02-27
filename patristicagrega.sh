#!/usr/bin/env bash

# Calls the OCR to the Patristica Grega volumes (PGXXX.pdf)
# using the Tesseract OCR engine.
# The language used is Latin+Greek (lat+grc).

FILES=/homessddata/patristica/PG*.pdf
for f in $FILES
do
  echo "Processing $f file..."
  # se existir teste/$f (sem o pdf)/*.txt, pula
  # exemplo teste/PG001/*.txt
  # if [ -d "teste/${f%.pdf}" ] && [ "$(ls -A teste/${f%.pdf}/*.txt 2>/dev/null)" ]; then
  #   echo "Skipping $f, already processed."
  #   continue
  # fi

  # python ./main2.py --algorithm ollama --llm-model qwen3.5:9b --procs 4 --omp-threads 2 --out teste/ --lang lat+grc "$f"
  python ./main2.py --algorithm openai --llm-model gpt-5-mini --procs 100 --omp-threads 2 --out teste/ --lang lat+grc "$f"
done

