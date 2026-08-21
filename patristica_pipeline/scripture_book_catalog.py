"""Catholic biblical-book catalogue for conservative OCR citation matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ScriptureBook:
    key: str
    aliases: tuple[str, ...]
    ordinal: int | None = None


def normalize_book_alias(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = (
        text.casefold()
        .replace("ſ", "s")
        .replace("æ", "ae")
        .replace("œ", "oe")
    )
    text = re.sub(r"[^\w\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _aliases(*values: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


_ORDINAL_WORDS = {
    1: ("1", "i", "first", "premier", "premiere", "primeiro", "primeira", "primus", "prima"),
    2: ("2", "ii", "second", "deuxieme", "segundo", "segunda", "secundus", "secunda"),
    3: ("3", "iii", "third", "troisieme", "terceiro", "terceira", "tertius", "tertia"),
    4: ("4", "iv", "fourth", "quatrieme", "quarto", "quarta", "quartus", "quarta"),
}


def _numbered(key: str, ordinal: int, *bases: str, extra: Iterable[str] = ()) -> ScriptureBook:
    generated = [
        f"{marker} {base}"
        for marker in _ORDINAL_WORDS[ordinal]
        for base in bases
    ]
    return ScriptureBook(key, _aliases(key, *generated, *extra), ordinal)


BOOKS: tuple[ScriptureBook, ...] = (
    ScriptureBook("genesis", _aliases("genesis", "genese", "genese", "genesis", "gen", "gn")),
    ScriptureBook("exodo", _aliases("exodo", "exode", "exodus", "exod", "ex")),
    ScriptureBook("levitico", _aliases("levitico", "levitique", "leviticus", "levit", "lev", "lv")),
    ScriptureBook(
        "numeros",
        _aliases(
            "numeros", "nombres", "numbers", "numeri", "numerorum", "num", "nm",
        ),
    ),
    ScriptureBook("deuteronomio", _aliases("deuteronomio", "deuteronome", "deuteronomy", "deuteronomium", "deuter", "deut", "dt")),
    ScriptureBook("josue", _aliases("josue", "joshua", "iosue", "jos")),
    ScriptureBook(
        "juizes",
        _aliases("juizes", "juges", "judges", "judices", "iudicum", "jz"),
    ),
    ScriptureBook("rute", _aliases("rute", "ruth", "rt")),
    _numbered("1 samuel", 1, "samuel", "sam", "sm"),
    _numbered("2 samuel", 2, "samuel", "sam", "sm"),
    _numbered(
        "1 reis",
        1,
        "reis",
        "kings",
        "kgs",
        extra=("iii regum", "3 regum", "iii reg", "3 reg"),
    ),
    _numbered(
        "2 reis",
        2,
        "reis",
        "kings",
        "kgs",
        extra=("iv regum", "4 regum", "iv reg", "4 reg"),
    ),
    _numbered(
        "1 cronicas",
        1,
        "cronicas", "chroniques", "chronicles", "paralipomenon",
        "paralipomenes", "paralip", "chr", "cr",
    ),
    _numbered(
        "2 cronicas",
        2,
        "cronicas", "chroniques", "chronicles", "paralipomenon",
        "paralipomenes", "paralip", "chr", "cr",
    ),
    ScriptureBook("esdras", _aliases("esdras", "ezra", "esd")),
    ScriptureBook("neemias", _aliases("neemias", "nehemie", "nehemiah", "nehemiae", "neh", "ne")),
    ScriptureBook("tobias", _aliases("tobias", "tobie", "tobit", "tobiae", "tob", "tb")),
    ScriptureBook("judite", _aliases("judite", "judith", "iudith", "jdt")),
    ScriptureBook("ester", _aliases("ester", "esther", "est")),
    _numbered(
        "1 macabeus",
        1,
        "macabeus", "macchabees", "maccabees", "maccabaeorum",
        "machabaeorum", "machabeorum", "machabeus", "machab", "macchab",
        "mach", "macc", "mac",
    ),
    _numbered(
        "2 macabeus",
        2,
        "macabeus", "macchabees", "maccabees", "maccabaeorum",
        "machabaeorum", "machabeorum", "machabeus", "machab", "macchab",
        "mach", "macc", "mac",
    ),
    ScriptureBook("jo", _aliases("jo", "job", "iob", "iyob")),
    ScriptureBook(
        "salmos",
        _aliases(
            "salmos", "salmo", "psaumes", "psaume", "psalms", "psalmi",
            "psalmus", "psalmo", "psalmorum", "psalterium", "psalm", "psal",
            "ps", "sl",
        ),
    ),
    ScriptureBook(
        "proverbios",
        _aliases(
            "proverbios", "proverbes", "proverbs", "proverbia",
            "proverbiorum", "prov", "prv",
        ),
    ),
    ScriptureBook("eclesiastes", _aliases("eclesiastes", "ecclesiaste", "ecclesiastes", "qohelet", "qoh")),
    ScriptureBook(
        "cantico dos canticos",
        _aliases(
            "cantico dos canticos",
            "cantique des cantiques",
            "song of songs",
            "song of solomon",
            "canticum canticorum",
            "cant",
        ),
    ),
    ScriptureBook(
        "sabedoria",
        _aliases(
            "sabedoria", "sagesse", "wisdom", "sapientia", "sapientiae",
            "wis", "sap", "sap salom",
        ),
    ),
    ScriptureBook(
        "eclesiastico",
        _aliases(
            "eclesiastico",
            "siracida",
            "sirac",
            "sir",
            "sirach",
            "ecclesiastique",
            "ecclesiasticus",
            "ecclesiastici",
            "eccli",
            "ben sira",
            "si",
        ),
    ),
    ScriptureBook("isaias", _aliases("isaias", "esaias", "isaie", "isaiah", "isaiae", "isa", "is")),
    ScriptureBook("jeremias", _aliases("jeremias", "jeremie", "jeremiah", "ieremias", "jerem", "jer", "jr")),
    ScriptureBook("lamentacoes", _aliases("lamentacoes", "lamentations", "lamentationes", "lam")),
    ScriptureBook("baruc", _aliases("baruc", "baruch", "bar")),
    ScriptureBook("ezequiel", _aliases("ezequiel", "ezechiel", "ezekiel", "ezech", "ezek")),
    ScriptureBook("daniel", _aliases("daniel", "dan")),
    ScriptureBook(
        "oseias",
        _aliases("oseias", "osee", "ose", "hosea", "os", "hos"),
    ),
    ScriptureBook("joel", _aliases("joel", "ioel", "jl")),
    ScriptureBook("amos", _aliases("amos", "am")),
    ScriptureBook("abdias", _aliases("abdias", "abdias", "obadiah", "obad", "abd")),
    ScriptureBook("jonas", _aliases("jonas", "ionas", "jonah", "jon")),
    ScriptureBook("miqueias", _aliases("miqueias", "michee", "micah", "micheas", "mich", "mic")),
    ScriptureBook("naum", _aliases("naum", "nahum", "nah")),
    ScriptureBook("habacuc", _aliases("habacuc", "habacuque", "habakkuk", "habac", "hab")),
    ScriptureBook("sofonias", _aliases("sofonias", "sophonie", "zephaniah", "sophonias", "soph")),
    ScriptureBook("ageu", _aliases("ageu", "aggee", "haggai", "aggaeus", "ag")),
    ScriptureBook("zacarias", _aliases("zacarias", "zacharie", "zechariah", "zacharias", "zachar", "zac", "zech")),
    ScriptureBook("malaquias", _aliases("malaquias", "malachie", "malachi", "malachias", "malach", "mal")),
    ScriptureBook(
        "mateus",
        _aliases(
            "mateus",
            "sao mateus",
            "matthieu",
            "saint matthieu",
            "matthew",
            "matthaeus",
            "matthaeum",
            "matth",
            "matt",
            "mt",
        ),
    ),
    ScriptureBook(
        "marcos",
        _aliases(
            "marcos", "sao marcos", "marc", "saint marc", "mark", "marcus",
            "marcum", "mc", "mk",
        ),
    ),
    ScriptureBook(
        "lucas",
        _aliases(
            "lucas", "sao lucas", "saint luc", "luc", "luke", "lucam", "lc", "lk",
        ),
    ),
    ScriptureBook(
        "joao",
        _aliases(
            "joao",
            "sao joao",
            "jean",
            "saint jean",
            "john",
            "ioannes",
            "joannes",
            "ioannem",
            "joannem",
            "ioan",
            "joan",
            "jn",
        ),
    ),
    ScriptureBook(
        "atos",
        _aliases(
            "atos",
            "atos dos apostolos",
            "actes",
            "actes des apotres",
            "acts",
            "acts of the apostles",
            "actus apostolorum",
            "act apost",
            "actus",
            "act",
        ),
    ),
    ScriptureBook("romanos", _aliases("romanos", "romains", "romans", "romani", "rom", "rm")),
    _numbered("1 corintios", 1, "corintios", "corinthiens", "corinthians", "corinthios", "corinth", "cor"),
    _numbered("2 corintios", 2, "corintios", "corinthiens", "corinthians", "corinthios", "corinth", "cor"),
    ScriptureBook("galatas", _aliases("galatas", "galates", "galatians", "galatae", "galat", "gal")),
    ScriptureBook("efesios", _aliases("efesios", "ephesios", "ephesiens", "ephesians", "ephesii", "ephes", "eph", "ef")),
    ScriptureBook(
        "filipenses",
        _aliases(
            "filipenses", "philippiens", "philippians", "philippenses",
            "philipp", "philip", "phil",
        ),
    ),
    ScriptureBook("colossenses", _aliases("colossenses", "colossiens", "colossians", "colossenses", "coloss", "col")),
    _numbered("1 tessalonicenses", 1, "tessalonicenses", "thessaloniciens", "thessalonians", "thessalonicenses", "thessal", "thess", "thes", "tess"),
    _numbered("2 tessalonicenses", 2, "tessalonicenses", "thessaloniciens", "thessalonians", "thessalonicenses", "thessal", "thess", "thes", "tess"),
    _numbered(
        "1 timoteo",
        1,
        "timoteo", "timothee", "timothy", "timotheum", "timoth", "tim", "tm",
    ),
    _numbered(
        "2 timoteo",
        2,
        "timoteo", "timothee", "timothy", "timotheum", "timoth", "tim", "tm",
    ),
    ScriptureBook("tito", _aliases("tito", "tite", "titus", "tit")),
    ScriptureBook("filemon", _aliases("filemon", "philemon", "philem", "phm")),
    ScriptureBook("hebreus", _aliases("hebreus", "hebreux", "hebrews", "hebraeos", "hebr", "heb")),
    ScriptureBook(
        "tiago",
        _aliases("tiago", "sao tiago", "jacques", "saint jacques", "james", "iacobus", "jacobi", "jac", "iac"),
    ),
    _numbered(
        "1 pedro",
        1,
        "pedro",
        "sao pedro",
        "pierre",
        "saint pierre",
        "peter",
        "saint peter",
        "petri",
        "petr",
        "pet",
    ),
    _numbered(
        "2 pedro",
        2,
        "pedro",
        "sao pedro",
        "pierre",
        "saint pierre",
        "peter",
        "saint peter",
        "petri",
        "petr",
        "pet",
    ),
    _numbered("1 joao", 1, "joao", "sao joao", "jean", "saint jean", "john", "saint john", "ioannis", "ioan", "joan", "jn"),
    _numbered("2 joao", 2, "joao", "sao joao", "jean", "saint jean", "john", "saint john", "ioannis", "ioan", "joan", "jn"),
    _numbered("3 joao", 3, "joao", "sao joao", "jean", "saint jean", "john", "saint john", "ioannis", "ioan", "joan", "jn"),
    ScriptureBook("judas", _aliases("judas", "sao judas", "jude", "saint jude", "iudae")),
    ScriptureBook("apocalipse", _aliases("apocalipse", "apocalypse", "revelation", "apocalypsis", "apoc", "rev")),
)


BOOK_BY_KEY = {book.key: book for book in BOOKS}
MAX_CHAPTER_BY_BOOK = {
    "genesis": 50,
    "exodo": 40,
    "levitico": 27,
    "numeros": 36,
    "deuteronomio": 34,
    "josue": 24,
    "juizes": 21,
    "rute": 4,
    "1 samuel": 31,
    "2 samuel": 24,
    "1 reis": 22,
    "2 reis": 25,
    "1 cronicas": 29,
    "2 cronicas": 36,
    "esdras": 10,
    "neemias": 13,
    "tobias": 14,
    "judite": 16,
    "ester": 16,
    "1 macabeus": 16,
    "2 macabeus": 15,
    "jo": 42,
    "salmos": 151,
    "proverbios": 31,
    "eclesiastes": 12,
    "cantico dos canticos": 8,
    "sabedoria": 19,
    "eclesiastico": 51,
    "isaias": 66,
    "jeremias": 52,
    "lamentacoes": 5,
    "baruc": 6,
    "ezequiel": 48,
    "daniel": 14,
    "oseias": 14,
    "joel": 4,
    "amos": 9,
    "abdias": 1,
    "jonas": 4,
    "miqueias": 7,
    "naum": 3,
    "habacuc": 3,
    "sofonias": 3,
    "ageu": 2,
    "zacarias": 14,
    "malaquias": 4,
    "mateus": 28,
    "marcos": 16,
    "lucas": 24,
    "joao": 21,
    "atos": 28,
    "romanos": 16,
    "1 corintios": 16,
    "2 corintios": 13,
    "galatas": 6,
    "efesios": 6,
    "filipenses": 4,
    "colossenses": 4,
    "1 tessalonicenses": 5,
    "2 tessalonicenses": 3,
    "1 timoteo": 6,
    "2 timoteo": 4,
    "tito": 3,
    "filemon": 1,
    "hebreus": 13,
    "tiago": 5,
    "1 pedro": 5,
    "2 pedro": 3,
    "1 joao": 5,
    "2 joao": 1,
    "3 joao": 1,
    "judas": 1,
    "apocalipse": 22,
}
if set(MAX_CHAPTER_BY_BOOK) != set(BOOK_BY_KEY):
    raise RuntimeError("Chapter-bound catalogue is out of sync with BOOKS")
CANONICAL_BOOK_NAMES_BY_LOCALE = {
    "fr": dict(zip(BOOK_BY_KEY, (
        "Genèse", "Exode", "Lévitique", "Nombres", "Deutéronome", "Josué",
        "Juges", "Ruth", "1 Samuel", "2 Samuel", "1 Rois", "2 Rois",
        "1 Chroniques", "2 Chroniques", "Esdras", "Néhémie", "Tobie", "Judith",
        "Esther", "1 Maccabées", "2 Maccabées", "Job", "Psaumes", "Proverbes",
        "Ecclésiaste", "Cantique des cantiques", "Sagesse", "Siracide", "Isaïe",
        "Jérémie", "Lamentations", "Baruch", "Ézéchiel", "Daniel", "Osée", "Joël",
        "Amos", "Abdias", "Jonas", "Michée", "Nahum", "Habaquq", "Sophonie",
        "Aggée", "Zacharie", "Malachie", "Matthieu", "Marc", "Luc", "Jean",
        "Actes des Apôtres", "Romains", "1 Corinthiens", "2 Corinthiens", "Galates",
        "Éphésiens", "Philippiens", "Colossiens", "1 Thessaloniciens",
        "2 Thessaloniciens", "1 Timothée", "2 Timothée", "Tite", "Philémon",
        "Hébreux", "Jacques", "1 Pierre", "2 Pierre", "1 Jean", "2 Jean",
        "3 Jean", "Jude", "Apocalypse",
    ))),
    "en": dict(zip(BOOK_BY_KEY, (
        "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua",
        "Judges", "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings",
        "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah", "Tobit", "Judith",
        "Esther", "1 Maccabees", "2 Maccabees", "Job", "Psalms", "Proverbs",
        "Ecclesiastes", "Song of Songs", "Wisdom", "Sirach", "Isaiah", "Jeremiah",
        "Lamentations", "Baruch", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos",
        "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai",
        "Zechariah", "Malachi", "Matthew", "Mark", "Luke", "John", "Acts of the Apostles",
        "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians",
        "Philippians", "Colossians", "1 Thessalonians", "2 Thessalonians",
        "1 Timothy", "2 Timothy", "Titus", "Philemon", "Hebrews", "James",
        "1 Peter", "2 Peter", "1 John", "2 John", "3 John", "Jude", "Revelation",
    ))),
    "it": dict(zip(BOOK_BY_KEY, (
        "Genesi", "Esodo", "Levitico", "Numeri", "Deuteronomio", "Giosuè",
        "Giudici", "Rut", "1 Samuele", "2 Samuele", "1 Re", "2 Re",
        "1 Cronache", "2 Cronache", "Esdra", "Neemia", "Tobia", "Giuditta",
        "Ester", "1 Maccabei", "2 Maccabei", "Giobbe", "Salmi", "Proverbi",
        "Qoelet", "Cantico dei Cantici", "Sapienza", "Siracide", "Isaia", "Geremia",
        "Lamentazioni", "Baruc", "Ezechiele", "Daniele", "Osea", "Gioele", "Amos",
        "Abdia", "Giona", "Michea", "Naum", "Abacuc", "Sofonia", "Aggeo",
        "Zaccaria", "Malachia", "Matteo", "Marco", "Luca", "Giovanni",
        "Atti degli Apostoli", "Romani", "1 Corinzi", "2 Corinzi", "Galati",
        "Efesini", "Filippesi", "Colossesi", "1 Tessalonicesi", "2 Tessalonicesi",
        "1 Timoteo", "2 Timoteo", "Tito", "Filemone", "Ebrei", "Giacomo",
        "1 Pietro", "2 Pietro", "1 Giovanni", "2 Giovanni", "3 Giovanni", "Giuda",
        "Apocalisse",
    ))),
    "la": dict(zip(BOOK_BY_KEY, (
        "Genesis", "Exodus", "Leviticus", "Numeri", "Deuteronomium", "Iosue",
        "Iudicum", "Ruth", "I Regum", "II Regum", "III Regum", "IV Regum",
        "I Paralipomenon", "II Paralipomenon", "Esdrae", "Nehemiae", "Tobiae",
        "Iudith", "Esther", "I Machabaeorum", "II Machabaeorum", "Iob", "Psalmi",
        "Proverbia", "Ecclesiastes", "Canticum Canticorum", "Sapientia",
        "Ecclesiasticus", "Isaias", "Ieremias", "Lamentationes", "Baruch",
        "Ezechiel", "Daniel", "Osee", "Ioel", "Amos", "Abdias", "Ionas",
        "Michaeas", "Nahum", "Habacuc", "Sophonias", "Aggaeus", "Zacharias",
        "Malachias", "Matthaeus", "Marcus", "Lucas", "Ioannes", "Actus Apostolorum",
        "Romani", "I Corinthii", "II Corinthii", "Galatae", "Ephesii", "Philippenses",
        "Colossenses", "I Thessalonicenses", "II Thessalonicenses", "I Timotheum",
        "II Timotheum", "Titus", "Philemon", "Hebraei", "Iacobi", "I Petri",
        "II Petri", "I Ioannis", "II Ioannis", "III Ioannis", "Iudae", "Apocalypsis",
    ))),
}
if any(set(names) != set(BOOK_BY_KEY) for names in CANONICAL_BOOK_NAMES_BY_LOCALE.values()):
    raise RuntimeError("Localized biblical-book catalogue is out of sync with BOOKS")
CANONICAL_BOOK_LABELS = {
    "genesis": "Gênesis",
    "exodo": "Êxodo",
    "levitico": "Levítico",
    "numeros": "Números",
    "deuteronomio": "Deuteronômio",
    "josue": "Josué",
    "juizes": "Juízes",
    "rute": "Rute",
    "1 samuel": "1 Samuel",
    "2 samuel": "2 Samuel",
    "1 reis": "1 Reis",
    "2 reis": "2 Reis",
    "1 cronicas": "1 Crônicas",
    "2 cronicas": "2 Crônicas",
    "esdras": "Esdras",
    "neemias": "Neemias",
    "tobias": "Tobias",
    "judite": "Judite",
    "ester": "Ester",
    "1 macabeus": "1 Macabeus",
    "2 macabeus": "2 Macabeus",
    "jo": "Jó",
    "salmos": "Salmos",
    "proverbios": "Provérbios",
    "eclesiastes": "Eclesiastes",
    "cantico dos canticos": "Cântico dos Cânticos",
    "sabedoria": "Sabedoria",
    "eclesiastico": "Eclesiástico",
    "isaias": "Isaías",
    "jeremias": "Jeremias",
    "lamentacoes": "Lamentações",
    "baruc": "Baruc",
    "ezequiel": "Ezequiel",
    "daniel": "Daniel",
    "oseias": "Oseias",
    "joel": "Joel",
    "amos": "Amós",
    "abdias": "Abdias",
    "jonas": "Jonas",
    "miqueias": "Miqueias",
    "naum": "Naum",
    "habacuc": "Habacuc",
    "sofonias": "Sofonias",
    "ageu": "Ageu",
    "zacarias": "Zacarias",
    "malaquias": "Malaquias",
    "mateus": "São Mateus",
    "marcos": "São Marcos",
    "lucas": "São Lucas",
    "joao": "São João",
    "atos": "Atos dos Apóstolos",
    "romanos": "Romanos",
    "1 corintios": "1 Coríntios",
    "2 corintios": "2 Coríntios",
    "galatas": "Gálatas",
    "efesios": "Efésios",
    "filipenses": "Filipenses",
    "colossenses": "Colossenses",
    "1 tessalonicenses": "1 Tessalonicenses",
    "2 tessalonicenses": "2 Tessalonicenses",
    "1 timoteo": "1 Timóteo",
    "2 timoteo": "2 Timóteo",
    "tito": "Tito",
    "filemon": "Filêmon",
    "hebreus": "Hebreus",
    "tiago": "São Tiago",
    "1 pedro": "1 Pedro",
    "2 pedro": "2 Pedro",
    "1 joao": "1 João",
    "2 joao": "2 João",
    "3 joao": "3 João",
    "judas": "São Judas",
    "apocalipse": "Apocalipse",
}
_ALIAS_TO_KEYS: dict[str, set[str]] = {}
for _book in BOOKS:
    _localized_aliases = (
        names[_book.key] for names in CANONICAL_BOOK_NAMES_BY_LOCALE.values()
    )
    for _alias in (*_book.aliases, *_localized_aliases):
        _ALIAS_TO_KEYS.setdefault(normalize_book_alias(_alias), set()).add(_book.key)

_VULGATE_MIGNE_ALIASES = {
    "jo": "joao",
    "i regum": "1 samuel",
    "1 regum": "1 samuel",
    "i reg": "1 samuel",
    "1 reg": "1 samuel",
    "ii regum": "2 samuel",
    "2 regum": "2 samuel",
    "ii reg": "2 samuel",
    "2 reg": "2 samuel",
    "iii regum": "1 reis",
    "3 regum": "1 reis",
    "iii reg": "1 reis",
    "3 reg": "1 reis",
    "iv regum": "2 reis",
    "4 regum": "2 reis",
    "iv reg": "2 reis",
    "4 reg": "2 reis",
    "i esdrae": "esdras",
    "1 esdrae": "esdras",
    "i esdr": "esdras",
    "1 esdr": "esdras",
    "i esd": "esdras",
    "1 esd": "esdras",
    "ii esdrae": "neemias",
    "2 esdrae": "neemias",
    "ii esdr": "neemias",
    "2 esdr": "neemias",
    "ii esd": "neemias",
    "2 esd": "neemias",
}
for _contextual_alias in ("i regum", "ii regum", "esdrae"):
    _ALIAS_TO_KEYS.pop(_contextual_alias, None)

_OLD_FRENCH_VULGATE_ALIASES = {
    "i rois": "1 samuel",
    "1 rois": "1 samuel",
    "ii rois": "2 samuel",
    "2 rois": "2 samuel",
    "iii rois": "1 reis",
    "3 rois": "1 reis",
    "iv rois": "2 reis",
    "4 rois": "2 reis",
}

_OLD_ENGLISH_VULGATE_ALIASES = {
    "i kings": "1 samuel",
    "1 kings": "1 samuel",
    "ii kings": "2 samuel",
    "2 kings": "2 samuel",
    "iii kings": "1 reis",
    "3 kings": "1 reis",
    "iv kings": "2 reis",
    "4 kings": "2 reis",
}

_HISTORICAL_NONCANONICAL_ALIASES = {
    "iii esdras": "3 esdras",
    "3 esdras": "3 esdras",
    "iv esdras": "4 esdras",
    "4 esdras": "4 esdras",
    "iii esdrae": "3 esdras",
    "3 esdrae": "3 esdras",
    "iv esdrae": "4 esdras",
    "4 esdrae": "4 esdras",
}


def _alias_candidates(folded: str) -> tuple[str, ...]:
    candidates = [folded]
    stripped = re.sub(r"^liber\s+", "", folded)
    if stripped != folded:
        candidates.append(stripped)
    match = re.match(
        r"^epist(?:ola)?\s+"
        r"(i{1,3}|iv|[1-4]|prima|secunda|tertia|quarta)\s+ad\s+(.+)$",
        folded,
    )
    if match:
        candidates.append(f"{match.group(1)} {match.group(2)}")
    stripped = re.sub(r"^epist(?:ola)?\s+ad\s+", "", folded)
    if stripped != folded:
        candidates.append(stripped)
    stripped = re.sub(r"^evangelium\s+secundum\s+", "", folded)
    if stripped != folded:
        candidates.append(stripped)
    return tuple(dict.fromkeys(candidates))


def canonical_book_key(
    value: object,
    *,
    tradition: str | None = None,
) -> str | None:
    raw_value = str(value or "").strip()
    folded_value = normalize_book_alias(value)
    if not folded_value:
        return None
    tradition_key = normalize_book_alias(str(tradition or "").replace("_", " "))
    for folded in _alias_candidates(folded_value):
        if tradition_key in {"vulgate", "vulgate migne", "migne patrologia"}:
            contextual = _VULGATE_MIGNE_ALIASES.get(folded)
            if contextual:
                return contextual
        if tradition_key in {
            "old french vulgate",
            "po",
            "po french editorial",
            "patrologia orientalis",
        }:
            contextual = _OLD_FRENCH_VULGATE_ALIASES.get(folded)
            if contextual:
                return contextual
        if tradition_key in {
            "old english vulgate",
            "po old english",
            "po english editorial",
        }:
            contextual = _OLD_ENGLISH_VULGATE_ALIASES.get(folded)
            if contextual:
                return contextual
        if folded == "jo" and re.fullmatch(r"(?i)jo\.", raw_value):
            continue
        keys = _ALIAS_TO_KEYS.get(folded, set())
        if len(keys) == 1:
            return next(iter(keys))
    return None


def historical_noncanonical_book_key(value: object) -> str | None:
    folded = normalize_book_alias(value)
    for candidate in _alias_candidates(folded):
        historical_key = _HISTORICAL_NONCANONICAL_ALIASES.get(candidate)
        if historical_key:
            return historical_key
    return None


def contextual_book_tradition(
    collection: object,
    *book_values: object,
    local_profile: object = None,
    section_uses_old_english: bool = False,
) -> str | None:
    collection_key = str(collection or "").strip().upper()
    if collection_key in {"PG", "PL"}:
        return "vulgate_migne"
    if collection_key != "PO":
        return None

    profile = normalize_book_alias(str(local_profile or "").replace("_", " "))
    if profile in {
        "old english vulgate",
        "po old english",
        "po english editorial",
    }:
        return "po_old_english"
    if profile in {
        "old french vulgate",
        "po french editorial",
    }:
        return "po_french_editorial"

    folded = " ".join(normalize_book_alias(value) for value in book_values)
    if re.search(r"\b(?:i{1,3}|iv|[1-4])\s+rois\b", folded):
        return "po_french_editorial"
    if section_uses_old_english or re.search(
        r"\b(?:iii|iv|3|4)\s+kings\b",
        folded,
    ):
        return "po_old_english"
    return None


def canonical_book_label(book_key: object) -> str | None:
    return CANONICAL_BOOK_LABELS.get(str(book_key or "").strip())


def alias_has_matching_ordinal(value: object, book_key: str) -> bool:
    book = BOOK_BY_KEY.get(book_key)
    if book is None or book.ordinal is None:
        return True
    folded = normalize_book_alias(value)
    markers = _ORDINAL_WORDS[book.ordinal]
    return any(
        re.search(rf"(?:^|\s){re.escape(marker)}(?:\s|$)", folded)
        for marker in markers
    )


def aliases_for_book(book_key: str, *observed_values: object) -> set[str]:
    book = BOOK_BY_KEY.get(book_key)
    if book is None:
        return set()
    aliases = {normalize_book_alias(alias) for alias in book.aliases}
    aliases.update(
        normalize_book_alias(names[book_key])
        for names in CANONICAL_BOOK_NAMES_BY_LOCALE.values()
    )
    for value in observed_values:
        folded = normalize_book_alias(value)
        if folded and alias_has_matching_ordinal(folded, book_key):
            aliases.add(folded)
    return {alias for alias in aliases if len(alias) >= 2}


__all__ = [
    "BOOKS",
    "BOOK_BY_KEY",
    "CANONICAL_BOOK_LABELS",
    "CANONICAL_BOOK_NAMES_BY_LOCALE",
    "MAX_CHAPTER_BY_BOOK",
    "ScriptureBook",
    "alias_has_matching_ordinal",
    "aliases_for_book",
    "canonical_book_key",
    "canonical_book_label",
    "contextual_book_tradition",
    "historical_noncanonical_book_key",
    "normalize_book_alias",
]
