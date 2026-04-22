import regex
import time
from scripture_ref_normalizer import _REF_SPECS

class RegexMatcher:
    def __init__(self, specs):
        self.specs = specs
        
        patterns = []
        for i, spec in enumerate(specs):
            p = spec["pattern"].pattern
            p = p.replace("(?P<raw>", "(")
            p = p.replace("(?P<main>", "(")
            p = p.replace("(?P<verse>", "(")
            p = p.replace("(?P<alt>", "(")
            patterns.append(f"(?P<G_{i}>{p})")
            
        combined = "|".join(patterns)
        try:
            self.matcher = regex.compile(combined, flags=regex.IGNORECASE)
            print("Compiled mega regex with `regex` module!")
        except Exception as e:
            print(f"Error compiling: {e}")

matcher = RegexMatcher(_REF_SPECS)

large_text = "Jesus disse em Mateus 28:19 e também em lc. 24:12 e jo. 3:16"*1000
start = time.time()
for _ in range(100):
   for m in matcher.matcher.finditer(large_text):
       pass
print(f"Total time mega regex: {time.time()-start}")
