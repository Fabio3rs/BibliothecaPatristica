#!/usr/bin/env python3
"""Connect catalog entries to source-index titles reconstructed from OCR regions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.regional_section_provenance import main

if __name__ == "__main__":
    main()
