#!/usr/bin/env python3
"""eval.py — roda JS no Chromium CDP (headless) e imprime o resultado.

Uso:  venv/bin/python eval.py arquivo.js [--emular] [--nav URL]
      venv/bin/python eval.py --expr "document.title"
      --emular   aplica as métricas do aparelho (480x1040 @3x, touch)
      --nav URL  navega antes de avaliar
O JS pode ser uma IIFE que devolve string/objeto (returnByValue).
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

import websockets

CDP = "http://127.0.0.1:9222"


class C:
    def __init__(self, ws):
        self.ws, self.i = ws, 0

    async def cmd(self, metodo, params=None):
        self.i += 1
        i = self.i
        await self.ws.send(json.dumps({"id": i, "method": metodo, "params": params or {}}))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=120))
            if m.get("id") == i:
                return m.get("result", m)

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate",
                           {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            return "EXCECAO: " + json.dumps(r["exceptionDetails"])[:600]
        return r.get("result", {}).get("value")


async def main():
    args = sys.argv[1:]
    emular = "--emular" in args
    nav = None
    if "--nav" in args:
        nav = args[args.index("--nav") + 1]
    if "--expr" in args:
        expr = args[args.index("--expr") + 1]
    else:
        # primeiro argumento que não é flag nem valor de flag
        consumidos = {nav} if nav else set()
        arqs = [a for a in args if not a.startswith("--") and a not in consumidos]
        if not arqs:
            print("uso: eval.py arquivo.js [--emular] [--nav URL] | eval.py --expr '...'")
            return
        with open(arqs[0], encoding="utf-8") as fh:
            expr = fh.read()

    with urllib.request.urlopen(f"{CDP}/json/list", timeout=10) as r:
        alvos = json.load(r)
    pag = [a for a in alvos if a.get("type") == "page"]
    if not pag:
        with urllib.request.urlopen(f"{CDP}/json/new?about:blank", timeout=10) as r:
            pag = [json.load(r)]

    async with websockets.connect(pag[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        c = C(ws)
        await c.cmd("Page.enable"); await c.cmd("Runtime.enable")
        if emular:
            await c.cmd("Emulation.setDeviceMetricsOverride",
                        {"width": 480, "height": 1040, "deviceScaleFactor": 3, "mobile": True})
            await c.cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        if nav:
            await c.cmd("Page.navigate", {"url": nav})
            for _ in range(30):
                await asyncio.sleep(1)
                n = await c.js("document.querySelectorAll('*').length")
                if isinstance(n, int) and n > 200:
                    break
            await asyncio.sleep(4)
        v = await c.js(expr)
        print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
