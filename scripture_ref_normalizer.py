import json
import re
import sqlite3
from pathlib import Path
import unicodedata


def connect_readwrite(path: Path) -> sqlite3.Connection:
    """Abre DB de resumos em modo leitura/escrita (para gravar keywords)."""
    if not path.exists():
        raise FileNotFoundError(f"DB de resumos não encontrado: {path}")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_bible_citation_schema(con: sqlite3.Connection) -> None:
    """Cria tabelas de citações bíblicas sem tocar na tabela resumos."""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS bible_citations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_idx INTEGER NOT NULL,
            book TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('book','chapter','chapter_verse')),
            number INTEGER,
            verse TEXT,
            normalized TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            raw_sample TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(book_idx, kind, number, verse)
        );
        CREATE INDEX IF NOT EXISTS idx_bible_citations_book ON bible_citations(book_idx);

        CREATE TABLE IF NOT EXISTS resumo_citations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resumo_id INTEGER NOT NULL REFERENCES resumos(id) ON DELETE CASCADE,
            citation_id INTEGER NOT NULL REFERENCES bible_citations(id) ON DELETE CASCADE,
            source_kind TEXT NOT NULL,
            source_path TEXT NOT NULL,
            raw TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(resumo_id, citation_id, source_path)
        );
        CREATE INDEX IF NOT EXISTS idx_resumo_citations_citation ON resumo_citations(citation_id);
        """
    )
    con.commit()


def query_keywords_from_doc(
    con: sqlite3.Connection,
    documento: str,
    pagina_num: int,
    source: str,
) -> sqlite3.Row | None:
    row = con.execute(
        """SELECT id, keywords_json FROM resumos
           WHERE documento=? AND pagina_num=? AND source=?""",
        (documento, pagina_num, source),
    ).fetchone()
    return row


def query_keywords(
    con: sqlite3.Connection,
) -> list[sqlite3.Row]:
    rows = con.execute(
        """SELECT * FROM resumos""",
    ).fetchall()
    print(len(rows))
    return rows


def strip_markdown_wrappers(text: str) -> str:
    """Remove bullets/ênfase/code simples que podem envolver a keyword."""
    t = (text or "").strip()
    # Fences e blocos de code inline
    t = re.sub(r"^```[\w-]*\s*|\s*```$", "", t, flags=re.DOTALL)
    t = re.sub(r"`{1,3}([^`]+?)`{1,3}", r"\1", t)
    # Bullets / headers iniciais
    t = re.sub(r"^(?:[>#]+|\*+|[-+\u2022•]+|#+)\s*", "", t)
    # Ênfase simples
    t = re.sub(r"\*{1,3}([^*]+?)\*{1,3}", r"\1", t)
    t = re.sub(r"_{1,3}([^_]+?)_{1,3}", r"\1", t)
    # Aspas/fences simétricos
    while len(t) >= 2 and t[0] == t[-1] and t[0] in "*_`'\"":
        t = t[1:-1].strip()
    return t


def clean_keyword_original(text: str) -> str:
    """Limpa keyword para armazenar como original (sem aspas/pontuação de borda)."""
    text = strip_markdown_wrappers(text)
    text = unicodedata.normalize("NFKC", text or "")
    text = " ".join(text.split())
    strip_chars = " \"'«»“”‘’()[]{}|\\/–—-:;.,!?·•*&"
    return text.strip(strip_chars)


"""
bible_books.py
Arrays dos nomes dos livros bíblicos em PT, LA (Vulgata/Nova Vulgata) e GR (LXX/NT grego).
Inclui utilitário de normalização e lookup para matching em keywords.
"""

import re
import unicodedata

# ── Português (Bíblia Sagrada / CNBB) ────────────────────────────────────────

LIVROS_PT = [
    # Pentateuco
    "Gênesis",
    "Êxodo",
    "Levítico",
    "Números",
    "Deuteronômio",
    # Históricos
    "Josué",
    "Juízes",
    "Rute",
    "I Samuel",
    "II Samuel",
    "I Reis",
    "II Reis",
    "I Crônicas",
    "II Crônicas",
    "Esdras",
    "Neemias",
    "Tobias",
    "Judite",
    "Ester",
    # Poéticos/Sapienciais
    "Jó",
    "Salmos",
    "I Macabeus",
    "II Macabeus",
    "Provérbios",
    "Eclesiastes",
    "Cântico dos Cânticos",
    "Sabedoria",
    "Eclesiástico",
    # Proféticos maiores
    "Isaías",
    "Jeremias",
    "Lamentações",
    "Baruc",
    "Ezequiel",
    "Daniel",
    # Proféticos menores
    "Oséias",
    "Joel",
    "Amós",
    "Abdias",
    "Jonas",
    "Miquéias",
    "Naum",
    "Habacuc",
    "Sofonias",
    "Ageu",
    "Zacarias",
    "Malaquias",
    # NT – Evangelhos e Atos
    "São Mateus",
    "São Marcos",
    "São Lucas",
    "São João",
    "Atos dos Apóstolos",
    # NT – Paulo
    "Romanos",
    "I Coríntios",
    "II Coríntios",
    "Gálatas",
    "Efésios",
    "Filipenses",
    "Colossenses",
    "I Tessalonicenses",
    "II Tessalonicenses",
    "I Timóteo",
    "II Timóteo",
    "Tito",
    "Filêmon",
    "Hebreus",
    # NT – Católicas e Apocalipse
    "São Tiago",
    "I São Pedro",
    "II São Pedro",
    "I São João",
    "II São João",
    "III São João",
    "São Judas",
    "Apocalipse",
]

# ── Latim (Vulgata / Nova Vulgata) ───────────────────────────────────────────

LIVROS_LA = [
    # Pentateuco
    "Genesis",
    "Exodus",
    "Leviticus",
    "Numeri",
    "Deuteronomium",
    # Históricos
    "Iosue",
    "Iudicum",
    "Ruth",
    "I Samuel",
    "II Samuel",
    "I Regum",
    "II Regum",
    "I Paralipomenon",
    "II Paralipomenon",
    "Esdrae",
    "Nehemiae",
    "Tobiae",
    "Iudith",
    "Esther",
    # Poéticos/Sapienciais
    "Iob",
    "Psalmi",
    "I Maccabaeorum",
    "II Maccabaeorum",
    "Proverbia",
    "Ecclesiastes",
    "Canticum Canticorum",
    "Sapientia",
    "Ecclesiasticus",
    # Proféticos maiores
    "Isaias",
    "Ieremias",
    "Lamentationes",
    "Baruch",
    "Ezechiel",
    "Daniel",
    # Proféticos menores
    "Osee",
    "Ioel",
    "Amos",
    "Abdias",
    "Ionas",
    "Micheas",
    "Nahum",
    "Habacuc",
    "Sophonias",
    "Aggaeus",
    "Zacharias",
    "Malachias",
    # NT – Evangelhos e Atos
    "Matthaeus",
    "Marcus",
    "Lucas",
    "Ioannes",
    "Actus Apostolorum",
    # NT – Paulo
    "Romani",
    "I Corinthios",
    "II Corinthios",
    "Galatae",
    "Ephesii",
    "Philippenses",
    "Colossenses",
    "I Thessalonicenses",
    "II Thessalonicenses",
    "I Timotheum",
    "II Timotheum",
    "Titus",
    "Philemon",
    "Hebraeos",
    # NT – Católicas e Apocalipse
    "Iacobus",
    "I Petri",
    "II Petri",
    "I Ioannis",
    "II Ioannis",
    "III Ioannis",
    "Iudae",
    "Apocalypsis",
]

# ── Grego (LXX para AT; NA28/UBS5 para NT) ───────────────────────────────────
# Formas nominativas canônicas; inclui transliterações comuns entre parênteses
# para facilitar matching em textos patrísticos.

LIVROS_GR = [
    # Pentateuco
    "Γένεσις",
    "Ἔξοδος",
    "Λευιτικόν",
    "Ἀριθμοί",
    "Δευτερονόμιον",
    # Históricos
    "Ἰησοῦς Ναυῆ",
    "Κριταί",
    "Ῥούθ",
    "Α Βασιλειῶν",
    "Β Βασιλειῶν",
    "Γ Βασιλειῶν",
    "Δ Βασιλειῶν",
    "Α Παραλειπομένων",
    "Β Παραλειπομένων",
    "Ἔσδρας",
    "Νεεμίας",
    "Τωβίτ",
    "Ἰουδίθ",
    "Ἐσθήρ",
    # Poéticos/Sapienciais
    "Ἰώβ",
    "Ψαλμοί",
    "Α Μακκαβαίων",
    "Β Μακκαβαίων",
    "Παροιμίαι",
    "Ἐκκλησιαστής",
    "Ἆσμα Ἀσμάτων",
    "Σοφία Σαλωμῶντος",
    "Σοφία Σειράχ",
    # Proféticos maiores
    "Ἡσαΐας",
    "Ἱερεμίας",
    "Θρῆνοι",
    "Βαρούχ",
    "Ἰεζεκιήλ",
    "Δανιήλ",
    # Proféticos menores (Δωδεκαπρόφητον)
    "Ὠσηέ",
    "Ἰωήλ",
    "Ἀμώς",
    "Ἀβδιού",
    "Ἰωνᾶς",
    "Μιχαίας",
    "Ναούμ",
    "Ἀββακούμ",
    "Σοφονίας",
    "Ἀγγαῖος",
    "Ζαχαρίας",
    "Μαλαχίας",
    # NT – Evangelhos e Atos
    "Κατὰ Ματθαῖον",
    "Κατὰ Μᾶρκον",
    "Κατὰ Λουκᾶν",
    "Κατὰ Ἰωάννην",
    "Πράξεις Ἀποστόλων",
    # NT – Paulo
    "Πρὸς Ῥωμαίους",
    "Α Κορινθίους",
    "Β Κορινθίους",
    "Πρὸς Γαλάτας",
    "Πρὸς Ἐφεσίους",
    "Πρὸς Φιλιππησίους",
    "Πρὸς Κολοσσαεῖς",
    "Α Θεσσαλονικεῖς",
    "Β Θεσσαλονικεῖς",
    "Α Τιμόθεον",
    "Β Τιμόθεον",
    "Πρὸς Τίτον",
    "Πρὸς Φιλήμονα",
    "Πρὸς Ἑβραίους",
    # NT – Católicas e Apocalipse
    "Ἰακώβου",
    "Α Πέτρου",
    "Β Πέτρου",
    "Α Ἰωάννου",
    "Β Ἰωάννου",
    "Γ Ἰωάννου",
    "Ἰούδα",
    "Ἀποκάλυψις",
]

# Transliterações gregas comuns (para matching em textos latinos/portugueses)
# Cobre as formas que aparecem mais em patrística grega transliterada.
LIVROS_GR_TRANSLIT = [
    "Genesis",
    "Exodus",
    "Leuitikon",
    "Arithmoi",
    "Deuteronomion",
    "Iesous Naue",
    "Kritai",
    "Routh",
    "A Basileiōn",
    "B Basileiōn",
    "G Basileiōn",
    "D Basileiōn",
    "A Paraleipoménōn",
    "B Paraleipoménōn",
    "Esdras",
    "Neemias",
    "Tōbit",
    "Ioudith",
    "Esthēr",
    "Iōb",
    "Psalmoi",
    "A Makkabaiōn",
    "B Makkabaiōn",
    "Paroimiai",
    "Ekklēsiastēs",
    "Asma Asmatōn",
    "Sophia Salōmōntos",
    "Sophia Seirach",
    "Hēsaias",
    "Hieremias",
    "Thrēnoi",
    "Barouch",
    "Iezekiēl",
    "Daniēl",
    "Ōsēe",
    "Iōēl",
    "Amōs",
    "Abdiu",
    "Iōnas",
    "Michaias",
    "Naoum",
    "Abbakum",
    "Sophonias",
    "Angaios",
    "Zacharias",
    "Malachias",
    "Kata Matthaion",
    "Kata Markon",
    "Kata Loukan",
    "Kata Iōannēn",
    "Praxeis Apostolōn",
    "Pros Rōmaious",
    "A Korinthious",
    "B Korinthious",
    "Pros Galatas",
    "Pros Ephesious",
    "Pros Philippēsious",
    "Pros Kolossaeis",
    "A Thessalonikeis",
    "B Thessalonikeis",
    "A Timotheon",
    "B Timotheon",
    "Pros Titon",
    "Pros Philēmona",
    "Pros Hebraious",
    "Iakōbou",
    "A Petrou",
    "B Petrou",
    "A Iōannou",
    "B Iōannou",
    "G Iōannou",
    "Iouda",
    "Apokalypsis",
]

# ── Normalização e lookup ────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    """Lowercase + remove acentos + colapsa espaços."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_approx = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_approx).strip().lower()


# Índice pré-computado: forma normalizada → (nome canônico PT, índice)
_INDEX: dict[str, tuple[str, int]] = {}

_BOOK_ABBREVIATIONS: dict[str, int] = {
    "gn": 0,
    "gen": 0,
    "ex": 1,
    "lv": 2,
    "lev": 2,
    "nm": 3,
    "dt": 4,
    "jos": 5,
    "jz": 6,
    "rt": 7,
    "1sm": 8,
    "2sm": 9,
    "1rs": 10,
    "2rs": 11,
    "1cr": 12,
    "2cr": 13,
    "esd": 14,
    "ne": 15,
    "tb": 16,
    "jt": 17,
    "est": 18,
    "jb": 19,
    "sl": 20,
    "sal": 20,
    "ps": 20,
    "1mc": 21,
    "2mc": 22,
    "pv": 23,
    "ecl": 24,
    "ct": 25,
    "sb": 26,
    "eclo": 27,
    "sir": 27,
    "is": 28,
    "jr": 29,
    "lm": 30,
    "br": 31,
    "ez": 32,
    "dn": 33,
    "os": 34,
    "jl": 35,
    "am": 36,
    "ab": 37,
    "jn": 38,
    "mq": 39,
    "na": 40,
    "hc": 41,
    "sf": 42,
    "ag": 43,
    "zc": 44,
    "ml": 45,
    "mt": 46,
    "mc": 47,
    "lc": 48,
    "jo": 49,
    "rm": 51,
    "1cor": 52,
    "2cor": 53,
    "gl": 54,
    "ef": 55,
    "fl": 56,
    "cl": 57,
    "1ts": 58,
    "2ts": 59,
    "1tm": 60,
    "2tm": 61,
    "tt": 62,
    "fm": 63,
    "hb": 64,
    "tg": 65,
    "1pe": 66,
    "2pe": 67,
    "1jo": 68,
    "2jo": 69,
    "3jo": 70,
    "jd": 71,
    "ap": 72,
}

_ORDINAL_ROMAN = {"1": "i", "2": "ii", "3": "iii"}

# Prefixos neutros em construções "Livro de X" / "Evangelho de X" / "Carta de X"
# São removidos antes do lookup para que "Livro de Daniel" → "Daniel".
_STRIP_PREFIXES = re.compile(
    r"^(?:livro\s+d[aeo]s?\s+|carta\s+d[aeo]s?\s+|epístola\s+d[aeo]s?\s+"
    r"|epistola\s+d[aeo]s?\s+|evangelho\s+(?:segundo\s+|de\s+)?|gospel\s+of\s+"
    r"|segundo\s+|liber\s+|prophetia\s+d[aeo]s?\s+)",
    re.IGNORECASE,
)


def _build_index() -> None:
    sources = [LIVROS_PT, LIVROS_LA, LIVROS_GR_TRANSLIT]
    for src in sources:
        for i, name in enumerate(src):
            key = _normalize(name)
            if key not in _INDEX:
                _INDEX[key] = (LIVROS_PT[i], i)

    # Aliases explícitos: formas singulares, genitivas e variantes frequentes
    # em keywords patrísticas que NÃO coincidem com nenhuma entrada canônica.
    aliases: dict[str, int] = {
        # Salmos — singular PT/LA/EN
        "salmo": 20,
        "psalm": 20,
        "psalmo": 20,
        "psaume": 20,
        # Provérbios — singular
        "provérbio": 23,
        "proverbio": 23,
        "proverb": 23,
        # Cântico — forma curta
        "cantico": 25,
        "cântico": 25,
        "canticle": 25,
        "song of songs": 25,
        # Atos — formas curtas
        "atos": 50,
        "acta": 50,
        "acts": 50,
        "actus": 50,
        # Evangelhos — pelo nome do evangelista sem "São"
        "mateus": 46,
        "matthaeus": 46,
        "matthew": 46,
        "marcos": 47,
        "marcus": 47,
        "mark": 47,
        "lucas": 48,
        "luke": 48,
        "joao": 49,
        "joãoo": 49,
        "john": 49,  # "joãoo" nunca ocorre mas garante
        # Apóstolo — forma genitiva latina
        "apostolorum": 50,
        # Profetas menores — formas alternativas PT
        "oseias": 34,
        "hosea": 34,
        "jonas": 38,
        "jonah": 38,
        "micheas": 39,
        "micah": 39,
        # Lamentações — formas curtas
        "lamentacao": 30,
        "lamentacoes": 30,
        "threni": 30,
        "lamentatio": 30,
        # Sabedoria — formas curtas
        "sabedoria": 26,
        "wisdom": 26,
        "sapientia": 26,
        # Sirácida / Eclesiástico
        "siracida": 27,
        "sirach": 27,
        "sirac": 27,
        "ben sira": 27,
        # Apocalipse
        "apocalipsis": 72,
        "revelation": 72,
        "revelacao": 72,
        # Hebreus
        "hebraeus": 64,
        "hebraeos": 64,
        "hebrews": 64,
        # Epístolas numeradas — formas "1 X" e "2 X" sem prefixo romano
        "1 samuel": 8,
        "2 samuel": 9,
        "1 reis": 10,
        "2 reis": 11,
        "1 reges": 10,
        "2 reges": 11,
        "1 cronicas": 12,
        "2 cronicas": 13,
        "1 macabeus": 21,
        "2 macabeus": 22,
        "1 corintios": 52,
        "2 corintios": 53,
        "1 tessalonicenses": 58,
        "2 tessalonicenses": 59,
        "1 timoteo": 60,
        "2 timoteo": 61,
        "1 pedro": 66,
        "2 pedro": 67,
        "1 joao": 68,
        "2 joao": 69,
        "3 joao": 70,
    }
    for alias, idx in aliases.items():
        key = _normalize(alias)
        if key not in _INDEX:
            _INDEX[key] = (LIVROS_PT[idx], idx)

    # Abreviações sigla
    for abbr, idx in _BOOK_ABBREVIATIONS.items():
        if abbr not in _INDEX:
            _INDEX[abbr] = (LIVROS_PT[idx], idx)


_build_index()


def lookup_book(phrase: str) -> tuple[str, int] | None:
    """
    Recebe qualquer forma (PT/LA/GR translit/abreviação/construção genitiva)
    e devolve (nome_canonico_PT, indice_0based) ou None.

    Tenta, em ordem:
      1. Frase completa normalizada
      2. Frase sem prefixos neutros ("Livro de", "Evangelho de", etc.)
    """
    key = _normalize(phrase)
    if key in _INDEX:
        return _INDEX[key]
    # Remove prefixo neutro e tenta de novo
    stripped = _STRIP_PREFIXES.sub("", phrase).strip()
    if stripped != phrase:
        key2 = _normalize(stripped)
        if key2 in _INDEX:
            return _INDEX[key2]
    return None


def _sliding_window_lookup(tokens: list[str]) -> tuple[str, int] | None:
    """
    Tenta ngrams decrescentes (4→3→2→1) sobre a lista de tokens.
    Retorna o primeiro match encontrado, ou None.
    Janela maior primeiro evita que "João" bata em "I São João".
    """
    n = len(tokens)
    for size in range(min(n, 4), 0, -1):
        for start in range(n - size + 1):
            candidate = " ".join(tokens[start : start + size])
            result = lookup_book(candidate)
            if result:
                return result
    return None


# Tokens que sozinhos nunca identificam um livro (muito genéricos)
_STOPWORDS = {
    "de",
    "do",
    "da",
    "dos",
    "das",
    "e",
    "a",
    "o",
    "os",
    "as",
    "em",
    "no",
    "na",
    "nos",
    "nas",
    "ao",
    "aos",
    "à",
    "às",
    "livro",
    "carta",
    "epistola",
    "epístola",
    "evangelho",
    "profecia",
    "segundo",
    "conforme",
}


def keywords_cite_books(keywords: list[str]) -> list[tuple[str, str, int]]:
    """
    Varre uma lista de keywords e detecta quais citam livros bíblicos.
    Retorna lista de (keyword_original, nome_canonico_PT, indice).

    Estratégia:
      1. Tenta lookup direto (cobre formas canônicas e prefixos neutros).
      2. Tokeniza e aplica janela deslizante 4→1, ignorando stopwords soltos.
    """
    hits: list[tuple[str, str, int]] = []
    for kw in keywords:
        # 1. Lookup direto
        result = lookup_book(kw)
        if result:
            hits.append((kw, result[0], result[1]))
            continue
        # 2. Tokeniza — remove pontuação de borda de cada token, filtra números puros
        raw_tokens = kw.split()
        tokens = [re.sub(r"^[\W_]+|[\W_]+$", "", t) for t in raw_tokens]
        tokens = [t for t in tokens if t and not re.fullmatch(r"\d+[\d:.,]*", t)]
        # filtra stopwords isolados mas mantém-nos para janelas maiores
        result = _sliding_window_lookup(tokens)
        if result:
            hits.append((kw, result[0], result[1]))
    return hits


"""
{"keywords": ["Cristo", "confissão oblíqua", "Filho de Deus", "Pilatos", "Herodes", "Barrabás", "crucificação", "cumprimento profético", "Salmo 22", "Isaías 53", "silêncio de Cristo", "tribunal", "justiça divina", "substituição penal", "traição"], "categorias": {"pessoas": ["Cristo", "Pilatos", "Herodes", "Barrabás", "Judas", "David", "Daniel", "Isaías", "Oséias"], "obras_citadas": ["Evangelho de Lucas", "Livro de Daniel", "Salmo 110 (109)", "Salmo 2", "Salmo 22 (21)", "Livro de Isaías", "Livro de Oséias"], "temas_teologicos": ["Cristologia", "Paixão de Cristo", "Cumprimento profético", "Justiça retributiva", "Silêncio de Cristo", "Substituição penal", "Julgamento de Cristo", "Confissão de fé"], "termos_tecnicos_lat_gr": ["archontes", "synagoga maleficorum"]}}
{"keywords": ["expiração (morte de Cristo)", "carne (realidade corpórea de Cristo)", "phantasma (refutação do docetismo)", "profecia (cumprimento)", "sepultamento (de Cristo)", "ressurreição (prova física)", "espírito", "testemunho (legal/angélico)", "incredulidade (dos discípulos)", "Criador (Deus)", "redemptor Israelis", "oportebat (necessidade escritural)", "túmulo (vazio)", "mulheres (no túmulo)", "anjos"], "categorias": {"pessoas": ["Cristo", "José de Arimateia", "Isaías", "Oseias", "Amós", "Pilatos", "Judas"], "obras_citadas": ["Evangelho segundo Lucas", "Livro de Isaías", "Livro de Oseias", "Livro de Amós", "Salmo 1", "Salmo 30"], "temas_teologicos": ["Realidade da encarnação", "Morte de Cristo", "Ressurreição corporal", "Cumprimento profético", "Unidade do Criador e Cristo", "Refutação do docetismo", "Testemunho", "Necessidade escritural da paixão"], "termos_tecnicos_lat_gr": ["phantasma", "exspirare/exspiratio", "oportebat", "redemptor"]}}
{"keywords": ["mutilação textual", "corporeidade de Cristo", "ressurreição corporal", "espírito não tem ossos", "Lucas 24:39", "origem apostólica", "autoridade paulina", "Epístolas de Paulo", "Marcion", "fantasma", "prova física", "Evangelho corrupto", "álbum dos apóstolos", "Instrumento Antigo", "Atos dos Apóstolos"], "categorias": {"pessoas": ["Tertuliano", "Marcion", "Paulo", "Epifânio"], "obras_citadas": ["Evangelho segundo Lucas", "Epístolas de Paulo", "Atos dos Apóstolos", "Salmos"], "temas_teologicos": ["Cristologia", "Dualismo", "Autoridade apostólica", "Cânon bíblico", "Corrupção textual", "Realismo eucarístico"], "termos_tecnicos_lat_gr": ["phantasma", "Instrumento Veteri", "album apostolorum"]}}

"""

_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
_VERSE_RE = re.compile(r"^\d{1,3}(?:-\d{1,3})?$")
_REF_TOKEN = r"(?:\d{1,3}|[IVXLCDMivxlcdm]{1,12})"
_REF_WITH_VERSE = rf"(?P<main>{_REF_TOKEN})(?:\s*[:.,]\s*(?P<verse>\d{{1,3}}(?:-\d{{1,3}})?))?(?:\s*\(\s*(?P<alt>{_REF_TOKEN})\s*\))?"


def _roman_to_int(token: str) -> int | None:
    token = (token or "").upper()
    if not _ROMAN_RE.fullmatch(token):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for char in reversed(token):
        value = values[char]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total if 0 < total < 2000 else None


def _parse_ref_number(token: str | None) -> int | None:
    if not token:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _roman_to_int(token)


def _index_book_label(name: str) -> str:
    roman_prefixes = {"I ": "1 ", "II ": "2 ", "III ": "3 "}
    for prefix, replacement in roman_prefixes.items():
        if name.startswith(prefix):
            name = replacement + name[len(prefix):]
            break
    if name.startswith("São "):
        name = name[4:]
    name = name.replace("1 São ", "1 ")
    name = name.replace("2 São ", "2 ")
    name = name.replace("3 São ", "3 ")
    return name


INDEX_BOOK_LABELS = [_index_book_label(name) for name in LIVROS_PT]


_EPISTLE_TARGETS = {
    51: "aos Romanos",
    52: "aos Coríntios",
    53: "aos Coríntios",
    54: "aos Gálatas",
    55: "aos Efésios",
    56: "aos Filipenses",
    57: "aos Colossenses",
    58: "aos Tessalonicenses",
    59: "aos Tessalonicenses",
    60: "a Timóteo",
    61: "a Timóteo",
    62: "a Tito",
    63: "a Filêmon",
    64: "aos Hebreus",
    65: "de Tiago",
    66: "de Pedro",
    67: "de Pedro",
    68: "de João",
    69: "de João",
    70: "de João",
    71: "de Judas",
}


def _epistle_aliases(idx: int) -> list[str]:
    canon = LIVROS_PT[idx]
    aliases: list[str] = []
    if idx not in _EPISTLE_TARGETS:
        return aliases
    target = _EPISTLE_TARGETS[idx]
    if canon.startswith("I "):
        ord_label = "Primeira"
    elif canon.startswith("II "):
        ord_label = "Segunda"
    elif canon.startswith("III "):
        ord_label = "Terceira"
    else:
        ord_label = None
    base_target = target
    if "São " in target:
        base_target = target.replace("São ", "")
    if ord_label:
        aliases.append(f"{ord_label} Epístola {base_target}")
        if "São " not in base_target and "São " in target:
            aliases.append(f"{ord_label} Epístola {target}")
    else:
        aliases.append(f"Epístola {base_target}")
        if "São " not in base_target and "São " in target:
            aliases.append(f"Epístola {target}")
    return aliases


def _literal_pattern(text: str) -> str:
    return re.escape(text).replace(r"\ ", r"\s+")


def _is_support_safe_title(alias: str) -> bool:
    alias_norm = _normalize(alias)
    if any(
        alias_norm.startswith(prefix)
        for prefix in ("livro ", "evangelho ", "epistola ", "epistula ", "carta ")
    ):
        return True
    return len(alias_norm.split()) >= 2


def _build_title_specs() -> list[dict]:
    specs: list[dict] = []
    seen: set[tuple[int, str]] = set()

    def add(idx: int, fragment: str, support_safe: bool) -> None:
        key = (idx, fragment)
        if key in seen:
            return
        seen.add(key)
        specs.append(
            {
                "idx": idx,
                "pattern": re.compile(rf"^\s*(?:{fragment})\s*(?:\([^)]*\))?\s*$", re.IGNORECASE),
                "support_safe": support_safe,
            }
        )

    for idx, name in enumerate(LIVROS_PT):
        frag = _literal_pattern(name)
        add(idx, frag, _is_support_safe_title(name))
        add(idx, rf"livro\s+d[aeo]s?\s+{frag}", True)

    for idx, name in enumerate(LIVROS_LA):
        frag = _literal_pattern(name)
        add(idx, frag, _is_support_safe_title(name))
        add(idx, rf"liber\s+{frag}", True)

    for idx, name in enumerate(LIVROS_GR_TRANSLIT):
        frag = _literal_pattern(name)
        add(idx, frag, _is_support_safe_title(name))

    for idx in range(46, 50):
        simple_pt = LIVROS_PT[idx].replace("São ", "")
        simple_la = LIVROS_LA[idx]
        add(idx, rf"evangelho\s+(?:segundo\s+|de\s+){_literal_pattern(simple_pt)}", True)
        add(idx, rf"evangelium\s+secundum\s+{_literal_pattern(simple_la)}", True)

    epistle_titles = {
        51: [r"ep[íi]stola\s+aos?\s+romanos", r"epistula\s+ad\s+romanos"],
        52: [
            r"primeira\s+ep[íi]stola\s+aos?\s+cor[íi]ntios",
            r"1\s+cor[íi]ntios",
            r"i\s+cor[íi]ntios",
        ],
        53: [
            r"segunda\s+ep[íi]stola\s+aos?\s+cor[íi]ntios",
            r"2\s+cor[íi]ntios",
            r"ii\s+cor[íi]ntios",
        ],
        54: [r"ep[íi]stola\s+aos?\s+g[áa]latas", r"epistula\s+ad\s+galatas"],
        55: [r"ep[íi]stola\s+aos?\s+ef[ée]sios", r"epistula\s+ad\s+ephesios"],
        56: [r"ep[íi]stola\s+aos?\s+filipenses", r"epistula\s+ad\s+philippenses"],
        57: [r"ep[íi]stola\s+aos?\s+colossenses", r"epistula\s+ad\s+colossenses"],
        58: [
            r"primeira\s+ep[íi]stola\s+aos?\s+tessalonicenses",
            r"1\s+tessalonicenses",
            r"i\s+tessalonicenses",
        ],
        59: [
            r"segunda\s+ep[íi]stola\s+aos?\s+tessalonicenses",
            r"2\s+tessalonicenses",
            r"ii\s+tessalonicenses",
        ],
        60: [
            r"primeira\s+ep[íi]stola\s+a\s+tim[óo]teo",
            r"1\s+tim[óo]teo",
            r"i\s+tim[óo]teo",
        ],
        61: [
            r"segunda\s+ep[íi]stola\s+a\s+tim[óo]teo",
            r"2\s+tim[óo]teo",
            r"ii\s+tim[óo]teo",
        ],
        62: [r"carta\s+de\s+tito", r"epistula\s+ad\s+titum"],
        64: [r"ep[íi]stola\s+aos?\s+hebreus", r"epistula\s+ad\s+hebraeos"],
        65: [r"ep[íi]stola\s+de\s+tiago", r"epistula\s+iacobi"],
        66: [
            r"primeira\s+ep[íi]stola\s+de\s+pedro",
            r"1\s+pedro",
            r"i\s+pedro",
        ],
        67: [
            r"segunda\s+ep[íi]stola\s+de\s+pedro",
            r"2\s+pedro",
            r"ii\s+pedro",
        ],
        68: [r"primeira\s+ep[íi]stola\s+de\s+jo[aã]o", r"1\s+jo[aã]o", r"i\s+jo[aã]o"],
        69: [r"segunda\s+ep[íi]stola\s+de\s+jo[aã]o", r"2\s+jo[aã]o", r"ii\s+jo[aã]o"],
        70: [r"terceira\s+ep[íi]stola\s+de\s+jo[aã]o", r"3\s+jo[aã]o", r"iii\s+jo[aã]o"],
        71: [r"ep[íi]stola\s+de\s+judas", r"epistula\s+iudae"],
    }
    for idx, patterns in epistle_titles.items():
        for pattern in patterns:
            add(idx, pattern, True)

    return specs


def _build_ref_specs() -> list[dict]:
    specs: list[dict] = []
    seen_patterns: set[tuple[int, str]] = set()

    def add(idx: int, fragment: str) -> None:
        key = (idx, fragment)
        if key in seen_patterns:
            return
        seen_patterns.add(key)
        specs.append(
            {
                "idx": idx,
                "pattern": re.compile(
                    rf"(?P<raw>(?<!\w)(?:{fragment})(?:\s+|,\s*)(?P<main>{_REF_TOKEN})(?![A-Za-z])(?:\s*[:.,]\s*(?P<verse>\d{{1,3}}(?:-\d{{1,3}})?))?(?:\s*\(\s*(?P<alt>{_REF_TOKEN})\s*\))?)",
                    re.IGNORECASE,
                ),
            }
        )
        specs.append(
            {
                "idx": idx,
                "pattern": re.compile(
                    rf"(?P<raw>(?<!\w)(?:{fragment}).{{0,40}}?cap[íi]tulo\s+(?P<main>{_REF_TOKEN})(?:.{{0,20}}?vers[íi]culo\s+(?P<verse>\d{{1,3}}(?:-\d{{1,3}})?))?)",
                    re.IGNORECASE,
                ),
            }
        )

    def add_abbrev(idx: int, fragment: str) -> None:
        key = (idx, f"abbr:{fragment}")
        if key in seen_patterns:
            return
        seen_patterns.add(key)
        specs.append(
            {
                "idx": idx,
                "pattern": re.compile(
                    rf"^\s*(?P<raw>(?:{fragment})\s+(?P<main>{_REF_TOKEN})\s*[:.,]\s*(?P<verse>\d{{1,3}}(?:-\d{{1,3}})?))\s*$",
                    re.IGNORECASE,
                ),
            }
        )

    def _numbered_abbrev_variants(abbr: str) -> list[str]:
        match = re.fullmatch(r"([123])(.*)", abbr)
        if not match:
            return [re.escape(abbr)]
        number, base = match.groups()
        roman = _ORDINAL_ROMAN[number]
        base_fragment = re.escape(base)
        return [
            re.escape(abbr),
            rf"{number}\s+{base_fragment}",
            rf"{roman}\s+{base_fragment}",
        ]

    for idx, name in enumerate(LIVROS_PT):
        add(idx, _literal_pattern(name))
        add(idx, rf"livro\s+d[aeo]s?\s+{_literal_pattern(name)}")

    for idx, name in enumerate(LIVROS_LA):
        add(idx, _literal_pattern(name))
        add(idx, rf"liber\s+{_literal_pattern(name)}")

    for idx in range(46, 50):
        simple_pt = LIVROS_PT[idx].replace("São ", "")
        simple_la = LIVROS_LA[idx]
        add(idx, rf"evangelho\s+(?:segundo\s+|de\s+){_literal_pattern(simple_pt)}")
        add(idx, rf"evangelium\s+secundum\s+{_literal_pattern(simple_la)}")

    for idx, pattern in {
        20: r"ps(?:al(?:m(?:i|us|orum)?)?)?\.",
        28: r"is\.",
        48: r"luc\.",
        49: r"jo\.",
        1: r"exod?\.",
        2: r"(?:lev|lv)\.",
        3: r"(?:num|nm)\.",
        4: r"(?:deut|dt)\.",
        5: r"(?:jos|ios)\.",
        46: r"(?:mt|mat|matth)\.",
        47: r"(?:mc|marc)\.",
        50: r"(?:act|at)\.",
        51: r"(?:rom|rm)\.",
        52: r"(?:1\s*cor|i\s*cor)\.",
        53: r"(?:2\s*cor|ii\s*cor)\.",
        54: r"(?:gal|gl)\.",
        55: r"(?:ef|eph)\.",
        57: r"(?:col|cl)\.",
        64: r"(?:heb|hb)\.",
    }.items():
        add(idx, pattern)

    for abbr, idx in _BOOK_ABBREVIATIONS.items():
        for fragment in _numbered_abbrev_variants(abbr):
            add_abbrev(idx, fragment)

    return specs


_TITLE_SPECS = _build_title_specs()
_REF_SPECS = _build_ref_specs()


def _make_citation_record(
    idx: int,
    raw_fragment: str,
    source_kind: str,
    source_path: str,
    raw_source: str,
    main: int | None = None,
    verse: str | None = None,
    alt: int | None = None,
) -> dict | None:
    if main is None and verse is not None:
        return None
    record = {
        "book": INDEX_BOOK_LABELS[idx],
        "book_canonical": LIVROS_PT[idx],
        "book_idx": idx,
        "number": main,
        "verse": verse,
        "alt_number": alt,
        "raw": raw_fragment.strip(),
        "source_kind": source_kind,
        "source_path": source_path,
        "source_text": raw_source,
        "aliases": [],
    }
    normalized = INDEX_BOOK_LABELS[idx]
    if main is not None:
        normalized = f"{normalized} {main}"
    if verse:
        normalized = f"{normalized},{verse}"
    if alt is not None and alt != main:
        normalized = f"{normalized} ({alt})"
    record["normalized"] = normalized
    if record["number"] is None:
        record["kind"] = "book"
    elif record["verse"]:
        record["kind"] = "chapter_verse"
    else:
        record["kind"] = "chapter"
    record["aliases"] = _epistle_aliases(idx)
    return record


def _comparison_keys(record: dict) -> set[tuple[int, int | None, str | None]]:
    keys = {(record["book_idx"], record["number"], record["verse"])}
    if record["verse"] is None and record["alt_number"] is not None:
        keys.add((record["book_idx"], record["alt_number"], None))
    return keys


def _extract_explicit_citations(
    text: str,
    *,
    source_kind: str,
    source_path: str,
) -> list[dict]:
    found: list[tuple[int, dict]] = []
    seen_spans: set[tuple[int, int, int | None]] = set()
    for spec in _REF_SPECS:
        idx = spec["idx"]
        for match in spec["pattern"].finditer(text):
            main = _parse_ref_number(match.group("main"))
            if main is None:
                continue
            verse = match.groupdict().get("verse")
            if verse and not _VERSE_RE.fullmatch(verse.strip()):
                verse = None
            alt = _parse_ref_number(match.groupdict().get("alt"))
            span_key = (idx, match.start(), main)
            if span_key in seen_spans:
                continue
            record = _make_citation_record(
                idx,
                match.group("raw"),
                source_kind,
                source_path,
                text,
                main=main,
                verse=verse.strip() if verse else None,
                alt=alt,
            )
            if record:
                found.append((match.start(), record))
                seen_spans.add(span_key)
    found.sort(key=lambda item: item[0])
    return [record for _, record in found]


def _extract_book_only_titles(
    text: str,
    *,
    source_kind: str,
    source_path: str,
    support_mode: bool,
) -> list[dict]:
    found: list[dict] = []
    for spec in _TITLE_SPECS:
        if support_mode and not spec["support_safe"]:
            continue
        if spec["pattern"].fullmatch(text):
            record = _make_citation_record(
                spec["idx"],
                text,
                source_kind,
                source_path,
                text,
            )
            if record:
                found.append(record)
    return found


def _extract_citations_from_value(
    raw_value: str,
    *,
    source_kind: str,
    source_path: str,
    support_mode: bool,
) -> list[dict]:
    cleaned = clean_keyword_original(raw_value)
    if not cleaned:
        return []
    explicit = _extract_explicit_citations(
        cleaned,
        source_kind=source_kind,
        source_path=source_path,
    )
    if explicit:
        return explicit
    return _extract_book_only_titles(
        cleaned,
        source_kind=source_kind,
        source_path=source_path,
        support_mode=support_mode,
    )


def _coerce_keywords_payload(payload: object) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"keywords": [item for item in payload if isinstance(item, str)]}
    return {}


def _get_category_items(payload: object, target_name: str) -> list[str]:
    payload = _coerce_keywords_payload(payload)
    categorias = payload.get("categorias")
    if not isinstance(categorias, dict):
        return []
    target_norm = _normalize(target_name)
    for key, value in categorias.items():
        if _normalize(str(key)) == target_norm and isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str)]
    return []


def _get_keywords_items(payload: object) -> list[str]:
    payload = _coerce_keywords_payload(payload)
    keywords = payload.get("keywords")
    if not isinstance(keywords, list):
        return []
    return [str(item) for item in keywords if isinstance(item, str)]


def _reconcile_keyword_support(anchors: list[dict], keyword_hits: list[dict]) -> dict[str, list[dict]]:
    support_map: dict[str, list[dict]] = {}
    for anchor in anchors:
        anchor_keys = _comparison_keys(anchor)
        matches: list[dict] = []
        for candidate in keyword_hits:
            candidate_keys = _comparison_keys(candidate)
            if anchor_keys & candidate_keys:
                matches.append(candidate)
                continue
            if anchor["number"] is None and candidate["book_idx"] == anchor["book_idx"]:
                matches.append(candidate)
        support_map[anchor["normalized"]] = matches
    return support_map


def keywords_check(keywords_json: object, debug_label: str = "") -> bool:
    obras_citadas = _get_category_items(keywords_json, "obras_citadas")
    keywords_items = _get_keywords_items(keywords_json)

    anchor_hits: list[dict] = []
    seen_anchor_keys: set[tuple[int, int | None, str | None, str]] = set()
    for idx, raw_value in enumerate(obras_citadas):
        for record in _extract_citations_from_value(
            raw_value,
            source_kind="obras_citadas",
            source_path=f"categorias.obras_citadas[{idx}]",
            support_mode=False,
        ):
            dedupe_key = (
                record["book_idx"],
                record["number"],
                record["verse"],
                record["source_path"],
            )
            if dedupe_key in seen_anchor_keys:
                continue
            seen_anchor_keys.add(dedupe_key)
            anchor_hits.append(record)

    if not anchor_hits:
        return False

    keyword_hits: list[dict] = []
    seen_keyword_keys: set[tuple[int, int | None, str | None, str]] = set()
    for idx, raw_value in enumerate(keywords_items):
        for record in _extract_citations_from_value(
            raw_value,
            source_kind="keywords",
            source_path=f"keywords[{idx}]",
            support_mode=True,
        ):
            dedupe_key = (
                record["book_idx"],
                record["number"],
                record["verse"],
                record["source_path"],
            )
            if dedupe_key in seen_keyword_keys:
                continue
            seen_keyword_keys.add(dedupe_key)
            keyword_hits.append(record)

    support_map = _reconcile_keyword_support(anchor_hits, keyword_hits)

    header = "[debug] referencias biblicas normalizadas:"
    if debug_label:
        header = f"{header} {debug_label}"
    print(header)
    exported: list[dict] = []
    for record in anchor_hits:
        print(
            f"  - {record['source_path']}: {record['raw']!r} -> {record['normalized']}"
        )
        support_list = []
        for support in support_map.get(record["normalized"], []):
            print(
                f"    [keywords] {support['source_path']}: {support['raw']!r} -> {support['normalized']}"
            )
            support_list.append(
                {
                    "source_kind": support["source_kind"],
                    "source_path": support["source_path"],
                    "raw": support["raw"],
                    "normalized": support["normalized"],
                    "book": support["book"],
                    "book_idx": support["book_idx"],
                    "kind": support["kind"],
                    "number": support["number"],
                    "verse": support["verse"],
                    "alt_number": support["alt_number"],
                }
            )
        exported.append(
            {
                "book": record["book"],
                "book_canonical": record["book_canonical"],
                "book_idx": record["book_idx"],
                "kind": record["kind"],
                "number": record["number"],
                "verse": record["verse"],
                "alt_number": record["alt_number"],
                "normalized": record["normalized"],
                "aliases": record.get("aliases", []),
                "raw": record["raw"],
                "source_kind": record["source_kind"],
                "source_path": record["source_path"],
                "supports": support_list,
            }
        )
    if exported:
        print("[debug] citations_json:", json.dumps(exported, ensure_ascii=False))
    return True


# ── Demo / sanity check ─────────────────────────────────────────────────────

if __name__ == "__main__":
    test_keywords = [
        # lookup direto — formas canônicas
        "Salmos",
        "Isaías",
        "Atos dos Apóstolos",
        "Oséias",
        "Osee",
        "Ōsēe",
        # lookup direto — prefixo neutro
        "Evangelho de Lucas",
        "Evangelho segundo Marcos",
        "Livro de Daniel",
        "Carta de Tito",
        # janela deslizante — "Salmo NN", "Sal NN"
        "Salmo 22",
        "Salmo 110 (109)",
        "Sal 30",
        # janela deslizante — referências com capítulo:versículo
        "Isaías 53",
        "Lucas 24:39",
        "João 1:1",
        # janela deslizante — construções descritivas
        "cumprimento profético de Isaías",
        "conforme o Salmo 2",
        "como diz São Mateus",
        "conforme Oséias profetizou",
        # latim direto
        "Psalmi",
        "Isaias",
        "Ioannes",
        "Kata Matthaion",
        # dados reais dos exemplos no script
        "Salmo 22",
        "Isaías 53",
        "Salmo 110 (109)",
        "Salmo 2",
        "Salmo 22 (21)",
        "Livro de Isaías",
        "Livro de Oséias",
        "Evangelho segundo Lucas",
        "Livro de Daniel",
        "Evangelho de Lucas",
        "Livro de Oseias",
        "Livro de Amós",
        "Salmo 1",
        "Salmo 30",
        # não-livros → nenhum hit esperado
        "substituição penal",
        "cristologia",
        "archontes",
        "phantasma",
        "justiça retributiva",
        "silêncio de Cristo",
    ]

    print("=== lookup_book (direto) ===")
    direto = [
        "Salmos",
        "Evangelho de Lucas",
        "Livro de Daniel",
        "Salmo 22",
        "Isaías 53",
        "Lucas 24:39",
    ]
    for kw in direto:
        r = lookup_book(kw)
        print(f"  {kw!r:40s} → {r}")

    print("\n=== keywords_cite_books (completo) ===")
    seen = set()
    for orig, canon, idx in keywords_cite_books(test_keywords):
        if orig not in seen:
            print(f"  {orig!r:40s} → {canon} (idx {idx})")
            seen.add(orig)

    # Livros sem nenhum hit (esperados)
    no_hit = [
        kw
        for kw in test_keywords
        if kw not in {o for o, _, _ in keywords_cite_books(test_keywords)}
    ]
    print("\n=== sem hit (esperado) ===")
    for kw in no_hit:
        print(f"  {kw!r}")


    con = connect_readwrite(Path("data/patristica_resumos.db"))
    ensure_bible_citation_schema(con)

    rows = query_keywords(con)

    for row in rows:
        row_id = row["id"]
        keywords = json.loads(row["keywords_json"]) if row["keywords_json"] else []
        keywords_check(keywords, debug_label=f"(row_id={row_id})")
