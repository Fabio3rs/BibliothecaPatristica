import sqlite3
import os
from pathlib import Path

def get_missing_but_available():
    con = sqlite3.connect("data/patristica_resumos.db")
    con.row_factory = sqlite3.Row
    
    docs_with_gaps = con.execute("""
        SELECT documento, MIN(pagina_num) as min_pg, MAX(pagina_num) as max_pg
        FROM resumos 
        GROUP BY documento 
        HAVING (MAX(pagina_num) - MIN(pagina_num) + 1) - COUNT(*) > 0
    """).fetchall()
    
    root = Path("teste")
    total_found_in_disk = 0
    
    for doc in docs_with_gaps:
        doc_name = doc["documento"]
        min_pg = doc["min_pg"]
        max_pg = doc["max_pg"]
        
        pages = [row["pagina_num"] for row in con.execute(
            "SELECT pagina_num FROM resumos WHERE documento = ? ORDER BY pagina_num",
            (doc_name,)
        ).fetchall()]
        
        gaps = set(range(min_pg, max_pg + 1)) - set(pages)
        
        # Agora vamos checar se esses arquivos existem no disco
        doc_dir = root / doc_name / "text"
        if not doc_dir.exists():
            continue
            
        real_gaps_to_process = []
        for p in gaps:
            # Buscar arquivo com este sufixo numérico
            # Normalmente "-p.txt" ou "\d.txt"
            matching_files = list(doc_dir.glob(f"*-{p}.txt")) + list(doc_dir.glob(f"*{p}.txt"))
            
            # Filtro básico: certificar que termina exatamente com a página
            found = False
            for f in matching_files:
                if f.name.endswith(f"-{p}.txt") or f.name.endswith(f"_{p}.txt") or f.name.endswith(f"{p}.txt"):
                    # Mas cuidar com falsos positivos tipo 1234.txt qdo busca 34
                    # Uma checagem regexp simples:
                    import re
                    if re.search(rf"[^0-9]?{p}\.txt$", f.name):
                        found = True
                        break
            
            if found:
                real_gaps_to_process.append(p)
                
        if real_gaps_to_process:
            print(f"{doc_name}: {len(real_gaps_to_process)} páginas no disco prontas pra processar -> {', '.join(map(str, sorted(real_gaps_to_process)))}")
            total_found_in_disk += len(real_gaps_to_process)
            
    print(f"\nTotal de gaps que PODEM ser processados pois os arquivos existem: {total_found_in_disk}")

get_missing_but_available()
