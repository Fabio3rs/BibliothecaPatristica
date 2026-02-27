#!/usr/bin/env bash

# Calls the OCR to the Patristica Latina volumes (PLXXX.pdf)
# using the Tesseract OCR engine.

FILES=./PL*.pdf
for f in $FILES
do
  echo "Processing $f file..."
  # se existir teste/$f (sem o pdf)/*.txt, pula
  # exemplo teste/PL001/*.txt
  if [ -d "teste/${f%.pdf}" ] && [ "$(ls -A teste/${f%.pdf}/*.txt 2>/dev/null)" ]; then
    echo "Skipping $f, already processed."
    continue
  fi

  python ./main2.py --out teste/ --lang lat --concat "$f"
done


# lat+grc
