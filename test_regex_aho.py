import re
import time
from scripture_ref_normalizer import _REF_SPECS

class OptimizedMatcher:
    def __init__(self, specs):
        self.specs = specs
        
        self.chunk_size = 30
        self.matchers = []
        for i in range(0, len(specs), self.chunk_size):
            chunk = specs[i:i+self.chunk_size]
            
            patterns = []
            for j, spec in enumerate(chunk):
                p = spec["pattern"].pattern
                # Remove os ?P<name> e troca por grupos normais, que podemos usar usando o map `idx_to_spec` ou contar os grupos.
                # Mas groups normais em regex mtoo grandes são difíceis, e max groups é 100 em re... 
                # (actually max is 100 in <3.something, now it's more but still)
                
                p = p.replace("(?P<raw>", "(")
                p = p.replace("(?P<main>", "(")
                p = p.replace("(?P<verse>", "(")
                p = p.replace("(?P<alt>", "(")
                # Adiciona prefixo literal de match!
                p = f"(?P<m_{i}_{j}>{p})"
                patterns.append(p)
                
            combined = "|".join(patterns)
            try:
                self.matchers.append((chunk, re.compile(combined, flags=re.IGNORECASE)))
            except Exception as e:
                print(f"Error compiling chunk {i}: {e}")

    def finditer(self, text):
        for chunk, matcher in self.matchers:
            for match in matcher.finditer(text):
                # find which one matched
                for j, spec in enumerate(chunk):
                    # We can use the old spec['pattern'] locally here just for the exact string slice matched!
                    # Actually we can just run the spec['pattern'] over the small matched span!
                    pass

matcher = OptimizedMatcher(_REF_SPECS)
print(f"Compiled {len(matcher.matchers)} matcher chunks")

large_text = "Jesus disse em Mateus 28:19 e também em lc. 24:12 e jo. 3:16"*1000
start = time.time()
for _ in range(100):
   for chunk, matcher_re in matcher.matchers:
       for match in matcher_re.finditer(large_text):
           # for j, spec in enumerate(chunk):
           pass
print(f"Optimized match phase only time: {time.time()-start}")
