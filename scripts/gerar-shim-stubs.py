#!/usr/bin/env python3
"""Gera os stubs de TODA a superfície de window.hermesDesktop a partir do contrato
de tipos do próprio app (apps/desktop/src/global.d.ts).

Motivo: caçar método faltante por método faltante era lento e frágil — cada
`on*` ausente virava uma Promise no lugar de um cancelador e derrubava a UI com
"X is not a function". Aqui a lista sai do contrato, não da minha memória.

Saída: ~/.hermes/bin/shim-stubs.js  (define window.__HERMES_STUBS__)
Uso:   gerar-shim-stubs.py [caminho/global.d.ts]
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

DTS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    pathlib.Path.home() / "hermes-agent" / "apps" / "desktop" / "src" / "global.d.ts")
SAIDA = pathlib.Path.home() / ".hermes" / "bin" / "shim-stubs.js"

texto = DTS.read_text(encoding="utf-8")

# ── isola o bloco `hermesDesktop: { ... }` ────────────────────────────────────
m = re.search(r"hermesDesktop:\s*\{", texto)
if not m:
    raise SystemExit("nao achei hermesDesktop em " + str(DTS))
i = m.end() - 1
prof = 0
for j in range(i, len(texto)):
    if texto[j] == "{":
        prof += 1
    elif texto[j] == "}":
        prof -= 1
        if prof == 0:
            break
bloco = texto[i + 1:j]

# ── parser simples de membros (respeita aninhamento) ─────────────────────────
def membros(src: str, nivel: int = 0) -> list[tuple[str, str]]:
    """[(nome, tipo)] no nível atual, ignorando comentários.

    A indentação é a fonte de verdade: no `global.d.ts` os membros do bloco estão
    a 6 espaços, e os de um objeto aninhado a 8+. Filtrar por indentação evita
    capturar nomes de PARÂMETROS (ex.: `(profile?: string, opts?: {...})`), que foi
    o erro da primeira versão deste gerador.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    linhas = src.split("\n")
    # descobre a indentação dos membros: a menor indentação de uma linha `nome:`
    indentacoes = [len(l) - len(l.lstrip()) for l in linhas
                   if re.match(r"^\s*[A-Za-z_$][\w$]*\??\s*:", l)]
    if not indentacoes:
        return []
    alvo = min(indentacoes)
    saida: list[tuple[str, str]] = []
    vistos: set[str] = set()
    i = 0
    while i < len(linhas):
        l = linhas[i]
        ind = len(l) - len(l.lstrip())
        m2 = re.match(r"^\s*([A-Za-z_$][\w$]*)\??\s*:\s*(.*)$", l)
        if ind != alvo or not m2:
            i += 1
            continue
        nome, resto = m2.group(1), m2.group(2)
        if nome in vistos:
            i += 1
            continue
        vistos.add(nome)
        if resto.strip().startswith("{"):
            # objeto aninhado: consome até fechar, com aninhamento por chaves
            prof = 0
            pedaco: list[str] = []
            j = i
            while j < len(linhas):
                pedaco.append(linhas[j])
                prof += linhas[j].count("{") - linhas[j].count("}")
                if prof <= 0 and j >= i:
                    break
                j += 1
            bloco = "\n".join(pedaco)
            interno = bloco[bloco.index("{") + 1:bloco.rindex("}")] if "}" in bloco else ""
            saida.append((nome, "{" + interno + "}"))
            i = j + 1
            continue
        saida.append((nome, resto.strip().rstrip(";")))
        i += 1
    return saida


def eh_funcao(tipo: str) -> bool:
    return tipo.lstrip().startswith("(") or "=>" in tipo.split("\n")[0]


def eh_assinatura(nome: str, tipo: str) -> bool:
    """Inscrição: devolve um cancelador (função), não uma Promise."""
    return nome.startswith("on") or "Unsubscribe" in tipo or "=> () =>" in tipo or tipo.count("=>") >= 2


def stub(nome: str, tipo: str, nivel: int = 0) -> str:
    tipo_limpo = tipo.strip()
    if tipo_limpo.startswith("{") and nivel < 3:
        filhos = membros(tipo_limpo[1:-1] if tipo_limpo.endswith("}") else tipo_limpo[1:])
        if filhos:
            corpo = ",".join(f"{json.dumps(n)}:{stub(n, t, nivel + 1)}" for n, t in filhos)
            return "{" + corpo + "}"
        return "{}"
    if eh_funcao(tipo_limpo):
        if eh_assinatura(nome, tipo_limpo):
            return "() => () => {}"          # cancelador de verdade
        return "async () => null"
    if re.match(r"^(boolean|false|true)\b", tipo_limpo):
        return "false"
    if re.match(r"^number\b", tipo_limpo):
        return "0"
    if re.match(r"^string\b", tipo_limpo):
        return "''"
    if "[]" in tipo_limpo or tipo_limpo.startswith("Array<"):
        return "[]"
    return "null"


itens = membros(bloco)
js = []
for nome, tipo in itens:
    js.append(f"  {json.dumps(nome)}: {stub(nome, tipo)}")

corpo = ",\n".join(js)
saida = (
    "// GERADO por gerar-shim-stubs.py a partir de apps/desktop/src/global.d.ts\n"
    "// NÃO editar à mão: rode o gerador de novo quando o contrato mudar.\n"
    "window.__HERMES_STUBS__ = {\n" + corpo + "\n}\n"
)
SAIDA.write_text(saida, encoding="utf-8")
print(f"{len(itens)} chaves geradas → {SAIDA}")
print("amostra:", ", ".join(n for n, _ in itens[:14]))
