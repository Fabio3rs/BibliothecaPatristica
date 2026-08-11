#!/usr/bin/env python3
"""
sample.py — amostragem priorizada por volume + segmentação Tesseract → SQLite

Uso:
    python sample.py --root ./teste --db ocr.db --session "amostra inicial" --per-volume 10
    python sample.py --root ./teste --db ocr.db --append --per-volume 10
    python sample.py --root ./teste --db ocr.db --append --dry-run --volume PG004

Estrutura esperada:
    {root}/P{G|L}{vol}/images/*-{page}.png
    {root}/P{G|L}{vol}/text/*-{page}.txt
"""

import argparse
from datetime import datetime, timezone
import io
import json
import logging
import os
import random
import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path
import multiprocessing as mp

from PIL import Image
import cv2
import numpy as np
import pytesseract

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SUPPORTED_PREFIXES = ("PG", "PL")  # PO fica para fase 2
TESS_LANG = "migne"
MIN_LINES = 10
DEFAULT_PER_VOLUME = 10  # páginas por volume
DEFAULT_PREPROCESS_MODE = "adaptive_soft"
DEFAULT_TESS_LANG = "migne"
BBOX_IOU_THRESHOLD = 0.80
PRIORITY_ORDER = ("mixed", "greek", "other")
PRIORITY_WEIGHTS = {"mixed": 0.50, "greek": 0.40, "other": 0.10}

GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
LATIN_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
SCRIPT_RE = re.compile(
    r"\bscript\s*=\s*['\"](?P<script>misto|mixed|grego|greek)['\"]",
    re.IGNORECASE,
)

LOGGER = logging.getLogger("sample")


class IngestionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_ingestion_report(
    *, db_path: str, root: str, mode: str, per_volume: int
) -> dict:
    return {
        "started_at": utc_now(),
        "finished_at": None,
        "status": "running",
        "mode": mode,
        "database": str(Path(db_path).expanduser().resolve(strict=False)),
        "root": str(Path(root).expanduser().resolve(strict=False)),
        "per_volume": per_volume,
        "session_id": None,
        "summary": {
            "pages_planned": 0,
            "lines_inserted": 0,
            "bbox_conflicts": 0,
        },
        "volumes": {},
        "issues": [],
    }


def add_report_issue(
    report: dict | None,
    severity: str,
    code: str,
    message: str,
    **context,
) -> None:
    issue = {
        "created_at": utc_now(),
        "severity": severity,
        "code": code,
        "message": message,
    }
    issue.update({key: value for key, value in context.items() if value is not None})
    if report is not None:
        report.setdefault("issues", []).append(issue)
    log_method = {
        "error": LOGGER.error,
        "warning": LOGGER.warning,
    }.get(severity, LOGGER.info)
    details = " ".join(f"{key}={value}" for key, value in context.items())
    log_method("%s: %s%s", code, message, f" | {details}" if details else "")


def default_report_path(db_path: str) -> Path:
    database = Path(db_path).expanduser().resolve(strict=False)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return database.parent / "logs" / f"{database.name}.ingest-report-{timestamp}.json"


def write_ingestion_report(report: dict, path: str | Path) -> Path:
    output = Path(path).expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    report["finished_at"] = report.get("finished_at") or utc_now()
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Relatório de ingestão gravado em %s", output)
    return output

# ---------------------------------------------------------------------------
# Schema SQLite
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    description TEXT,
    sample_size INTEGER,
    volumes     TEXT   -- JSON list
);

CREATE TABLE IF NOT EXISTS lines (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER REFERENCES sessions(id),
    page_id         TEXT,      -- "PG009-217"
    volume          TEXT,
    image_path      TEXT,
    line_index      INTEGER,
    bbox            TEXT,      -- JSON {x, y, w, h}
    line_image      BLOB,      -- crop PNG bytes
    tesseract_text  TEXT,
    qwen_text       TEXT,
    detected_lang   TEXT,
    agreement_score REAL,
    reviewed_text   TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS line_versions (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT,
    line_id         INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    text_content    TEXT NOT NULL,
    text_hash       TEXT NOT NULL,
    source_score    REAL,
    is_current      INTEGER NOT NULL DEFAULT 1,
    meta_json       TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_line_versions_dedup
    ON line_versions(run_id, line_id, provider, model, text_hash);

CREATE INDEX IF NOT EXISTS idx_line_versions_line_id
    ON line_versions(line_id, is_current, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_line_versions_provider_model_line_id
    ON line_versions(provider, model, line_id);

CREATE INDEX IF NOT EXISTS idx_lines_session  ON lines(session_id);
CREATE INDEX IF NOT EXISTS idx_lines_status   ON lines(status);
CREATE INDEX IF NOT EXISTS idx_lines_page     ON lines(page_id);
CREATE INDEX IF NOT EXISTS idx_lines_created_at ON lines(created_at);
CREATE INDEX IF NOT EXISTS idx_lines_status_score_id
    ON lines(status, IFNULL(agreement_score, 0), id);
"""

# ---------------------------------------------------------------------------
# Helpers: filesystem
# ---------------------------------------------------------------------------


def find_volumes(root: Path) -> list[Path]:
    """Retorna diretórios PG*/PL* dentro de root."""
    vols = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and any(d.name.upper().startswith(p) for p in SUPPORTED_PREFIXES):
            vols.append(d)
    return vols


def find_page_pairs(vol: Path) -> list[tuple[Path, Path, str]]:
    """
    Retorna lista de (img_path, txt_path, page_id) onde ambos existem.
    page_id = "{volume}-{pagenum}"
    """
    img_dir = vol / "images"
    txt_dir = vol / "text"
    if not img_dir.exists() or not txt_dir.exists():
        return []

    # índice txt por número de página
    txt_by_page: dict[str, Path] = {}
    for f in txt_dir.glob("*.txt"):
        m = re.search(r"-(\d+)\.txt$", f.name)
        if m:
            txt_by_page[m.group(1)] = f

    pairs = []
    for img in sorted(img_dir.glob("*.png")):
        m = re.search(r"-(\d+)\.png$", img.name)
        if m:
            pnum = m.group(1)
            if pnum in txt_by_page:
                page_id = f"{vol.name}-{pnum}"
                pairs.append((img, txt_by_page[pnum], page_id))

    return pairs


def normalize_source_path(path: str | Path) -> str:
    """Retorna uma identidade de caminho estável dentro do checkout atual."""
    return str(Path(path).expanduser().resolve(strict=False))


def classify_ocr_text(text: str) -> str:
    """Classifica o OCR prévio em mixed, greek ou other."""
    normalized = unicodedata.normalize("NFC", text or "")
    scripts = {m.group("script").lower() for m in SCRIPT_RE.finditer(normalized)}
    if scripts & {"misto", "mixed"}:
        return "mixed"
    if scripts & {"grego", "greek"}:
        return "greek"

    content = re.sub(r"<[^>]+>", " ", normalized)
    has_greek = bool(GREEK_RE.search(content))
    has_latin = bool(LATIN_RE.search(content))
    if has_greek and has_latin:
        return "mixed"
    if has_greek:
        return "greek"
    return "other"


def classify_ocr_file(path: Path) -> str:
    return classify_ocr_text(path.read_text(encoding="utf-8", errors="replace"))


def priority_targets(total: int) -> dict[str, int]:
    """Distribui `total` pelas cotas 50/40/10 usando maiores restos."""
    if total < 0:
        raise ValueError("total deve ser >= 0")
    raw = {name: total * PRIORITY_WEIGHTS[name] for name in PRIORITY_ORDER}
    targets = {name: int(raw[name]) for name in PRIORITY_ORDER}
    remaining = total - sum(targets.values())
    ranked = sorted(
        PRIORITY_ORDER,
        key=lambda name: (-(raw[name] - targets[name]), PRIORITY_ORDER.index(name)),
    )
    for name in ranked[:remaining]:
        targets[name] += 1
    return targets


def allocate_priority_counts(
    total: int, available: dict[str, int]
) -> dict[str, int]:
    """Aplica as cotas e redistribui faltas por mixed → greek → other."""
    wanted = priority_targets(total)
    allocated = {
        name: min(wanted[name], max(0, available.get(name, 0)))
        for name in PRIORITY_ORDER
    }
    remaining = min(total, sum(max(0, available.get(name, 0)) for name in PRIORITY_ORDER))
    remaining -= sum(allocated.values())
    for name in PRIORITY_ORDER:
        spare = max(0, available.get(name, 0) - allocated[name])
        take = min(spare, remaining)
        allocated[name] += take
        remaining -= take
        if remaining == 0:
            break
    return allocated


def existing_material_for_volume(
    conn: sqlite3.Connection, volume: str
) -> tuple[set[str], set[str]]:
    rows = conn.execute(
        """
        SELECT DISTINCT page_id, image_path
        FROM lines
        WHERE volume = ?
        """,
        (volume,),
    ).fetchall()
    page_ids: set[str] = set()
    image_paths: set[str] = set()
    for row in rows:
        page_id = row["page_id"] if isinstance(row, sqlite3.Row) else row[0]
        image_path = row["image_path"] if isinstance(row, sqlite3.Row) else row[1]
        if page_id:
            page_ids.add(str(page_id))
        if image_path:
            image_paths.add(normalize_source_path(image_path))
    return page_ids, image_paths


def select_prioritized_pages(
    pairs: list[tuple[Path, Path, str]],
    per_volume: int,
    rng: random.Random,
    existing_page_ids: set[str] | None = None,
    existing_image_paths: set[str] | None = None,
) -> tuple[list[tuple[Path, Path, str]], dict[str, int], int]:
    """Seleciona páginas inéditas segundo as cotas de script."""
    if per_volume < 0:
        raise ValueError("per_volume deve ser >= 0")
    existing_page_ids = existing_page_ids or set()
    existing_image_paths = existing_image_paths or set()
    buckets: dict[str, list[tuple[Path, Path, str]]] = {
        name: [] for name in PRIORITY_ORDER
    }
    existing_count = 0
    for pair in pairs:
        image_path, txt_path, page_id = pair
        if page_id in existing_page_ids or normalize_source_path(image_path) in existing_image_paths:
            existing_count += 1
            continue
        buckets[classify_ocr_file(txt_path)].append(pair)

    limit = min(per_volume, sum(len(items) for items in buckets.values()))
    counts = allocate_priority_counts(
        limit, {name: len(buckets[name]) for name in PRIORITY_ORDER}
    )
    selected: list[tuple[Path, Path, str]] = []
    for name in PRIORITY_ORDER:
        selected.extend(rng.sample(buckets[name], counts[name]))
    return selected, counts, existing_count


# ---------------------------------------------------------------------------
# Helpers: Tesseract
# ---------------------------------------------------------------------------


def deskew_like_sample_color(img: np.ndarray, border_size: int = 50) -> np.ndarray:
    """Aplica o mesmo deskew do modo sample.py, mas preserva as cores originais."""
    arr = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    thresh = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilate = cv2.dilate(thresh, kernel, iterations=2)

    contours, _ = cv2.findContours(dilate, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if 500 < area < 50000:
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            rw, rh = rect[1]
            if rw < rh:
                angle = angle + 90
            if abs(angle) <= 10:
                angles.append(angle)

    median_angle = np.median(angles) if angles else 0.0
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def _preprocess_legacy(img: np.ndarray, border_size: int = 50) -> tuple[np.ndarray, np.ndarray]:
    rotated = deskew_like_sample_color(img, border_size=border_size)
    _, thresh = cv2.threshold(rotated, 100, 255, cv2.THRESH_BINARY)
    return thresh, rotated




def _preprocess_adaptive_soft(
    img: np.ndarray,
    clahe_clip: float = 2.0,
    block_size: int = 31,
    c_value: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    rotated = deskew_like_sample_color(img)
    gray = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY) if len(rotated.shape) == 3 else rotated
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    # binary = cv2.adaptiveThreshold(
    #     gray2, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c_value
    # )
    return gray, gray


def preprocess_image(
    img: np.ndarray,
    mode: str = DEFAULT_PREPROCESS_MODE,
    border_size: int = 50,
    clahe_clip: float = 2.0,
    block_size: int = 31,
    c_value: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    if mode == "sample":
        raise NotImplementedError("Modo 'sample' não implementado.")
        # return _preprocess_legacy(img, border_size=border_size)
    if mode == "adaptive_soft":
        return _preprocess_adaptive_soft(
            img, clahe_clip=clahe_clip, block_size=block_size, c_value=c_value
        )
    raise ValueError(f"Modo de preprocessamento desconhecido: {mode}")


def tesseract_lines(img: np.ndarray) -> list[dict]:
    """
    Roda Tesseract em modo TSV e retorna lista de linhas com bbox + texto.
    Agrupa word-level TSV em linhas pelo campo line_num.
    Descarta linhas com texto vazio.
    """
    tsv = pytesseract.image_to_data(
        img,
        lang=TESS_LANG,
        output_type=pytesseract.Output.DICT,
    )

    # Agrupa por (block_num, par_num, line_num)
    line_map: dict[tuple, dict] = {}
    n = len(tsv["text"])
    for i in range(n):
        text = tsv["text"][i].strip()
        if not text:
            continue
        key = (tsv["block_num"][i], tsv["par_num"][i], tsv["line_num"][i])
        if key not in line_map:
            line_map[key] = {
                "text": [],
                "x": tsv["left"][i],
                "y": tsv["top"][i],
                "x2": tsv["left"][i] + tsv["width"][i],
                "y2": tsv["top"][i] + tsv["height"][i],
            }
        else:
            line_map[key]["x"] = min(line_map[key]["x"], tsv["left"][i])
            line_map[key]["y"] = min(line_map[key]["y"], tsv["top"][i])
            line_map[key]["x2"] = max(
                line_map[key]["x2"], tsv["left"][i] + tsv["width"][i]
            )
            line_map[key]["y2"] = max(
                line_map[key]["y2"], tsv["top"][i] + tsv["height"][i]
            )
        line_map[key]["text"].append(text)

    # Converte para lista ordenada por posição vertical
    result = []
    for key in sorted(line_map, key=lambda k: (line_map[k]["y"], line_map[k]["x"])):
        d = line_map[key]
        result.append(
            {
                "text": unicodedata.normalize("NFC", " ".join(d["text"])),
                "bbox": {
                    "x": d["x"],
                    "y": d["y"],
                    "w": d["x2"] - d["x"],
                    "h": d["y2"] - d["y"],
                },
            }
        )

    return result


def crop_line(img: np.ndarray, bbox: dict) -> bytes:
    """Recorta a linha da imagem e retorna PNG em bytes."""
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    # margem de 2px
    x1 = max(0, x - 2)
    y1 = max(0, y - 2)
    x2 = min(img.shape[1], x + w + 2)
    y2 = min(img.shape[0], y + h + 2)
    crop = img[y1:y2, x1:x2]
    buf = io.BytesIO()
    Image.fromarray(crop).save(buf, format="PNG")
    return buf.getvalue()


def build_tesseract_config(tessdata_dir: str | None = None) -> str:
    parts = ["--psm 6", "--oem 1"]
    if tessdata_dir:
        parts.append(f'--tessdata-dir "{tessdata_dir}"')
    return " ".join(parts)


def tesseract_text_for_image(
    img: Image.Image,
    lang: str = DEFAULT_TESS_LANG,
    tessdata_dir: str | None = None,
) -> str:
    config = build_tesseract_config(tessdata_dir)
    text = pytesseract.image_to_string(img, lang=lang, config=config).strip()
    return unicodedata.normalize("NFC", text)


def resolve_reference_text(row: sqlite3.Row) -> str:
    if isinstance(row, sqlite3.Row):
        qwen_text = (row["qwen_text"] or "").strip() if "qwen_text" in row.keys() else ""
        reviewed_text = (
            (row["reviewed_text"] or "").strip() if "reviewed_text" in row.keys() else ""
        )
    else:
        qwen_text = (row[2] or "").strip() if len(row) > 2 else ""
        reviewed_text = (row[3] or "").strip() if len(row) > 3 else ""
    return qwen_text or reviewed_text


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def open_db(db_path: str, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        resolved = Path(db_path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Banco SQLite não encontrado: {resolved}")
        conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    journal_mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
    if str(journal_mode).lower() != "wal":
        conn.close()
        raise RuntimeError(f"SQLite não entrou em WAL mode: {journal_mode}")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.executescript(DDL)
    _migrate_bbox_columns(conn)
    _migrate_line_versions(conn)
    conn.commit()
    return conn


def _migrate_bbox_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_xinfo(lines)").fetchall()
    }
    expressions = {
        "bbox_x": "CAST(json_extract(bbox, '$.x') AS INTEGER)",
        "bbox_y": "CAST(json_extract(bbox, '$.y') AS INTEGER)",
        "bbox_w": "CAST(json_extract(bbox, '$.w') AS INTEGER)",
        "bbox_h": "CAST(json_extract(bbox, '$.h') AS INTEGER)",
    }
    for column, expression in expressions.items():
        if column not in columns:
            conn.execute(
                f"ALTER TABLE lines ADD COLUMN {column} INTEGER "
                f"GENERATED ALWAYS AS ({expression}) VIRTUAL"
            )

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_lines_page_bbox_lookup
            ON lines(page_id, bbox_x, bbox_y);
        CREATE INDEX IF NOT EXISTS idx_lines_image_bbox_lookup
            ON lines(image_path, bbox_x, bbox_y);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_lines_page_bbox_exact
            ON lines(page_id, bbox_x, bbox_y, bbox_w, bbox_h)
            WHERE page_id IS NOT NULL AND page_id <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS uq_lines_image_bbox_exact
            ON lines(image_path, bbox_x, bbox_y, bbox_w, bbox_h)
            WHERE image_path IS NOT NULL AND image_path <> '';
        """
    )


def _migrate_line_versions(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(line_versions)").fetchall()]
    if not cols:
        return
    if "run_id" not in cols:
        conn.execute("ALTER TABLE line_versions ADD COLUMN run_id TEXT")
    if "source_score" not in cols:
        conn.execute("ALTER TABLE line_versions ADD COLUMN source_score REAL")
    if "is_current" not in cols:
        conn.execute("ALTER TABLE line_versions ADD COLUMN is_current INTEGER DEFAULT 1")
    if "meta_json" not in cols:
        conn.execute("ALTER TABLE line_versions ADD COLUMN meta_json TEXT")
    conn.execute("DROP INDEX IF EXISTS uq_line_versions_dedup")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_line_versions_dedup
        ON line_versions(run_id, line_id, provider, model, text_hash)
        """
    )

    existing = conn.execute("SELECT COUNT(*) FROM line_versions").fetchone()[0]
    if existing:
        return
    rows = conn.execute(
        "SELECT id, qwen_text, agreement_score FROM lines WHERE IFNULL(qwen_text, '') <> ''"
    ).fetchall()
    for row in rows:
        txt = unicodedata.normalize("NFC", row["qwen_text"] or "")
        conn.execute(
            """
            INSERT OR IGNORE INTO line_versions
                (run_id, line_id, provider, model, text_content, text_hash,
                 source_score, is_current, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)
            """,
            (
                "legacy",
                row["id"],
                "legacy",
                "legacy",
                txt,
                hashlib.sha256(txt.encode("utf-8")).hexdigest(),
                row["agreement_score"],
            ),
        )


def session_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    return row[0] > 0


def create_session(
    conn: sqlite3.Connection, description: str, sample_size: int, volumes: list[str]
) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (description, sample_size, volumes) VALUES (?, ?, ?)",
        (unicodedata.normalize("NFC", description), sample_size, json.dumps(volumes)),
    )
    conn.commit()
    return cur.lastrowid


def insert_line(conn: sqlite3.Connection, session_id: int, row: dict) -> None:
    tesseract_text = unicodedata.normalize("NFC", row["tesseract_text"] or "")
    conn.execute(
        """
        INSERT INTO lines
            (session_id, page_id, volume, image_path, line_index,
             bbox, line_image, tesseract_text, detected_lang)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            row["page_id"],
            row["volume"],
            normalize_source_path(row["image_path"]),
            row["line_index"],
            json.dumps(row["bbox"]),
            row["line_image"],
            tesseract_text,
            row["detected_lang"],
        ),
    )


def bbox_iou(first: dict, second: dict) -> float:
    """Calcula intersection-over-union de duas caixas x/y/w/h."""
    ax1, ay1 = float(first["x"]), float(first["y"])
    ax2 = ax1 + max(0.0, float(first["w"]))
    ay2 = ay1 + max(0.0, float(first["h"]))
    bx1, by1 = float(second["x"]), float(second["y"])
    bx2 = bx1 + max(0.0, float(second["w"]))
    by2 = by1 + max(0.0, float(second["h"]))
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    first_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    second_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def find_bbox_conflict(
    conn: sqlite3.Connection,
    page_id: str,
    image_path: str,
    bbox: dict,
    threshold: float = BBOX_IOU_THRESHOLD,
) -> int | None:
    """Retorna o id de uma linha conflitante no mesmo material, se houver."""
    if not 0 <= threshold <= 1:
        raise ValueError("threshold deve estar entre 0 e 1")
    x = int(bbox["x"])
    y = int(bbox["y"])
    w = int(bbox["w"])
    h = int(bbox["h"])
    if w <= 0 or h <= 0:
        raise ValueError("bbox deve ter largura e altura positivas")
    x2 = x + w
    y2 = y + h
    overlap_params = (x2, x, y2, y)
    rows = conn.execute(
        """
        SELECT id, bbox_x, bbox_y, bbox_w, bbox_h
        FROM lines
        WHERE page_id = ?
          AND bbox_x < ? AND bbox_x + bbox_w > ?
          AND bbox_y < ? AND bbox_y + bbox_h > ?
        UNION
        SELECT id, bbox_x, bbox_y, bbox_w, bbox_h
        FROM lines
        WHERE image_path = ?
          AND bbox_x < ? AND bbox_x + bbox_w > ?
          AND bbox_y < ? AND bbox_y + bbox_h > ?
        """,
        (
            page_id,
            *overlap_params,
            normalize_source_path(image_path),
            *overlap_params,
        ),
    ).fetchall()
    for row in rows:
        existing = {
            "x": row["bbox_x"],
            "y": row["bbox_y"],
            "w": row["bbox_w"],
            "h": row["bbox_h"],
        }
        if bbox_iou(bbox, existing) >= threshold:
            return int(row["id"])
    return None


def insert_nonconflicting_page(
    conn: sqlite3.Connection,
    session_id: int,
    result: dict,
    threshold: float = BBOX_IOU_THRESHOLD,
    report: dict | None = None,
) -> tuple[int, int]:
    """Insere uma página atomicamente e ignora caixas similares/exatas."""
    inserted = 0
    conflicts = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in result.get("rows", []):
            material_row = {
                "page_id": result["page_id"],
                "volume": result["volume"],
                "image_path": result["image_path"],
                "line_index": row["line_index"],
                "bbox": row["bbox"],
                "line_image": row["line_image"],
                "tesseract_text": row["tesseract_text"],
                "detected_lang": result["detected_lang"],
            }
            if find_bbox_conflict(
                conn,
                material_row["page_id"],
                material_row["image_path"],
                material_row["bbox"],
                threshold=threshold,
            ) is not None:
                conflicts += 1
                continue
            try:
                insert_line(conn, session_id, material_row)
                inserted += 1
            except sqlite3.IntegrityError as exc:
                conflict_id = find_bbox_conflict(
                    conn,
                    material_row["page_id"],
                    material_row["image_path"],
                    material_row["bbox"],
                    threshold=threshold,
                )
                if conflict_id is not None:
                    conflicts += 1
                    continue
                add_report_issue(
                    report,
                    "error",
                    "unexpected_integrity_error",
                    str(exc),
                    page_id=material_row["page_id"],
                    line_index=material_row["line_index"],
                    sqlite_errorcode=getattr(exc, "sqlite_errorcode", None),
                    sqlite_errorname=getattr(exc, "sqlite_errorname", None),
                )
                raise
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return inserted, conflicts


from functools import lru_cache

# @cache
@lru_cache(maxsize=32)
def load_process_image(image_path: str, preprocess_mode: str) -> tuple[np.ndarray, dict]:
    img_original = cv2.imread(str(Path(image_path)))
    if img_original is None:
        raise FileNotFoundError(f"Não foi possível abrir {image_path}")

    _, gray_image = preprocess_image(img=img_original, mode=preprocess_mode)

    return gray_image

def _rebuild_line_image(row: tuple, preprocess_mode: str) -> bytes:
    image_path = row[1]
    bbox_value = row[2]

    img_proc = load_process_image(image_path, preprocess_mode)
    bbox = json.loads(bbox_value) if isinstance(bbox_value, str) else bbox_value
    x = int(bbox.get("x", 0))
    y = int(bbox.get("y", 0))
    w = int(bbox.get("w", 0))
    h = int(bbox.get("h", 0))
    x1 = max(0, x - 2)
    y1 = max(0, y - 2)
    x2 = min(img_proc.shape[1], x + w + 2)
    y2 = min(img_proc.shape[0], y + h + 2)
    crop = img_proc[y1:y2, x1:x2]
    buf = io.BytesIO()
    Image.fromarray(crop).save(buf, format="PNG")
    return buf.getvalue()


def _refresh_line_image_job(args: tuple[int, str, str, str]) -> tuple[int, bytes]:
    line_id, image_path, bbox, preprocess_mode = args
    row = (line_id, image_path, bbox)
    return line_id, _rebuild_line_image(row, preprocess_mode)


def refresh_line_images(
    conn: sqlite3.Connection,
    preprocess_mode: str = DEFAULT_PREPROCESS_MODE,
    limit: int | None = None,
    dry_run: bool = False,
    jobs: int = 1,
) -> None:
    query = """
        SELECT id, image_path, bbox
        FROM lines
        WHERE image_path IS NOT NULL
        ORDER BY image_path, agreement_score ASC, id ASC
    """
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("[INFO] Nenhuma linha elegível para refresh de imagens.")
        return

    print(f"[INFO] Refresh de {len(rows)} line_image(s) com preprocess_mode={preprocess_mode}")
    if dry_run:
        sample_ids = ", ".join(str(row["id"]) for row in rows[:20])
        print(f"[DRY-RUN] Nenhuma linha será alterada. IDs elegíveis: {sample_ids}")
        if len(rows) > 20:
            print(f"[DRY-RUN] ... e mais {len(rows) - 20} linha(s).")
        return

    jobs = max(1, jobs)
    updated = 0
    tasks = [(row[0], row[1], row[2], preprocess_mode) for row in rows]
    if jobs == 1:
        for task in tasks:
            try:
                line_id, new_image = _refresh_line_image_job(task)
                conn.execute(
                    "UPDATE lines SET line_image=?, updated_at=datetime('now') WHERE id=?",
                    (new_image, line_id),
                )
                updated += 1
            except Exception as e:
                print(f"[WARN] id={task[0]}: não foi possível atualizar image - {e}")
    else:
        with mp.Pool(processes=jobs) as pool:
            for line_id, new_image in pool.imap_unordered(
                _refresh_line_image_job, tasks, chunksize=32
            ):
                conn.execute(
                    "UPDATE lines SET line_image=?, updated_at=datetime('now') WHERE id=?",
                    (new_image, line_id),
                )
                updated += 1

    conn.commit()
    print(f"[DONE] line_image atualizado em {updated} linha(s)")


def recalc_tesseract_fields(
    conn: sqlite3.Connection,
    lang: str = DEFAULT_TESS_LANG,
    tessdata_dir: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    query = """
        SELECT id, line_image, qwen_text, reviewed_text
        FROM lines
        WHERE line_image IS NOT NULL
        ORDER BY agreement_score, id ASC
    """
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("[INFO] Nenhuma linha elegível para recálculo de Tesseract.")
        return

    config = build_tesseract_config(tessdata_dir)
    print(
        f"[INFO] Recálculo de tesseract_text/agreement_score em {len(rows)} linha(s) "
        f"com lang={lang} config={config}"
    )
    if dry_run:
        sample_ids = ", ".join(str(row["id"]) for row in rows[:20])
        print(f"[DRY-RUN] Nenhuma linha será alterada. IDs elegíveis: {sample_ids}")
        if len(rows) > 20:
            print(f"[DRY-RUN] ... e mais {len(rows) - 20} linha(s).")
        return

    updated = 0
    for row in rows:
        try:
            blob = row["line_image"] if isinstance(row, sqlite3.Row) else row[1]
            if blob is None:
                continue
            img = Image.open(io.BytesIO(blob))
            new_text = tesseract_text_for_image(img, lang=lang, tessdata_dir=tessdata_dir)
            ref_text = resolve_reference_text(row)
            score = agreement_score(ref_text, new_text) if ref_text else 0.0
            conn.execute(
                """
                UPDATE lines
                SET tesseract_text=?, agreement_score=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (new_text, score, row["id"] if isinstance(row, sqlite3.Row) else row[0]),
            )
            updated += 1
        except Exception as e:
            row_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            print(f"[WARN] id={row_id}: não foi possível recalcular Tesseract - {e}")

    conn.commit()
    print(f"[DONE] tesseract_text/agreement_score atualizado em {updated} linha(s)")


# Multiprocessing

"""
Examples:  

 
setenv OMP_PLACES threads 
setenv OMP_PLACES "threads(4)" 
setenv OMP_PLACES "{0,1,2,3},{4,5,6,7},{8,9,10,11},{12,13,14,15}" 
setenv OMP_PLACES "{0:4},{4:4},{8:4},{12:4}" 
setenv OMP_PLACES "{0:4}:4:4"
"""
# 12 cores, 24 threads CPU, OMP_PLACES
places = [
    "{0,1}",
    "{2,3}",
    "{4,5}",
    "{6,7}",
    "{8,9}",
    "{10,11}",
    "{12,13}",
    "{14,15}",
    "{16,17}",
    "{18,19}",
    "{20,21}",
    "{22,23}",
]


def return_place_by_process_index(i: int) -> str:
    return places[i % len(places)]


def apply_to_env_by_process_index(i: int) -> None:
    os.environ["OMP_PLACES"] = return_place_by_process_index(i)
    print(f"Process {i} applying OMP_PLACES={os.environ['OMP_PLACES']}")


# ---- pool helpers ----
# Variável global para rastrear os processos inicializados
_process_counter_lock = mp.Lock()
_process_counter = mp.Value("i", 0)
_process_index_map = {}


def _init_omp_env(omp_threads: int = 2):
    """Inicializado no início de cada worker do Pool"""
    global _process_index_map

    # Obter PID atual
    current_pid = os.getpid()

    # Atribuir um índice sequencial para este processo
    with _process_counter_lock:
        if current_pid not in _process_index_map:
            process_index = _process_counter.value
            _process_index_map[current_pid] = process_index
            _process_counter.value += 1
        else:
            process_index = _process_index_map[current_pid]

    # Aplicar configurações de ambiente baseadas no índice do processo
    apply_to_env_by_process_index(process_index)

    # Configurações adicionais do OpenMP
    os.environ["OMP_THREAD_LIMIT"] = str(omp_threads)
    os.environ["OMP_NUM_THREADS"] = str(omp_threads)
    os.environ["OMP_DYNAMIC"] = "TRUE"
    os.environ.setdefault("OMP_PROC_BIND", "spread")

    print(f"Worker PID {current_pid} inicializado com índice {process_index}")
    print(f"OMP_PLACES={os.environ.get('OMP_PLACES', 'not set')}")
    print(f"OMP_THREAD_LIMIT={os.environ.get('OMP_THREAD_LIMIT')}")

    return process_index


def get_current_process_index() -> int:
    """Retorna o índice do processo atual baseado no PID"""
    current_pid = os.getpid()
    return _process_index_map.get(current_pid, -1)


def _process_page(
    img_path_str: str,
    txt_path_str: str,
    page_id: str,
    volume: str,
    preprocess_mode: str,
) -> dict:
    """Processa uma página: roda tesseract, recorta linhas e retorna dados serializáveis."""
    try:
        img_path = Path(img_path_str)
        img_original = cv2.imread(str(img_path))
    except Exception as e:
        return {
            "status": "error",
            "page_id": page_id,
            "message": f"Image.open failed: {e}",
        }

    binaryzed, img = preprocess_image(img=img_original, mode=preprocess_mode)

    try:
        # Executa segmentação (TSV -> linhas)
        lines = tesseract_lines(binaryzed)
    except Exception as e:
        return {
            "status": "error",
            "page_id": page_id,
            "message": f"tesseract_lines failed: {e}",
        }

    if len(lines) < MIN_LINES:
        return {"status": "skip", "page_id": page_id, "num_lines": len(lines)}

    # Detecta língua dominante (heurística simples)
    try:
        osd = pytesseract.image_to_osd(binaryzed, output_type=pytesseract.Output.DICT)
        detected_lang = osd.get("script", "unknown")
    except Exception:
        detected_lang = "unknown"

    rows = []
    for idx, line in enumerate(lines):
        try:
            line_png = crop_line(binaryzed, line["bbox"])
        except Exception as e:
            return {
                "status": "error",
                "page_id": page_id,
                "message": f"crop_line failed: {e}",
            }

        rows.append(
            {
                "line_index": idx,
                "bbox": line["bbox"],
                "line_image": line_png,
                "tesseract_text": line["text"],
            }
        )

    return {
        "status": "ok",
        "page_id": page_id,
        "num_lines": len(rows),
        "detected_lang": detected_lang,
        "image_path": str(img_path.resolve()),
        "volume": volume,
        "rows": rows,
    }


def _process_page_wrapper(task_tuple: tuple) -> dict:
    """Wrapper para Pool.map — recebe uma tupla e repassa para _process_page."""
    try:
        img_path_str, txt_path_str, page_id, volume, preprocess_mode = task_tuple
        return _process_page(
            img_path_str, txt_path_str, page_id, volume, preprocess_mode
        )
    except Exception as e:
        return {
            "status": "error",
            "page_id": task_tuple[2] if len(task_tuple) > 2 else "?",
            "message": str(e),
        }


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def finalize_interrupted_session(
    conn: sqlite3.Connection, session_id: int, report: dict | None = None
) -> tuple[int, list[str]]:
    """Remove sessão vazia ou preserva contagens reais após uma interrupção."""
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM lines WHERE session_id = ?", (session_id,)
    ).fetchone()
    line_count = int(row["total"] if isinstance(row, sqlite3.Row) else row[0])
    if line_count == 0:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        if report is not None:
            report["session_id"] = None
        LOGGER.warning("Sessão vazia %s removida após interrupção", session_id)
        return 0, []

    rows = conn.execute(
        """
        SELECT DISTINCT volume
        FROM lines
        WHERE session_id = ? AND volume IS NOT NULL AND volume <> ''
        ORDER BY volume
        """,
        (session_id,),
    ).fetchall()
    volumes = [str(row[0]) for row in rows]
    conn.execute(
        """
        UPDATE sessions
        SET sample_size = ?, volumes = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (line_count, json.dumps(volumes), session_id),
    )
    conn.commit()
    LOGGER.warning(
        "Sessão parcial %s preservada com %s linha(s)", session_id, line_count
    )
    return line_count, volumes


def sample_and_ingest(
    root: Path,
    conn: sqlite3.Connection,
    per_volume: int,
    description: str,
    preprocess_mode: str = DEFAULT_PREPROCESS_MODE,
    seed: int | None = None,
    volume_names: set[str] | None = None,
    dry_run: bool = False,
    jobs: int | None = None,
    report: dict | None = None,
) -> dict | None:
    if per_volume <= 0:
        raise ValueError("per_volume deve ser > 0")
    rng = random.Random(seed)
    volumes = find_volumes(root)
    if volume_names:
        requested = {name.upper() for name in volume_names}
        volumes = [vol for vol in volumes if vol.name.upper() in requested]
        found = {vol.name.upper() for vol in volumes}
        missing = sorted(requested - found)
        if missing:
            add_report_issue(
                report,
                "warning",
                "volumes_not_found",
                "Um ou mais volumes solicitados não foram encontrados.",
                volumes=missing,
            )
    if not volumes:
        message = f"Nenhum volume PG/PL encontrado em {root}"
        add_report_issue(report, "error", "no_volumes", message)
        if report is not None:
            report["status"] = "failed"
        raise IngestionError(message)

    print(f"[INFO] {len(volumes)} volumes encontrados: {[v.name for v in volumes]}")

    selections: list[tuple[Path, list[tuple[Path, Path, str]], dict[str, int], int]] = []
    for vol in volumes:
        pairs = find_page_pairs(vol)
        if not pairs:
            add_report_issue(
                report,
                "warning",
                "no_page_pairs",
                "Nenhum par de imagem e texto encontrado.",
                volume=vol.name,
            )
            continue
        existing_page_ids, existing_image_paths = existing_material_for_volume(
            conn, vol.name
        )
        selected, class_counts, existing_count = select_prioritized_pages(
            pairs,
            per_volume,
            rng,
            existing_page_ids=existing_page_ids,
            existing_image_paths=existing_image_paths,
        )
        selections.append((vol, selected, class_counts, existing_count))
        if report is not None:
            report["volumes"][vol.name] = {
                "pages_planned": len(selected),
                "priority": class_counts,
                "existing_pages_excluded": existing_count,
                "lines_inserted": 0,
                "bbox_conflicts": 0,
            }
        print(
            f"[PLAN] {vol.name}: mixed={class_counts['mixed']} "
            f"greek={class_counts['greek']} other={class_counts['other']} "
            f"existentes_excluídas={existing_count}"
        )
        if dry_run and selected:
            print(f"[DRY-RUN] {vol.name}: {[page_id for _, _, page_id in selected]}")

    selected_pages = sum(len(selected) for _, selected, _, _ in selections)
    if report is not None:
        report["summary"]["pages_planned"] = selected_pages
    if dry_run:
        print(
            f"[DRY-RUN] {selected_pages} página(s) seriam processadas; "
            "nenhuma sessão ou linha foi gravada."
        )
        if report is not None:
            report["status"] = "dry_run"
        return report
    if selected_pages == 0:
        message = "Nenhuma página inédita elegível; nenhuma sessão foi criada."
        add_report_issue(report, "warning", "no_new_pages", message)
        if report is not None:
            report["status"] = "completed_with_issues"
        return report

    all_lines_count = 0
    all_conflicts = 0
    included_volumes: list[str] = []

    session_id = create_session(conn, description, 0, [])
    if report is not None:
        report["session_id"] = session_id

    cpu_threads = max(1, jobs or mp.cpu_count() // 2)
    omp_threads = 2
    print(f"[INFO] Multiprocessing: workers={cpu_threads}, omp_threads={omp_threads}")

    try:
        for vol, selected, _class_counts, _existing_count in selections:
            if not selected:
                continue
            vol_lines = 0
            vol_conflicts = 0
            tasks = [
                (str(img_path), str(txt_path), page_id, vol.name, preprocess_mode)
                for img_path, txt_path, page_id in selected
            ]

            with mp.Pool(
                processes=min(cpu_threads, len(tasks)),
                initializer=_init_omp_env,
                initargs=(omp_threads,),
            ) as pool:
                results = pool.map(_process_page_wrapper, tasks)

            for res in results:
                if res.get("status") == "error":
                    add_report_issue(
                        report,
                        "warning",
                        "page_processing_error",
                        res.get("message", "Erro desconhecido ao processar página."),
                        volume=vol.name,
                        page_id=res.get("page_id"),
                    )
                    continue
                if res.get("status") == "skip":
                    add_report_issue(
                        report,
                        "warning",
                        "page_below_minimum_lines",
                        f"Página abaixo do mínimo de {MIN_LINES} linhas.",
                        volume=vol.name,
                        page_id=res.get("page_id"),
                        detected_lines=res.get("num_lines"),
                    )
                    continue

                inserted, conflicts = insert_nonconflicting_page(
                    conn, session_id, res, report=report
                )
                vol_lines += inserted
                vol_conflicts += conflicts
                if conflicts:
                    add_report_issue(
                        report,
                        "warning",
                        "bbox_conflicts",
                        "Linhas conflitantes foram ignoradas.",
                        volume=vol.name,
                        page_id=res.get("page_id"),
                        count=conflicts,
                    )
                print(
                    f"[OK] {res.get('page_id')}: {inserted} linhas inseridas, "
                    f"{conflicts} conflito(s) ignorado(s)"
                )

            all_lines_count += vol_lines
            all_conflicts += vol_conflicts
            if vol_lines:
                included_volumes.append(vol.name)
            if report is not None:
                report["volumes"][vol.name]["lines_inserted"] = vol_lines
                report["volumes"][vol.name]["bbox_conflicts"] = vol_conflicts
            print(
                f"[VOL] {vol.name}: {vol_lines} linhas de {len(selected)} páginas; "
                f"conflitos={vol_conflicts}"
            )
    except Exception as exc:
        add_report_issue(
            report,
            "error",
            "ingestion_interrupted",
            str(exc),
            session_id=session_id,
        )
        recovered_count = all_lines_count
        recovered_volumes = list(included_volumes)
        try:
            recovered_count, recovered_volumes = finalize_interrupted_session(
                conn, session_id, report
            )
        except Exception as cleanup_exc:
            conn.rollback()
            add_report_issue(
                report,
                "error",
                "session_reconciliation_failed",
                str(cleanup_exc),
                session_id=session_id,
            )
        if report is not None:
            report["status"] = "failed"
            report["summary"]["lines_inserted"] = recovered_count
            report["summary"]["bbox_conflicts"] = all_conflicts
            for volume in recovered_volumes:
                report["volumes"].setdefault(volume, {})
        raise

    if report is not None:
        report["summary"]["lines_inserted"] = all_lines_count
        report["summary"]["bbox_conflicts"] = all_conflicts

    if all_lines_count == 0:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        print("[INFO] Nenhuma linha nova foi inserida; sessão vazia removida.")
        if report is not None:
            report["session_id"] = None
            report["status"] = "completed_with_issues"
        return report

    conn.execute(
        "UPDATE sessions SET sample_size=?, volumes=?, updated_at=datetime('now') WHERE id=?",
        (all_lines_count, json.dumps(included_volumes), session_id),
    )
    conn.commit()
    print(
        f"\n[DONE] Sessão {session_id}: {all_lines_count} linhas de "
        f"{len(included_volumes)} volumes; conflitos ignorados={all_conflicts}"
    )
    if report is not None:
        report["status"] = (
            "completed_with_issues" if report.get("issues") else "completed"
        )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Amostragem e segmentação para finetuning Tesseract"
    )
    parser.add_argument(
        "--root", required=True, help="Diretório raiz com volumes PG*/PL*"
    )
    parser.add_argument(
        "--db", default="ocr.db", help="Caminho do SQLite (default: ocr.db)"
    )
    parser.add_argument("--session", default="amostra", help="Descrição da sessão")
    parser.add_argument(
        "--per-volume", type=int, default=DEFAULT_PER_VOLUME, help="Páginas por volume"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Seed aleatória para reprodutibilidade"
    )
    ingest_mode = parser.add_mutually_exclusive_group()
    ingest_mode.add_argument(
        "--append",
        action="store_true",
        help="Cria uma sessão adicional sem apagar linhas existentes.",
    )
    ingest_mode.add_argument(
        "--resample",
        action="store_true",
        help="Modo legado destrutivo: apaga linhas/sessões antes da amostragem.",
    )
    parser.add_argument(
        "--volume",
        action="append",
        dest="volumes",
        help="Restringe a um volume; pode ser repetido (ex.: --volume PG004).",
    )
    parser.add_argument(
        "--preprocess-mode",
        choices=["adaptive_soft", "sample"],
        default=DEFAULT_PREPROCESS_MODE,
        help="Receita de pré-processamento usada no ingest",
    )
    parser.add_argument(
        "--refresh-images",
        action="store_true",
        help="Atualiza line_image em linhas já existentes sem apagar o DB.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita a quantidade de linhas afetadas por --refresh-images.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria alterado sem gravar mudanças.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, mp.cpu_count() // 2),
        help="Número de processos para ingestão ou --refresh-images.",
    )
    parser.add_argument(
        "--recalc-tesseract",
        action="store_true",
        help="Recalcula tesseract_text e agreement_score nas linhas existentes.",
    )
    parser.add_argument(
        "--tesseract-lang",
        default=DEFAULT_TESS_LANG,
        help="Idioma/traineddata para o recálculo do Tesseract (ex.: lat, grc, lat+grc, migne).",
    )
    parser.add_argument(
        "--tessdata-dir",
        default=None,
        help="Diretório opcional com os traineddata do Tesseract.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Caminho do relatório JSON; com problemas, um caminho automático é usado.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Nível dos logs emitidos no stderr.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.resample and args.dry_run:
        parser.error(
            "--resample não pode ser combinado com --dry-run; "
            "use --append --dry-run para inspecionar uma ingestão incremental."
        )

    mode = (
        "refresh_images"
        if args.refresh_images
        else "recalc_tesseract"
        if args.recalc_tesseract
        else "resample"
        if args.resample
        else "append"
    )
    report = create_ingestion_report(
        db_path=args.db,
        root=args.root,
        mode="dry_run" if args.dry_run else mode,
        per_volume=args.per_volume,
    )
    conn: sqlite3.Connection | None = None
    exit_code = 0
    try:
        conn = open_db(args.db, read_only=args.dry_run)

        if args.refresh_images:
            refresh_line_images(
                conn,
                preprocess_mode=args.preprocess_mode,
                limit=args.limit,
                dry_run=args.dry_run,
                jobs=args.jobs,
            )
            report["status"] = "dry_run" if args.dry_run else "completed"
        elif args.recalc_tesseract:
            recalc_tesseract_fields(
                conn,
                lang=args.tesseract_lang,
                tessdata_dir=args.tessdata_dir,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            report["status"] = "dry_run" if args.dry_run else "completed"
        elif session_exists(conn) and not (
            args.append or args.resample or args.dry_run
        ):
            count = conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
            print(
                f"[INFO] Sessão existente com {count} linhas. Use --append para "
                "adicionar material sem apagar a base."
            )
            report["status"] = "not_run"
        else:
            if args.resample:
                print("[INFO] --resample: limpando dados anteriores...")
                conn.executescript("DELETE FROM lines; DELETE FROM sessions;")
                conn.commit()

            sample_and_ingest(
                root=Path(args.root),
                conn=conn,
                per_volume=args.per_volume,
                description=args.session,
                preprocess_mode=args.preprocess_mode,
                seed=args.seed,
                volume_names=set(args.volumes or []),
                dry_run=args.dry_run,
                jobs=args.jobs,
                report=report,
            )
    except Exception as exc:
        if report.get("status") != "failed":
            add_report_issue(
                report,
                "error",
                "execution_failed",
                str(exc),
                exception_type=type(exc).__name__,
            )
            report["status"] = "failed"
        LOGGER.exception("Execução de sample.py interrompida")
        exit_code = 1
    finally:
        report["finished_at"] = utc_now()
        if conn is not None:
            conn.close()
        if args.report or report.get("issues"):
            output = Path(args.report) if args.report else default_report_path(args.db)
            try:
                write_ingestion_report(report, output)
            except OSError:
                LOGGER.exception("Não foi possível gravar o relatório em %s", output)
                exit_code = 1

    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
