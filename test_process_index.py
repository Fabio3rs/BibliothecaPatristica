#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script de teste para demonstrar o funcionamento do mapeamento de process index
"""

import os
import sys
import multiprocessing as mp
from pathlib import Path

# Adicionar o diretório atual ao path para importar main2
sys.path.append(str(Path(__file__).parent))

from main2 import apply_to_env_by_process_index, _init_omp_env, get_current_process_index

def test_worker(task_id):
    """Função de teste que demonstra o uso do process index"""
    pid = os.getpid()
    process_index = get_current_process_index()
    omp_places = os.environ.get('OMP_PLACES', 'not set')
    
    print(f"Task {task_id}: PID={pid}, ProcessIndex={process_index}, OMP_PLACES={omp_places}")
    
    # Simular algum trabalho
    import time
    time.sleep(1)
    
    return f"Task {task_id} completed by process {process_index} (PID: {pid})"


def main():
    print("=== Teste do Sistema de Process Index ===")
    print(f"Processo principal PID: {os.getpid()}")
    
    # Teste com diferentes números de processos
    num_processes = 4
    num_tasks = 8
    
    print(f"\nCriando pool com {num_processes} processos para {num_tasks} tarefas...")
    
    # Inicializar variáveis globais (mesmo padrão do main2.py)
    import main2
    with main2._process_counter_lock:
        main2._process_counter.value = 0
        main2._process_index_map.clear()
    
    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    
    with ctx.Pool(
        processes=num_processes,
        initializer=_init_omp_env,
        initargs=(2,),  # 2 threads OpenMP por processo
    ) as pool:
        
        # Executar tarefas
        results = pool.map(test_worker, range(num_tasks))
        
        print("\n=== Resultados ===")
        for result in results:
            print(result)
    
    print("\n=== Teste de Mapeamento de Places ===")
    for i in range(6):
        from main2 import return_place_by_process_index
        place = return_place_by_process_index(i)
        print(f"Process index {i} -> OMP_PLACES = {place}")


if __name__ == "__main__":
    main()
