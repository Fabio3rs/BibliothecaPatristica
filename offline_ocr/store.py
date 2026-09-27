"""Content-addressed artifacts, independent from data/ocr_versions.db."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

from .layout import FORMAT_VERSION, NORMALIZER_VERSION, ALIGNER_VERSION, align, parse_llm, parse_tsv

DEFAULT_DB = Path(".ocr-workbench/layouts.sqlite3")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(value):
    return digest(canonical(value).encode())


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS artifacts (
            key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS comparisons (
            key TEXT PRIMARY KEY, label TEXT NOT NULL, image_path TEXT NOT NULL,
            image_hash TEXT NOT NULL, llm_key TEXT NOT NULL REFERENCES artifacts(key),
            tess_key TEXT NOT NULL REFERENCES artifacts(key), payload TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS comparisons_created ON comparisons(created_at DESC,key);
        CREATE INDEX IF NOT EXISTS comparisons_label ON comparisons(label COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS comparisons_image ON comparisons(image_hash);
        CREATE INDEX IF NOT EXISTS comparisons_llm ON comparisons(llm_key);
        CREATE INDEX IF NOT EXISTS comparisons_tess ON comparisons(tess_key);
        CREATE TABLE IF NOT EXISTS revisions (
            id INTEGER PRIMARY KEY, comparison_key TEXT NOT NULL REFERENCES comparisons(key),
            parent_id INTEGER REFERENCES revisions(id), payload TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS revisions_comparison ON revisions(comparison_key,id);
    """)
    return con


def artifact(con, key):
    row = con.execute("SELECT payload FROM artifacts WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def save_artifact(con, key, kind, payload):
    with con:
        con.execute("INSERT OR IGNORE INTO artifacts(key,kind,payload) VALUES (?,?,?)",
                    (key, kind, canonical(payload)))
    return artifact(con, key)


def engine_config(lang, psm, oem, tessdata):
    args = ["tesseract"]
    if tessdata:
        args += ["--tessdata-dir", str(Path(tessdata).resolve())]
    listing = subprocess.run(args + ["--list-langs"], check=True, capture_output=True, text=True)
    match = re.search(r'"([^"\n]+)"', listing.stdout + listing.stderr)
    if not match:
        raise ValueError("Não foi possível localizar tessdata; informe --tessdata-dir")
    root = Path(match.group(1)).resolve()
    models = {}
    for name in lang.split("+"):
        if not re.fullmatch(r"[\w-]+", name):
            raise ValueError("Nome de idioma inválido")
        models[name] = digest((root / (name + ".traineddata")).read_bytes())
    return {"lang": lang, "psm": psm, "oem": oem, "tessdata": str(root), "models": models,
            "engine": subprocess.run(["tesseract", "--version"], check=True, capture_output=True, text=True).stdout.strip(),
            "preprocessing": "none", "coordinate_transform": [1, 0, 0, 1, 0, 0], "format": FORMAT_VERSION}


def read_version(db_path, result_id, image_hash):
    path = Path(db_path).resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM ocr_results WHERE id=?", (result_id,)).fetchone()
        if row is None:
            raise ValueError(f"Versão {result_id} não encontrada")
        if row["image_hash"] != image_hash:
            raise ValueError("A imagem não corresponde ao hash da versão OCR")
        version = dict(row)
        engine = con.execute("SELECT * FROM ocr_engine_versions WHERE id=?", (row["engine_version_id"],)).fetchone()
    raw = version.pop("text_content")
    if digest(raw.encode()) != version["text_hash"]:
        raise ValueError("Hash do texto inconsistente no banco de origem")
    return raw, {"versions_db": str(path), "result": version, "engine": dict(engine) if engine else None}


def ingest(con, *, image_path, text_path=None, result_id=None, versions_db=None,
           lang="migne", psm=3, oem=1, tessdata=None, tsv_path=None, dehyphenate=True, tolerance=25):
    from PIL import Image

    if not 0 <= tolerance <= 200:
        raise ValueError("Tolerância deve estar entre 0 e 200")
    image_path = Path(image_path).resolve()
    image_bytes = image_path.read_bytes()
    image_hash = digest(image_bytes)
    with Image.open(image_path) as im:
        width, height = im.size
        if getattr(im, "n_frames", 1) != 1:
            raise ValueError("Use uma imagem de uma única página")
    if result_id is not None:
        raw, provenance = read_version(versions_db, result_id, image_hash)
    else:
        text_path = Path(text_path).resolve()
        raw = text_path.read_bytes().decode("utf-8")
        provenance = {"text_path": str(text_path)}
    llm_key = identity({"kind": "llm", "image": image_hash, "text": digest(raw.encode()),
                        "provenance": provenance, "parser": FORMAT_VERSION})
    llm = artifact(con, llm_key)
    if llm is None:
        llm = save_artifact(con, llm_key, "llm", {**parse_llm(raw), "provenance": provenance})
    if tsv_path:
        tsv = Path(tsv_path).read_bytes().decode("utf-8")
        config = {"imported_tsv_hash": digest(tsv.encode()), "format": FORMAT_VERSION,
                  "preprocessing": "external_identity_required", "coordinate_transform": [1, 0, 0, 1, 0, 0]}
    else:
        config = engine_config(lang, psm, oem, tessdata)
    tess_key = identity({"kind": "tesseract", "image": image_hash, "config": config})
    tess = artifact(con, tess_key)
    tess_cached = tess is not None
    if tess is None:
        if not tsv_path:
            proc = subprocess.run(["tesseract", str(image_path), "stdout", "--tessdata-dir", config["tessdata"],
                                   "-l", lang, "--psm", str(psm), "--oem", str(oem), "tsv"],
                                  check=True, capture_output=True, text=True, timeout=300)
            tsv = proc.stdout
            config["stderr"] = proc.stderr
        if digest(image_path.read_bytes()) != image_hash:
            raise ValueError("Imagem mudou durante a extração; execute novamente")
        tess = save_artifact(con, tess_key, "tesseract", {**parse_tsv(tsv, width, height), "config": config})
    key = identity({"llm": llm_key, "tess": tess_key, "normalizer": NORMALIZER_VERSION,
                    "aligner": ALIGNER_VERSION, "dehyphenate": dehyphenate, "tolerance": tolerance})
    existing = con.execute("SELECT key FROM comparisons WHERE key=?", (key,)).fetchone()
    if existing is None:
        payload = align(llm, tess, dehyphenate, tolerance)
        with con:
            con.execute("""INSERT OR IGNORE INTO comparisons
                (key,label,image_path,image_hash,llm_key,tess_key,payload) VALUES (?,?,?,?,?,?,?)""",
                        (key, image_path.stem, str(image_path), image_hash, llm_key, tess_key, canonical(payload)))
    return {"key": key, "reused": existing is not None, "tesseract_cached": tess_cached}


def get_comparison(con, key):
    row = con.execute("SELECT * FROM comparisons WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["alignment"] = json.loads(result.pop("payload"))
    result["llm"] = artifact(con, result["llm_key"])
    result["tesseract"] = artifact(con, result["tess_key"])
    result["schema_version"] = FORMAT_VERSION
    return result


def list_comparisons(con, prefix="", after=None, limit=100):
    """Indexed keyset pagination; never fetch the corpus-sized list into the browser."""
    if not 1 <= limit <= 200:
        raise ValueError("Limite deve estar entre 1 e 200")
    conditions, values = [], []
    if prefix:
        escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        conditions.append("label LIKE ? ESCAPE '!'")
        values.append(escaped + "%")
    if after:
        row = con.execute("SELECT created_at,key FROM comparisons WHERE key=?", (after,)).fetchone()
        if row is None:
            raise ValueError("Cursor não encontrado")
        conditions.append("(created_at < ? OR (created_at = ? AND key > ?))")
        values.extend((row["created_at"], row["created_at"], row["key"]))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = con.execute("SELECT key,label,created_at FROM comparisons" + where + " ORDER BY created_at DESC,key LIMIT ?", (*values, limit)).fetchall()
    return [dict(row) for row in rows]
