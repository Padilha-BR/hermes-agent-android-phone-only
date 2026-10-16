#!/usr/bin/env python3
"""medir-texto.py — mede o TEXTO DAS MENSAGENS (o que se lê) em cada escala,
com a sessão aberta. Também mede a largura útil da coluna e da lateral, para
decidir o padrão sem chutar.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

import websockets

CDP = "http://127.0.0.1:9222"
URL = "http://127.0.0.1:8890/"


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
        await self.cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.09)
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


M = r"""
(function () {
  var vv = window.visualViewport || {};
  var esc = vv.scale || 1;
  var dip = function (px) { return Math.round(px * esc * 10) / 10; };
  var css = function (el) { return el ? parseFloat(getComputedStyle(el).fontSize) : 0; };
  var msg = document.querySelector('[data-slot="aui_assistant-message-content"]');
  var par = msg ? msg.querySelector('p') : null;
  var usuario = document.querySelector('[data-slot="aui_user-message-root"], [data-slot="aui_user-message-content"]');
  var comp = document.querySelector('[data-slot="composer-rich-input"]');
  var thread = document.querySelector('[data-slot="aui_thread-content"]');
  var rt = thread ? thread.getBoundingClientRect() : null;
  // lateral: o painel mais largo e alto encostado na esquerda
  var lat = null;
  var cands = document.querySelectorAll('[data-slot="sidebar"],[data-pane-id],[class*="pane-"]');
  for (var i = 0; i < cands.length; i++) {
    var r = cands[i].getBoundingClientRect();
    if (r.x > 8 || r.height < 400 || r.width < 100) continue;
    if (!lat || r.width > lat.w) lat = { w: Math.round(r.width), slot: cands[i].getAttribute('data-slot') || cands[i].getAttribute('data-pane-id') || '?' };
  }
  return JSON.stringify({
    escala: Math.round(esc * 1000) / 1000,
    janela_css: innerWidth,
    meta: (document.querySelector('meta[name=viewport]')||{}).content,
    var_thread: thread ? getComputedStyle(thread).getPropertyValue('--conversation-text-font-size').trim() : '-',
    hint: document.querySelector('[data-slot="composer-rich-input"]') ? 'ok' : 'sem composer',
    msg_css: css(msg), msg_dip: dip(css(msg)),
    par_css: css(par), par_dip: dip(css(par)),
    par_linha: par ? getComputedStyle(par).lineHeight : '-',
    usuario_css: css(usuario), usuario_dip: dip(css(usuario)),
    composer_css: css(comp), composer_dip: dip(css(comp)),
    coluna_thread_px_css: rt ? Math.round(rt.width) : null,
    coluna_thread_dip: rt ? Math.round(rt.width * esc) : null,
    lateral: lat,
    lateral_dip: lat ? Math.round(lat.w * esc) : null
  });
})()
"""


async def main():
    with urllib.request.urlopen(f"{CDP}/json/list", timeout=10) as r:
        pag = [a for a in json.load(r) if a.get("type") == "page"]
    async with websockets.connect(pag[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        c = C(ws)
        await c.cmd("Page.enable"); await c.cmd("Runtime.enable")
        await c.cmd("Emulation.setDeviceMetricsOverride",
                    {"width": 480, "height": 1040, "deviceScaleFactor": 3, "mobile": True})
        await c.cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        await c.cmd("Page.navigate", {"url": URL})
        for _ in range(30):
            await asyncio.sleep(1)
            if isinstance(await c.js("document.querySelectorAll('*').length"), int):
                break
        await asyncio.sleep(6)

        alvo = await c.js("""(function(){
          var els = document.querySelectorAll('span.hover-marquee-inner');
          for (var i=0;i<els.length;i++){
            var t=(els[i].textContent||'').trim();
            if (t.length < 18) continue;
            var r = els[i].getBoundingClientRect();
            if (r.x > 300 || r.height < 8) continue;
            if (/navegador|docker|openssh/i.test(t))
              return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), txt: t.slice(0,40)});
          }
          return null;
        })()""")
        if alvo:
            d = json.loads(alvo)
            print(f"abrindo sessão {d['txt']!r}")
            await c.clique(d["x"], d["y"])
            await asyncio.sleep(9)

        print("\n=== TEXTO DAS MENSAGENS POR ESCALA DA UI (chat-scale semeado = 150%) ===")
        cab = ("escala", "meta", "ajuste", "msg CSS", "msg dip", "linha", "composer dip",
               "coluna dip", "lateral dip")
        print("  " + " | ".join(f"{x:>11}" for x in cab))
        for pct in (100, 110, 125, 150):
            await c.js(f"window.hermesDesktop.zoom.setPercent({pct})")
            await asyncio.sleep(2.5)
            m = json.loads(await c.js(M))
            print("  " + " | ".join(f"{str(v):>11}" for v in (
                pct, m["meta"].split(",")[0], m["escala"], m["msg_css"], m["msg_dip"],
                m["par_linha"], m["composer_dip"], m["coluna_thread_dip"], m["lateral_dip"])))

        print("\n=== CONTROLE: com o texto do chat no padrão do app (110%) ===")
        await c.js("localStorage.setItem('hermes.desktop.chat-text-scale.v1','110')")
        await c.cmd("Page.navigate", {"url": URL})
        await asyncio.sleep(8)
        v = await c.js("localStorage.getItem('hermes.desktop.chat-text-scale.v1')")
        print("  chat-scale no storage:", v, "(o shim NÃO deve sobrescrever)")
        await c.js("window.hermesDesktop.zoom.setPercent(125)")
        await asyncio.sleep(2.5)
        m = json.loads(await c.js(M))
        print("  125% + 110%:", json.dumps({k: m[k] for k in
              ("escala", "msg_css", "msg_dip", "linha", "coluna_thread_dip", "lateral_dip")}))


if __name__ == "__main__":
    asyncio.run(main())
