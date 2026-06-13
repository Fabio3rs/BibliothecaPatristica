from scripts.migne_training_text import (
    cleanup_line,
    estimated_width,
    has_banned_char,
    iter_grapheme_clusters,
    validar_latim_grego,
)


def test_iter_grapheme_clusters_keeps_combining_marks_together():
    text = "A\u0301B Ἀ\u0313"
    clusters = list(iter_grapheme_clusters(text))
    assert clusters[0] == "A\u0301"
    assert clusters[3] == "Ἀ\u0313"


def test_cleanup_line_preserves_greek_and_symbols():
    assert cleanup_line("ἐν ἀρχῇ") == "ἐν ἀρχῇ"
    assert cleanup_line("℟. Amen.") == "℟. Amen."
    assert cleanup_line("† Ego Aethelwine sacerd.") == "† Ego Aethelwine sacerd."


def test_cleanup_line_rejects_obvious_ocr_noise():
    assert cleanup_line("&nbsp;") is None
    assert cleanup_line("[ilegivel]") is None
    assert cleanup_line("..................................................................... 513") is None
    assert cleanup_line("Ὁ Δη-") == "Ὁ Δη-"


def test_cleanup_line_strips_html_tags_and_entities():
    assert cleanup_line("dit jah <b> fiskam, quod nullus gr. agnoscit.") == "dit jah fiskam, quod nullus gr. agnoscit."
    assert cleanup_line("§ 5. Tarasius &#8224; patriarcha &#8224;&#8224; dixit : Quicum-") == "§ 5. Tarasius † patriarcha †† dixit : Quicum-"
    assert cleanup_line("vera &#39;locutio&#39;") == "vera 'locutio'"
    assert cleanup_line("[linha]Thomas iste Ordinis Cisterciensis in Clara-Valle sese devo-[/linha]") == "Thomas iste Ordinis Cisterciensis in Clara-Valle sese devo-"
    assert cleanup_line("[i]dum ipse fecerat[/i]") == "dum ipse fecerat"
    assert cleanup_line("RESP. Utile fuit [br/]re discere utilitatem") == "RESP. Utile fuit re discere utilitatem"


def test_validar_latim_grego_accepts_mixed_editorial_lines():
    assert validar_latim_grego("Homiliae XIII in Jeremiam, quas Antwerp. 1648") is True
    assert validar_latim_grego("ἐν τῇ ἐκκλησίᾳ") is True


def test_estimated_width_counts_greek_graphemes():
    latin = estimated_width("a")
    greek = estimated_width("ἐ")
    assert greek > latin
    assert has_banned_char("†. Amen.", {"†"}) is True
