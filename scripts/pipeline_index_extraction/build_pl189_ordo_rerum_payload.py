#!/usr/bin/env python3
"""Usage: build the PL189 closing ORDO RERUM payload from the OCR tail.

Run from the repository root:
  python scripts/pipeline_index_extraction/build_pl189_ordo_rerum_payload.py \
    --source-root /homessddata/Projects/pdfocr/teste/PL189/text \
    --helper-request-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL189_helper_request.json \
    --helper-output-json /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL189_helper_output.json \
    --intermediate-dir /homessddata/Projects/pdfocr/data/intermediate_payloads/PL189 \
    --output-file /homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PL189_alphabetical_indices.json

This wrapper reuses the PL188 ORDO RERUM builder and repoints it to PL189.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path("/homessddata/Projects/pdfocr")
BASE_SCRIPT = ROOT / "scripts" / "pipeline_index_extraction" / "build_pl188_ordo_rerum_payload.py"


def load_base_module():
    spec = importlib.util.spec_from_file_location("build_pl188_ordo_rerum_payload", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load base builder from {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    base = load_base_module()
    base.VOLUME_ID = "PL189"
    base.COLLECTION = "PL"
    base.VOLUME_LABEL = "Patrologia Latina 189"
    base.SECTION_KEY = f"{base.VOLUME_ID}:alpha:ordo_rerum:001"
    base.main()


if __name__ == "__main__":
    main()
