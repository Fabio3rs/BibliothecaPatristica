from tools.indexing.epistle_destination_map import parse_page
from tools.indexing.epistle_continuation_context import continuation_context
from tools.indexing.epistle_catalog_recovery import ranked_openings


def block(kind, text):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def setup_pages(tmp_path, tail, next_body, next_header='REGESTORUM LIB. I'):
    folder = tmp_path/'teste'/'PL214'/'text'
    folder.mkdir(parents=True)
    source = folder/'test-1.txt'
    raw = block('cabecalho', 'REGESTORUM LIB. I') + block('texto', 'I. ABBATI MONASTERII\nPrima verba.\n' + tail)
    source.write_text(raw)
    other = folder/'test-2.txt'
    other.write_text(block('cabecalho', next_header) + next_body)
    anchors, _ = parse_page(raw, file=str(source), volume='PL214', physical=1)
    return anchors, source, other


def test_context_stops_at_rejected_number_and_excludes_notes(tmp_path):
    anchors, source, other = setup_pages(tmp_path, '',
        block('nota', 'Forbidden content') + block('texto', 'Cum dilectus filius noster.\nII. lowercase unrecognized rubric\nWrong next letter'))
    result = continuation_context(anchors, tmp_path)
    context = result[0]['continuation_context']
    assert context['text'] == 'Cum dilectus filius noster.'
    raw = other.read_text()
    assert all(raw[s['start']:s['end']] == s['raw'] for s in context['spans'])
    loc = dict(series='register', book=1, epistle=1, suffix=None)
    ranked, _ = ranked_openings([loc], 'Cum dilectus filius noster', {('register', 1, 1, None): result}, tmp_path)
    assert ranked[0]['file'] == str(source)
    assert ranked[0]['evidence']['supports'][0]['incipit']['context_file'] == str(other)
    assert 'incipit_support_uses_candidate_continuation' in ranked[0]['uncertainties']


def test_rejected_current_number_prevents_cross_letter_borrowing(tmp_path):
    anchors, _, _ = setup_pages(tmp_path, 'II. unknown recipient\nUnparsed letter', block('texto', 'Next page text'))
    assert 'continuation_context' not in continuation_context(anchors, tmp_path)[0]


def test_changed_book_prevents_context(tmp_path):
    anchors, _, _ = setup_pages(tmp_path, '', block('texto', 'Next page text'), 'REGESTORUM LIB. II')
    assert 'continuation_context' not in continuation_context(anchors, tmp_path)[0]


def test_section_title_stops_context(tmp_path):
    anchors, _, _ = setup_pages(tmp_path, '', block('titulo', 'LIBER SECUNDUS') + block('texto', 'Wrong book'))
    assert 'continuation_context' not in continuation_context(anchors, tmp_path)[0]


def test_context_never_skips_missing_page(tmp_path):
    anchors, _, other = setup_pages(tmp_path, '', block('texto', 'Next page text'))
    other.rename(other.with_name('test-3.txt'))
    assert 'continuation_context' not in continuation_context(anchors, tmp_path)[0]
