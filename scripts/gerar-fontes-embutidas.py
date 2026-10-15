#!/usr/bin/env python3
"""Gera o CSS com as fontes do bundle embutidas em data URI.

Por que existe: os arquivos do bundle carregam hash de conteudo no nome
(`Collapse-Bold-mgICk9-_.woff2`). Todo rebuild troca o hash, entao um CSS de
fontes escrito "a mao" passa a apontar para arquivo inexistente — e a falha e
SILENCIOSA, porque `font-display: swap` mostra a fonte de sistema e o usuario so
percebe que "a fonte do app mudou". Rode este script depois de cada rebuild.

Uso:
    python scripts/gerar-fontes-embutidas.py [dir-do-bundle] [saida.css]

Padroes: bundle = ~/hermes-agent/.hermes-port/renderer
         saida  = ~/.hermes/desktop-fonts.css
"""
from __future__ import annotations

import base64
import pathlib
import sys

BUNDLE = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    pathlib.Path.home() / "hermes-agent" / ".hermes-port" / "renderer")
SAIDA = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else (
    pathlib.Path.home() / ".hermes" / "desktop-fonts.css")

# (familia CSS, prefixo do arquivo sem hash, peso, estilo)
FAMILIAS = [
    ("Collapse", "Collapse", 700, "normal"),
    ("JetBrains Mono", "JetBrainsMono-Regular", 400, "normal"),
    ("JetBrains Mono", "JetBrainsMono-Italic", 400, "italic"),
    ("JetBrains Mono", "JetBrainsMono-Bold", 700, "normal"),
]

assets = BUNDLE / "assets"
if not assets.is_dir():
    print(f"nao achei {assets} — passe o diretorio do bundle como 1o argumento")
    raise SystemExit(1)

cabecalho = (
    "/* GERADO por gerar-fontes-embutidas.py — NAO editar a mao.\n"
    "   Fontes do bundle embutidas em data URI com font-display:block: o wordmark\n"
    "   (HERMES AGENT) usa a Collapse com font-display:swap e, quando a requisicao\n"
    "   falha, a tela fica na fonte de sistema. Embutida, nao depende de rede nem\n"
    "   de cache. Regenerar depois de todo rebuild do bundle (o hash do arquivo muda).\n"
    "   Injetado via HERMES_WEB_EXTRA_CSS (lista separada por virgula). */"
)

regras = [cabecalho]
faltando: list[str] = []
for familia, prefixo, peso, estilo in FAMILIAS:
    achados = sorted(assets.glob(prefixo + "*.woff2"))
    if not achados:
        faltando.append(prefixo)
        continue
    arquivo = achados[0]
    b64 = base64.b64encode(arquivo.read_bytes()).decode()
    regras.append(
        f"@font-face{{font-family:'{familia}';font-style:{estilo};font-weight:{peso};"
        f"font-display:block;src:url(data:font/woff2;base64,{b64}) format('woff2')}}"
    )
    print(f"{familia} {peso}/{estilo}: {arquivo.name} ({arquivo.stat().st_size // 1024} KB)")

SAIDA.write_text("\n".join(regras) + "\n", encoding="utf-8")
print(f"gerado {SAIDA} ({SAIDA.stat().st_size // 1024} KB)")

if faltando:
    print("ATENCAO: nao achei " + ", ".join(faltando) + " — confira os nomes no bundle")
    raise SystemExit(2)
print("ok: reinicie o servidor (hermes-desktop-up) para a injecao usar o novo CSS")
raise SystemExit(0)
