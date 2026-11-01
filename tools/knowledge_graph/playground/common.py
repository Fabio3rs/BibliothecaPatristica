from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / 'scripts/playgrounds'
SCHEMA_PATH = KIT / 'kg-extraction-v1.schema.json'
DEFAULT_DB = ROOT / 'data/playgrounds/kg_extractor_playground.db'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    if not isinstance(value, (str, bytes)):
        value = dumps(value)
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def now():
    return datetime.now(timezone.utc).isoformat()


def readonly(path):
    if not Path(path).is_file():
        raise FileNotFoundError(f'Database unavailable: {path}')
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def safe_child(root, name):
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Source path escapes its configured root')
    return path
