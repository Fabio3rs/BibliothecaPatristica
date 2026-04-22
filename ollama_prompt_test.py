#!/usr/bin/env python3
"""
Small helper to hit a local Ollama model (default: qwen3.5:9b) with custom
system and user prompts.

Usage examples:
    python ollama_prompt_test.py --system "You are concise." --user "Hello"
    python ollama_prompt_test.py --model qwen3.5:9b --user "Explique a graça"

If a prompt is omitted, you will be prompted for it interactively.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
import os

DEFAULT_TOP_P = 0.8
DEFAULT_TEMPERATURE = 0.1

DEFAULT_SYSTEM="""
Você vai receber uma palavra ou expressão em português, latim, grego, inglês, francês, hebraico, aramaico, sírio, armênio ou etíope do contexto Patrístico/bíblico.
Objetivo: identificar a língua e fornecer informações relevantes sobre a palavra ou expressão.
Aponte se a original está incorreta (erros OCR ou grafia em geral).

Saída APENAS JSON UTF-8:
{
  "palavras": [
    {
      "palavra": "exemplo",
      "correcao": "exemplo",
      "lingua": "português",
      "informacoes": {
        "definicao": "Um exemplo é uma amostra ou um caso que ilustra uma regra ou conceito.",
        "sinonimos": ["modelo", "amostra", "ilustração"]
      }
    },
    ... outras palavras se existir ...
  ]
}
"""

DEFAULT_SYSTEM_CURTO = """
Você vai receber uma palavra ou expressão em português, latim, grego, inglês, francês, hebraico, aramaico, sírio, armênio ou etíope do contexto Patrístico/bíblico.
Objetivo: identificar a língua e se a original está incorreta (erros OCR ou grafia em geral).

Saída APENAS JSON UTF-8:
{
  "palavras": [
    {
      "palavra": "exemplo",
      "correcao": "exemplo",
      "lingua": "português",
      "notas": "Nota sobre a palavra exemplo. Opcional, pode ser vazio"
    }
    ... outras palavras se existir ...
  ]
}
"""

def call_ollama(model: str, system_prompt: str, user_prompt: str, base_url: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "top_p": DEFAULT_TOP_P,
            "temperature": DEFAULT_TEMPERATURE,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick Ollama chat call")
    parser.add_argument("--model", default="qwen3.5:9b", help="Model name (default: qwen3.5:9b)")
    parser.add_argument("--system", help="System prompt text", default=DEFAULT_SYSTEM_CURTO)
    parser.add_argument("--user", help="User prompt text")
    parser.add_argument("--url", default=os.getenv("OLLAMA_URL", "http://localhost:11434"), help="Base URL")
    args = parser.parse_args()

    system_prompt = args.system or input("System prompt: ").strip()
    user_prompt = args.user or input("User prompt: ").strip()

    try:
        answer = call_ollama(args.model, system_prompt, user_prompt, args.url)
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error calling Ollama: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n=== Response ===\n")
    print(answer)


if __name__ == "__main__":
    main()
