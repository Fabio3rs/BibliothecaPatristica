import ahocorasick
import time
import re
from scripture_ref_normalizer import _REF_SPECS

class AhoCorasickFilter:
    def __init__(self, specs):
        self.automaton = ahocorasick.Automaton()
        literal_words = set()
        
        for spec in specs:
            p = spec["pattern"].pattern
            m = re.search(r'\(\?\:(.*?)\)', p)
            if m:
                frag = m.group(1).replace(r'\ ', ' ').replace(r'\s+', ' ').lower()
                frag = re.sub(r'\[.*?\]', '', frag)
                frag = re.sub(r'(?<!\\)\.', '', frag)
                frag = frag.replace(r'\.', '.')
                frag = frag.replace('(?:', '').replace(')?', '').replace(')', '')
                for word in frag.split():
                    if len(word) > 2 and not word.isdigit():
                        literal_words.add(word.lower())

        for idx, word in enumerate(literal_words):
            self.automaton.add_word(word, (idx, word))
            
        self.automaton.make_automaton()
        print(f"Ahocorasick automaton has {len(literal_words)} words")

    def might_contain(self, text):
        # lower is required because words in automaton are lowercase
        for _, _ in self.automaton.iter(text.lower()):
            return True
        return False

matcher = AhoCorasickFilter(_REF_SPECS)

large_text = "Neste texto não há nenhuma menção a livros da bíblia, apenas texto aleatório para testar o filtro rápido que deve ignorar este texto"*5000
start = time.time()
for _ in range(1000):
   if matcher.might_contain(large_text):
       pass
print(f"Miss time: {time.time()-start}")

large_text2 = "Aqui sim tem mateus 2 e também outras coisas mateus 2"*5000
start = time.time()
for _ in range(1000):
    if matcher.might_contain(large_text2):
        pass
print(f"Hit time: {time.time()-start}")
