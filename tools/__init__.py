
"""Utilities package for the project's helper scripts under ``tools/``.

This module purposely exposes the most-used python modules from the
``tools`` folder through a small ``__all__`` and a lazy loader (PEP 562).

Benefits:
- makes the package explicit for runtime and static analyzers (Pylance/pyright)
- avoids heavy eager imports at package import time

Usage examples:
    import tools.classify_scan_kind
    from tools import classify_scan_kind  # returns the submodule object lazily
"""

__all__ = [
    "classify_scan_kind",
    "backfill_clean_resumos",
    "bbox_playground",
    "bench_keywords",
    "build_dict_bundle",
    "cluster_keywords",
    "color_band_compare",
    "export_enrichment_shards",
    "export_keywords_dicts",
    "fill_canon_keywords",
    "generate_keyword_embeddings",
    "generate_resumo_embeddings",
    "hdbscan_resumo_embeddings",
    "ingest_keywords_from_resumos",
    "opencv_playground",
    "preview_related_pages",
    "profile_keywords",
    "read_poribp",
    "render_publication_from_shards",
    "reingest_line_images",
    "rerun_bad_evals",
    "resumo_embedding_utils",
    "tesseract_playground",
]

import importlib
import sys


def __getattr__(name: str):
    """Lazily import submodules when accessed as attributes.

    e.g. `tools.classify_scan_kind` will import `tools.classify_scan_kind`
    the first time it's referenced and cache it on the package module.
    """
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        setattr(sys.modules[__name__], name, module)
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(__all__) + list(globals().keys()))
