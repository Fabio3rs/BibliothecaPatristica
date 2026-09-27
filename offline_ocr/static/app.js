'use strict';
const $ = id => document.getElementById(id);
const ns = 'http://www.w3.org/2000/svg';
const labels = {equal:'Iguais', normalized_equal:'Após normalização', split_merge:'Divisão / junção', replace:'Substituição', unresolved:'Trecho incerto', llm_only:'Só na LLM', tesseract_only:'Só no Tesseract'};
let data, mode='flow', selected=null, loadId=0, draftBox=null, drawing=false, dragStart=null;
let llmTokens=new Map(), tessTokens=new Map(), llmLinks=new Map(), tessLinks=new Map();
function element(tag, text, cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function issue(link){return link && (link.uncertain || !['equal','normalized_equal'].includes(link.status));}
function textFor(ids,map){return ids.map(id=>map.get(id)?.text || '').join(' ');}
function drawBox(box, cls, action, title){if(!box)return;const r=document.createElementNS(ns,'rect');['x','y','width','height'].forEach((k,i)=>r.setAttribute(k,[box[0],box[1],box[2]-box[0],box[3]-box[1]][i]));r.setAttribute('class',cls);if(title){const t=document.createElementNS(ns,'title');t.textContent=title;r.append(t);}if(action)r.addEventListener('click',action);$('overlay').append(r);}
function renderOverlay(){
 if(!data)return;$('overlay').replaceChildren();$('overlay').style.opacity=$('opacity').value/100;
 if($('blocks').checked)for(const b of data.llm.blocks)drawBox(b.bbox,'block',()=>{$('block-select').value=b.id;loadBox();$('edit-panel').open=true;},`${b.id} · ${b.type}`);
 if($('anchors').checked)for(const e of Object.values(data.alignment.region_evidence))drawBox(e.anchor_bbox,'anchor',null,`${e.anchor_count} âncoras textuais`);
 if($('lines').checked)for(const n of data.tesseract.nodes)if(n.level===4)drawBox(n.bbox,'line');
 if($('words').checked)for(const t of tessTokens.values())for(const p of t.pieces)drawBox(p.bbox,'word',()=>select(tessLinks.get(t.id)),p.text);
 if($('differences').checked)for(const l of data.alignment.links)if(issue(l))for(const id of l.tesseract_ids)for(const p of tessTokens.get(id).pieces)drawBox(p.bbox,'difference',()=>select(l),`${labels[l.status]} · ${p.text}`);
 if($('corrections').checked){for(const box of Object.values(data.revision.boxes))drawBox(box,'adjusted');for(const l of data.alignment.links)if(l.llm_ids.some(id=>Object.hasOwn(data.revision.words,id)))for(const id of l.tesseract_ids)for(const p of tessTokens.get(id).pieces)drawBox(p.bbox,'adjusted');}
 if(draftBox)drawBox(draftBox,'draft');
 updateRasterLink();
 if(selected)for(const id of selected.tesseract_ids)for(const p of tessTokens.get(id).pieces)drawBox(p.bbox,'selected');
}
function select(link){
 if(!link)return;selected=link;const panel=$('selection');panel.replaceChildren(element('h3',`${labels[link.status]}${link.uncertain?' · revisar':''}`));
 panel.append(element('p',`LLM: ${textFor(link.llm_ids,llmTokens)||'—'}`),element('p',`Tesseract: ${textFor(link.tesseract_ids,tessTokens)||'—'}`));
 const geometry = link.tesseract_ids.length ? 'Caixas do Tesseract; vínculo com a LLM inferido.' : 'Sem localização de palavra confirmada.';
 panel.append(element('p',`${geometry} Similaridade textual: ${Math.round(link.similarity*100)}% (não é probabilidade de acerto).`));
 for(const [ids,map,name] of [[link.llm_ids,llmTokens,'LLM'],[link.tesseract_ids,tessTokens,'Tesseract']])for(const id of ids){const t=map.get(id);panel.append(element('p',`${name} ${id}: ${t.pieces.map(p=>p.text).join(' + ')} · ${t.transform==='identity'?'original':'junção de hifenização'}`));for(const p of t.pieces){panel.append(element('code',p.bbox?`${p.id} · caixa ${p.bbox.map(x=>x.toFixed(1)).join(', ')} · conf. ${p.conf}`:`${p.id} · intervalos no original ${JSON.stringify(p.source_spans)}`),document.createElement('br'));}}
 const ev=data.alignment.region_evidence[link.region];if(ev)panel.append(element('p',`Bloco ${link.region}: ${ev.anchor_count} âncoras. Margem: ${data.alignment.bbox_tolerance}/1000. Diferença das bordas: ${ev.edge_delta?.join(', ') || 'indisponível'}.`));
 const edits=element('p');edits.append(document.createTextNode('Diferenças de caracteres: '));const a=textFor(link.llm_ids,llmTokens),b=textFor(link.tesseract_ids,tessTokens);for(const op of link.character_edits||[]){if(op.llm_span[1]>op.llm_span[0])edits.append(element('del',a.slice(...op.llm_span)));edits.append(document.createTextNode(' → '));if(op.tesseract_span[1]>op.tesseract_span[0])edits.append(element('ins',b.slice(...op.tesseract_span)));edits.append(document.createTextNode(' · '));}panel.append(edits);
 const candidates=link.tesseract_ids.flatMap(id=>tessTokens.get(id).region_candidates||[]).slice(0,6);if(candidates.length)panel.append(element('p',`Relações candidatas: ${candidates.map(c=>`${c.block_id} (pontuação ${c.score}, sobreposição ${Math.round(c.overlap*100)}%)`).join('; ')}`));
 const first=link.tesseract_ids.length?tessTokens.get(link.tesseract_ids[0]).pieces[0].bbox:null;if(first){const scroller=$('image-scroll'),sheet=$('sheet');scroller.scrollTo({top:Math.max(0,first[1]/1000*sheet.clientHeight-scroller.clientHeight/3),left:Math.max(0,first[0]/1000*sheet.clientWidth-scroller.clientWidth/3),behavior:'smooth'});}
 prepareWordEdit();if(link.region){$('block-select').value=link.region;loadBox();}renderOverlay();document.querySelectorAll('.token').forEach(e=>e.classList.toggle('chosen',link.llm_ids.includes(e.dataset.id)));
}
function renderContent(){
 if(!data)return;const container=$('content');container.replaceChildren();container.className=mode==='flow'?'flow':'';const query=$('filter').value.toLocaleLowerCase();
 if(mode==='raw'){container.append(element('pre',data.llm.raw));return;}
 if(mode==='diff'){
  for(const link of data.alignment.links){const a=textFor(link.llm_ids,llmTokens), b=textFor(link.tesseract_ids,tessTokens);if(!issue(link)||!(a+' '+b).toLocaleLowerCase().includes(query))continue;const row=element('button',undefined,'diff-row');row.append(element('strong',labels[link.status]),element('span',`LLM: ${a||'—'}`),element('span',`Tesseract: ${b||'—'}`),element('small',link.tesseract_ids.length?'Inspecionar posições':'Sem caixa de palavra'));row.addEventListener('click',()=>select(link));container.append(row);}
 }else{
  for(const block of data.llm.blocks){const section=element('section');section.append(element('h3',`${block.id} · ${block.type} · ${block.script}`));for(const t of llmTokens.values()){if(t.block_id!==block.id||!t.text.toLocaleLowerCase().includes(query))continue;const link=llmLinks.get(t.id);const corrected=Object.hasOwn(data.revision.words,t.id);const button=element('button',corrected?data.revision.words[t.id]||'∅':t.text,`token${issue(link)?' problem':''}`);button.dataset.id=t.id;if(corrected)button.classList.add('corrected');button.title=labels[link?.status]||'Sem correspondência';button.addEventListener('click',()=>select(link));section.append(button,document.createTextNode(' '));}container.append(section);}
 }
 if(!container.childElementCount)container.append(element('p','Nenhum resultado neste filtro.','empty'));
}
async function load(key){
 const current=++loadId;if(!key)return;$('status').textContent='Carregando página…';
 try{const response=await fetch(`/api/comparisons/${key}`);if(!response.ok)throw Error(`HTTP ${response.status}`);const result=await response.json();if(current!==loadId)return;data=result;selected=null;
 llmTokens=new Map(data.alignment.llm_tokens.map(t=>[t.id,t]));tessTokens=new Map(data.alignment.tesseract_tokens.map(t=>[t.id,t]));llmLinks=new Map();tessLinks=new Map();for(const l of data.alignment.links){for(const id of l.llm_ids)llmLinks.set(id,l);for(const id of l.tesseract_ids)tessLinks.set(id,l);}
 $('page').src=`/image/${key}`;$('page').alt=`Página digitalizada ${data.label}`;$('download').href=`/api/comparisons/${key}`;$('stats').replaceChildren();
 for(const [status,count]of Object.entries(data.alignment.stats)){const item=element('div',undefined,'stat');item.append(element('strong',count),document.createTextNode(labels[status]));$('stats').append(item);}
 $('warnings').replaceChildren(...data.alignment.warnings.map(w=>element('li',w)));$('selection').replaceChildren(element('h3','Selecione uma palavra'),element('p','As posições da LLM são inferidas a partir do Tesseract.'));
 $('status').textContent=`${data.label} · ${llmTokens.size} palavras LLM · ${tessTokens.size} palavras Tesseract · ${data.alignment.dehyphenate?'hifenização unida':'hifenização preservada'} · versão ${key.slice(0,8)}`;
 renderReview();renderOverlay();renderContent();
 }catch(error){$('status').textContent=`Falha ao carregar: ${error.message}`;}
}
$('page').addEventListener('error',()=>{$('status').textContent='Imagem ausente ou alterada após a ingestão. Confira o arquivo original.';});
for(const id of ['blocks','anchors','lines','words','differences','corrections','opacity'])$(id).addEventListener('input',renderOverlay);
$('zoom').addEventListener('change',()=>{$('sheet').style.width=`${Number($('zoom').value)*100}%`;});
$('comparison').addEventListener('change',e=>load(e.target.value));$('filter').addEventListener('input',renderContent);
for(const value of ['flow','diff','raw'])$(`show-${value}`).addEventListener('click',()=>{mode=value;for(const m of ['flow','diff','raw'])$(`show-${m}`).setAttribute('aria-pressed',String(m===mode));renderContent();});
let pageCursor=null;
async function loadPages(next=false){try{const query=new URLSearchParams({prefix:$('page-prefix').value});if(next&&pageCursor)query.set('after',pageCursor);const response=await fetch(`/api/comparisons?${query}`);if(!response.ok)throw Error(`HTTP ${response.status}`);const rows=await response.json();$('comparison').replaceChildren();for(const row of rows){const option=element('option',`${row.label} · ${row.key.slice(0,8)}`);option.value=row.key;$('comparison').append(option);}pageCursor=rows.at(-1)?.key;$('next-pages').disabled=rows.length<100;if(rows.length)await load(rows[0].key);else{$('comparison').append(element('option','Nenhuma página encontrada'));$('status').textContent='Nenhuma comparação neste filtro. Processe uma página ou altere a busca.';}}catch(error){$('status').textContent=`Falha ao listar páginas: ${error.message}`;}}
$('search-pages').addEventListener('click',()=>loadPages());$('next-pages').addEventListener('click',()=>loadPages(true));$('page-prefix').addEventListener('keydown',event=>{if(event.key==='Enter')loadPages();});loadPages();

function updateRasterLink(){if(data)$('download-raster').href=`/raster/${data.key}?layers=${['blocks','anchors','lines','words','differences','corrections'].filter(id=>$(id).checked).join(',')}&opacity=${Number($('opacity').value)/100}`;}
function renderReview(){
 $('download-xml').href=`/xml/${data.key}`;
 const previous=$('block-select').value;$('block-select').replaceChildren(...data.llm.blocks.map(b=>{const option=element('option',`${b.id} · ${b.type} · ${b.script}`);option.value=b.id;return option;}));if(data.llm.blocks.some(b=>b.id===previous))$('block-select').value=previous;
 $('review-status').textContent=`Revisão ${data.revision.id} · ${Object.keys(data.revision.words).length} palavras ajustadas · ${Object.keys(data.revision.boxes).length} caixas ajustadas`;
 $('history').replaceChildren(...data.history.map(h=>{const p=JSON.parse(h.payload);return element('li',`#${h.id} · ${h.created_at} · ${Object.keys(p.words).length} palavras / ${Object.keys(p.boxes).length} caixas · ${p.note||'sem nota'}`);}));
 loadBox();prepareWordEdit();
}
function prepareWordEdit(){
 const available=Boolean(selected?.llm_ids.length);for(const id of ['correction-text','save-word','undo-word'])$(id).disabled=!available;
 $('use-tesseract').disabled=!available||!selected.tesseract_ids.length;
 $('correction-text').value=available?selected.llm_ids.map(id=>Object.hasOwn(data.revision.words,id)?data.revision.words[id]:llmTokens.get(id).text).filter(Boolean).join(' '):'';
}
function loadBox(){
 if(!data)return;const id=$('block-select').value;const block=data.llm.blocks.find(b=>b.id===id);const ev=data.alignment.region_evidence[id];draftBox=null;
 const box=data.revision.boxes[id]||block?.bbox||[];['x1','y1','x2','y2'].forEach((name,i)=>$(name).value=box[i]??'');
 $('suggest-box').disabled=!ev?.anchor_bbox;$('box-evidence').textContent=ev?`${ev.anchor_count} âncoras textuais. A aproximação pode omitir bordas sem palavras reconhecidas; confira antes de salvar.`:'Sem evidência geométrica.';renderOverlay();
}
function previewBox(){const values=['x1','y1','x2','y2'].map(id=>Number($(id).value));draftBox=values.every(Number.isFinite)&&values[0]<values[2]&&values[1]<values[3]?values:null;renderOverlay();}
async function post(action, payload){const response=await fetch(`/api/comparisons/${data.key}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_id:data.revision.id,...payload})});const result=await response.json();if(!response.ok)throw Error(result.error||`HTTP ${response.status}`);return result;}
async function saveChanges(words,boxes){try{const revision=await post('revisions',{words,boxes,note:$('review-note').value});data.revision=revision;const response=await fetch(`/api/comparisons/${data.key}`);if(!response.ok)throw Error('Não foi possível atualizar o histórico');const result=await response.json();data.history=result.history;renderReview();renderContent();renderOverlay();}catch(error){$('review-status').textContent=error.message;}}
$('word-form').addEventListener('submit',event=>{event.preventDefault();if(!selected?.llm_ids.length)return;const words=Object.fromEntries(selected.llm_ids.map((id,i)=>[id,i===0?$('correction-text').value:'']));saveChanges(words,{});});
$('use-tesseract').addEventListener('click',()=>{$('correction-text').value=textFor(selected.tesseract_ids,tessTokens);});
$('undo-word').addEventListener('click',()=>saveChanges(Object.fromEntries(selected.llm_ids.map(id=>[id,null])),{}));
$('block-select').addEventListener('change',loadBox);
for(const id of ['x1','y1','x2','y2'])$(id).addEventListener('input',previewBox);
$('suggest-box').addEventListener('click',()=>{const box=data.alignment.region_evidence[$('block-select').value]?.anchor_bbox;if(box){['x1','y1','x2','y2'].forEach((id,i)=>$(id).value=box[i].toFixed(1));previewBox();}});
$('box-form').addEventListener('submit',event=>{event.preventDefault();saveChanges({}, {[$('block-select').value]:['x1','y1','x2','y2'].map(id=>Number($(id).value))});});
$('undo-box').addEventListener('click',()=>saveChanges({}, {[$('block-select').value]:null}));
$('refine').addEventListener('click',async()=>{try{const result=await post('refine',{});const option=element('option',`${data.label} · ajustado · ${result.key.slice(0,8)}`);option.value=result.key;$('comparison').prepend(option);$('comparison').value=result.key;await load(result.key);}catch(error){$('review-status').textContent=error.message;}});
$('draw-box').addEventListener('click',()=>{drawing=!drawing;$('overlay').classList.toggle('drawing',drawing);$('draw-box').textContent=drawing?'Cancelar desenho':'Desenhar na página';$('status').textContent=drawing?'Arraste na imagem para delimitar a caixa; depois use Salvar bbox.':'Desenho cancelado.';});
function point(event){const rect=$('overlay').getBoundingClientRect();return [Math.max(0,Math.min(1000,(event.clientX-rect.left)/rect.width*1000)),Math.max(0,Math.min(1000,(event.clientY-rect.top)/rect.height*1000))];}
$('overlay').addEventListener('pointerdown',event=>{if(!drawing)return;event.preventDefault();dragStart=point(event);$('overlay').setPointerCapture(event.pointerId);});
$('overlay').addEventListener('pointermove',event=>{if(!drawing||!dragStart)return;const p=point(event);draftBox=[Math.min(p[0],dragStart[0]),Math.min(p[1],dragStart[1]),Math.max(p[0],dragStart[0]),Math.max(p[1],dragStart[1])];['x1','y1','x2','y2'].forEach((id,i)=>$(id).value=draftBox[i].toFixed(1));renderOverlay();});
$('overlay').addEventListener('pointerup',()=>{if(!drawing)return;drawing=false;dragStart=null;$('overlay').classList.remove('drawing');$('draw-box').textContent='Desenhar na página';$('status').textContent='Caixa desenhada. Confira as coordenadas e use Salvar bbox.';});
