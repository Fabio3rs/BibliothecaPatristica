import re
from scripture_ref_normalizer import _REF_SPECS

class RefMatcher:
    def __init__(self, specs):
        self.specs = specs
        
        # Não podemos usar o mesmo nome para os grupos dentro do mesmo padrão.
        # Precisaremos usar dicts pra mapear qual specs foi matched.
        self.chunk_size = 50
        self.matchers = []
        for i in range(0, len(specs), self.chunk_size):
            chunk = specs[i:i+self.chunk_size]
            
            patterns = []
            for j, spec in enumerate(chunk):
                p = spec["pattern"].pattern
                p = p.replace("?P<raw>", f"?P<raw_{i+j}>")
                p = p.replace("?P<main>", f"?P<main_{i+j}>")
                p = p.replace("?P<verse>", f"?P<verse_{i+j}>")
                p = p.replace("?P<alt>", f"?P<alt_{i+j}>")
                patterns.append(p)
                
            combined = "|".join(patterns)
            try:
                self.matchers.append(re.compile(combined, flags=re.IGNORECASE))
            except Exception as e:
                print(f"Error compiling chunk {i}: {e}")
                
        print(f"Compiled {len(self.matchers)} chunks")

    def finditer(self, text):
        for matcher in self.matchers:
            for match in matcher.finditer(text):
                yield match

print(f"Initial: {len(_REF_SPECS)}")
matcher = RefMatcher(_REF_SPECS)
print("Finished!")
import time
large_text = "Jesus disse em Mateus 28:19 e também em lc. 24:12 e jo. 3:16"*1000
start = time.time()
for _ in range(100):
   for s in _REF_SPECS:
      list(s["pattern"].finditer(large_text))
print(f"Brute force: {time.time()-start}")
start = time.time()
for _ in range(100):
   list(matcher.finditer(large_text))
print(f"Chunked: {time.time()-start}")
