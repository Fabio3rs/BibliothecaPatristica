import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import main2


def test_remover_markdown_backticks_xml():
    src = """
Some intro
```xml
<pagina estado="com_texto">\n  <bloco>conteudo</bloco>\n</pagina>
```
Some outro
"""
    out = main2.remover_markdown(src)
    assert "<pagina estado=\"com_texto\">" in out
    assert "Some intro" in out


def test_remover_markdown_tildes_language_id():
    src = """
Antes
~~~python
print('hello')
~~~
Depois
"""
    out = main2.remover_markdown(src)
    assert "print('hello')" in out
    assert "Antes" in out


def test_remover_markdown_multiple_blocks_and_empty():
    src = """
A
```xml
<root>1</root>
```
B
~~~xml
<other>2</other>
~~~
C
"""
    out = main2.remover_markdown(src)
    assert "<root>1</root>" in out
    assert "<other>2</other>" in out


def test_remover_markdown_no_fences_returns_trimmed():
    src = "   Texto sem fences\n\n"
    out = main2.remover_markdown(src)
    assert out == "Texto sem fences"