from copy import deepcopy
import json

import pytest

from tools.indexing.entry_parser_experiment import digest, source_span
from tools.indexing.entry_parser_literal_rows import parse_page, propose_analytic_column_links
from tools.indexing.index_conservation_audit import coverage
from tools.indexing.segmentation_reviews import VERSION, load_reviews


def fixture(tmp_path):
    text='Alpha mor-\ntis, I, 1.\nBeta cor-\nporis, II, 2.\nFragmentum sine fine'
    raw=f'<bloco tipo="texto_principal">{text}\n\nGamma, I, 3.</bloco>'
    a=raw.index(text);b=a+len(text)
    image=tmp_path/'page.png';image.write_bytes(b'image-fixture')
    starts=[]
    for quote in ['Alpha','Beta','Fragmentum']:
        x=raw.index(quote);starts.append({'raw':quote,**source_span(raw,x,x+len(quote))})
    review={'source':'fixture','source_sha256':digest(raw.encode()),
        'parent':{'raw':text,**source_span(raw,a,b)},'starts':starts,
        'visual_review':{'reviewer':'assistant','human_reviewed':False,
            'status':'group_boundaries_supported','image':str(image),
            'image_sha256':digest(image.read_bytes()),'image_locator':'left column',
            'note':'Fixture review of three group starts, not of digits.'}}
    args=dict(source='fixture',physical=1,volume='PG046',scopes=[{'span':[0,len(raw)],'section_id':'s'}])
    return raw,args,review


def test_partition_recovers_complete_groups_but_keeps_open_tail(tmp_path):
    raw,args,review=fixture(tmp_path)
    old=parse_page(raw,**args)
    assert len(old['entries'])==1
    page=parse_page(raw,**args,segmentation_reviews=[review])
    assert len(page['entries'])==3
    assert page['residuals'][0]['raw']=='Fragmentum sine fine'
    assert [e['references_raw'] for e in page['entries']]==['I, 1.','II, 2.','I, 3.']
    assert page['entries'][0]['unit_kind']=='visually_delimited_group'
    assert page['entries'][0]['quality']['reference_reading']=='not_verified'
    assert page['entries'][0]['quality']['destination']=='not_attempted'
    assert page['entries'][-1]==old['entries'][0]
    audit=coverage(raw,args['scopes'],page['entries'])
    assert not audit['invalid_candidate_ids']
    assert audit['characters']['duplicate_consumption']==0
    assert audit['characters']['residual']==len('Fragmentumsinefine')
    for e in page['entries']:
        assert e['entry_raw']==raw[e['char_start']:e['char_end']]


@pytest.mark.parametrize('change',['hash','source','image','parent','omit_prefix','duplicate_cut','reverse','midline','duplicate_review','scope','evidence'])
def test_invalid_partition_cannot_rewrite_source_boundaries(tmp_path,change):
    raw,args,r=fixture(tmp_path);reviews=[r]
    if change=='hash':r['source_sha256']='stale'
    if change=='source':r['source']='another'
    if change=='image':r['visual_review']['image_sha256']='stale'
    if change=='parent':r['parent']['raw']='another'
    if change=='omit_prefix':r['starts']=r['starts'][1:]
    if change=='duplicate_cut':r['starts'].insert(1,deepcopy(r['starts'][0]))
    if change=='reverse':r['starts'].reverse()
    if change=='duplicate_review':reviews.append(deepcopy(r))
    if change=='scope':args['scopes'][0]['span'][1]=raw.index('Beta')
    if change=='evidence':r['visual_review']['note']=''
    if change=='midline':
        a=raw.index('cor-');r['starts'][1]={'raw':'cor-',**source_span(raw,a,a+4)}
    with pytest.raises(ValueError):parse_page(raw,**args,segmentation_reviews=reviews)


def test_profile_and_manifest_are_explicit(tmp_path):
    raw,args,r=fixture(tmp_path);args['volume']='PL217'
    with pytest.raises(ValueError):parse_page(raw,**args,segmentation_reviews=[r])
    p=tmp_path/'manifest.json';p.write_text(json.dumps({'version':VERSION,'reviews':[r]}))
    assert load_reviews(p)==[r]
    p.write_text('{}')
    with pytest.raises(ValueError):load_reviews(p)


def test_cross_column_link_preserves_two_spans_without_consumption():
    raw=('<bloco tipo="texto_principal">Peccata, I, 459. Peccati causa quæ-</bloco>'
         '<bloco tipo="texto_principal">nam sit, II, 26 et seqq. Aliud, I, 2.</bloco>')
    links=propose_analytic_column_links(raw,'fixture',[{'span':[0,len(raw)]}])
    link,=links
    assert link['word_candidate']=='quænam'
    assert link['left']['raw']=='Peccati causa quæ-'
    assert link['right']['raw']=='nam sit, II, 26 et seqq.'
    assert link['reference_attribution']=='pending_cross_column_review'
    assert not link['applied']
    for side in ['left','right']:
        part=link[side];assert raw[part['char_start']:part['char_end']]==part['raw']
    boundary=raw.index('<bloco',1)
    assert not propose_analytic_column_links(raw,'fixture',[{'span':[0,boundary]},{'span':[boundary,len(raw)]}])


@pytest.mark.parametrize('middle,right',[
    ('<bloco tipo="cabecalho">ORDO RERUM</bloco>','nam sit, II, 26.'),
    ('','Aliud, II, 26.'),('','nam sit sine numero'),
])
def test_column_link_never_crosses_heading_or_new_entry(middle,right):
    raw=('<bloco tipo="texto_principal">Peccata, I, 459. Peccati causa quæ-</bloco>'
         +middle+f'<bloco tipo="texto_principal">{right}</bloco>')
    assert not propose_analytic_column_links(raw,'fixture',[{'span':[0,len(raw)]}])
