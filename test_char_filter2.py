import re
import time
from scripture_ref_normalizer import _extract_explicit_citations, _REF_SPECS

class OptimizedRegexMatcher:
    def __init__(self, specs):
        self.specs = specs
        self.spec_rules = []
        for spec in specs:
            p = spec["pattern"].pattern
            m = re.search(r'\(\?\:(.*?)\)', p)
            if m:
                # Pegar explicitamente as palavras separadas por espaço
                # e.g fragment "mateus|mat\.", ou "ep[íi]stola\s+aos?\s+romanos"
                # na verdade o (?:mateus) é simples
                frag = m.group(1).lower()
                frag = frag.replace(r'\ ', ' ').replace(r'\s+', ' ')
                frag = re.sub(r'\[.*?\]', '', frag) # remove [] like [íi] -> epstola
                
                # regex choices (x|y) are bad for simple split:
                frag = re.sub(r'\(.*?\)', '', frag)
                frag = frag.replace('(?:', '').replace(')?', '').replace(')', '')
                
                frag = re.sub(r'(?<!\\)\.', '', frag)
                frag = frag.replace(r'\.', '.')
                words = [w for w in frag.split() if len(w)>2 and not w.isdigit()]
                self.spec_rules.append((spec, words))
            else:
                self.spec_rules.append((spec, []))

    def get_hits(self, text):
        t_low = text.lower()
        res = []
        for spec, words in self.spec_rules:
            ok = True
            for w in words:
                if w not in t_low:
                    ok = False
                    break
            if ok:
                res.append(spec)
        return res

matcher = OptimizedRegexMatcher(_REF_SPECS)

from pprint import pprint

text = "E vimos João 3:16."
print(text)
for s in matcher.get_hits(text):
   print(s['idx'], s['pattern'].pattern)

