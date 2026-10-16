#!/usr/bin/env python3
"""teste-divisor-port.py — prova que o divisor de painéis (slider lateral) responde ao DEDO.

Mede a POSIÇÃO DO FIO entre os painéis antes/depois de um arrasto de TOQUE sintético e
conta pointercancel. Arrasto de 60px CSS deve mover o fio ~60px CSS com cancel=0.

Sem a correção (touch-action: auto + faixa de 8px) o resultado é: pointercancel no 3º
movimento e o fio praticamente parado. Com a correção: 1:1, sem cancelamento, inclusive
com o dedo fora da faixa original (é o alvo ampliado por ::after).

Requer: Chromium com CDP em 127.0.0.1:9222 (comando chrome-cdp up) e o injetor no ar.
Rodar com o python do venv do Hermes:
    ~/hermes-agent/venv/bin/python teste-divisor-port.py
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

    async def toque(self, tipo, x, y):
        pts = [] if tipo == "touchEnd" else [{"x": x, "y": y, "radiusX": 12, "radiusY": 12,
                                              "force": 1, "id": 1}]
        await self.cmd("Input.dispatchTouchEvent", {"type": tipo, "touchPoints": pts})


SONDA = r"""
(function () {
  if (!window.__inst) {
    window.__inst = true;
    window.__ev = {down: 0, move: 0, cancel: 0, up: 0, naSash: 0};
    addEventListener('pointerdown', function (e) {
      if (e.pointerType !== 'touch') return;
      window.__ev.down++;
      if (e.target && e.target.closest && e.target.closest('[data-sash-overlap]')) window.__ev.naSash++;
    }, true);
    ['pointermove', 'pointercancel', 'pointerup'].forEach(function (n) {
      addEventListener(n, function (e) {
        if (e.pointerType !== 'touch') return;
        window.__ev[n.replace('pointer', '')]++;
      }, true);
    });
  }
  var s = document.querySelector('[data-sash-overlap]');
  if (!s) return JSON.stringify(null);
  var r = s.getBoundingClientRect();
  var cs = getComputedStyle(s);
  var fa = getComputedStyle(s, '::after');
  return JSON.stringify({
    x: Math.round(r.x * 10) / 10, largura: Math.round(r.width * 10) / 10,
    cx: Math.round(r.x + r.width / 2), cy: Math.round(r.y + r.height / 2),
    touch_action: cs.touchAction,
    alvo_after: fa.content !== 'none' ? (fa.left + ' / ' + fa.right) : 'sem ::after'
  });
})()
"""


async def main():
    with urllib.request.urlopen(f"{CDP}/json/list", timeout=10) as r:
        pag = [a for a in json.load(r) if a.get("type") == "page"]
    if not pag:  # /json/new exige PUT nas versoes novas do Chrome
        with urllib.request.urlopen(
                urllib.request.Request(f"{CDP}/json/new?about:blank", method="PUT"), timeout=10) as r:
            pag = [json.load(r)]
    async with websockets.connect(pag[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        c = C(ws)
        await c.cmd("Page.enable")
        await c.cmd("Runtime.enable")
        # a tela do aparelho: 480 dip de largura, DPR 3
        await c.cmd("Emulation.setDeviceMetricsOverride",
                    {"width": 480, "height": 1040, "deviceScaleFactor": 3, "mobile": True})
        await c.cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        await c.cmd("Page.navigate", {"url": URL})
        for _ in range(30):
            await asyncio.sleep(1)
            if isinstance(await c.js("document.querySelectorAll('*').length"), int):
                break
        await asyncio.sleep(6)

        # abre uma sessão: sem layout de painéis não existe divisor para medir
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
            await c.toque("touchStart", d["x"], d["y"])
            await c.toque("touchEnd", 0, 0)
            await asyncio.sleep(9)

        s = json.loads(await c.js(SONDA) or "null")
        if not s:
            print("!! nenhum divisor ([data-sash-overlap]) na tela")
            return
        print("=== DIVISOR ===")
        print(f"  fio em x={s['x']}  faixa={s['largura']}px CSS  touch-action={s['touch_action']}")
        print(f"  alvo estendido (::after): {s['alvo_after']}")

        async def arrastar(rotulo, desloc, passos=8):
            await c.js("window.__ev={down:0,move:0,cancel:0,up:0,naSash:0}")
            # RELER sempre: o arrasto anterior moveu o fio
            agora = json.loads(await c.js(SONDA))
            antes = agora["x"]
            x0 = agora["cx"] + desloc
            await c.toque("touchStart", x0, agora["cy"])
            await asyncio.sleep(0.15)
            passo = 60.0 / passos
            for k in range(1, passos + 1):
                await c.toque("touchMove", x0 + k * passo, agora["cy"])
                await asyncio.sleep(0.045)
            await c.toque("touchEnd", 0, 0)
            await asyncio.sleep(1.2)
            depois = json.loads(await c.js(SONDA))["x"]
            ev = json.loads(await c.js("JSON.stringify(window.__ev)"))
            print(f"\n  {rotulo}: dedo +60px CSS | fio {antes} → {depois}"
                  f" = {round(depois - antes, 1)}px | eventos {ev}")

        await arrastar("no centro da faixa", 0)
        await arrastar("dedo 16px CSS à ESQUERDA do centro (fora da faixa, dentro do alvo novo)", -16)
        await arrastar("dedo 12px CSS à ESQUERDA do fio", -12)
        await arrastar("dedo 14px CSS à DIREITA do fio", 14)


if __name__ == "__main__":
    asyncio.run(main())
