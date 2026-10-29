#!/usr/bin/env python3
"""Prova final do "segurar para copiar" no webapp (8890).

1) toque longo num parágrafo → seleção não vazia + balão "Copiar"
2) clique com ativação de usuário → clipboard do sistema com ESSE texto
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

import websockets

CDP = "http://127.0.0.1:9222"
ORIGEM = "http://127.0.0.1:8890"
SESSAO = "#/20261016_000853_3e1fc1"
BALAO = '[aria-label="Copiar texto selecionado"]'


class C:
    def __init__(self, ws):
        self.ws, self.i = ws, 0

    async def cmd(self, m, p=None, timeout=90):
        self.i += 1
        i = self.i
        await self.ws.send(json.dumps({"id": i, "method": m, "params": p or {}}))
        while True:
            r = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            if r.get("id") == i:
                return r.get("result", r)

    async def js(self, e, gesto=False):
        p = {"expression": e, "returnByValue": True, "awaitPromise": True}
        if gesto:
            p["userGesture"] = True
        r = await self.cmd("Runtime.evaluate", p)
        if "exceptionDetails" in r:
            return "EXCECAO " + str(r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")


async def main():
    with urllib.request.urlopen(f"{CDP}/json/list", timeout=10) as r:
        pags = [a for a in json.load(r) if a.get("type") == "page"]
    if not pags:
        with urllib.request.urlopen(urllib.request.Request(f"{CDP}/json/new?about:blank",
                                                           method="PUT"), timeout=10) as r:
            pags = [json.load(r)]
    async with websockets.connect(pags[0]["webSocketDebuggerUrl"], max_size=2**26) as ws:
        c = C(ws)
        for d_ in ("Page", "Runtime"):
            await c.cmd(f"{d_}.enable")
        try:
            await c.cmd("Browser.grantPermissions", {"origin": ORIGEM, "permissions":
                       ["clipboardReadWrite", "clipboardSanitizedWrite"]})
        except Exception as e:
            print("(aviso) permissão de clipboard:", e)
        await c.cmd("Emulation.setDeviceMetricsOverride",
                    {"width": 384, "height": 832, "deviceScaleFactor": 3, "mobile": True})
        await c.cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        await c.cmd("Page.navigate", {"url": f"{ORIGEM}/?v={asyncio.get_event_loop().time()}{SESSAO}"})
        await asyncio.sleep(11)

        alvo = await c.js("""(async function(){
          var sc=[...document.querySelectorAll('div')].filter(function(e){
            var cs=getComputedStyle(e); return (cs.overflowY==='auto'||cs.overflowY==='scroll')
              && e.scrollHeight>e.clientHeight+50 && e.getBoundingClientRect().x>200;})[0];
          if(sc){ sc.scrollTop=sc.scrollHeight; await new Promise(r=>setTimeout(r,600)); }
          var els=[...document.querySelectorAll('p,div,span')].filter(function(e){
            var r=e.getBoundingClientRect(); return e.children.length===0 &&
              (e.textContent||'').trim().length>60 && r.x>250 && r.y>80 && r.y<620;});
          if(!els.length) return null;
          var e=els[0], r=e.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.x+40),y:Math.round(r.y+r.height/2),
                                 txt:(e.textContent||'').trim()});})()""")
        if not alvo:
            print("!! sem texto visível na tela")
            return
        a = json.loads(alvo)
        print("alvo:", a["txt"][:60])

        await c.cmd("Input.dispatchTouchEvent", {"type": "touchStart",
                                                 "touchPoints": [{"x": a["x"], "y": a["y"]}]})
        await asyncio.sleep(1.1)
        durante = json.loads(await c.js("""JSON.stringify({
          selecao:(window.getSelection()||{}).toString().trim(),
          balao:!!document.querySelector('%s'),
          menus:document.querySelectorAll('[role=menu]').length})""" % BALAO))
        await c.cmd("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        await asyncio.sleep(0.4)

        print("1) toque longo → seleção de %d chars | balão: %s | menus abertos: %d"
              % (len(durante["selecao"]), durante["balao"], durante["menus"]))

        if not durante["balao"]:
            print("RESULTADO: FALHOU ✘ (o balão não apareceu)")
            return

        await c.js("document.querySelector('%s').click()" % BALAO, gesto=True)
        await asyncio.sleep(1.0)
        lido = await c.js("navigator.clipboard.readText().catch(e=>'ERRO '+e.name)")
        print("2) clique no balão → clipboard: %r" % (str(lido)[:70]))
        ok = isinstance(lido, str) and lido.strip() and lido.strip()[:40] in a["txt"]
        print("RESULTADO:", "OK ✔ (a seleção foi para o clipboard)" if ok else "FALHOU ✘")
        await c.js("(window.getSelection()||{removeAllRanges(){}}).removeAllRanges()")


if __name__ == "__main__":
    asyncio.run(main())
