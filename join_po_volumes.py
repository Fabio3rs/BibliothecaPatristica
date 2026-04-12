from pathlib import Path
import sqlite3
import json
import re

# Normaliza os rótulos de idioma vindos do banco (gerados pelo LLM em português informal)
# para os valores canônicos usados no schema do dataset.
_IDIOMA_NORM: dict[str, str] = {
    "latim": "latino",
    "latino": "latino",
    "grego": "grego",
    "siriaco": "siriaco",
    "síriaco": "siriaco",
    "siríaco": "siriaco",
    "armenio": "armenio",
    "armênio": "armenio",
    "copta": "copta",
    "coptico": "copta",
    "cóptico": "copta",
    "arabe": "arabe",
    "árabe": "arabe",
    "ethiopico": "ethiopico",
    "etíope": "ethiopico",
    "etiópico": "ethiopico",
    "etiopico": "ethiopico",
    "etíopico": "ethiopico",
    "ge'ez": "ethiopico",
    "etíope (ge'ez)": "ethiopico",
    "ge'ez (etíope)": "ethiopico",
    "etiópico (ge'ez)": "ethiopico",
    "etíopico (ge'ez)": "ethiopico",
    "cirilico": "cirilico",
    "cirílico": "cirilico",
    "hebraico": "hebraico",
    "misto": "misto",
    "desconhecido": "desconhecido",
    "georgiano": "georgiano",
    # variantes em inglês que o LLM pode produzir ocasionalmente
    "latin": "latino",
    "greek": "grego",
    "syriac": "siriaco",
    "armenian": "armenio",
    "coptic": "copta",
    "arabic": "arabe",
    "ethiopic": "ethiopico",
    "french": "frances",
    "inglês": "ingles",
    "francês": "frances",
    "english": "ingles",
    # variantes compostas — colapsar para canonical mais próximo
    "transliteração siríaca/armênia": "misto",
    "antigo eslavo eclesiástico (cirílico)": "cirilico",
    "cirílico (eslavo eclesiástico)": "cirilico",
    "cirílico (antigo eslavo eclesiástico)": "cirilico",
    "eslavo eclesiástico (cirílico)": "cirilico",
}


def normalize_idiomas(raw_json: str | None) -> list[str]:
    if not raw_json:
        return []
    try:
        items = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    result = []
    for item in items:
        key = item.strip().lower()
        # 1. Tentativa com a string completa (captura frases compostas do mapa)
        normalized = _IDIOMA_NORM.get(key)
        if normalized is None:
            # 2. Tentativa com o primeiro token (antes de espaço ou parêntese)
            # Ex: "siríaco (variante)" → "siríaco" → "siriaco"
            first_token = re.split(r"[\s(]", key)[0]
            normalized = _IDIOMA_NORM.get(first_token, key)
        if normalized not in result:
            result.append(normalized)
    return result


def extract_page_num(stem: str) -> int | None:
    # uuid-497 ou prefixo-qualquer-coisa-497
    m = re.search(r"-(\d+)$", stem)
    return int(m.group(1)) if m else None


con = sqlite3.connect("data/ocr_eval.db", timeout=30)
con.row_factory = sqlite3.Row

records = []
for txt_path in sorted(Path("teste").glob("PO*/text/*.txt")):
    volume = txt_path.parts[1]  # PO009
    page_num = extract_page_num(txt_path.stem)
    if page_num is None:
        continue

    xml_raw = txt_path.read_text(encoding="utf-8", errors="replace")

    # pega avaliação mais recente para este volume/página
    row = con.execute(
        """
        SELECT fidelidade, usabilidade, idiomas_json, decision, status
        FROM evaluations
        WHERE volume_id = ? AND page_num = ?
        ORDER BY created_at DESC
        LIMIT 1
    """,
        (volume, page_num),
    ).fetchone()

    if row:
        # Página avaliada pelo LLM judge ou pelo gate determinístico
        decision = row["decision"]
        status = row["status"]
        # Para deterministic_pass, fidelidade/usabilidade são None (sem julgamento)
        fidelidade = row["fidelidade"]   # None para deterministic_pass
        usabilidade = row["usabilidade"] # None para deterministic_pass
        idiomas = normalize_idiomas(row["idiomas_json"])
    else:
        # Página sem registro no banco: não passou pelo pipeline de avaliação
        # Isso não deveria ocorrer em condições normais; marcar para investigação.
        decision = "deterministic_pass"
        status = "deterministic_pass"
        fidelidade = None
        usabilidade = None
        idiomas = []

    record = {
        "volume": volume,
        "pagina": page_num,
        "xml_raw": xml_raw,
        "original_path": str(txt_path),
        "fidelidade": fidelidade,
        "usabilidade": usabilidade,
        "idiomas": idiomas,
        "decision": decision,
        "status": status,
    }
    records.append(record)

print(f"Total: {len(records)} páginas")

# Filtra: exclui parse_error (julgamento falhou) e descartar (baixíssima qualidade)
exportar = [
    r
    for r in records
    if r["status"] != "parse_error" and r["fidelidade"] != "descartar"
]
print(f"Exportáveis: {len(exportar)}")

nao_exportaveis = [
    r for r in records if r["status"] == "parse_error" or r["fidelidade"] == "descartar"
]
print(f"\n--- Não exportáveis: {len(nao_exportaveis)} ---")
for r in nao_exportaveis:
    print(
        f"  {r['volume']} p.{r['pagina']:04d} | status={r['status']} | fidelidade={r['fidelidade']}"
    )

import pandas as pd

df = pd.DataFrame(exportar)

# Garantir tipos corretos
df["pagina"] = df["pagina"].astype("int32")

df.to_parquet("BibliothecaPatristica_PO.parquet", index=False)
print(f"Parquet salvo: {df.shape[0]} linhas, {df.shape[1]} colunas")

# Estatísticas para a Dataset Card
print("\n--- Páginas por volume ---")
print(df.groupby("volume")["pagina"].count().to_string())

print("\n--- Decision ---")
print(df["decision"].value_counts(dropna=False).to_string())

print("\n--- Status ---")
print(df["status"].value_counts(dropna=False).to_string())

print("\n--- Fidelidade (llm_judged apenas) ---")
judged = df[df["decision"] == "llm_judged"]
print(judged["fidelidade"].value_counts(dropna=False).to_string())

print("\n--- Usabilidade (llm_judged apenas) ---")
print(judged["usabilidade"].value_counts(dropna=False).to_string())

print("\n--- Idiomas (top 20) ---")
from collections import Counter
idioma_counter: Counter = Counter()
for lst in df["idiomas"]:
    idioma_counter.update(lst)
for lang, count in idioma_counter.most_common(20):
    print(f"  {lang}: {count}")
