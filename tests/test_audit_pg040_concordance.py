from pathlib import Path
import sqlite3

import pytest

from test_jev_index_results import bundle
from scripts import audit_pg040_concordance as audit


def table_xml(first_left='9.\n10.', first_right='2.'):
    columns = [first_left, first_right, '65.\n66.', '113.\n114.',
               '137.\n138.\n139.\n140.', '257.\n381.\n261.\n262.']
    return '\n'.join('<bloco tipo="texto_principal">Edit. '+
                     ('Antv.' if i % 2 == 0 else 'Oxon.')+'\n'+rows+'</bloco>'
                     for i, rows in enumerate(columns))


def test_unbalanced_columns_are_not_zipped_and_anomaly_is_preserved():
    raw = table_xml()
    result = audit.extract_concordance(raw, {'file': 'source', 'sha256': 'hash'})
    assert result['groups'][0]['status'] == 'unaligned'
    assert {p['antwerp'] for p in result['pairs']} == {65,66,137,138,139,140}
    pair = next(p for p in result['pairs'] if p['antwerp']==138)
    assert pair['status']=='ocr_anomaly' and pair['oxford_raw_number']==381
    assert pair['source']['sha256']=='hash'
    assert raw.splitlines()[pair['oxford_cell']['file_line']-1]=='381.'


def test_qualifiers_are_preserved_in_table_cells():
    result = audit.extract_concordance(table_xml('49 fin.\n50 fin.', '82.\n58.'), {})
    assert result['groups'][0]['antwerp_rows'][0]['literal']=='49 fin.'


def test_unexpected_layout_requires_review():
    with pytest.raises(ValueError,match='columns'):
        audit.extract_concordance('<bloco tipo="texto_principal">Edit. Antv.\n65.</bloco>', {})


def test_markers_exclude_headers_notes_latin_and_parenthesized_numbers():
    raw = ('<bloco tipo="cabecalho" script="grego">114</bloco>'
           '<bloco tipo="nota" script="grego">114</bloco>'
           '<bloco tipo="texto_principal" script="latino">114</bloco>'
           '<bloco tipo="texto_principal" script="grego">λόγος (114) [115] **116**\n117 λόγος</bloco>')
    markers = audit.extract_markers(raw, {'sha256': 'hash'}, 355)
    assert [m['number'] for m in markers]==[116,117]
    assert markers[1]['file_line']==2 and markers[1]['source']['sha256']=='hash'


def marker_series():
    sources={p:{'file':str(p),'sha256':str(p)} for p in range(1,12)}
    markers=[{'number':p+9,'physical_page':p,'source_offset':10} for p in sources]
    return markers,sources


def test_intervals_retain_both_boundary_files_without_interpolation():
    markers,sources=marker_series()
    intervals=audit.build_intervals(markers,sources)
    assert [f['physical_page'] for f in intervals[12]['files']]==[3,4]
    assert intervals[20]['status']=='missing_or_ambiguous_boundary'
    missing=[m for m in markers if m['number']!=13]
    assert audit.build_intervals(missing,sources)[12]['files']==[]


def test_duplicate_occurrence_and_missing_file_do_not_establish_interval():
    markers,sources=marker_series()
    markers.append({**markers[2],'source_offset':50})
    assert audit.build_intervals(markers,sources)[12]['files']==[]
    markers,sources=marker_series()
    del sources[4]
    assert audit.build_intervals(markers,sources)[12]['files']==[]


def test_audit_is_readonly_and_never_outputs_apply_proposal(bundle):
    args,spec,_=bundle
    root=Path(spec['root'])
    (root/'page-426.txt').write_text('Numeri signant paginas editionis Antverpiensis')
    (root/'page-427.txt').write_text(table_xml())
    for p in range(253,261):
        (root/f'page-{p}.txt').write_text(
            f'<bloco tipo="texto_principal" script="grego">λόγος {p-244} λόγος</bloco>')
    with sqlite3.connect(args.db) as con:
        con.execute("UPDATE alphabetical_volumes SET volume_id='PG040'")
        con.execute("UPDATE alphabetical_sections SET volume_id='PG040',file_start=?",(str(root/'page-426.txt'),))
        con.execute("UPDATE alphabetical_refs SET page_ref_int=11,ref_raw='11'")
    before=args.db.read_bytes()
    summary,table,markers,intervals,records=audit.investigate(args.db,root)
    assert args.db.read_bytes()==before
    assert summary['application_supported'] is False and summary['db_updates']==0
    assert records[0]['status']=='bounded_marker_candidate' and records[0]['needs_review']
    assert 'proposal' not in records[0]


def test_actual_concordance_selected_pairs():
    path=audit.ROOT/'teste/PG040/text/ef8e8d4f-8c6d-4c3a-b2e2-aa66ad4ab313-427.txt'
    raw,proof=audit.read_page(path)
    table=audit.extract_concordance(raw,proof)
    pairs={p['antwerp']:p['oxford_raw_number'] for p in table['pairs']}
    assert len(pairs)==117
    assert {n:pairs[n] for n in (65,66,108,114,115,123)}=={
        65:113,66:114,108:198,114:210,115:213,123:229}
    assert len(table['groups'][0]['antwerp_rows'])==56
    assert len(table['groups'][0]['oxford_rows'])==43
