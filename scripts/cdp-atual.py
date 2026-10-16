#!/usr/bin/env python3
"""cdp-atual — piloto CDP contra o Chromium JA NO AR (9222), perfil persistente.

Por que existe (e por que o webcdp nao serve para isto): o webcdp LANCA e MATA um
Chromium a cada chamada (perfil $TMPDIR/webcdp-perfil, ~5 s de vida), e o
Runtime.evaluate dele nao usa awaitPromise (Promise vira {}). Isso torna
impossivel medir service worker — registro, ativacao e controle nao cabem em
5 s de vida. Aqui o navegador e o mesmo entre comandos.

Uso:
    cdp-atual.py nav <url> [segundos]           # navega e espera
    cdp-atual.py js  '<expressao>'              # avalia na pagina atual (awaitPromise)
    cdp-atual.py nav-js <url> '<expr>' [seg]    # navega, espera e avalia

Rode com o python do venv do Hermes (`~/hermes-agent/venv/bin/python`): precisa
de `websockets`, que so existe la.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import websockets

PORTA = 9222


def pagina_ws() -> str:
    alvos = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{PORTA}/json/list", timeout=5).read())
    paginas = [t for t in alvos if t.get("type") == "page"]
    if not paginas:
        raise SystemExit("sem alvo de pagina no CDP (o Chromium de 9222 esta de pe?)")
    return paginas[0]["webSocketDebuggerUrl"]


async def falar(comandos: list[tuple[str, dict]], timeout: float = 40) -> list[dict]:
    saida = []
    async with websockets.connect(pagina_ws(), max_size=32 * 1024 * 1024) as ws:
        for i, (metodo, params) in enumerate(comandos, start=1):
            await ws.send(json.dumps({"id": i, "method": metodo, "params": params}))
        pendentes = set(range(1, len(comandos) + 1))
        while pendentes:
            r = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if r.get("id") in pendentes:
                pendentes.discard(r["id"])
                saida.append(r)
    return saida


def cmd_nav(url: str, segundos: float) -> None:
    asyncio.run(falar([("Page.enable", {}), ("Page.navigate", {"url": url})]))
    time.sleep(segundos)
    print(f"navegou {url} (espera {segundos}s)")


def cmd_js(expr: str) -> None:
    res = asyncio.run(falar([("Runtime.evaluate", {
        "expression": expr, "awaitPromise": True, "returnByValue": True})]))
    r = res[0].get("result", {}).get("result", {})
    print(r["value"] if "value" in r else json.dumps(r))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    modo = sys.argv[1]
    if modo == "nav":
        cmd_nav(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 5.0)
    elif modo == "js":
        cmd_js(sys.argv[2])
    elif modo == "nav-js":
        cmd_nav(sys.argv[2], float(sys.argv[4]) if len(sys.argv) > 4 else 5.0)
        cmd_js(sys.argv[3])
    else:
        raise SystemExit(__doc__)
