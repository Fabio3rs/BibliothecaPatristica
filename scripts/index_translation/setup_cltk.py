#!/usr/bin/env python3
"""Install the Stanza resources used by the index CLTK worker."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def model_present(root: Path, stanza_code: str) -> bool:
    tokenizer_dir = root / stanza_code / "tokenize"
    return tokenizer_dir.is_dir() and any(tokenizer_dir.glob("*.pt"))


def reuse_stanza_cache(*, source_root: Path, target_root: Path, stanza_code: str) -> bool:
    if source_root == target_root or not model_present(source_root, stanza_code):
        return False
    target_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_root / stanza_code,
        target_root / stanza_code,
        dirs_exist_ok=True,
    )
    resources_json = source_root / "resources.json"
    if resources_json.is_file():
        shutil.copy2(resources_json, target_root / "resources.json")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicitly download Stanza models used by the isolated CLTK worker."
    )
    parser.add_argument(
        "--languages",
        default="lat,grc",
        help="Comma-separated CLTK language codes (lat,grc)",
    )
    args = parser.parse_args()
    wanted = [item.strip().lower() for item in args.languages.split(",") if item.strip()]
    invalid = sorted(set(wanted) - {"lat", "grc"})
    if invalid:
        raise SystemExit(f"Unsupported languages: {', '.join(invalid)}")

    import stanza
    from stanza.resources.common import DEFAULT_MODEL_DIR

    default_root = Path(DEFAULT_MODEL_DIR).expanduser().resolve()
    cltk_root = (Path.home() / "stanza_resources").resolve()

    for language in wanted:
        stanza_code = {"lat": "la", "grc": "grc"}[language]
        if model_present(cltk_root, stanza_code):
            print(
                f"[OK] Stanza resources already installed for {language} "
                f"under {cltk_root}"
            )
            continue
        if reuse_stanza_cache(
            source_root=default_root,
            target_root=cltk_root,
            stanza_code=stanza_code,
        ):
            print(
                f"[OK] reused cached Stanza resources for {language} "
                f"under {cltk_root}"
            )
            continue
        stanza.download(stanza_code, model_dir=str(cltk_root))
        if not model_present(cltk_root, stanza_code):
            raise SystemExit(
                f"Stanza reported success but no {language} tokenizer model was found "
                f"under {cltk_root}"
            )
        print(
            f"[OK] installed Stanza resources for {language} "
            f"under {cltk_root}"
        )


if __name__ == "__main__":
    main()
