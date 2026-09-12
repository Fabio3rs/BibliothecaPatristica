from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class VolumeInfo:
    volume_id: str
    series: str
    number: int
    suffix: str
    source_dir: str


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_DB = PROJECT_ROOT / "data" / "patristica_catalog.db"
DEFAULT_TEXT_DB = PROJECT_ROOT / "data" / "patristica_text.db"

SERIES_RE = re.compile(r"^(PG|PL|PO)(\d+)(.*)$")
PAGE_NUM_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)
HEADER_RE = re.compile(
    r"^(?:LIBER|CAP(?:UT|\.)|PRAEFATIO|TOMUS|SERIES|INDEX|TABULA|CONTENTS|TABLE|"
    r"VITA|VITAE|HOMILIA|TRACTATUS|EPIST|SERM|PSALM|CANON|ACTA|PATROLOGIA|PATROLOGIE|PATROLOGIAE)\b",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_volume_info(volume_dir: Path) -> Optional[VolumeInfo]:
    match = SERIES_RE.match(volume_dir.name)
    if not match:
        return None
    return VolumeInfo(
        volume_id=volume_dir.name,
        series=match.group(1),
        number=int(match.group(2)),
        suffix=match.group(3) or "",
        source_dir=str(volume_dir.resolve()),
    )


def page_sort_key(path: Path) -> Tuple[int, str]:
    match = PAGE_NUM_RE.search(path.name)
    if match:
        return (int(match.group(1)), path.name)
    fallback = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    if fallback:
        return (int(fallback.group(1)), path.name)
    return (0, path.name)


def page_number(path: Path) -> Optional[int]:
    match = PAGE_NUM_RE.search(path.name)
    if match:
        return int(match.group(1))
    fallback = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    return int(fallback.group(1)) if fallback else None


def is_header(text: str) -> bool:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if not first:
        return False
    if HEADER_RE.search(first):
        return True
    if len(first) <= 80:
        letters = [character for character in first if character.isalpha()]
        if letters:
            upper = sum(1 for character in letters if character.isupper())
            if upper / max(len(letters), 1) > 0.7:
                return True
    return False


def split_long_block(text: str, hard_max_chars: int) -> List[str]:
    if len(text) <= hard_max_chars:
        return [text]

    parts = re.split(r"(?<=[.!?;:])\s+", text)
    output: List[str] = []
    buffer = ""
    for part in parts:
        if not part:
            continue
        if len(buffer) + len(part) + 1 > hard_max_chars:
            if buffer:
                output.append(buffer.strip())
                buffer = part
            else:
                output.append(part[:hard_max_chars].strip())
                remainder = part[hard_max_chars:]
                if remainder:
                    output.extend(split_long_block(remainder, hard_max_chars))
                buffer = ""
        else:
            buffer = f"{buffer} {part}".strip()
    if buffer:
        output.append(buffer.strip())
    return output
