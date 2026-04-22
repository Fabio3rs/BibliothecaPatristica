import pandas as pd
import xml.etree.ElementTree as ET
from xml.dom import minidom

def split_merged_blocks(df, gap_threshold=100):
    """
    Detecta blocos que cruzam o meio da página e os divide 
    se houver um espaço (gap) horizontal significativo.
    """
    # 1. Definir o centro aproximado da página (tenta usar level==1, senão faz fallback)
    level1 = df[df['level'] == 1]
    if not level1.empty:
        page_width = level1['width'].iloc[0]
    else:
        # Fallback: usa a bbox máxima das palavras/elementos disponíveis
        if not df.empty:
            page_width = int((df['left'] + df['width']).max())
        else:
            page_width = 0
    center_x = page_width / 2 if page_width else 0
    
    new_blocks = []
    
    # Filtramos apenas as palavras para análise
    words = df[df['level'] == 5].copy()

    # Agrupamos pelo block_num original do Tesseract
    for b_num, group in words.groupby('block_num'):
        # Ordena palavras pela posição 'left'
        group = group.sort_values('left')
        
        # Calcula o espaço entre o fim de uma palavra e o início da próxima
        group['gap_to_next'] = group['left'].shift(-1) - (group['left'] + group['width'])
        
        # Procura um gap que esteja próximo ao centro da página
        mask_split = (group['gap_to_next'] > gap_threshold) & \
                     (group['left'] < center_x) & \
                     ((group['left'] + group['width'] + group['gap_to_next']) > center_x)
        
        if mask_split.any():
            # Encontrou o ponto de divisão (o maior gap central)
            split_idx = group[mask_split]['gap_to_next'].idxmax()
            pos_na_lista = group.index.get_loc(split_idx)
            
            col_a = group.iloc[:pos_na_lista + 1]
            col_b = group.iloc[pos_na_lista + 1:]
            
            new_blocks.append(formatar_bloco_xml(col_a, "coluna_A"))
            new_blocks.append(formatar_bloco_xml(col_b, "coluna_B"))
        else:
            # Bloco normal, não precisa de split
            new_blocks.append(formatar_bloco_xml(group, "corpo"))
            
    return new_blocks

def formatar_bloco_xml(group, tipo):
    # Calcula a BBox externa do novo grupo de palavras
    x1 = int(group['left'].min())
    y1 = int(group['top'].min())
    x2 = int((group['left'] + group['width']).max())
    y2 = int((group['top'] + group['height']).max())
    
    texto = " ".join(group['text'].dropna().astype(str))
    return f'<bloco tipo="{tipo}" bbox="{x1},{y1},{x2},{y2}">{texto}</bloco>'


def gerar_xml_ocr(caminho_tsv):
    # Carrega o arquivo TSV
    df = pd.read_csv(caminho_tsv, sep='\t')

    # Cria o elemento raiz
    pagina = ET.Element("pagina", estado="com_texto")

    # Filtra apenas os blocos (level 2)
    blocos = df[df['level'] == 2]

    for _, row_bloco in blocos.iterrows():
        b_num = row_bloco['block_num']
        
        # Coordenadas da BBox: x1, y1, x2, y2
        # Onde x2 = left + width e y2 = top + height
        x1, y1 = int(row_bloco['left']), int(row_bloco['top'])
        x2 = x1 + int(row_bloco['width'])
        y2 = y1 + int(row_bloco['height'])
        bbox_str = f"{x1},{y1},{x2},{y2}"

        # Recupera as palavras (level 5) pertencentes a este bloco específico
        palavras = df[(df['level'] == 5) & (df['block_num'] == b_num)]
        
        # Concatena o texto das palavras com espaços, removendo valores nulos
        texto_transcrito = " ".join(palavras['text'].dropna().astype(str))

        # Cria o elemento <bloco>
        # Nota: 'tipo' e 'script' exigem lógica adicional ou heurística para preenchimento
        bloco_el = ET.SubElement(pagina, "bloco", 
                                 # tipo="corpo", 
                                 # script="latim", 
                                 bbox=bbox_str)
        bloco_el.text = texto_transcrito

    # Adiciona a tag de notas
    notas = ET.SubElement(pagina, "notas")
    notas.text = "Processamento realizado via script Python. Scripts complexos não detectados automaticamente."

    # Formatação para string XML legível (Pretty Print)
    xml_string = ET.tostring(pagina, encoding='utf-8')
    reparsed = minidom.parseString(xml_string)
    return reparsed.toprettyxml(indent="  ")


import pandas as pd
import xml.etree.ElementTree as ET
from xml.dom import minidom

def analisar_layout_duplo_passe(caminho_tsv):
    df = pd.read_csv(caminho_tsv, sep='\t')
    
    # Ignora linhas vazias ou sem confiança
    df = df[df['conf'] != -1]
    
    # ---------------------------------------------------------
    # PASSE 1: Análise Global da Página
    # ---------------------------------------------------------
    # Tenta obter dimensões da página a partir do registro level==1.
    # Se não existir (muitos TSVs do Tesseract não fornecem esse nível),
    # faz fallback calculando a bbox máxima a partir de todos os elementos.
    level1 = df[df['level'] == 1]
    if not level1.empty:
        page_width = level1['width'].iloc[0]
        page_height = level1['height'].iloc[0]
    else:
        if not df.empty:
            page_width = int((df['left'] + df['width']).max())
            page_height = int((df['top'] + df['height']).max())
        else:
            # Valores conservadores para evitar divisão por zero
            page_width = 1000
            page_height = 1000
    centro_x = page_width / 2
    
    # Limiar para considerar se algo está no "topo" da página (ex: primeiros 15%)
    topo_threshold = page_height * 0.15 
    
    # ---------------------------------------------------------
    # PASSE 2: Classificação e Splitting
    # ---------------------------------------------------------
    pagina_xml = ET.Element("pagina", estado="com_texto")
    palavras = df[df['level'] == 5].copy()
    # estatísticas úteis para heurísticas
    median_word_h = int(palavras['height'].median()) if not palavras.empty else 12

    # coletar blocos primeiro para depois escrever em ordem de leitura
    blocos_coletados = []

    for b_num, group in palavras.groupby('block_num'):
        if group.empty:
            continue

        x1 = int(group['left'].min())
        y1 = int(group['top'].min())
        x2 = int((group['left'] + group['width']).max())
        y2 = int((group['top'] + group['height']).max())

        cruza_o_centro = (x1 < centro_x) and (x2 > centro_x)
        esta_no_topo = y1 < topo_threshold

        texto_bloco = " ".join(group['text'].dropna().astype(str))

        # Lógica de Classificação (aqui apenas coletamos os blocos)
        # Heurística de cabeçalho melhorada:
        # - rejeita blocos muito altos (provavelmente corpo)
        # - detecta padrões de header: maioria de caracteres maiúsculos / acrônimos
        bbox_width = x2 - x1
        bbox_height = y2 - y1
        centro_bloco = (x1 + x2) / 2.0
        texto_stripped = texto_bloco.strip()
        texto_len = len(texto_stripped)

        def is_header_like(text):
            if not text:
                return False
            # tokens típicos e acrônimos
            tokens = ['EDIT', 'NOTAE', 'LIBER', 'DIGITIZED', 'OXON', 'PAGE', 'PAG', 'DIGITIZED BY', 'DIGITISED']
            up = sum(1 for c in text if c.isupper())
            letters = sum(1 for c in text if c.isalpha())
            up_ratio = (up / letters) if letters > 0 else 0
            # exigir maior razão de maiúsculas para reduzir falsos positivos
            if up_ratio > 0.75 and letters >= 2:
                return True
            tu = text.upper()
            for t in tokens:
                if t in tu:
                    return True
            # siglas pontuadas como 'E D I T.' -> detecta ponto interior e maiúsculas
            if any(part.isupper() and len(part) <= 4 for part in ''.join(ch if ch.isalpha() or ch.isspace() else ' ' for ch in text).split()):
                return True
            return False

        # condição base: bloco pequeno em altura (próximo da altura de linha) e texto curto
        # reduzir tolerância para evitar classificar parágrafos curtos como cabeçalho
        small_height = bbox_height <= max(1.2 * median_word_h, int(page_height * 0.05))

        # aceita cabeçalho se centralizado no topo e largura moderada
        if y1 < topo_threshold and texto_len > 0:
            if abs(centro_bloco - centro_x) < (page_width * 0.18) and bbox_width < (page_width * 0.6) and small_height:
                blocos_coletados.append({'tipo': 'cabecalho', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})
                continue
            # pequenos blocos no topo que sejam header-like
            if small_height and texto_len <= 14 and is_header_like(texto_stripped):
                blocos_coletados.append({'tipo': 'cabecalho', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})
                continue
            # margem: número de página (muito curto) — encaixado na regra mas exige small_height
            if small_height and texto_len <= 6 and bbox_width < (page_width * 0.2):
                blocos_coletados.append({'tipo': 'cabecalho', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})
                continue

        # aceitar também rodapés/headers pequenos fora do topo se claramente 'header-like'
        near_bottom = (page_height - y2) < (page_height * 0.08)
        if (near_bottom or texto_len <= 6) and small_height and is_header_like(texto_stripped):
            blocos_coletados.append({'tipo': 'cabecalho', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})
            continue

        # regra adicional: pequenos blocos 'header-like' em qualquer posição podem ser cabeçalho
        # mas evitar blocos largos ou altos
        if small_height and is_header_like(texto_stripped) and bbox_width < (page_width * 0.5) and texto_len <= 30:
            blocos_coletados.append({'tipo': 'cabecalho', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})
            continue
        if cruza_o_centro and esta_no_topo:
            # bloco cruza o centro e está no topo: pode ser cabeçalho curto ou fusão de colunas
            col_a, col_b = tentar_dividir_colunas(group, centro_x)
            if col_a is not None:
                blocos_coletados.append({'tipo': 'cabecalho', 'bbox': obter_bbox(col_a), 'texto': obter_texto(col_a), 'y': int(col_a['top'].min())})
                blocos_coletados.append({'tipo': 'cabecalho', 'bbox': obter_bbox(col_b), 'texto': obter_texto(col_b), 'y': int(col_b['top'].min())})
            else:
                # se for um bloco muito alto (provavelmente corpo), não rotular como cabeçalho
                if bbox_height > (page_height * 0.25):
                    blocos_coletados.append({'tipo': 'corpo_largo', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})
                else:
                    blocos_coletados.append({'tipo': 'cabecalho', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})

        elif cruza_o_centro and not esta_no_topo:
            # Erro do OCR: fundiu duas colunas no corpo do texto. Tentamos dividir.
            col_a, col_b = tentar_dividir_colunas(group, centro_x)
            if col_a is not None:
                blocos_coletados.append({'tipo': 'coluna_A', 'bbox': obter_bbox(col_a), 'texto': obter_texto(col_a), 'y': int(col_a['top'].min())})
                blocos_coletados.append({'tipo': 'coluna_B', 'bbox': obter_bbox(col_b), 'texto': obter_texto(col_b), 'y': int(col_b['top'].min())})
            else:
                blocos_coletados.append({'tipo': 'corpo_largo', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})

        elif x2 < centro_x:
            # totalmente à esquerda
            blocos_coletados.append({'tipo': 'coluna_A', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})

        elif x1 > centro_x:
            # totalmente à direita
            blocos_coletados.append({'tipo': 'coluna_B', 'bbox': f"{x1},{y1},{x2},{y2}", 'texto': texto_bloco, 'y': y1})

    # agora escreve os blocos em ordem de leitura: cabeçalhos, coluna esquerda, coluna direita, outros
    escrever_blocos_em_ordem(pagina_xml, blocos_coletados)

    # Formatação XML
    xml_string = ET.tostring(pagina_xml, encoding='utf-8')
    return minidom.parseString(xml_string).toprettyxml(indent="  ")

# Funções Auxiliares
def adicionar_bloco_xml(parent, tipo, bbox, texto):
    el = ET.SubElement(parent, "bloco", tipo=tipo, bbox=bbox)
    el.text = texto

def obter_bbox(group):
    x1, y1 = int(group['left'].min()), int(group['top'].min())
    x2, y2 = int((group['left'] + group['width']).max()), int((group['top'] + group['height']).max())
    return f"{x1},{y1},{x2},{y2}"

def obter_texto(group):
    return " ".join(group['text'].dropna().astype(str))

def tentar_dividir_colunas(group, centro_x, gap_threshold=80):
    """ Tenta encontrar o 'vácuo' (gap) central para separar o dataframe em dois """
    group = group.sort_values('left').copy()
    group['gap'] = group['left'].shift(-1) - (group['left'] + group['width'])
    
    # Procura gaps que aconteçam perto do centro_x
    possiveis_splits = group[(group['gap'] > gap_threshold) & 
                             (group['left'] < centro_x) & 
                             ((group['left'] + group['width'] + group['gap']) > centro_x)]
    
    if not possiveis_splits.empty:
        idx_split = possiveis_splits['gap'].idxmax()
        pos = group.index.get_loc(idx_split)
        return group.iloc[:pos + 1], group.iloc[pos + 1:]
    # --- Fallback 1: tentar detectar um split consistente por linhas (gaps verticais)
    split_x = detectar_split_por_linhas(group, gap_multiplier=2.0, min_coverage=0.30, line_tol=10)
    if split_x is not None:
        # Atribui palavras que cruzam o split pela distância ao centro da palavra
        centers = (group['left'] + group['width'] / 2.0)
        left_grp = group[centers <= split_x].copy()
        right_grp = group[centers > split_x].copy()
        # Se um dos grupos estiver vazio, evita split inválido
        if not left_grp.empty and not right_grp.empty:
            return left_grp, right_grp

    # --- Fallback 2: se ainda não, particionar por 1D-clustering nos centros (antigo)
    if len(group) >= 2:
        centers = (group['left'] + group['width'] / 2.0).values
        # ordena e avalia cortes entre palavras consecutivas
        order = centers.argsort()
        centers_sorted = centers[order]
        n = len(centers_sorted)
        total_mean = centers_sorted.mean()

        best_score = -1.0
        best_pos = None
        for i in range(1, n):
            left_chunk = centers_sorted[:i]
            right_chunk = centers_sorted[i:]
            mu_l = left_chunk.mean()
            mu_r = right_chunk.mean()
            n_l = i
            n_r = n - i
            score = n_l * (mu_l - total_mean) ** 2 + n_r * (mu_r - total_mean) ** 2
            if score > best_score:
                best_score = score
                best_pos = i

        if best_pos is not None and best_pos > 0 and best_pos < n:
            # recupera a posição de split no dataframe ordenado por 'left'
            split_idx = group.sort_values('left').index[best_pos - 1]
            pos = group.index.get_loc(split_idx)
            return group.iloc[:pos + 1], group.iloc[pos + 1:]

    return None, None


def agrupar_por_linhas(palavras, line_tol=10):
    """Agrupa palavras em linhas aproximadas usando tolerância vertical.

    Retorna um array `line_id` com o mesmo índice de `palavras`.
    """
    if palavras.empty:
        return pd.Series(dtype=int)
    tops = palavras['top'].values
    order = tops.argsort()
    sorted_tops = tops[order]
    line_ids = [0] * len(sorted_tops)
    current_line = 0
    last_top = sorted_tops[0]
    for i, t in enumerate(sorted_tops):
        if abs(t - last_top) > line_tol:
            current_line += 1
            last_top = t
        line_ids[i] = current_line

    # map back to original index order
    s = pd.Series(index=palavras.index[order], data=line_ids).sort_index()
    return s


def detectar_split_por_linhas(group, gap_multiplier=2.0, min_coverage=0.3, line_tol=10, cross_frac_threshold=0.12):
    """Detecta um split vertical consistente analisando gaps por linha.

    Retorna coordenada x do split ou None.
    """
    palavras = group.copy()
    if palavras.empty:
        return None

    # atribui id de linha
    line_ids = agrupar_por_linhas(palavras, line_tol=line_tol)
    palavras = palavras.assign(line_id=line_ids)
    n_lines = int(line_ids.max()) + 1 if not line_ids.empty else 0
    if n_lines <= 1:
        return None

    # calcula mediana de largura para normalizar gaps
    median_w = palavras['width'].median() if not palavras['width'].empty else 1.0

    candidate_centers = []
    for lid, line in palavras.groupby('line_id'):
        line = line.sort_values('left')
        if len(line) < 2:
            continue
        rights = (line['left'] + line['width']).values
        lefts = line['left'].values
        gaps = lefts[1:] - rights[:-1]
        # considerar gaps relativos
        for i, g in enumerate(gaps):
            if g > gap_multiplier * median_w and g > 1:
                gap_start = rights[:-1][i]
                gap_end = lefts[1:][i]
                cx = (gap_start + gap_end) / 2.0
                # anotar centro por linha
                candidate_centers.append((lid, cx))

    if not candidate_centers:
        return None

    # agrupa centers por proximidade (bin de bin_size) e conta linhas únicas por bin
    bin_size = max(8, int(median_w))
    bins = {}
    for lid, c in candidate_centers:
        b = int(c // bin_size)
        if b not in bins:
            bins[b] = set()
        bins[b].add(lid)

    # encontra bin com maior número de linhas distintas
    best_bin, best_line_set = max(bins.items(), key=lambda kv: len(kv[1]))
    coverage = len(best_line_set) / float(n_lines) if n_lines > 0 else 0.0
    if coverage < min_coverage:
        return None

    # calcula split_x como média dos centros nas linhas que contribuíram para o melhor bin
    vals = [c for lid, c in candidate_centers if int(c // bin_size) == best_bin]
    split_x = sum(vals) / len(vals)

    # verifica fração de palavras que cruzam esse x
    crosses = palavras[(palavras['left'] < split_x) & ((palavras['left'] + palavras['width']) > split_x)]
    cross_frac = len(crosses) / float(len(palavras))
    if cross_frac > cross_frac_threshold:
        return None

    return split_x


def escrever_blocos_em_ordem(parent_el, blocos):
    """Ordena e escreve blocos no elemento XML `parent_el` em ordem de leitura.

    Regras:
    - primeiro todos os `cabecalho` na ordem de Y ascendente (top->down)
    - depois `coluna_A` por Y ascendente
    - depois `coluna_B` por Y ascendente
    - por fim outros (ex: corpo_largo) por Y ascendente
    """
    if not blocos:
        return

    # função auxiliar para filtrar e ordenar
    def filtra_ordena(tipo):
        f = [b for b in blocos if b.get('tipo') == tipo]
        return sorted(f, key=lambda x: x.get('y', 0))

    # cabeçalhos centralizados primeiro
    for b in filtra_ordena('cabecalho'):
        el = ET.SubElement(parent_el, 'bloco', tipo='cabecalho', bbox=b['bbox'])
        el.text = b['texto']

    # coluna esquerda
    for b in filtra_ordena('coluna_A'):
        el = ET.SubElement(parent_el, 'bloco', tipo='coluna_A', bbox=b['bbox'])
        el.text = b['texto']

    # coluna direita
    for b in filtra_ordena('coluna_B'):
        el = ET.SubElement(parent_el, 'bloco', tipo='coluna_B', bbox=b['bbox'])
        el.text = b['texto']

    # outros
    others = [b for b in blocos if b.get('tipo') not in ('cabecalho', 'coluna_A', 'coluna_B')]
    for b in sorted(others, key=lambda x: x.get('y', 0)):
        el = ET.SubElement(parent_el, 'bloco', tipo=b.get('tipo', 'outro'), bbox=b['bbox'])
        el.text = b.get('texto', '')

# Execução
xml_final = gerar_xml_ocr('teste.tsv.tsv')
print(xml_final)


teste = analisar_layout_duplo_passe('teste.tsv.tsv')
print(teste)

