import sqlite3
import os
from pathlib import Path

from tools.corpus_utils import discover_unique_page_map

def find_gaps():
    con = sqlite3.connect("data/patristica_resumos.db")
    con.row_factory = sqlite3.Row
    
    # Pegar todos os documentos que sabemos que tem gap
    docs_with_gaps = con.execute("""
        SELECT documento, MIN(pagina_num) as min_pg, MAX(pagina_num) as max_pg
        FROM resumos 
        GROUP BY documento 
        HAVING (MAX(pagina_num) - MIN(pagina_num) + 1) - COUNT(*) > 0
    """).fetchall()
    
    for doc in docs_with_gaps:
        doc_name = doc["documento"]
        min_pg = doc["min_pg"]
        max_pg = doc["max_pg"]
        
        # Pega todas as páginas processadas desse doc
        pages = [row["pagina_num"] for row in con.execute(
            "SELECT pagina_num FROM resumos WHERE documento = ? ORDER BY pagina_num",
            (doc_name,)
        ).fetchall()]
        
        doc_dir = Path("teste") / doc_name / "text"
        disk_pages = (
            discover_unique_page_map(doc_dir, volume_id=doc_name)
            if doc_dir.is_dir()
            else {}
        )
        
        gaps = []
        zero_bytes = []
        
        for p in range(min_pg, max_pg + 1):
            if p not in pages:
                # Vamos verificar se o arquivo existe e se é zero bytes 
                # (já pulado intencionalmente)
                is_zero_byte = False
                
                page_file = disk_pages.get(p)
                if page_file is not None and page_file.stat().st_size == 0:
                    is_zero_byte = True
                            
                if is_zero_byte:
                    zero_bytes.append(p)
                else:
                    gaps.append(p)
                
        # Mostrar as intencionalmente puladas em cinza ou como info se houver
        if zero_bytes:
            from itertools import groupby
            z_ranges = []
            for k, g in groupby(enumerate(zero_bytes), lambda x: x[0]-x[1]):
                group = list(map(lambda x: x[1], g))
                if len(group) == 1:
                    z_ranges.append(str(group[0]))
                else:
                    z_ranges.append(f"{group[0]}-{group[-1]}")
            # print(f"  [{doc_name} INFO] {len(zero_bytes)} pgs vazias (0 bytes) puladas intencionalmente: {', '.join(z_ranges)}")
                
        if gaps:
            # Agrupar gaps consecutivos
            from itertools import groupby
            ranges = []
            for k, g in groupby(enumerate(gaps), lambda x: x[0]-x[1]):
                group = list(map(lambda x: x[1], g))
                if len(group) == 1:
                    ranges.append(str(group[0]))
                else:
                    ranges.append(f"{group[0]}-{group[-1]}")
                    
            print(f"{doc_name}: faltam {len(gaps)} páginas -> {', '.join(ranges)}")
            if zero_bytes:
                 print(f"    (Ignoradas {len(zero_bytes)} páginas de 0 bytes: {', '.join(z_ranges)})")

if __name__ == "__main__":
    find_gaps()
