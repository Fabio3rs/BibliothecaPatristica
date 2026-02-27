#!/usr/bin/env bash

# Calls the OCR to the Patristica Latina volumes (PLXXX.pdf)
# using the Tesseract OCR engine.

FILES=/homessddata/patristica/PL*.pdf
for f in $FILES
do
  echo "Processing $f file..."
  # se existir teste/$f (sem o pdf)/*.txt, pula
  # exemplo teste/PL001/*.txt
  # if [ -d "teste/${f%.pdf}" ] && [ "$(ls -A teste/${f%.pdf}/*.txt 2>/dev/null)" ]; then
  #   echo "Skipping $f, already processed."
  #   continue
  # fi

  # python ./main2.py --algorithm gemini --llm-model gemini-2.5-flash-lite --verify-fix --procs 150 --omp-threads 1 --out teste/ --lang lat+grc "$f"
  python ./main2.py --algorithm ollama --procs 9 --omp-threads 1 --out teste/ --lang lat+grc "$f"
  # python ./main2.py --algorithm openai --llm-model gpt-5-mini --verify-fix --procs 50 --omp-threads 1 --out teste/ --lang lat+grc "$f"
done


# lat+grc
