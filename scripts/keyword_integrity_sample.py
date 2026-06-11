"""
Pequeno smoke test manual para o verificador de integridade de keywords.

Execute dentro do .venv para usar os corpora NLTK/CLTK instalados:
    source .venv/bin/activate
    python scripts/keyword_integrity_sample.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keyword_integrity import KeywordIntegrityChecker, IntegrityStatus  # noqa: E402


EXAMPLES = [
    "Mors ei ultra non dominabitur",
    "Christus",
    "infernum",
    "forma servi",
    "fortis",
    "captivam duxit captivitatem",
    "tyrannus",
    "libertas",
    "resurrectio",
    "Jonas",
    "Codex Alexandrinus",
    "Atanásio",
    "títulos dos Salmos",
    "minium/cinnabaris",
    "encáustico púrpura",
    "demônios",
    "δαίμονας",
    "ἡγούμενον",
    "Persuasão",
    "Platão",
    "Fédon",
    "Logos",
    "Trinitas",
    "Baptismo",
    "Espírito Santo",
    "São João o Teólogo",
    "kingdom",
    "église",
    "كتاب",
    "ܡܠܟܘܬܐ",
    "et",  # latin stopword
    "και",  # greek stopword
    "the",  # english stopword
    "de",  # french/portuguese stopword
    "quod",  # latin stopword
    "λόγος",  # greek form
    "λόγου",  # greek genitive
    "Trinitatis",  # latin inflected
    "Sancti Spiritus",  # latin phrase
    "ἁγίου πνεύματος",  # greek phrase
    "Jerusalem",
    "Hierosolyma",
    "Ἱεροσόλυμα",
    "ܐܘܪܫܠܡ",  # Syriac Jerusalem
]


def main() -> None:
    checker = KeywordIntegrityChecker()
    print(f"Missing resources: {checker.missing_resources}")
    for kw in EXAMPLES:
        ev = checker.check_keyword_integrity(kw)
        print(
            f"{kw!r:35} -> {ev.status.value:8} "
            f"| lang={ev.language} | reasons={ev.reasons}"
        )


if __name__ == "__main__":
    main()
