#!/usr/bin/env bash

# Calls the OCR to the Patristica Oriental volumes (POXXX.pdf)
# using the Tesseract OCR engine.
# The language used is Syriac+Latin+Greek+French (fra+lat+grc+ell+syr).

FILES=./PO*.pdf
for f in $FILES
do
  echo "Processing $f file..."
  # se existir teste/$f (sem o pdf)/*.txt, pula
  # exemplo teste/PO001/*.txt
  if [ -d "teste/${f%.pdf}" ] && [ "$(ls -A teste/${f%.pdf}/*.txt 2>/dev/null)" ]; then
    echo "Skipping $f, already processed."
    continue
  fi

  python ./main2.py --out teste/ --lang fra+lat+grc+ell+syr --concat "$f"
done

