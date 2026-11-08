#!/usr/bin/env python3
"""Experimento isolado de recuperação de referências com Qwen/Ollama."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.indexing.semantic_retrieval_experiment import main

if __name__ == "__main__":
    main()
