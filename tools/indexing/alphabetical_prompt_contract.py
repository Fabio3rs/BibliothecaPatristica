"""Versioned references shared by alphabetical-index agent prompts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROMPT_CONTRACT_VERSION = 4
INTERPRETATION_CONTRACT_VERSION = 1
GLOSSARY_VERSION = 1
OUTPUT_SCHEMA_VERSION = 2
DISCOVERY_CONTRACT_VERSION = 1
LOCATOR_CONTRACT_VERSION = 4

SKILL_ROOT = PROJECT_ROOT / ".codex" / "skills" / "alphabetical-index-extractor"
SKILL_PATH = SKILL_ROOT / "SKILL.md"
PROMPT_CONTRACT_PATH = SKILL_ROOT / "references" / "prompt-contract.md"
OUTPUT_FORMAT_PATH = SKILL_ROOT / "references" / "output-format.md"

INTERPRETATION_CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "contrato_interpretacao_indices.md"
)
GLOSSARY_DOCUMENT_PATH = (
    PROJECT_ROOT / "docs" / "glossario_notacao_editorial_indices.md"
)
GLOSSARY_DATA_PATH = PROJECT_ROOT / "data" / "editorial_notation_glossary.json"
DISCOVERY_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "alphabetical_discovery_manifest.schema.json"
)
SEMANTIC_FRAGMENT_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "alphabetical_semantic_fragment.schema.json"
)
SPATIAL_FIELD_DICTIONARY_PATH = (
    PROJECT_ROOT / "docs" / "dicionario_campos_indices_alfabeticos.md"
)
EXTRACTOR_CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "contrato_extrator_indices_alfabeticos.md"
)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prompt_reference_bundle() -> dict[str, Any]:
    """Return paths and hashes that make interpretation checkpoints reusable."""

    references = {
        "skill": SKILL_PATH,
        "prompt_contract": PROMPT_CONTRACT_PATH,
        "output_format": OUTPUT_FORMAT_PATH,
        "interpretation_contract": INTERPRETATION_CONTRACT_PATH,
        "glossary_document": GLOSSARY_DOCUMENT_PATH,
        "glossary_data": GLOSSARY_DATA_PATH,
        "spatial_field_dictionary": SPATIAL_FIELD_DICTIONARY_PATH,
        "extractor_contract": EXTRACTOR_CONTRACT_PATH,
        "discovery_schema": DISCOVERY_SCHEMA_PATH,
        "semantic_fragment_schema": SEMANTIC_FRAGMENT_SCHEMA_PATH,
    }
    return {
        "prompt_contract_version": PROMPT_CONTRACT_VERSION,
        "interpretation_contract_version": INTERPRETATION_CONTRACT_VERSION,
        "glossary_version": GLOSSARY_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "references": {
            name: {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
            }
            for name, path in references.items()
        },
    }


def prompt_reference_lines() -> str:
    """Compact path-only reference block for runtime prompts."""

    bundle = prompt_reference_bundle()
    references = bundle["references"]
    return "\n".join(
        [
            (
                "- versions: "
                f"prompt={PROMPT_CONTRACT_VERSION}; "
                f"interpretation={INTERPRETATION_CONTRACT_VERSION}; "
                f"glossary={GLOSSARY_VERSION}; output={OUTPUT_SCHEMA_VERSION}"
            ),
            f"- interpretation contract: {references['interpretation_contract']['path']}",
            f"- skill contract: {references['skill']['path']}",
            f"- phase contract: {references['prompt_contract']['path']}",
            f"- output format: {references['output_format']['path']}",
            f"- notation glossary: {references['glossary_document']['path']}",
            f"- machine glossary: {references['glossary_data']['path']}",
            f"- spatial field dictionary: {references['spatial_field_dictionary']['path']}",
            f"- canonical extractor contract: {references['extractor_contract']['path']}",
            f"- discovery schema: {references['discovery_schema']['path']}",
            (
                "- semantic fragment schema: "
                f"{references['semantic_fragment_schema']['path']}"
            ),
        ]
    )
