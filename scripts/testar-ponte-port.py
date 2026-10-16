#!/usr/bin/env python3
"""teste-final.py — prova, headless, as três coisas que mudaram:

  1) BOTÃO DE COPIAR: abre uma sessão real, acha um botão de copiar, CLICA com
     evento de verdade (Input do CDP, que dá ativação de usuário) e LÊ DE VOLTA
     a área de transferência. Sem a leitura de volta não há prova nenhuma.
  2) TAMANHO: mede o texto antes/depois de aplicar a escala da UI pelo caminho
     do app (window.hermesDesktop.zoom.setPercent) —o mesmo que o controle de
     Ajustes → Aparência chama.
  3) COLAR TEXTO GRANDE: chama savePastedText e confirma o arquivo em disco.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

import websockets

CDP = "http://127.0.0.1:9222"
URL = "http://127.0.0.1:8890/"
ORIGEM = "http://127.0.0.1:8890"


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
            return "EXCECAO: " + json.dumps(r["exceptionDetails"])[:400]
        return r.get("result", {}).get("value")

    async def clique(self, x, y):
        await self.cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await asyncio.sleep(0.09)
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


MEDIDA = r"""
(function () {
  var vv = window.visualViewport || {};
  var esc = vv.scale || 1;
  var cs = getComputedStyle(document.documentElement);
  var meta = (document.querySelector('meta[name=viewport]') || {}).content || '';
  var fsChat = 0, fsUi = 0, n = 0;
  var els = document.querySelectorAll('p, li, span, div');
  for (var i = 0; i < els.length; i++) {
    var e = els[i];
    if (e.children.length > 0) continue;
    var t = (e.textContent || '').trim();
    if (t.length < 20) continue;
    var r = e.getBoundingClientRect();
    if (r.height < 6) continue;
    var f = parseFloat(getComputedStyle(e).fontSize) || 0;
    if (!f) continue;
    if (!fsChat) fsChat = f;
    n++;
  }
  var inp = document.querySelectorAll('textarea,[contenteditable="true"]');
  var comp = null;
  for (var m = inp.length - 1; m >= 0; m--) {
    var rc = inp[m].getBoundingClientRect();
    if (rc.height < 8) continue;
    comp = { y: Math.round(rc.y), h: Math.round(rc.height),
             visivel: rc.bottom <= (vv.height || innerHeight) + 1 };
    break;
  }
  return JSON.stringify({
    meta: meta,
    janela: innerWidth + 'x' + innerHeight,
    escala: Math.round(esc * 1000) / 1000,
    chat_scale: cs.getPropertyValue('--chat-text-scale').trim() || '-',
    texto_chat_css: fsChat,
    texto_chat_dip: Math.round(fsChat * esc * 10) / 10,
    paragrafos: n,
    composer: comp,
    overflow: document.documentElement.scrollWidth > innerWidth + 2,
    zoom_percent: (function () {
      try { return window.hermesDesktop && window.hermesDesktop.zoom ? null : null; } catch (e) { return null; }
    })()
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
        # permissão de clipboard para poder LER DE VOLTA o que foi copiado
        try:
            r = await c.cmd("Browser.grantPermissions",
                            {"origin": ORIGEM,
                             "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
            print("permissao de clipboard concedida:", "ok" if "error" not in r else r)
        except Exception as e:
            print("AVISO: nao consegui conceder permissao de clipboard:", e)
        # estado limpo: o perfil do chromium guarda o localStorage de execucoes anteriores
        await c.cmd("Page.navigate", {"url": URL})
        for _ in range(30):
            await asyncio.sleep(1)
            if isinstance(await c.js("document.querySelectorAll('*').length"), int):
                break
        await asyncio.sleep(5)

        print("\n=== 0. ESTADO QUE O SHIM APLICOU AO CARREGAR ===")
        print("  chat-scale no localStorage:", await c.js("localStorage.getItem('hermes.desktop.chat-text-scale.v1')"))
        print("  zoom do app (zoom.get):", await c.js("window.hermesDesktop.zoom.get().then(z=>JSON.stringify(z))"))
        print("  fator (zoom.factor):", await c.js("window.hermesDesktop.zoom.factor()"))

        # ---- abrir uma sessão real ----
        print("\n=== 1. ABRINDO UMA SESSÃO COM HISTÓRICO ===")
        alvo = await c.js("""(function(){
          var els = document.querySelectorAll('span.hover-marquee-inner');
          var melhor = null;
          for (var i=0;i<els.length;i++){
            var t=(els[i].textContent||'').trim();
            if (t.length < 18) continue;
            var r = els[i].getBoundingClientRect();
            if (r.x > 300 || r.height < 8) continue;
            melhor = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), txt: t.slice(0,40)};
            if (/navegador|docker|openssh/i.test(t)) break;
          }
          return JSON.stringify(melhor);
        })()""")
        d = json.loads(alvo) if alvo and alvo.startswith("{") else None
        if not d:
            print("  !! nao achei sessao para abrir — seguindo sem chat aberto")
        else:
            print(f"  clicando em {d['txt']!r} em ({d['x']},{d['y']})")
            await c.clique(d["x"], d["y"])
            await asyncio.sleep(8)
            print("  mensagens na tela:", await c.js(
                "document.querySelectorAll('[data-slot=\"aui_assistant-message-content\"],p,pre').length"))

        print("\n=== 2. TAMANHO: MEDIDA EM CADA ESCALA (pelo caminho do app) ===")
        for pct in (100, 125, 150):
            await c.js(f"window.hermesDesktop.zoom.setPercent({pct})")
            await asyncio.sleep(2.5)
            m = json.loads(await c.js(MEDIDA))
            print(f"  escala {pct}%: meta='{m['meta']}' janela={m['janela']} ajuste={m['escala']}"
                  f" texto_chat={m['texto_chat_css']}px CSS = {m['texto_chat_dip']} dip"
                  f" | composer={m['composer']} overflow_x={m['overflow']}")

        # volta para 125% (o alvo do porte)
        await c.js("window.hermesDesktop.zoom.setPercent(125)")
        await asyncio.sleep(2.5)

        print("\n=== 3. ÁREA DE TRANSFERÊNCIA: escrita direta + leitura de volta ===")
        teste = "TESTE-HERMES-CLIPBOARD-2026"
        r = await c.js(
            f"window.hermesDesktop.writeClipboard({json.dumps(teste)}).then(()=> 'resolveu sem erro')"
            ".catch(e => 'LANCOU: ' + e.message)")
        print("  writeClipboard →", r)
        lido = await c.js("navigator.clipboard.readText().then(t=>t).catch(e=>'LEITURA FALHOU: '+e.message)")
        print("  lido de volta    →", repr(lido))
        print("  IGUAL?" , "SIM ✔" if lido == teste else "NÃO ✘")

        print("\n=== 4. BOTÃO DE COPIAR DE UM BLOCO (clique de verdade) ===")
        info = await c.js("""(function(){
          var bs = document.querySelectorAll('button[aria-label]');
          var achou = [];
          for (var i=0;i<bs.length;i++){
            var lbl = bs[i].getAttribute('aria-label')||'';
            if (!/copiar|copy/i.test(lbl)) continue;
            var r = bs[i].getBoundingClientRect();
            if (r.width < 5 || r.height < 5 || r.y < 0) continue;
            achou.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), lbl: lbl});
          }
          // preferir um botão que esteja dentro de um bloco de código
          var dentro = null;
          for (var j=0;j<achou.length;j++){
            var el = document.elementFromPoint(achou[j].x, achou[j].y);
            if (el && el.closest && el.closest('pre')) { dentro = achou[j]; break; }
          }
          return JSON.stringify({total: achou.length, primeiro: achou[0]||null, emPre: dentro});
        })()""")
        print("  botões de copiar na tela:", info)
        ii = json.loads(info) if info and info.startswith("{") else {}
        escolhido = ii.get("emPre") or ii.get("primeiro")
        if not escolhido:
            print("  !! nenhum botão de copiar visível (sessão sem bloco de código na área visível)")
        else:
            await c.js("navigator.clipboard.writeText('LIMPO-ANTES-DO-CLIQUE')")
            print("  clicando no botão:", escolhido["lbl"])
            await c.clique(escolhido["x"], escolhido["y"])
            await asyncio.sleep(1.5)
            lido2 = await c.js("navigator.clipboard.readText()")
            print("  clipboard depois do clique →", repr(str(lido2)[:120]))
            print("  MUDOU do sentinela?", "SIM ✔ (o botão copiou de verdade)"
                  if lido2 and lido2 != "LIMPO-ANTES-DO-CLIQUE" else "NÃO ✘")

        print("\n=== 5. COLAR TEXTO GRANDE (savePastedText) ===")
        grande = "linha de teste do paste grande\n" * 3000
        res = await c.js("window.hermesDesktop.savePastedText(JSON.parse(" + json.dumps(json.dumps(grande)) + "))"
                         ".then(p => JSON.stringify({path: p})).catch(e => 'ERRO: ' + e.message)")
        print("  resultado:", str(res)[:200])

        print("\n=== 6. ESTADO PERSISTIDO PELO /__zoom ===")
        print("  localStorage webViewportW:",
              await c.js("localStorage.getItem('hermes.desktop.webViewportW.v1')"))


if __name__ == "__main__":
    asyncio.run(main())
