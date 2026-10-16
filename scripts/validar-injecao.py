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

# 1) `%`: so e problema se a formatacao REAL falhar (etapa 3). O heurístico antigo rodava em
#    cima do `%%` ja escapado e acusava o SEGUNDO `%` de cada par — falso positivo que mandava
#    o usuario cacar bug que nao existe.
sem_escape = template.replace("%%", "")
suspeitos = [(mm.start(), sem_escape[max(0, mm.start() - 50):mm.start() + 20])
             for mm in re.finditer(r"%(?![%sd(])", sem_escape)]

# 2) `{{` que viraria chave dobrada no JS
if "{{" in template:
    print("AVISO: `{{` no template — em template `%`-formatado isso chega literal no JS")

# 3) a formatacao em si (o teste que reproduz o erro do servidor)
VALORES = {
    "backend": '"http://127.0.0.1:9119"',
    "token": '"token-de-teste"',
    "flags": '{"telemetria": true, "dom": true, "desktop": true, "cmd": true}',
    "base_w": "800",
    "telemetria": "true",
}
try:
    injecao = template % VALORES
    print(f"formatacao OK ({len(injecao)} chars)")
    if suspeitos:
        print(f"NOTA: {len(suspeitos)} `%` fora do padrao, mas a formatacao passou — sao verbos "
              "validos do Python (`%r`, `%5.1f`...). Nada a corrigir.")
except Exception as e:
    print(f"FALHA na formatacao: {type(e).__name__}: {e}")
    if suspeitos:
        print("  -> `%` que o Python recusou, com contexto:")
        for pos, ctx in suspeitos[:5]:
            print(f"     @{pos}: ...{ctx.replace(chr(10), ' ')}...")
    print("  -> todo `%` que deve chegar literal no JS precisa ser `%%`")
    raise SystemExit(1)

# 4) TODO bloco <script> inline precisa ser JS valido (antes so o 1o era checado)
blocos = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", injecao, flags=re.S)
if not blocos:
    print("AVISO: nenhum <script> inline na injecao")
for n, bloco in enumerate(blocos, 1):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(bloco)
        tmp = fh.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    except FileNotFoundError:
        print("FALHA: `node` nao encontrado no PATH (precisa dele para checar a sintaxe)")
        falhas.append("node")
        break
    finally:
        # o temporario vazava a cada execucao
        pathlib.Path(tmp).unlink(missing_ok=True)
    if r.returncode == 0:
        print(f"sintaxe JS OK no bloco {n} ({len(bloco)} chars)")
    else:
        print(f"FALHA na sintaxe do JS injetado (bloco {n}):")
        print(r.stderr.strip()[:600])
        falhas.append("sintaxe")

# 5) marcas que ja custaram bug: retorno no meio do IIFE e chaves obrigatorias.
#    Procura na injecao INTEIRA: `__HERMES_STUBS__` nunca esteve no bloco inline (os stubs
#    vem por <script src="/__stubs.js">) e o aviso antigo era falso positivo fixo.
for bloco in blocos:
    if re.search(r"^\s*return\s*;?\s*$", bloco, flags=re.M):
        print("AVISO: `return` solto dentro do IIFE — aborta SSE/probes/reset que vem depois")
for marca in ("/__stubs.js", "/__shim.js", "/__events", "localStorage", "FLAGS"):
    if marca not in injecao:
        print(f"AVISO: a injecao nao menciona {marca} (era esperado)")

if falhas:
    raise SystemExit(1)
print("injecao valida")
raise SystemExit(0)
