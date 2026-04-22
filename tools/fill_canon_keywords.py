import argparse
import hashlib
import struct
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


# Garantir import do projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scripture_ref_normalizer import (
    keywords_cite_books,
    extract_citations_from_value_cached,
)


def connect_db(path: Path) -> sqlite3.Connection:
    """Abre conexão SQLite com pragmas seguros e WAL ativado."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 30000;")
    con.execute("PRAGMA foreign_keys = ON;")
    return con


# Lets add the bit is_scripture_citation
ADD_FIELDS_TO_KEYWORDS_TABLE = """
ALTER TABLE keywords ADD COLUMN is_scripture_citation BOOLEAN DEFAULT FALSE;
"""

#
CREATE_TABLE_KEYWORD_GROUP_CANON = """
CREATE TABLE cluster_canonical_names AS
WITH GroupStats AS (
    -- Passo 1: Calcula o tamanho médio das palavras dentro de cada cluster
    SELECT 
        hdbscan_group_id, 
        AVG(LENGTH(keyword_norm)) as avg_length
    FROM keywords
    WHERE hdbscan_group_id IS NOT NULL
    GROUP BY hdbscan_group_id
),
RankedKeywords AS (
    -- Passo 2: Ranqueia as palavras cruzando com a média do seu respectivo grupo
    SELECT 
        k.hdbscan_group_id AS group_id, 
        k.keyword_norm,
        k.keyword_original,
        kc.membership_probability,
        -- Critérios de ordenação:
        -- 1. Maior probabilidade de pertencer ao grupo (Âncora)
        -- 2. Diferença absoluta entre o tamanho da palavra e a média do grupo (quanto menor, mais próximo da média)
        -- 3. Ordem alfabética para garantir desempate final determinístico
        ROW_NUMBER() OVER (
            PARTITION BY k.hdbscan_group_id 
            ORDER BY 
                kc.membership_probability DESC, 
                ABS(LENGTH(k.keyword_norm) - gs.avg_length) ASC, 
                k.keyword_norm ASC
        ) as rank
    FROM keywords k
    JOIN keyword_clusters kc ON k.id = kc.keyword_id
    JOIN GroupStats gs ON k.hdbscan_group_id = gs.hdbscan_group_id
    WHERE k.hdbscan_group_id IS NOT NULL
)
-- Passo 3: Seleciona apenas a campeã de cada grupo
SELECT 
    group_id,
    keyword_norm AS nome_canonico,
    keyword_original AS nome_canonico_original,
    membership_probability AS score_ancora
FROM RankedKeywords
WHERE rank = 1;
"""

CREATE_INDEXES_KEYWORD_GROUP_CANON = """
CREATE UNIQUE INDEX idx_group_id ON cluster_canonical_names (group_id);
CREATE UNIQUE INDEX idx_nome_canonico ON cluster_canonical_names (nome_canonico);
"""

"""
SELECT 
    group_id,
    COUNT(*) as total_ancoras,
    GROUP_CONCAT(keyword_norm, ' | ') AS keywords_principais
FROM (
    SELECT 
        k.hdbscan_group_id as group_id, 
        k.keyword_norm,
        kc.membership_probability,
        ROW_NUMBER() OVER (PARTITION BY k.hdbscan_group_id ORDER BY kc.membership_probability DESC) as rank
    FROM keywords k
    JOIN keyword_clusters kc ON k.id = kc.keyword_id
    WHERE k.hdbscan_group_id IS NOT NULL 
      AND kc.membership_probability > 0.9  -- Filtra apenas o que é muito certeiro
)
WHERE rank <= 15
GROUP BY group_id
ORDER BY total_ancoras DESC;
"""



def ensure_fields_tables(conn: sqlite3.Connection):
    """Garante que todos os campos necessários estão presentes nas tabelas."""
    cursor = conn.cursor()
    try:
        cursor.execute(ADD_FIELDS_TO_KEYWORDS_TABLE)
    except sqlite3.OperationalError as e:
        print(f"Error ensuring fields in tables: {e}")
    conn.commit()


def _is_scripture_citation(keyword_text: str) -> bool:
    """
    Detecta se uma keyword é uma citação bíblica.
    Usa a mesma lógica do keywords_serial.py:
      1. extract_citations_from_value_cached (robusto, inclui abreviações e suporte AC)
      2. Fallback para keywords_cite_books (cobre nomes de livros sem capítulo/versículo)
    """
    # Verificação principal — idêntica à usada em looks_like_reference_keyword
    # e em extract_citations() no keywords_serial.py
    citation_results = extract_citations_from_value_cached(
        keyword_text,
        source_kind="keywords",
        source_path="backfill",
        support_mode=False,
    )
    # Se meramente citou o nome, não marcaremos
    if citation_results and citation_results[0]["number"]:
        # print(f"{keyword_text!r} -> {citation_results}")
        return True
    # Fallback: cobre casos onde só o nome do livro é mencionado
    # if keywords_cite_books([keyword_text]):
    #     return True
    return False


def backfill_keywords_scripture_flag(
    conn: sqlite3.Connection, batch_size: int = 500, dry_run: bool = False
):
    """Preenche o campo is_scripture_citation para as palavras-chave existentes."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, keyword_original, hdbscan_group_id FROM keywords")
    keywords = cursor.fetchall()

    to_flag_true: list[int] = []
    to_flag_false: list[int] = []

    for keyword in keywords:
        keyword_id = keyword["id"]
        kw_text = keyword["keyword_original"]

        is_scripture = _is_scripture_citation(kw_text)

        if is_scripture:
            print(
                f"[SCRIPTURE] {kw_text!r} (id={keyword_id}, group={keyword['hdbscan_group_id']})"
            )
            to_flag_true.append(keyword_id)
        else:
            to_flag_false.append(keyword_id)

    # Grava em lote para eficiência
    update_cursor = conn.cursor()
    for i in range(0, len(to_flag_true), batch_size):
        batch = to_flag_true[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        update_cursor.execute(
            f"UPDATE keywords SET is_scripture_citation = TRUE WHERE id IN ({placeholders})",
            batch,
        )
    for i in range(0, len(to_flag_false), batch_size):
        batch = to_flag_false[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        update_cursor.execute(
            f"UPDATE keywords SET is_scripture_citation = FALSE WHERE id IN ({placeholders})",
            batch,
        )
    conn.commit()
    print(
        f"\nConcluído: {len(to_flag_true)} marcadas como citação bíblica, "
        f"{len(to_flag_false)} marcadas como não-bíblicas."
    )


def main():
    conn = connect_db(Path("data/patristica_keywords.db"))
    ensure_fields_tables(conn)
    backfill_keywords_scripture_flag(conn, dry_run=True)


    # Comando abaixo não deve ser rodado em modo DRY
    try:
        cursor = conn.cursor()
        cursor.execute(CREATE_TABLE_KEYWORD_GROUP_CANON)
        cursor.executescript(CREATE_INDEXES_KEYWORD_GROUP_CANON)
    except sqlite3.OperationalError as e:
        print(f"Error ensuring fields in tables: {e}")
    conn.close()


if __name__ == "__main__":
    main()
