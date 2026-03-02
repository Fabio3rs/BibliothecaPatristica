#!/usr/bin/env python3
import sqlite3
import subprocess
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import re

def process_doc(doc_name, idx, total):
    print(f"[{idx}/{total}] Solicitando gaps em {doc_name}...")
    
    # Redireciona a tela do log para um arquivo específico deste documento
    log_dir = Path("logs/fix_gaps")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{doc_name}.log"
    
    cmd = [
        "python3", "resumo_serial.py",
        "--volume-dir", f"teste/{doc_name}",
        "--fill-gaps",
        "--provider", "openai",
        "--model", "gpt-5-mini",
        "--reasoning-effort", "low"
    ]
    
    try:
        # Pega a saída e escreve direto em arquivo para acompanhar em tempo real (ex: tail -f)
        with open(log_file, "w") as f:
            subprocess.run(cmd, check=True, stdout=f, stderr=f)
        return (doc_name, True, f"Log salvo em: {log_file}")
    except subprocess.CalledProcessError as e:
        return (doc_name, False, f"Falha (Log em: {log_file})")

def run_fix_gaps(workers_count):
    print("Mapeando documentos que precisam de preenchimento de gaps...")
    con = sqlite3.connect("data/patristica_resumos.db")
    con.row_factory = sqlite3.Row
    
    docs_with_gaps = con.execute("""
        SELECT documento, MIN(pagina_num) as min_pg, MAX(pagina_num) as max_pg
        FROM resumos 
        GROUP BY documento 
        HAVING (MAX(pagina_num) - MIN(pagina_num) + 1) - COUNT(*) > 0
    """).fetchall()
    
    root = Path("teste")
    target_docs = []
    
    for doc in docs_with_gaps:
        doc_name = doc["documento"]
        min_pg = doc["min_pg"]
        max_pg = doc["max_pg"]
        doc_dir = root / doc_name / "text"
        
        if not doc_dir.exists():
            continue
            
        # Busca páginas processadas
        pages = [row["pagina_num"] for row in con.execute(
            "SELECT pagina_num FROM resumos WHERE documento = ? ORDER BY pagina_num",
            (doc_name,)
        ).fetchall()]
        
        # Verifica se há pelo menos um gap real (arquivo existe e é > 0 bytes)
        has_real_gap = False
        for p in range(min_pg, max_pg + 1):
            if p not in pages:
                matching_files = list(doc_dir.glob(f"*-{p}.txt")) + list(doc_dir.glob(f"*{p}.txt"))
                for f in matching_files:
                    if re.search(rf"[^0-9]?{p}\.txt$", f.name):
                        if f.stat().st_size > 0:
                            has_real_gap = True
                        break
            if has_real_gap:
                break
                
        if has_real_gap:
            target_docs.append(doc_name)
    
    if not target_docs:
        print("Nenhum gap que tenha arquivo real (e maior que 0 bytes) detectado no disco.")
        return
        
    total = len(target_docs)
    print(f"Iremos preencher gaps para {total} documentos usando {workers_count} rotinas em paralelo...")
    
    # ThreadPool Executor em ação!
    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {executor.submit(process_doc, doc_name, i+1, total): doc_name for i, doc_name in enumerate(target_docs)}
        
        for future in as_completed(futures):
            doc_name, success, log_out = future.result()
            if success:
                print(f"[OK] {doc_name} concluído com sucesso! ({log_out})")
            else:
                print(f"[ERRO] Falha em {doc_name}:\n{log_out}")
            
    print("\nTodos os processos de fill-gaps foram concluídos.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10, help="Quantidade de volumes para preencher simultaneamente.")
    args = parser.parse_args()
    
    run_fix_gaps(args.workers)
