from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


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
UUID_PAGE_RE = re.compile(
    r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-\d+\.[^.]+$",
    re.IGNORECASE,
)
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


def page_files(
    directory: Path,
    *,
    suffixes: Iterable[str] | None = None,
) -> dict[int, list[Path]]:
    """Agrupa arquivos pelo número lógico final, ignorando zero-padding.

    Assim, ``VOL-1.txt``, ``VOL-001.txt`` e ``uuid-0001.txt`` pertencem à
    mesma página. Arquivos sem sufixo numérico são ignorados.
    """
    allowed = None
    if suffixes is not None:
        allowed = {
            suffix.casefold() if suffix.startswith(".") else f".{suffix.casefold()}"
            for suffix in suffixes
        }

    grouped: dict[int, list[Path]] = {}
    if not directory.is_dir():
        return grouped
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if allowed is not None and path.suffix.casefold() not in allowed:
            continue
        number = page_number(path)
        if number is None:
            continue
        grouped.setdefault(number, []).append(path)
    for candidates in grouped.values():
        candidates.sort(key=lambda item: item.name.casefold())
    return grouped


def choose_page_file(
    candidates: Iterable[Path],
    *,
    volume_id: str,
    page_num: int,
) -> tuple[Path | None, bool]:
    """Escolhe uma fonte física para uma página lógica.

    O nome canônico ``<volume>-<página com ao menos 3 dígitos>`` vence uma
    variante UUID. Sem um candidato canônico, mais de um arquivo é ambíguo.
    """
    ordered = sorted(set(candidates), key=lambda item: item.name.casefold())
    if not ordered:
        return None, False

    canonical_name = f"{volume_id}-{page_num:03d}{ordered[0].suffix}".casefold()
    canonical = [path for path in ordered if path.name.casefold() == canonical_name]
    if len(canonical) == 1:
        return canonical[0], False
    if len(ordered) == 1:
        return ordered[0], False
    return None, True


def resolve_page_file(
    directory: Path,
    *,
    volume_id: str,
    page_num: int,
    suffixes: Iterable[str] | None = None,
) -> tuple[Path | None, bool]:
    """Resolve uma página por valor numérico, não pela largura do padding."""
    if suffixes is not None:
        for suffix in suffixes:
            normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
            canonical = directory / f"{volume_id}-{page_num:03d}{normalized_suffix}"
            if canonical.is_file():
                return canonical, False
    candidates = page_files(directory, suffixes=suffixes).get(page_num, [])
    return choose_page_file(candidates, volume_id=volume_id, page_num=page_num)


def discover_unique_pages(
    directory: Path,
    *,
    volume_id: str,
    suffixes: Iterable[str] = (".txt",),
) -> List[Path]:
    """Descobre uma única fonte por página ou falha diante de ambiguidade."""
    selected: list[Path] = []
    for number, candidates in sorted(page_files(directory, suffixes=suffixes).items()):
        chosen, ambiguous = choose_page_file(
            candidates,
            volume_id=volume_id,
            page_num=number,
        )
        if ambiguous:
            names = ", ".join(path.name for path in candidates)
            raise RuntimeError(
                f"{volume_id}:{number} tem fontes ambíguas e nenhuma canônica: {names}"
            )
        if chosen is not None:
            selected.append(chosen)
    return selected


def discover_unique_page_map(
    directory: Path,
    *,
    volume_id: str,
    suffixes: Iterable[str] = (".txt",),
) -> dict[int, Path]:
    """Devolve páginas lógicas indexadas pelo inteiro físico do sufixo."""
    return {
        number: path
        for path in discover_unique_pages(
            directory,
            volume_id=volume_id,
            suffixes=suffixes,
        )
        if (number := page_number(path)) is not None
    }


def discover_preferred_pages(
    directory: Path,
    *,
    volume_id: str,
    suffixes: Iterable[str] = (".txt",),
) -> List[Path]:
    """Remove apenas a variante UUID quando há um canônico equivalente.

    Diferentemente de :func:`discover_unique_pages`, preserva famílias físicas
    legítimas com o mesmo sufixo, como ``work-a-001`` e ``work-b-001``.
    """
    selected: list[Path] = []
    for number, candidates in page_files(directory, suffixes=suffixes).items():
        canonical_names = {
            f"{volume_id}-{number:03d}{path.suffix}".casefold()
            for path in candidates
        }
        canonical = [
            path for path in candidates if path.name.casefold() in canonical_names
        ]
        if len(canonical) == 1:
            selected.extend(
                path
                for path in candidates
                if path == canonical[0] or not UUID_PAGE_RE.fullmatch(path.name)
            )
        else:
            selected.extend(candidates)
    return sorted(selected, key=page_sort_key)


def discover_preferred_pages_recursive(
    root: Path,
    *,
    suffixes: Iterable[str] = (".txt",),
) -> List[Path]:
    """Versão recursiva que aplica a política por diretório/volume."""
    suffix_set = {
        suffix.casefold() if suffix.startswith(".") else f".{suffix.casefold()}"
        for suffix in suffixes
    }
    directories = sorted(
        {
            path.parent
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in suffix_set
        }
    )
    return [
        page
        for directory in directories
        for page in discover_preferred_pages(
            directory,
            volume_id=directory.parent.name,
            suffixes=suffix_set,
        )
    ]


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
