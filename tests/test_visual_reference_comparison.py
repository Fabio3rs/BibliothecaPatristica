import pytest

from tools.indexing.visual_reference_comparison import compare, normalized
from tools.indexing.editorial_regions_experiment import sha


def fixture(raw, candidates):
    ref={'ocr_sha256':sha(raw.encode()),'complete_region':True,'volume':'PG001','physical_page':1,
         'region':{'start_anchor_ocr':'Abbas, 12.','end_anchor_ocr':'Zeno, 14.'},
         'entries':[{'ordinal':i+1,'entry_raw':s,'uncertainties':[]} for i,s in enumerate(['Abbas, 12.','Adam, 13.','Zeno, 14.'])]}
    parsed={'sha256':ref['ocr_sha256'],'entries':[
        {'id':str(i),'char_start':raw.index(s),'char_end':raw.index(s)+len(s),'text_sha256':sha(s.encode()),'entry_raw':s}
        for i,s in enumerate(candidates)]}
    return ref,parsed


def test_exact_segmentation_distinguished_from_merged_coverage():
    raw='Abbas, 12. Adam, 13. Zeno, 14.'
    ref,parsed=fixture(raw,['Abbas, 12.','Adam, 13. Zeno, 14.'])
    result=compare(ref,raw,parsed)
    assert result['counts']=={'exact_segment_match':1,'contained_in_larger_candidate':2}
    assert result['gold_eligible'] is False


def test_no_parser_is_not_zero_ocr_coverage():
    raw='Abbas, 12. Adam, 13. Zeno, 14.'
    ref,_=fixture(raw,[])
    assert compare(ref,raw,None)['counts']=={'page_not_in_parser_run':3}


def test_uncertain_visual_readings_do_not_enter_strict_comparison():
    raw='Abbas, 12. Adam, 13. Zeno, 14.'
    ref,parsed=fixture(raw,[raw]);ref['entries'][1]['uncertainties']=['13 or 18']
    assert compare(ref,raw,parsed)['counts']['uncertain_visual_reading_excluded']==1


def test_digits_are_never_normalized_away():
    assert normalized('Quæ-\nrere 13.')=='Quaerere13.'
    assert normalized('13.') != normalized('18.')


def test_reject_stale_source_and_ambiguous_anchors():
    raw='Abbas, 12. Adam, 13. Zeno, 14.'
    ref,parsed=fixture(raw,[raw])
    with pytest.raises(ValueError,match='hash'):compare(ref,raw+'x',parsed)
    raw=raw+' Abbas, 12.';ref,parsed=fixture(raw,[])
    assert compare(ref,raw,parsed)['status']=='ambiguous_visual_region_anchors'
