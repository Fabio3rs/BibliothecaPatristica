#!/usr/bin/env python3
"""Extract reviewed compact onomastic lists into isolated SQLite staging."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.indexing.onomastic_extraction_recovery import main
if __name__ == '__main__':
    main()
