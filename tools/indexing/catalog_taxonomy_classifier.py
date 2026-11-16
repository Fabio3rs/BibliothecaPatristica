#!/usr/bin/env python3
"""
tools/indexing/catalog_taxonomy_classifier.py

Classificador taxonômico e ontológico em duas camadas para o catálogo onomástico unificado:
  - Camada 1: Categoria Macro do Índice (index_category) & Seção Padronizada (section)
    AUCTORES, SCRIPTURA, RERUM_ET_SENTENTIARUM, LEXICA, CHRONOLOGICA,
    CAPITULA_ET_CANONES, ONOMASTICA, INDEX_GENERALIS.
    Baseado em leitura dos cabeçalhos dos 2.731 arquivos-fonte de OCR com propagação
    bidirecional em páginas espelhadas / contíguas na impressão Migne.

  - Camada 2: Tipo de Entidade Ontológica do Verbete (entity_type)
    AUCTOR_CLASSICUS: Filósofos, poetas, historiadores e escritores clássicos pagãos da Antiguidade
                     (Aristóteles, Platão, Sócrates, Homero, Virgílio, Cícero, Sêneca, Plutarco, Galeno...)
    AUCTOR_PATRISTICUS: Padres da Igreja, doutores e escritores eclesiásticos cristãos
                       (Agostinho, Ambrósio, Jerônimo, Crisóstomo, Basílio, Gregório, Tertuliano, Cipriano...)
    LOCUS_SCRIPTURAE: Livros bíblicos, citações, versículos e perícopas
                     (Gênesis, Salmos, Isaías, Mateus, Romanos, Gen. 1, 3, Ps. 18...)
    PERSONA_BIBLICA: Personagens bíblicos do Antigo e Novo Testamento
                    (Adão, Abraão, Moisés, Davi, Salomão, Elias, Pedro apóstolo, Paulo apóstolo, Maria...)
    TEMA_DOGMATICO: Sentenças, verbetes temáticos, doutrinais e teológicos
                   (De Deo, De Trinitate, De Gratia, De Baptismo, De Incarnatione, Fides, Sacramentum...)
    VOCABULUM_GRAECUM: Verbetes e termos em grego (alfabeto grego / índices de grecidade)
    TOPONYMUS: Geografia antiga, cidades, regiões, rios, montes e sedes patriarcais
              (Roma, Constantinopla, Alexandria, Antioquia, Cartago, Jerusalém, Sinai, Nilo...)
    ONOMASTICA_HISTORICA: Imperadores, reis, papas, bispos, mártires, concílios, mosteiros e verbetes históricos gerais.

Banco autorizado: data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite
"""

import os
import sys
import re
import json
import sqlite3
import argparse
import time
from collections import Counter
from typing import Dict, Tuple, List, Optional, Any, Set

DEFAULT_DB = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
DEFAULT_SUMMARY = "data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json"

# ==============================================================================
# CAMADA 1: CABEÇALHOS DE PÁGINAS E CATEGORIAS MACRO DE ÍNDICE
# ==============================================================================

MACRO_CATEGORIES = {
    "AUCTORES",
    "SCRIPTURA",
    "RERUM_ET_SENTENTIARUM",
    "LEXICA",
    "CHRONOLOGICA",
    "CAPITULA_ET_CANONES",
    "ONOMASTICA",
    "INDEX_GENERALIS",
}

def extract_page_num(path: str) -> int:
    m = re.search(r'-(\d+)\.txt$', path)
    return int(m.group(1)) if m else -1

def clean_header_text(raw_text: str) -> str:
    headers = re.findall(r'<bloco tipo=[\"\']cabecalho[\"\'][^>]*>(.*?)</bloco>', raw_text, flags=re.DOTALL)
    if headers:
        combined = ' '.join(' '.join(h.split()) for h in headers)
    else:
        combined = raw_text
    # Remove colunas e números isolados
    combined = re.sub(r'\b\d+\b', '', combined)
    # Normalizar ligaduras latinas
    combined = combined.replace('Æ', 'AE').replace('æ', 'ae').replace('Œ', 'OE').replace('œ', 'oe')
    # Remove pontuação, preservando caracteres gregos e latinos
    clean = re.sub(r'[^\w\s\u0370-\u03ff\u1f00-\u1fff]', ' ', combined)
    return ' '.join(clean.split()).upper()

def identify_specific_header(hdr: str, volume: str) -> Tuple[Optional[str], Optional[str]]:
    h = hdr.upper().replace('Æ', 'AE').replace('Œ', 'OE')
    if any(k in h for k in ['AUCTOR', 'ELENCHUS AUCTORUM', 'SCRIPTOR', 'PATRES', 'POETARUM GENTILIUM']):
        if 'AUCTORUM VETERUM' in h:
            return 'AUCTORES', 'INDEX AUCTORUM VETERUM'
        if 'AUCTORUM RECENTIORUM' in h:
            return 'AUCTORES', 'INDEX AUCTORUM RECENTIORUM'
        if 'ALPHABETICUS AUCTORUM' in h or 'INDEX ALPHABETICUS AUCTORUM' in h:
            return 'AUCTORES', 'INDEX ALPHABETICUS AUCTORUM'
        if 'CHRONOLOGICUS AUCTORUM' in h:
            return 'AUCTORES', 'INDEX CHRONOLOGICUS AUCTORUM'
        if 'POETARUM' in h or 'ETHNIC' in h or 'PROFANIS' in h:
            return 'AUCTORES', 'INDEX SCRIPTORUM ETHNICORUM'
        return 'AUCTORES', 'INDEX AUCTORUM'
    if any(k in h for k in ['SCRIPTUR', 'BIBLIC', 'TESTAMENT', 'LOCORUM EX SCRIPTURA']):
        return 'SCRIPTURA', 'INDEX SCRIPTURAE SACRAE'
    if 'INDEX VERBORUM' in h and 'RERUM' not in h:
        return 'LEXICA', 'INDEX VERBORUM'
    if any(k in h for k in ['GRAECITAT', 'VOCABUL', 'GLOSSAR', 'LEXIC', 'VERBORUM GRAEC']):
        return 'LEXICA', 'INDEX VOCABULORUM GRAECORUM' if volume.startswith('PG') else 'INDEX VERBORUM'
    if any(k in h for k in ['CHRONOLOG', 'ANNALES', 'FASTI', 'SERIES PONTIFICUM', 'SERIES EPISCOP', 'TEMPORUM', 'SYNCHRONISM']):
        return 'CHRONOLOGICA', 'INDEX CHRONOLOGICUS'
    if any(k in h for k in ['CANON', 'CAPITUL', 'DECRET', 'CONCIL', 'TITUL']):
        return 'CAPITULA_ET_CANONES', 'INDEX CAPITULORUM ET CANONUM'
    if any(k in h for k in ['ONOMASTIC', 'NOMINUM', 'PROPRIORUM', 'HISTORIC']):
        if 'ONOMASTICUM DIONYSIANUM' in h:
            return 'ONOMASTICA', 'ONOMASTICUM DIONYSIANUM'
        return 'ONOMASTICA', 'INDEX ONOMASTICUS'
    if any(k in h for k in ['RERUM ET VERBORUM', 'RERUM ET SENTENTIARUM', 'RERUM AC VERBORUM', 'RERUM NOTABILIUM', 'ANALYTIC', 'METHODIC', 'RERUM', 'SENTENTIAR']):
        if 'ANALYTIC' in h:
            return 'RERUM_ET_SENTENTIARUM', 'INDEX ANALYTICUS'
        if 'METHODIC' in h:
            return 'RERUM_ET_SENTENTIARUM', 'INDEX METHODICUS'
        if 'SENTENTIAR' in h:
            return 'RERUM_ET_SENTENTIARUM', 'INDEX RERUM ET SENTENTIARUM'
        return 'RERUM_ET_SENTENTIARUM', 'INDEX RERUM ET VERBORUM'
    if 'PATRISTICO THEOLOGICUS' in h:
        return 'RERUM_ET_SENTENTIARUM', 'INDEX PATRISTICO THEOLOGICUS'
    if volume.startswith('PG') and any(0x0370 <= ord(c) <= 0x1fff for c in h):
        return 'LEXICA', 'INDEX VOCABULORUM GRAECORUM'
    return None, None

def identify_generic_header(hdr: str) -> Tuple[str, str]:
    return 'INDEX_GENERALIS', 'INDEX GENERALIS'

def parse_bbox_y(attribs: str) -> int:
    m = re.search(r'bbox=[\"\']([0-9]+),([0-9]+),([0-9]+),([0-9]+)[\"\']', attribs)
    return int(m.group(2)) if m else 0

class SourceFileClassifier:
    """Classifica os arquivos de origem com propagação bidirecional de cabeçalho e segmentação intra-página."""
    
    def __init__(self, sources_by_volume: Dict[str, List[str]]):
        self.sources_by_volume = sources_by_volume
        self.classification_map: Dict[str, Tuple[str, str]] = {}
        self.intra_sections_map: Dict[str, List[Tuple[int, Tuple[str, str]]]] = {}
        self._build_map()

    def _build_map(self):
        for vol, srcs in self.sources_by_volume.items():
            sorted_srcs = sorted(srcs, key=extract_page_num)
            raw_hdrs = {}
            specs = {}
            file_inner_headers = {}

            for s in sorted_srcs:
                content = ""
                if os.path.exists(s):
                    try:
                        with open(s, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                    except Exception:
                        content = ""

                matches = list(re.finditer(r'<bloco\s+tipo=[\"\']cabecalho[\"\']([^>]*)>(.*?)</bloco>', content, re.DOTALL))
                if matches:
                    top_text = ' '.join(matches[0].group(2).split())
                    raw_hdrs[s] = clean_header_text(top_text)
                    specs[s] = identify_specific_header(raw_hdrs[s], vol)

                    inner_headers = []
                    for m in matches[1:]:
                        y = parse_bbox_y(m.group(1))
                        if y > 150:
                            m_clean = clean_header_text(m.group(2))
                            m_spec = identify_specific_header(m_clean, vol)
                            if m_spec[0] is not None:
                                inner_headers.append((m.start(), m_spec))
                    if inner_headers:
                        file_inner_headers[s] = inner_headers
                else:
                    raw_hdrs[s] = ''
                    specs[s] = (None, None)

            # Propagação direta para páginas subsequentes próximas (<= 2 páginas)
            last_spec = None
            last_pg = -999
            forward_specs = {}
            for s in sorted_srcs:
                pg = extract_page_num(s)
                if specs[s][0] is not None:
                    last_spec = specs[s]
                    last_pg = pg
                    forward_specs[s] = specs[s]
                elif last_spec is not None and (pg - last_pg) <= 2:
                    forward_specs[s] = last_spec
                    last_pg = pg
                else:
                    forward_specs[s] = None

            # Propagação reversa (página esquerda espelhada)
            next_spec = None
            next_pg = 999999
            backward_specs = {}
            for s in reversed(sorted_srcs):
                pg = extract_page_num(s)
                if specs[s][0] is not None:
                    next_spec = specs[s]
                    next_pg = pg
                    backward_specs[s] = specs[s]
                elif next_spec is not None and (next_pg - pg) <= 2:
                    backward_specs[s] = next_spec
                    next_pg = pg
                else:
                    backward_specs[s] = None

            # Combinar especificidade para base da página
            for s in sorted_srcs:
                if specs[s][0] is not None:
                    self.classification_map[s] = specs[s]
                elif forward_specs[s] is not None:
                    self.classification_map[s] = forward_specs[s]
                elif backward_specs[s] is not None:
                    self.classification_map[s] = backward_specs[s]
                else:
                    self.classification_map[s] = identify_generic_header(raw_hdrs[s])

            # Construir segmentos intra-página
            for i, s in enumerate(sorted_srcs):
                if s in file_inner_headers:
                    inner_hdrs = file_inner_headers[s]
                    prev_spec = self.classification_map[sorted_srcs[i - 1]] if i > 0 else self.classification_map[s]
                    segments = [(0, prev_spec)]
                    for start_offset, in_spec in inner_hdrs:
                        segments.append((start_offset, in_spec))
                    self.intra_sections_map[s] = segments

    def get_file_classification(self, source_path: str) -> Tuple[str, str]:
        return self.classification_map.get(source_path, ('INDEX_GENERALIS', 'INDEX GENERALIS'))

    def get_entry_classification(self, source_path: str, char_offset: int = 0) -> Tuple[str, str]:
        if source_path in self.intra_sections_map:
            segments = self.intra_sections_map[source_path]
            chosen = segments[0][1]
            for start_offset, spec in segments:
                if char_offset >= start_offset:
                    chosen = spec
                else:
                    break
            return chosen
        return self.get_file_classification(source_path)

# ==============================================================================
# CAMADA 2: ONTOLOGIA MICRO DE ENTIDADES (entity_type)
# ==============================================================================

CLASSICAL_AUTHORS_STEMS = {
    'aristotel', 'plato', 'socrat', 'homer', 'hesiod', 'euripid', 'sophocl', 'aeschyl',
    'aristophan', 'pindar', 'theocrit', 'herodot', 'thucydid', 'xenophon', 'polybi', 'plutarch',
    'demosthen', 'aeschin', 'isocrat', 'lysia', 'lucian', 'strabo', 'pausania', 'galen',
    'hippocrat', 'dioscorid', 'ptolemae', 'archimed', 'euclid', 'epicur', 'zeno', 'chrysipp',
    'cleanth', 'epictet', 'marcus aureli', 'plotin', 'porphyri', 'iamblich', 'procl',
    'diogen laert', 'philo judae', 'philon', 'philochor', 'philolau', 'joseph flavi',
    'cicero', 'tullius', 'vergili', 'virgili', 'horati', 'ovidi', 'seneca', 'lucreti',
    'catullus', 'catulli', 'terenti', 'plaut', 'livius', 'tacit', 'suetoni', 'sallusti',
    'caesar', 'plini', 'quintilian', 'iuvenal', 'juvenal', 'martial', 'persius',
    'statius', 'statii', 'statium', 'lucan', 'varro', 'columell', 'vitruvi', 'celsus', 'frontin', 'apulei', 'aulus gelli',
    'macrobi', 'pythagor', 'democrit', 'heraclit', 'menander', 'sappho', 'alcaeu', 'callimach',
    'censorin', 'vegeti', 'solin', 'ammian'
}

PATRISTIC_AUTHORS_STEMS = {
    'augustin', 'ambrosi', 'hieronym', 'gregori', 'chrysostom', 'basili', 'athanasi',
    'cyrill', 'hilari', 'leo magnus', 'tertullian', 'cyprian', 'origen', 'clemens alexandr',
    'clemens roman', 'irenaeu', 'justin martyr', 'polycarp', 'ignati', 'lactanti', 'arnobi',
    'minuci felix', 'vincenti lirin', 'joannes cassian', 'prosper', 'fulgenti', 'isidor',
    'beda venerabil', 'alcuin', 'raban maur', 'paschasi radbert', 'hincmar', 'theodulf',
    'scotus eriugen', 'anselm', 'bernard', 'petrus damian', 'petrus lombard',
    'theodoret', 'epiphani', 'joannes damascen', 'maximus confessor', 'photi', 'theophylact',
    'euthymi', 'gennadi', 'cassiodor', 'boethi', 'ennodi', 'caesari arelat', 'martin bracarens',
    'eusebi pamphil', 'petrus chrysolog', 'sulpici sever', 'prudenti', 'paulin nolan',
    'didym caec', 'agobard', 'petrus cantor', 'hugo de s. victore', 'richard de s. victore'
}

SCRIPTURE_BOOKS_STEMS = {
    'genesis', 'exodus', 'leviticus', 'numeri', 'deuteronomium', 'josue', 'judices', 'ruth',
    'regum', 'paralipomenon', 'esdrae', 'nehemias', 'tobias', 'judith', 'esther', 'job',
    'psalmi', 'psalmus', 'proverbia', 'ecclesiastes', 'canticum canticorum', 'sapientia',
    'ecclesiasticus', 'sirach', 'isaias', 'jeremias', 'lamentationes', 'baruch', 'ezechiel',
    'daniel', 'osee', 'joel', 'amos', 'abdias', 'jonas', 'michaeas', 'nahum', 'habacuc',
    'sophonias', 'aggaeus', 'zacharias', 'malachias', 'machabaeorum', 'matthaeus', 'marcus',
    'lucas', 'joannes evangelista', 'actus apostolorum', 'romanos', 'corinthios', 'galatas',
    'ephesios', 'philippenses', 'colossenses', 'thessalonicenses', 'timotheum', 'titum',
    'philemonem', 'hebraeos', 'epistola petri', 'epistola joannis', 'epistola jacobi', 'apocalypsis'
}

SCRIPTURE_CITATION_RE = re.compile(
    r'\b(?:Gen(?:es)?|Exod?|Lev(?:it)?|Num(?:er)?|Deut(?:er)?|Jos(?:ue)?|Jud(?:ic)?|Ruth|'
    r'Reg(?:um)?|Paral(?:ip)?|Esdr?|Neh(?:em)?|Tob(?:i)?|Judith|Esth(?:er)?|Job|Ps(?:alm)?|'
    r'Prov(?:erb)?|Eccl(?:es)?|Cant(?:ic)?|Sap(?:ient)?|Eccli(?:ast)?|Isa(?:i)?|Jer(?:em)?|'
    r'Lam(?:ent)?|Bar(?:uch)?|Ezech|Dan(?:iel)?|Os(?:ee)?|Joel|Am(?:os)?|Abd(?:i)?|Jon(?:as)?|'
    r'Mich(?:ae)?|Nah(?:um)?|Hab(?:ac)?|Soph(?:on)?|Agg(?:ae)?|Zach(?:ar)?|Mal(?:ach)?|'
    r'Mach(?:ab)?|Matth?|Marc?|Luc(?:a)?|Joan(?:n)?|Act(?:us)?|Rom(?:an)?|Cor(?:inth)?|'
    r'Gal(?:at)?|Eph(?:es)?|Phil(?:ipp)?|Col(?:oss)?|Thess(?:al)?|Tim(?:oth)?|Tit(?:um)?|'
    r'Philem|Hebr(?:ae)?|Jac(?:ob)?|Petr?|Apoc(?:al)?)\b\.?\s*\d+',
    re.IGNORECASE
)

BIBLICAL_PERSONS_STEMS = {
    'adam', 'eva', 'abel', 'cain', 'seth', 'henoch', 'noe', 'melchisedech', 'abraham', 'sara',
    'agar', 'ismael', 'isaac', 'rebecca', 'jacob', 'rachel', 'lia', 'joseph patriarcha',
    'ruben', 'simeon', 'levi', 'juda', 'benjamin', 'moses', 'aaron', 'josue', 'gedeon',
    'samson', 'heli', 'samuel', 'saul', 'david', 'jonathan', 'absalom', 'salomon', 'elias',
    'eliseus', 'ezechias', 'josias', 'joannes baptista', 'maria virgo', 'joseph sponsus',
    'petrus', 'paulus', 'andreas apostolus', 'stephanus protomartyr',
    'lazarus', 'martha', 'maria magdalena', 'nicodemus', 'pilatus', 'caiphas', 'herodes'
}

TOPONYMS_STEMS = {
    'roma', 'constantinopolis', 'byzantium', 'alexandria', 'antiochia', 'hierosolyma', 'jerusalem',
    'carthago', 'athenae', 'ephesos', 'caesarea', 'mediolanum', 'ravenna', 'lugdunum', 'corinthus',
    'thessalonica', 'philippi', 'smyrna', 'sardes', 'pergamum', 'laodicea', 'nicomedia', 'nicaea',
    'chalcedon', 'tyrus', 'sidon', 'damascus', 'edessa', 'nisibis', 'babylon', 'memphis', 'thebae',
    'hippo', 'utica', 'nazianzus', 'nyssa', 'tarsus', 'aegyptus', 'judaea', 'samaria', 'palaestina',
    'syria', 'mesopotamia', 'armenia', 'cappadocia', 'galatia', 'pontus', 'bithynia', 'macedonia',
    'achaia', 'thracia', 'italia', 'gallia', 'hispania', 'britannia', 'africa', 'tiberis',
    'euphrates', 'tigris', 'nilus', 'jordanis', 'sinai', 'sion', 'oliveti', 'carmelus'
}

DOGMATIC_TERMS_STEMS = {
    'trinitas', 'incarnatio', 'gratia', 'fides', 'spes', 'charitas', 'baptismus', 'eucharistia',
    'transsubstantiatio', 'poenitentia', 'confessio', 'resurrectio', 'sacramentum', 'justificatio',
    'redemptio', 'praedestinatio', 'peccatum originale', 'libero arbitrio', 'spiritus sanctus',
    'verbum dei', 'creatio', 'haeresis', 'schisma', 'anathema', 'symbolum fidei'
}

def match_stem_tokens(text: str, stem_set: Set[str]) -> bool:
    """Verifica correspondência tokenizada evitando falsos positivos com substrings curtas."""
    l_text = text.lower()
    words = re.findall(r'[a-z\u0370-\u03ff\u1f00-\u1fff]+', l_text)
    for s in stem_set:
        if ' ' in s:
            if s in l_text:
                return True
        elif len(s) <= 4:
            if s in words:
                return True
        else:
            for w in words:
                if s == 'hilari' and w in {'hilaria', 'hilaritatis'}:
                    continue
                if w.startswith(s):
                    return True
    return False

class LemmaEntityClassifier:
    """Classifica um verbete individual na ontologia patrística respeitando a soberania da categoria impressa pelo editor."""
    
    @staticmethod
    def classify(label: str, volume: str, macro_category: str, section: str) -> str:
        # 1. Caracteres gregos ou léxico grego de PG
        if any(0x0370 <= ord(c) <= 0x1fff for c in label) or (
            volume.startswith("PG") and macro_category == "LEXICA"
        ):
            return "VOCABULUM_GRAECUM"

        l_strip = label.strip()
        l_lower = l_strip.lower()
        words = re.findall(r'[a-z\u0370-\u03ff\u1f00-\u1fff]+', l_lower)
        first_word = words[0] if words else ""

        # 2. ÍNDICES DE AUTORES (AUCTORES): A categoria impressa pelo editor é SOBERANA
        if macro_category == "AUCTORES":
            # Autores clássicos pagãos
            if match_stem_tokens(l_lower, CLASSICAL_AUTHORS_STEMS) or section in {
                "INDEX SCRIPTORUM ETHNICORUM", "INDEX POETARUM GENTILIUM"
            }:
                return "AUCTOR_CLASSICUS"
            # Em índices de autores, todos os verbetes representam autores (patrísticos, eclesiásticos,
            # comentadores modernos, ou autoridades canônicas/bíblicas tratadas como autores de livros).
            # Jamais devem ser convertidos em LOCUS_SCRIPTURAE ou PERSONA_BIBLICA.
            return "AUCTOR_PATRISTICUS"

        # 3. ÍNDICES DE ESCRITURAS (SCRIPTURA): A categoria impressa pelo editor é SOBERANA
        if macro_category == "SCRIPTURA":
            return "LOCUS_SCRIPTURAE"

        # 4. Sentenças e tratados dogmáticos iniciando por "De ..." ou termos teológicos
        if l_strip.startswith(('De ', 'DE ', 'de ')):
            return "TEMA_DOGMATICO"
        if first_word in DOGMATIC_TERMS_STEMS:
            return "TEMA_DOGMATICO"

        # 5. Citações e perícopas bíblicas explícitas (capítulo e versículo)
        if SCRIPTURE_CITATION_RE.search(label):
            return "LOCUS_SCRIPTURAE"

        # 6. Livros bíblicos (quando em índice geral ou temático)
        if match_stem_tokens(l_lower, SCRIPTURE_BOOKS_STEMS):
            return "LOCUS_SCRIPTURAE"

        # 7. Padres da Igreja e autores patrísticos (em índices gerais / temáticos)
        if match_stem_tokens(l_lower, PATRISTIC_AUTHORS_STEMS):
            return "AUCTOR_PATRISTICUS"

        # 8. Autores clássicos pagãos (em índices gerais / temáticos)
        if match_stem_tokens(l_lower, CLASSICAL_AUTHORS_STEMS):
            return "AUCTOR_CLASSICUS"

        # 9. Personagens bíblicos (em índices gerais / temáticos)
        if match_stem_tokens(l_lower, BIBLICAL_PERSONS_STEMS):
            return "PERSONA_BIBLICA"

        # 10. Topônimos e geografia patrística
        if first_word in TOPONYMS_STEMS:
            return "TOPONYMUS"

        # 11. Padrão onomástico histórico
        return "ONOMASTICA_HISTORICA"

# ==============================================================================
# PIPELINE COMPLETO DE CLASSIFICAÇÃO
# ==============================================================================

def run_taxonomy_pipeline(db_path: str, summary_path: str, dry_run: bool = True, limit: Optional[int] = None):
    print(f"=== CLASSIFICADOR TAXONÔMICO PATRÍSTICO ===")
    print(f"Banco: {db_path} (dry_run={dry_run})")
    
    t0 = time.time()
    read_uri = f"file:{os.path.abspath(db_path)}?mode=ro" if dry_run else db_path
    con = sqlite3.connect(read_uri, uri=dry_run)
    cur = con.cursor()

    # 1. Carregar pares volume/source para classificação de cabeçalhos
    print("1. Analisando cabeçalhos de arquivos-fonte com propagação bidirecional...")
    src_rows = cur.execute("SELECT DISTINCT volume, source FROM items").fetchall()
    vol_sources: Dict[str, List[str]] = {}
    for vol, src in src_rows:
        if src:
            vol_sources.setdefault(vol, []).append(src)

    source_classifier = SourceFileClassifier(vol_sources)
    print(f"   Mapeados {len(source_classifier.classification_map)} arquivos-fonte.")

    # 2. Carregar registros de items
    print("2. Carregando registros de items...")
    query = "SELECT id, label, volume, section, source, metadata_json FROM items"
    if limit:
        query += f" LIMIT {limit}"
    
    items_rows = cur.execute(query).fetchall()
    print(f"   Carregados {len(items_rows)} registros em {time.time() - t0:.2f}s.")

    # 3. Classificar cada registro
    print("3. Executando classificação em duas camadas...")
    macro_counts = Counter()
    entity_counts = Counter()
    section_counts = Counter()

    updates = []
    sample_per_entity: Dict[str, List[Tuple[str, str, str, str]]] = {}

    for item_id, label, volume, old_section, source, meta_str in items_rows:
        meta_dict = {}
        if meta_str:
            try:
                meta_dict = json.loads(meta_str)
            except Exception:
                meta_dict = {}
        span = meta_dict.get('source_span', [0, 0])
        char_offset = span[0] if isinstance(span, (list, tuple)) and len(span) > 0 else 0

        # Camada 1: Categoria Macro e Seção Padronizada (com sensibilidade intra-página)
        macro_cat, std_section = source_classifier.get_entry_classification(source or "", char_offset)
        
        # Camada 2: Ontologia Micro
        entity_type = LemmaEntityClassifier.classify(label or "", volume or "", macro_cat, std_section)

        macro_counts[macro_cat] += 1
        entity_counts[entity_type] += 1
        section_counts[std_section] += 1

        if len(sample_per_entity.setdefault(entity_type, [])) < 5:
            sample_per_entity[entity_type].append((volume, label, macro_cat, std_section))

        # Atualização de metadata_json preservando tudo existente
        meta_dict["index_category"] = macro_cat
        meta_dict["entity_type"] = entity_type
        new_meta_str = json.dumps(meta_dict, ensure_ascii=False)

        updates.append((std_section, new_meta_str, item_id))

    t_class = time.time() - t0
    print(f"   Classificação concluída em {t_class:.2f}s.")

    # 4. Imprimir relatórios
    print("\n--- DISTRIBUIÇÃO DA CAMADA 1: CATEGORIA MACRO (index_category) ---")
    total = len(items_rows)
    for cat, cnt in macro_counts.most_common():
        print(f"  {cat:25s}: {cnt:7d} ({cnt/total*100:5.2f}%)")

    print("\n--- DISTRIBUIÇÃO DA CAMADA 2: ENTIDADE MICRO (entity_type) ---")
    for ent, cnt in entity_counts.most_common():
        print(f"  {ent:25s}: {cnt:7d} ({cnt/total*100:5.2f}%)")

    print("\n--- TOP 10 SEÇÕES PADRONIZADAS (section) ---")
    for sec, cnt in section_counts.most_common(10):
        print(f"  {sec:35s}: {cnt:7d} ({cnt/total*100:5.2f}%)")

    print("\n--- AMOSTRAS DE VERBETES POR ENTIDADE ONTOLÓGICA ---")
    for ent, samples in sample_per_entity.items():
        print(f"\n  [{ent}]:")
        for vol, lbl, m_cat, sec in samples:
            print(f"    - [{vol}] '{lbl[:60]}' -> {m_cat} | {sec}")

    con.close()

    # 5. Aplicação no banco se solicitado
    if not dry_run:
        print("\n4. Aplicando classificação no banco SQLite autorizado...")
        t_app = time.time()
        write_con = sqlite3.connect(db_path)
        write_cur = write_con.cursor()

        write_cur.execute("BEGIN TRANSACTION;")
        write_cur.executemany(
            "UPDATE items SET section = ?, metadata_json = ? WHERE id = ?",
            updates
        )
        print(f"   Atualizados {len(updates)} registros na tabela items.")

        # Reconstruir items_fts para espelhar perfeitamente items com section preenchido
        print("   Sincronizando tabela virtual items_fts...")
        write_cur.execute("DROP TABLE IF EXISTS items_fts;")
        write_cur.execute("""
            CREATE VIRTUAL TABLE items_fts USING fts5(
                label,
                section,
                ref_raw,
                volume,
                tokenize='unicode61 remove_diacritics 2'
            );
        """)
        write_cur.execute("""
            INSERT INTO items_fts(rowid, label, section, ref_raw, volume)
            SELECT rowid, label, COALESCE(section, ''), COALESCE(ref_raw, ''), volume
            FROM items;
        """)
        write_con.commit()
        write_con.close()
        print(f"   Banco search.sqlite commitado com sucesso em {time.time() - t_app:.2f}s.")

        # Atualizar catalog_summary.json
        if os.path.exists(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as f:
                summary = json.load(f)
            summary["index_category_distribution"] = dict(macro_counts)
            summary["entity_type_distribution"] = dict(entity_counts)
            summary["classified_items_count"] = total
            summary["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            print(f"   Atualizado {summary_path} com dados taxonômicos.")

    print(f"\nConcluído com sucesso em {time.time() - t0:.2f}s.")

def main():
    parser = argparse.ArgumentParser(description="Classificador taxonômico e ontológico de índices patrísticos.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Caminho do banco search.sqlite")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="Caminho do catalog_summary.json")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Executar apenas simulação em leitura")
    parser.add_argument("--apply", action="store_true", default=False, help="Gravar alterações no banco de dados")
    parser.add_argument("--limit", type=int, default=None, help="Limite de registros para teste")

    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        print("Nenhuma ação especificada. Use --dry-run para testar ou --apply para executar.")
        sys.exit(1)

    dry_run = not args.apply
    run_taxonomy_pipeline(args.db, args.summary, dry_run=dry_run, limit=args.limit)

if __name__ == "__main__":
    main()
