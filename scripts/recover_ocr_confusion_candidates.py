#!/usr/bin/env python3
"""Generate bounded file candidates from observed OCR disagreements."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.ocr_confusion_recovery import main

if __name__ == '__main__':
    main()
