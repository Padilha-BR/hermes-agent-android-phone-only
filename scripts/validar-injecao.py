#!/usr/bin/env python3
"""Valida a injecao de hermes-desktop-web.py ANTES de abrir a pagina.

Por que existe: a injecao e um template Python `%`-formatado que carrega CSS/JS
junto. Um `%` literal (`left:50%`, `translateX(-50%)`) faz `PONTE % {...}`
estourar com `TypeError: not enough arguments for format string`, e o sintoma
engana: o servidor fecha a conexao sem resposta (o cliente ve
`RemoteDisconnected`) e o log NAO mostra traceback. Este script reproduz a
formatacao fora do servidor, extrai o bloco <script> resultante e roda
`node --check`.

Uso:
    python scripts/validar-injecao.py [caminho/do/hermes-desktop-web.py]

Saida: 0 = injecao valida; 1 = problema (com o motivo exato).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

PADRAO = pathlib.Path.home() / ".hermes" / "bin" / "hermes-desktop-web.py"
caminho = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else PADRAO

if not caminho.is_file():
    print(f"nao achei {caminho}")
    raise SystemExit(1)

texto = caminho.read_text(encoding="utf-8")
m = re.search(r'^PONTE = (?:r?)f?"""(.*?)"""', texto, flags=re.S | re.M)
if not m:
    print("nao achei o template PONTE (esperado: PONTE = \"\"\"...\"\"\")")
    raise SystemExit(1)

template = m.group(1)
falhas: list[str] = []

# 1) `%` solto: verbo de formatacao invalido
soltos = re.findall(r'%(?![%sd(])', template)
if soltos:
    print(f"AVISO: {len(soltos)} `%` literal no template — troque por `%%`")

# 2) `{{` que viraria chave dobrada no JS
if "{{" in template:
    print("AVISO: `{{` no template — em template `%`-formatado isso chega literal no JS")

# 3) a formatacao em si (o teste que reproduz o erro do servidor)
try:
    injecao = template % {"backend": '"http://127.0.0.1:9119"', "token": '"token-de-teste"'}
    print(f"formatacao OK ({len(injecao)} chars)")
except Exception as e:
    print(f"FALHA na formatacao: {type(e).__name__}: {e}")
    print("  -> procure `%` literal no template (todo `%` precisa virar `%%`)")
    raise SystemExit(1)

# 4) o bloco <script> inline precisa ser JS valido
blocos = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", injecao, flags=re.S)
if not blocos:
    print("AVISO: nenhum <script> inline na injecao")
else:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(blocos[0])
        tmp = fh.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"sintaxe JS OK ({len(blocos[0])} chars)")
    else:
        print("FALHA na sintaxe do JS injetado:")
        print(r.stderr.strip()[:600])
        falhas.append("sintaxe")

# 5) marcas que ja custaram bug: retorno no meio do IIFE e chaves obrigatorias
if re.search(r"^\s*return\s*;?\s*$", blocos[0], flags=re.M) if blocos else False:
    print("AVISO: `return` solto dentro do IIFE — aborta SSE/probes/reset que vem depois")
for marca in ("__HERMES_STUBS__", "/__events", "localStorage"):
    if blocos and marca not in blocos[0]:
        print(f"AVISO: a injecao nao menciona {marca} (era esperado)")

if falhas:
    raise SystemExit(1)
print("injecao valida")
raise SystemExit(0)
