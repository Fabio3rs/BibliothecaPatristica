#!/usr/bin/env python3
"""Build and update the isolated, consolidated index search experiment."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.search_catalog_consolidation import main
if __name__=='__main__':
    main()
