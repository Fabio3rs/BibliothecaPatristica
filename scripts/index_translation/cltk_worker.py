#!/usr/bin/env python3
"""Isolated JSONL CLTK worker used by the index translation pipeline."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import enum
import json
import sys
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "1"
PIPELINES: dict[str, Any] = {}
PIPELINE_ERRORS: dict[str, str] = {}


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {
            field.name: jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return jsonable(value.to_dict())
        except Exception:
            pass
    return str(value)


def model_directory(language: str) -> Path:
    stanza_code = {"lat": "la", "grc": "grc"}[language]
    # CLTK 1.5's Stanza wrapper uses this fixed user-level directory.
    root = Path.home() / "stanza_resources"
    return root / stanza_code / "tokenize"


def require_model(language: str) -> None:
    directory = model_directory(language)
    if not directory.is_dir() or not any(directory.glob("*.pt")):
        raise FileNotFoundError(
            f"missing Stanza {language} model under {directory}; "
            "run scripts/index_translation/setup_cltk.py"
        )


def build_pipeline(language: str) -> Any:
    if language in PIPELINES:
        return PIPELINES[language]
    if language in PIPELINE_ERRORS:
        raise RuntimeError(PIPELINE_ERRORS[language])
    try:
        require_model(language)
        from cltk import NLP
        from cltk.alphabet.processes import GreekNormalizeProcess, LatinNormalizeProcess
        from cltk.core.data_types import Pipeline
        from cltk.dependency.processes import GreekStanzaProcess, LatinStanzaProcess
        from cltk.languages.utils import get_lang

        processes = (
            [LatinNormalizeProcess, LatinStanzaProcess]
            if language == "lat"
            else [GreekNormalizeProcess, GreekStanzaProcess]
        )
        custom = Pipeline(
            description=f"Patristica {language} morphosyntax",
            processes=processes,
            language=get_lang(language),
        )
        PIPELINES[language] = NLP(
            language=language,
            custom_pipeline=custom,
            suppress_banner=True,
        )
        return PIPELINES[language]
    except Exception as exc:
        PIPELINE_ERRORS[language] = str(exc)
        raise


def package_version() -> str:
    try:
        import cltk

        return str(getattr(cltk, "__version__", "unknown"))
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"


def health() -> dict[str, Any]:
    version = package_version()
    available = not version.startswith("unavailable:")
    return {
        "status": "ok" if available else "error",
        "protocol_version": PROTOCOL_VERSION,
        "analyzer_name": "cltk-stanza",
        "analyzer_version": version,
        "languages": {
            language: {
                "model_present": model_directory(language).is_dir()
                and any(model_directory(language).glob("*.pt")),
                "model_directory": str(model_directory(language)),
            }
            for language in ("lat", "grc")
        },
    }


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    language = str(payload.get("language") or "").strip().lower()
    if language not in {"lat", "grc"}:
        raise ValueError(f"unsupported CLTK language: {language!r}")
    text = str(payload.get("text") or "")
    max_tokens = max(1, int(payload.get("max_tokens") or 512))
    pipeline = build_pipeline(language)
    doc = pipeline.analyze(text)
    words = list(getattr(doc, "words", None) or [])
    truncated = len(words) > max_tokens
    tokens: list[dict[str, Any]] = []
    for word in words[:max_tokens]:
        surface = getattr(word, "string", None)
        if not surface:
            continue
        tokens.append(
            {
                "surface": str(surface),
                "lemma": getattr(word, "lemma", None),
                "upos": jsonable(getattr(word, "upos", None)),
                "xpos": jsonable(getattr(word, "xpos", None)),
                "features": jsonable(getattr(word, "features", None)),
                "dependency_relation": jsonable(
                    getattr(word, "dependency_relation", None)
                ),
                "governor_token_order": getattr(word, "governor", None),
                "char_start": getattr(word, "index_char_start", None),
                "char_end": getattr(word, "index_char_stop", None),
                "sentence_index": getattr(word, "index_sentence", None),
                "is_stop": getattr(word, "stop", None),
                "confidence": jsonable(getattr(word, "confidence", None)),
                "annotation_sources": jsonable(
                    getattr(word, "annotation_sources", None)
                ),
            }
        )
    return {
        "status": "ok",
        "analyzer_name": "cltk-stanza",
        "analyzer_version": package_version(),
        "language": language,
        "is_truncated": truncated,
        "tokens": tokens,
        "raw": {
            "word_count": len(words),
            "stored_token_count": len(tokens),
            "protocol_version": PROTOCOL_VERSION,
        },
    }


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = payload.get("request_id")
    try:
        action = payload.get("action")
        result = health() if action == "health" else analyze(payload)
        return {"request_id": request_id, **result}
    except Exception as exc:
        return {
            "request_id": request_id,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "analyzer_name": "cltk-stanza",
            "analyzer_version": package_version(),
        }


def serve() -> int:
    protocol_stdout = sys.stdout
    for line in sys.stdin:
        with contextlib.redirect_stdout(sys.stderr):
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("request must be a JSON object")
                response = handle(payload)
            except Exception as exc:
                response = {"status": "error", "error": f"invalid request: {exc}"}
        print(
            json.dumps(response, ensure_ascii=False),
            file=protocol_stdout,
            flush=True,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated CLTK JSONL worker")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    if args.healthcheck:
        print(json.dumps(health(), ensure_ascii=False))
        return 0 if health()["status"] == "ok" else 1
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
