#!/usr/bin/env python3
"""Audit existing index records with mechanical parsers in isolated staging."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.catalog_parser_review import main
if __name__=='__main__':
    main()
