#!/usr/bin/env python3
"""Injeta o CSS "cara de desktop" dentro do index.html do dashboard do Hermes.

Por que inline e não <link>: o dashboard é servido por um servidor estático sem
rota própria, e o web_dist é reescrito a cada rebuild — então o CSS precisa ser
reaplicado sempre (o dashboard-up.sh chama este script).

Uso:  patch-dashboard-look.py [caminho_do_web_dist] [caminho_do_css]
Padrões: ~/hermes-agent/hermes_cli/web_dist  e  ~/.hermes/desktop-look.css
Idempotente: substitui o bloco anterior se já existir.
"""
from __future__ import annotations

import pathlib
import sys

D = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    pathlib.Path.home() / "hermes-agent" / "hermes_cli" / "web_dist")
CSS = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else (
    pathlib.Path.home() / ".hermes" / "desktop-look.css")

ABRE = '<style id="hermes-desktop-look">'
FECHA = "</style>"

idx = D / "index.html"
if not idx.exists():
    raise SystemExit(f"ERRO: nao achei index.html em {D}")

if not CSS.exists():
    print(f"AVISO: {CSS} nao existe — nada injetado (rode quando o CSS existir)")
    raise SystemExit(0)

# um "</style" dentro do CSS (comentário, string) fecharia o bloco antes da hora
css = CSS.read_text(encoding="utf-8").replace("</style", "<\\/style")
html = idx.read_text(encoding="utf-8")

# remove bloco anterior (idempotência)
if ABRE in html:
    ini = html.index(ABRE)
    fim = html.index(FECHA, ini) + len(FECHA)
    # leva junto o "\n  " que a injeção põe antes do </head> — sem isso o
    # arquivo crescia 4 bytes a cada execução do dashboard-up
    if html.startswith("\n  ", fim):
        fim += 3
    html = html[:ini] + html[fim:]

bloco = f"{ABRE}\n/* gerado por patch-dashboard-look.py a partir de {CSS.name} */\n{css}\n{FECHA}"
if "</head>" in html:
    html = html.replace("</head>", bloco + "\n  </head>", 1)
else:
    html = bloco + html

# escrita atômica: o dashboard pode estar servindo este arquivo agora, e uma
# escrita interrompida (OOM/kill no meio) deixava o index.html truncado
tmp = idx.with_name(".index.html.tmp")
tmp.write_text(html, encoding="utf-8")
tmp.replace(idx)
print(f"CSS de {CSS} injetado em {idx} ({len(css)} chars de CSS)")
