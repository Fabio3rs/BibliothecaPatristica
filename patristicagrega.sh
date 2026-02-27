#!/usr/bin/env bash

# Calls the OCR to the Patristica Grega volumes (PGXXX.pdf)
# using the Tesseract OCR engine.
# The language used is Latin+Greek (lat+grc).

FILES=./PG*.pdf
for f in $FILES
do
  echo "Processing $f file..."
  # se existir teste/$f (sem o pdf)/*.txt, pula
  # exemplo teste/PG001/*.txt
  if [ -d "teste/${f%.pdf}" ] && [ "$(ls -A teste/${f%.pdf}/*.txt 2>/dev/null)" ]; then
    echo "Skipping $f, already processed."
    continue
  fi

  python ./main2.py --out teste/ --lang lat+grc --concat "$f"
done

