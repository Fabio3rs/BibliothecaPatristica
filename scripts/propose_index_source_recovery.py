#!/usr/bin/env python3
"""Recover searchable OCR fragments or gather legacy evidence; no source repairs."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.source_recovery_evidence import main

if __name__=='__main__':
    main()
