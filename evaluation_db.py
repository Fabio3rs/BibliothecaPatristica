from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional
import xml.etree.ElementTree as ET


def connect_eval_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_eval_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            volume_id TEXT,
            page_num INTEGER,
            image_path TEXT NOT NULL,
            text_path TEXT,
            ocr_result_id INTEGER,
            provider TEXT,
            model TEXT,
            prompt_version TEXT,
            decision TEXT NOT NULL, -- deterministic_pass | llm_judged
            deterministic_reason TEXT,
            fidelidade TEXT,
            usabilidade TEXT,
            idiomas_json TEXT,
            comentario TEXT,
            xml_raw TEXT,
            duration_ms REAL,
            status TEXT NOT NULL, -- deterministic_pass | parse_ok | parse_error
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_eval_volume_page ON evaluations(volume_id, page_num);
        CREATE INDEX IF NOT EXISTS idx_eval_created_at ON evaluations(created_at);
        """
    )
    # Migração: adiciona coluna ocr_result_id se não existir (retrocompatível)
    try:
        con.execute("ALTER TABLE evaluations ADD COLUMN ocr_result_id INTEGER")
    except sqlite3.OperationalError:
        # coluna já existe
        pass
    con.commit()


def compute_prompt_version(prompt: str) -> str:
    """Hash curto para rastrear versões de prompt."""
    h = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    return f"judge-{h[:8]}"


def parse_llm_judge_xml(xml_str: str) -> Dict[str, Any]:
    """
    Parsea o XML retornado pelo prompt de julgamento.
    Retorna dict com status parse_ok/parse_error e campos extraídos.
    """
    result: Dict[str, Any] = {
        "status": "parse_error",
        "fidelidade": None,
        "usabilidade": None,
        "idiomas": [],
        "comentario": None,
    }

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return result

    try:
        julgamento = root.find("julgamento")
        if julgamento is not None:
            fid = julgamento.findtext("fidelidade")
            usa = julgamento.findtext("usabilidade")
            result["fidelidade"] = fid.strip() if fid else None
            result["usabilidade"] = usa.strip() if usa else None

        idiomas_identificados = root.find("idiomas_identificados")
        if idiomas_identificados is not None:
            result["idiomas"] = [
                i.text.strip()
                for i in idiomas_identificados.findall("idioma")
                if i.text
            ]

        comentario = root.find("comentario")
        if comentario is not None and comentario.text:
            result["comentario"] = comentario.text.strip()

        result["status"] = "parse_ok"
    except Exception:
        result["status"] = "parse_error"

    return result


def record_evaluation(
    con: sqlite3.Connection,
    *,
    volume_id: Optional[str],
    page_num: Optional[int],
    image_path: Path,
    text_path: Optional[Path],
    ocr_result_id: Optional[int] = None,
    provider: Optional[str],
    model: Optional[str],
    prompt_version: str,
    decision: str,
    deterministic_reason: str,
    xml_raw: Optional[str],
    parse_result: Dict[str, Any],
    duration_ms: Optional[float],
    status: str,
) -> None:
    idiomas_json = None
    if parse_result.get("idiomas"):
        idiomas_json = json.dumps(parse_result["idiomas"], ensure_ascii=False)

    con.execute(
        """
        INSERT INTO evaluations (
            volume_id, page_num, image_path, text_path,
            ocr_result_id, provider, model, prompt_version,
            decision, deterministic_reason,
            fidelidade, usabilidade, idiomas_json, comentario,
            xml_raw, duration_ms, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            volume_id,
            page_num,
            str(image_path),
            str(text_path) if text_path else None,
            ocr_result_id,
            provider,
            model,
            prompt_version,
            decision,
            deterministic_reason,
            parse_result.get("fidelidade"),
            parse_result.get("usabilidade"),
            idiomas_json,
            parse_result.get("comentario"),
            xml_raw,
            duration_ms,
            status,
        ),
    )
    con.commit()
