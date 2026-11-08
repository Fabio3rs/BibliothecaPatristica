"""Read-only, image-hash-gated OCR comparisons. Disagreements are never repairs."""
import argparse
from collections import Counter
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata

from tools.corpus_utils import page_files
from tools.indexing.editorial_ocr_blocks import blocks
from tools.indexing.entry_parser_bulk import readonly, encoded
from tools.indexing.entry_parser_experiment import digest

VERSION = 'index-ocr-variants-v1'


def tokens(raw):
    wrappers = blocks(raw)
    ranges = [(b['body_start'],b['body_end']) for b in wrappers if b['kind']=='texto_principal'] if wrappers else [(0,len(raw))]
    result = []
    for a,b in ranges:
        text = raw[a:b]
        excluded = bytearray(len(text))
        for m in re.finditer(r'<[^>]*>|\[(?:/?span|br/)[^\]]*\]',text):
            excluded[m.start():m.end()] = b'\1' * len(m[0])
        for m in re.finditer(r'(?<=\w)-\s*\n\s*(?=\w)',text):
            excluded[m.start():m.end()] = b'\2' * len(m[0])
        normalized,positions = [],[]
        for i,char in enumerate(text):
            if excluded[i] == 1:
                normalized.append(' ');positions.append(a+i)
                continue
            if excluded[i] == 2:
                continue
            char = char.casefold().replace('æ','ae').replace('œ','oe')
            for c in unicodedata.normalize('NFKD',char):
                if not unicodedata.combining(c):
                    normalized.append(c);positions.append(a+i)
        for match in re.finditer(r'\d+|[^\W\d_]+',''.join(normalized)):
            result.append({'token':match[0],'span':[positions[match.start()],positions[match.end()-1]+1]})
    return result


def evidence(raw,items,a,b):
    start = items[a]['span'][0] if a<len(items) else (items[-1]['span'][1] if items else 0)
    end = items[b-1]['span'][1] if a<b else start
    return {'span':[start,end],'quote':raw[start:end],'text_sha256':digest(raw[start:end].encode()),
            'context':raw[max(0,start-100):min(len(raw),end+100)]}


def compare(current,alternative):
    left,right = tokens(current),tokens(alternative)
    changes = []
    matcher = SequenceMatcher(None,[x['token'] for x in left],[x['token'] for x in right],autojunk=False)
    for op,a,b,c,d in matcher.get_opcodes():
        if op=='equal':
            continue
        old,new = [t['token'] for t in left[a:b]],[t['token'] for t in right[c:d]]
        changes.append({'operation':op,'current':evidence(current,left,a,b),
            'alternative':evidence(alternative,right,c,d),
            'numeric_disagreement':[t for t in old if t.isdigit()]!=[t for t in new if t.isdigit()],
            'single_letter_disagreement':[t for t in old if t in 'abcd']!=[t for t in new if t in 'abcd'],
            'status':'variant_disagreement_requires_review','automatic_repair_allowed':False})
    return changes


def run(args):
    if not re.fullmatch(r'P[GLO]\d{3}',args.volume) or any(n<1 for n in args.page):
        raise ValueError('Invalid volume or physical page')
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    if args.root.resolve() in args.out.resolve().parents:
        raise ValueError('Staging must be outside the corpus root')
    versions,tess = readonly(args.versions_db),readonly(args.tesseract_db)
    try:
        listing = page_files(args.root/args.volume/'text',suffixes=['.txt'])
        images = page_files(args.root/args.volume/'images',suffixes=['.png'])
        pages,changes = [],[]
        args.out.mkdir(parents=True,exist_ok=True)
        (args.out/'sources').mkdir()
        versions.execute('BEGIN');tess.execute('BEGIN')
        for n in sorted(set(args.page)):
            if len(listing.get(n,[]))!=1 or len(images.get(n,[]))!=1:
                raise ValueError(f'Missing or ambiguous source/image for {n}')
            source,image = listing[n][0],images[n][0]
            raw = source.read_bytes().decode('utf-8')
            image_hash = digest(image.read_bytes())
            page = {'volume':args.volume,'physical':n,'source':str(source.resolve()),
                    'source_sha256':digest(raw.encode()),'image':str(image.resolve()),
                    'image_sha256':image_hash,'versions':[],'tesseract_cache':[]}
            for row in versions.execute('''SELECT r.id,r.image_hash,r.image_path,r.text_content,r.text_hash,
                    r.is_current,r.created_at,e.engine,e.model FROM ocr_results r
                    JOIN ocr_engine_versions e ON e.id=r.engine_version_id
                    WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id''',(args.volume,n)):
                record = dict(row);text = record.pop('text_content')
                record['computed_text_sha256'] = digest(text.encode())
                record['text_hash_verified'] = record['computed_text_sha256']==record['text_hash']
                record['same_image_sha256'] = record['image_hash']==image_hash
                snapshot = args.out/'sources'/f'{args.volume}-{n}-version-{record["id"]}.txt'
                snapshot.write_bytes(text.encode());record['snapshot']=str(snapshot.resolve())
                record['matches_current_file'] = record['computed_text_sha256']==page['source_sha256']
                record['comparison_status'] = ('compared' if record['same_image_sha256'] and record['text_hash_verified']
                                                else 'excluded_hash_mismatch')
                edits = compare(raw,text) if record['comparison_status']=='compared' else []
                record['disagreements'] = len(edits)
                record['numeric_disagreements'] = sum(e['numeric_disagreement'] for e in edits)
                for edit in edits:
                    changes.append({'volume':args.volume,'physical':n,'source':page['source'],
                        'source_sha256':page['source_sha256'],'image_sha256':image_hash,
                        'version_id':record['id'],'alternative_sha256':record['computed_text_sha256'],**edit})
                page['versions'].append(record)
            for row in tess.execute('SELECT * FROM tesseract_cache WHERE image_hash=?',(image_hash,)):
                record = dict(row);payload=record.pop('result')
                snapshot=args.out/'sources'/f'{args.volume}-{n}-tesseract-cache.json'
                snapshot.write_bytes(payload.encode())
                record.update(snapshot=str(snapshot.resolve()),serialized_sha256=digest(payload.encode()),
                              status='exact_image_cache_snapshot_not_inferred_text_format')
                page['tesseract_cache'].append(record)
            pages.append(page)
        (args.out/'inventory.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2)+'\n')
        (args.out/'variant_differences.jsonl').write_text(''.join(encoded(c)+'\n' for c in changes))
        summary={'version':VERSION,'pages':len(pages),'versions':sum(len(p['versions']) for p in pages),
            'comparisons':dict(Counter(r['comparison_status'] for p in pages for r in p['versions'])),
            'disagreements':len(changes),'numeric_disagreements':sum(c['numeric_disagreement'] for c in changes),
            'tesseract_cache_hits':sum(len(p['tesseract_cache']) for p in pages),
            'sources_opened_readonly':True,'automatic_repairs':0,'api_calls':0}
        config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
        config.update(version=VERSION,code_sha256=digest(Path(__file__).read_bytes()))
        (args.out/'manifest.json').write_text(encoded(config)+'\n')
        (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        report=['# Comparação de versões OCR','','Somente propostas para revisão. Nenhuma versão é gold.',
            'Texto principal comparado; letras movidas a blocos marginais podem aparecer como diferenças.',
            'Normalização de espaços, acentos, ligaduras e hifenização; números nunca são normalizados.',
            'Alinhamento textual pode ser ambíguo em sequências repetidas; spans apontam aos textos, não à imagem.',
            '', '| Página | Versão | Engine/modelo | Mesma imagem | Diferenças | Com números |',
            '|---:|---:|---|---|---:|---:|']
        for p in pages:
            for r in p['versions']:
                report.append(f"| {p['physical']} | {r['id']} | {r['engine']} / {r['model']} | {r['same_image_sha256']} | {r['disagreements']} | {r['numeric_disagreements']} |")
        (args.out/'report.md').write_text('\n'.join(report)+'\n')
        return summary
    finally:
        versions.close();tess.close()


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--volume',required=True)
    cli.add_argument('--page',type=int,action='append',required=True)
    cli.add_argument('--root',type=Path,default=Path('teste'))
    cli.add_argument('--versions-db',type=Path,default=Path('data/ocr_versions.db'))
    cli.add_argument('--tesseract-db',type=Path,default=Path('data/tesseract.db'))
    cli.add_argument('--out',type=Path,required=True)
    print(json.dumps(run(cli.parse_args()),indent=2))
