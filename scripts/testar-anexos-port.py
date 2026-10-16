#!/usr/bin/env python3
"""testar-anexos-port.py — prova o fluxo de ANEXO pela INTERFACE (o que o dedo faz).

  botão "+" (Add context) → menu ATTACH → "Images…" → seletor do app
  (input type=file) → arquivo escolhido → chip do anexo no composer.

Detalhe que faz o teste ser possível: o seletor de arquivo precisa ser
INTERCEPTADO (`Page.setInterceptFileChooserDialog`). Sem isso o Chrome headless
CANCELA na hora (não existe diálogo para o usuário cancelar) e o que se mede é o
cancelamento, não o fluxo — foi assim que perdi uma rodada achando que a ponte
não era chamada.

Requer: Chromium com CDP em 127.0.0.1:9222 (`chrome-cdp up`), o injetor no ar e
uma imagem de teste em ~/.hermes/cache/scratch/teste-anexo.png.
Rodar com o python do venv do Hermes.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

import websockets

CDP = "http://127.0.0.1:9222"
URL = "http://127.0.0.1:8890/"
IMAGEM = "/data/data/com.termux/files/home/.hermes/cache/scratch/teste-anexo.png"


class C:
    def __init__(self, ws):
        self.ws, self.i = ws, 0

    async def cmd(self, m, p=None):
        self.i += 1
        i = self.i
        await self.ws.send(json.dumps({"id": i, "method": m, "params": p or {}}))
        while True:
            x = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=120))
            if x.get("id") == i:
                return x.get("result", x)

    async def js(self, e):
        r = await self.cmd("Runtime.evaluate",
                           {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            return "EXCECAO: " + json.dumps(r["exceptionDetails"])[:300]
        return r.get("result", {}).get("value")

    async def clique(self, x, y):
        """UM clique só. Clique duplo em menu Radix abre e FECHA (já me enganou)."""
        await self.cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await asyncio.sleep(0.05)
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.08)
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.6)


async def main():
    with urllib.request.urlopen(f"{CDP}/json/list", timeout=10) as r:
        pag = [a for a in json.load(r) if a.get("type") == "page"]
    if not pag:
        with urllib.request.urlopen(
                urllib.request.Request(f"{CDP}/json/new?about:blank", method="PUT"), timeout=10) as r:
            pag = [json.load(r)]
    async with websockets.connect(pag[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        c = C(ws)
        for dom in ("Page", "Runtime", "DOM"):
            await c.cmd(f"{dom}.enable")
        await c.cmd("Emulation.setDeviceMetricsOverride",
                    {"width": 384, "height": 832, "deviceScaleFactor": 3, "mobile": True})
        await c.cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        await c.cmd("Page.setInterceptFileChooserDialog", {"enabled": True})
        await c.cmd("Page.navigate", {"url": URL})
        for _ in range(30):
            await asyncio.sleep(1)
            if isinstance(await c.js("document.querySelectorAll('*').length"), int):
                break
        await asyncio.sleep(6)

        alvo = await c.js("""(function(){
          var els = document.querySelectorAll('span.hover-marquee-inner');
          for (var i=0;i<els.length;i++){
            var t=(els[i].textContent||'').trim(); if (t.length<18) continue;
            var r=els[i].getBoundingClientRect(); if (r.x>300||r.height<8) continue;
            if (/navegador|docker|openssh/i.test(t))
              return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});
          }
          return null; })()""")
        if alvo:
            d = json.loads(alvo)
            await c.clique(d["x"], d["y"])
            await asyncio.sleep(9)

        print("=== 1. toque no botao '+' (Add context) ===")
        b = await c.js("""(function(){var e=document.querySelector('[aria-label="Add context"]');
          if(!e) return null; var r=e.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});})()""")
        p = json.loads(b)
        await c.clique(p["x"], p["y"])
        print("   menu aberto?", await c.js("document.querySelectorAll('[role=menuitem]').length"), "itens")

        print("\n=== 2. toque em 'Images…' ===")
        im = await c.js("""(function(){var it=document.querySelectorAll('[role=menuitem]');
          for(var i=0;i<it.length;i++){var t=(it[i].textContent||'').trim();
            if(/^Images/i.test(t)){var r=it[i].getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),t:t});}}
          return null;})()""")
        if not im:
            print("   !! item Images nao encontrado")
            return
        q = json.loads(im)
        await c.clique(q["x"], q["y"])
        await asyncio.sleep(1.5)
        print("   seletor criado?", await c.js("!!document.querySelector('input[type=file]')"),
              "| accept:", await c.js("(document.querySelector('input[type=file]')||{}).accept"))

        print("\n=== 3. escolhendo o arquivo (o que o Android faria) ===")
        doc = await c.cmd("DOM.getDocument", {"depth": -1})
        qq = await c.cmd("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                               "selector": "input[type=file]"})
        if qq.get("nodeId"):
            await c.cmd("DOM.setFileInputFiles", {"files": [IMAGEM], "nodeId": qq["nodeId"]})
            await asyncio.sleep(4)

        print("\n=== 4. o anexo apareceu no composer? ===")
        print(await c.js("""(function(){
          var texto = document.body.innerText.replace(/\\s+/g,' ');
          var chips = [];
          var els = document.querySelectorAll('img[src^="blob:"],img[src^="data:image"]');
          for (var i=0;i<els.length;i++){ var r=els[i].getBoundingClientRect();
            if (r.width>8) chips.push(Math.round(r.width)+'x'+Math.round(r.height)); }
          return JSON.stringify({nome_do_arquivo:/teste-anexo/.test(texto), miniaturas:chips});})()"""))


if __name__ == "__main__":
    asyncio.run(main())
