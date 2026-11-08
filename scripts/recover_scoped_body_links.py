#!/usr/bin/env python3
"""Generate a reproducible page-link overlay; never modify the input catalog."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.scoped_body_numbering import main

if __name__ == '__main__':
    main()
