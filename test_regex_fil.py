import re
import time
from scripture_ref_normalizer import _REF_SPECS

class FilterMatcher:
    def __init__(self, specs):
        self.specs = specs
        
        # Build un-anchored literal words to search for quickly.
        # This will eliminate 99% of texts that don't even have bible books mentioned.
        literal_words = set()
        for spec in specs:
            p = spec["pattern"].pattern
            # Find the fragment inside (?:fragment)
            m = re.search(r'\(\?\:(.*?)\)', p)
            if m:
                frag = m.group(1).replace(r'\ ', ' ').replace(r'\s+', ' ').lower()
                # strip regex chars
                frag = re.sub(r'\[.*?\]', '', frag)
                frag = re.sub(r'(?<!\\)\.', '', frag)
                frag = frag.replace(r'\.', '.')
                frag = frag.replace('(?:', '').replace(')?', '').replace(')', '')
                for word in frag.split():
                    if len(word) > 2 and not word.isdigit():
                        literal_words.add(word)
                        
        self.literal_regex = re.compile(r'\b(' + '|'.join(re.escape(w) for w in literal_words) + r')\b', re.IGNORECASE)
        print(f"Filter regex has {len(literal_words)} words")

    def might_contain(self, text):
        return bool(self.literal_regex.search(text))

matcher = FilterMatcher(_REF_SPECS)

large_text = "Neste texto não há nenhuma menção a livros da bíblia, apenas texto aleatório para testar o filtro rápido que deve ignorar este texto"*5000
start = time.time()
for _ in range(1000):
   if matcher.might_contain(large_text):
       pass
print(f"Filter miss time: {time.time()-start}")

large_text2 = "Aqui sim tem Mateus 2 e também outras coisas Mateus 2"*5000
start = time.time()
for _ in range(1000):
    if matcher.might_contain(large_text2):
        pass
print(f"Filter hit time: {time.time()-start}")
