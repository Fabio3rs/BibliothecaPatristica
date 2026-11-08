#!/usr/bin/env python3
"""Prepare searchable context corrections without changing canonical extraction."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.index_context_repairs import main
if __name__=='__main__':main()
