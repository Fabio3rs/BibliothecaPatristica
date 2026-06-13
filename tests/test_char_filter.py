import re
import time
from scripture_ref_normalizer import _REF_SPECS

class OptimizedRegexMatcher:
    def __init__(self, specs):
        self.specs = specs
        self.spec_rules = []
        for spec in specs:
            p = spec["pattern"].pattern
            m = re.search(r'\(\?\:(.*?)\)', p)
            if m:
                frag = m.group(1).replace(r'\ ', ' ').replace(r'\s+', ' ').lower()
                frag = re.sub(r'\[.*?\]', '', frag)
                frag = re.sub(r'(?<!\\)\.', '', frag)
                frag = frag.replace(r'\.', '.')
                frag = frag.replace('(?:', '').replace(')?', '').replace(')', '')
                words = [w.lower() for w in frag.split() if len(w)>0 and not w.isdigit()]
                self.spec_rules.append((spec, words))
            else:
                self.spec_rules.append((spec, []))

    def iter_matches(self, text):
        t_low = text.lower()
        for spec, words in self.spec_rules:
            ok = True
            for w in words:
                if w not in t_low:
                    ok = False
                    break
            if ok:
                for m in spec["pattern"].finditer(text):
                    yield (spec, m)

matcher = OptimizedRegexMatcher(_REF_SPECS)
print("ready")

large_text = "Jesus disse em Mateus 28:19 e também em lc. 24:12 e jo. 3:16"*1000
start = time.time()
for _ in range(100):
   matches = list(matcher.iter_matches(large_text))
print(f"Total loop time: {time.time()-start}, matches {len(matches)}")


start = time.time()
for _ in range(100):
   matches2 = []
   for s in _REF_SPECS:
      matches2.extend(list(s["pattern"].finditer(large_text)))
print(f"Old total time: {time.time()-start}, matches {len(matches2)}")

