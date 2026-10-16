#!/usr/bin/env python3
"""medir-ui.py — mede TAMANHO e LAYOUT da UI portada (8890) num Chromium headless
que EMULA a tela do aparelho (S24 Ultra: 480 dip de largura, DPR 3).

Não encosta no WebAPK do usuário: é outro navegador, outra aba.

Uso:
    venv/bin/python medir-ui.py [url] [--variantes]

Mede, por variante:
  escala de ajuste (visualViewport.scale), tamanho FISICO do texto em px de tela,
  largura da lateral (docked ou colapsada), overflow horizontal, composer visível.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import websockets

CDP = "http://127.0.0.1:9222"
LARGURA_DIP = 480      # CSS px de largura do aparelho (1440 fisicos / DPR 3)
ALTURA_DIP = 1040
DPR = 3

# JS de medição: roda na página e devolve JSON com o que importa.
MEDIR = r"""
(function () {
  var vv = window.visualViewport || {};
  var escala = vv.scale || 1;
  var raiz = document.documentElement;
  var hist = [];
  var vistos = {};
  var todos = document.querySelectorAll('p,div,span,li,td,button,a,label,pre,code,h1,h2,h3');
  for (var i = 0; i < todos.length; i++) {
    var e = todos[i];
    if (e.children.length > 3) continue;
    var t = (e.textContent || '').trim();
    if (t.length < 3) continue;
    var r = e.getBoundingClientRect();
    if (r.width < 8 || r.height < 6 || r.bottom < 0 || r.top > (vv.height || innerHeight)) continue;
    var cs = getComputedStyle(e);
    var fs = parseFloat(cs.fontSize) || 0;
    if (!fs) continue;
    var k = cs.fontSize;
    if (!vistos[k]) { vistos[k] = { n: 0, ex: t.slice(0, 26) }; hist.push(k); }
    vistos[k].n++;
  }
  hist.sort(function (a, b) { return parseFloat(b) - parseFloat(a); });
  var histograma = hist.slice(0, 6).map(function (k) {
    var px = parseFloat(k);
    return {
      css: px,
      // tamanho FISICO na tela: css * escala de ajuste do viewport * densidade
      fisico_px: Math.round(px * escala * window.devicePixelRatio * 10) / 10,
      // 1 dip = 1/160in; 16dip e o minimo confortavel de leitura em mobile
      dip: Math.round(px * escala * 10) / 10,
      n: vistos[k].n,
      ex: vistos[k].ex
    };
  });
  var laterais = document.querySelectorAll('[data-slot="sidebar"],[class*="sidebar"]');
  var lat = null;
  for (var j = 0; j < laterais.length; j++) {
    var r2 = laterais[j].getBoundingClientRect();
    if (r2.width < 20 || r2.height < 100) continue;
    var cs2 = getComputedStyle(laterais[j]);
    if (cs2.position === 'fixed' || cs2.display === 'none') continue;
    if (!lat || r2.width > lat.w) {
      lat = { w: Math.round(r2.width), x: Math.round(r2.x), h: Math.round(r2.height),
              hcss: cs2.height, pos: cs2.position };
    }
  }
  var inp = document.querySelectorAll('textarea,[contenteditable="true"],input[type="text"]');
  var comp = null;
  for (var m = inp.length - 1; m >= 0; m--) {
    var rc = inp[m].getBoundingClientRect();
    if (rc.height < 8) continue;
    comp = { y: Math.round(rc.y), h: Math.round(rc.height), w: Math.round(rc.width),
             x: Math.round(rc.x), visivel: (rc.bottom <= (vv.height || innerHeight) + 1) };
    break;
  }
  var cs3 = getComputedStyle(raiz);
  return JSON.stringify({
    url: location.href.slice(0, 60),
    meta: (document.querySelector('meta[name=viewport]') || {}).content || 'AUSENTE',
    janela_css: innerWidth + 'x' + innerHeight,
    visual: Math.round(vv.width || 0) + 'x' + Math.round(vv.height || 0),
    escala: Math.round(escala * 1000) / 1000,
    dpr: window.devicePixelRatio,
    zoom_css: cs3.zoom,
    scrool_h: raiz.scrollWidth + 'x' + raiz.scrollHeight,
    overflow_x: raiz.scrollWidth > innerWidth + 2,
    lateral: lat,
    composer: comp,
    chat_scale: cs3.getPropertyValue('--chat-text-scale').trim() || '-',
    fs_raiz: cs3.fontSize,
    nós: document.querySelectorAll('*').length,
    histograma: histograma
  });
})()
"""

# Variantes testadas a partir da mesma carga da página
VARIANTES = [
    ("baseline (como está)", ""),
    ("meta width=660", "var mt=document.querySelector('meta[name=viewport]'); mt.content='width=660';"),
    ("meta width=700", "var mt=document.querySelector('meta[name=viewport]'); mt.content='width=700';"),
    ("meta width=640", "var mt=document.querySelector('meta[name=viewport]'); mt.content='width=640';"),
    ("meta 700 + chat-scale 175", "document.querySelector('meta[name=viewport]').content='width=700';"
                                   "localStorage.setItem('hermes.desktop.chat-text-scale.v1','175');"
                                   "document.documentElement.style.setProperty('--chat-text-scale','1.75');"),
    ("zoom css 1.25 (meta 800)", "document.querySelector('meta[name=viewport]').content='width=800';"
                                 "document.documentElement.style.zoom='1.25';"),
    ("zoom css 1.15 (meta 800)", "document.documentElement.style.zoom='1.15';"),
]


async def cmd(ws, metodo, params=None, ident=[0]):
    ident[0] += 1
    i = ident[0]
    await ws.send(json.dumps({"id": i, "method": metodo, "params": params or {}}))
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        if m.get("id") == i:
            return m.get("result", m)


async def avaliar(ws, expr):
    r = await cmd(ws, "Runtime.evaluate",
                  {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8890/"
    # alvo de PÁGINA (o /json/version é o endpoint do BROWSER e trava em Page.*)
    with urllib.request.urlopen(f"{CDP}/json/list", timeout=10) as r:
        alvos = json.load(r)
    paginas = [a for a in alvos if a.get("type") == "page"]
    if paginas:
        ws_url, alvo = paginas[0]["webSocketDebuggerUrl"], paginas[0]["id"]
    else:
        with urllib.request.urlopen(f"{CDP}/json/new?about:blank", timeout=10) as r:
            a = json.load(r)
        ws_url, alvo = a["webSocketDebuggerUrl"], a["id"]

    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        await cmd(ws, "Page.enable")
        await cmd(ws, "Runtime.enable")
        await cmd(ws, "Emulation.setDeviceMetricsOverride",
                  {"width": LARGURA_DIP, "height": ALTURA_DIP, "deviceScaleFactor": DPR,
                   "mobile": True, "screenOrientation": {"type": "portraitPrimary", "angle": 0}})
        await cmd(ws, "Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        await cmd(ws, "Page.navigate", {"url": url})
        # o app carrega o workspace depois do primeiro paint: dá tempo
        for _ in range(30):
            await asyncio.sleep(1)
            n = await avaliar(ws, "document.querySelectorAll('*').length")
            if isinstance(n, int) and n > 200:
                break
        await asyncio.sleep(4)

        for nome, prep in VARIANTES:
            if prep:
                await avaliar(ws, prep)
                await asyncio.sleep(2.5)
            bruto = await avaliar(ws, MEDIR)
            try:
                d = json.loads(bruto)
            except Exception:
                print(f"\n### {nome}: FALHOU ({str(bruto)[:200]})")
                continue
            print(f"\n### {nome}")
            print(f"  meta={d['meta']}  janela_css={d['janela_css']}  visual={d['visual']}"
                  f"  escala={d['escala']}  dpr={d['dpr']}  zoom_css={d['zoom_css']}")
            print(f"  lateral={d['lateral']}")
            print(f"  composer={d['composer']}")
            print(f"  overflow_x={d['overflow_x']}  scroll={d['scrool_h']}  chat_scale={d['chat_scale']}"
                  f"  fs_raiz={d['fs_raiz']}  nós={d['nós']}")
            for h in d["histograma"]:
                print(f"    css={h['css']:>7} → {h['dip']:>5} dip / {h['fisico_px']:>6} px de tela"
                      f"  (x{h['n']:>4})  ex: {h['ex']!r}")


if __name__ == "__main__":
    asyncio.run(main())
