#!/usr/bin/env python3
"""Serve a UI do desktop (bundle estático) num navegador comum, com:
  - injeção do token da sessão (pra falar com o backend local),
  - injeção do shim window.hermesDesktop (ver shim-desktop.js),
  - captura de erro do navegador em ~/.hermes/logs/desktop-web.log (debug sem ver a tela).

Uso:  hermes-desktop-web.py [DIR_DO_BUNDLE]
  env: HERMES_DESKTOP_WEB_PORT (default 8890), HERMES_BACKEND (default http://127.0.0.1:9119)

Nada é exposto pra fora: bind só em 127.0.0.1.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get("HOME", str(Path.home())))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(HOME / ".hermes")))
BUNDLE = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("HERMES_DESKTOP_WEB", str(HERMES_HOME / "desktop-web")))
SHIM = Path(os.environ.get("HERMES_DESKTOP_SHIM", str(HERMES_HOME / "bin" / "shim-desktop.js")))
PORT = int(os.environ.get("HERMES_DESKTOP_WEB_PORT", "8890"))
BACKEND = os.environ.get("HERMES_BACKEND", "http://127.0.0.1:9119")
LOG = Path(os.environ.get("HERMES_WEB_LOG", str(HERMES_HOME / "logs" / "desktop-web.log")))
# /__cmd executa JS arbitrário nas páginas abertas. Sem exigir token, qualquer
# app do aparelho alcança 127.0.0.1 (no Android o loopback NÃO exige permissão
# de rede) e dirige a UI que carrega o token da sessão. Token = env
# HERMES_DESKTOP_CMD_TOKEN (estável, para scripts) ou um aleatório por processo
# (impresso no log de startup). HERMES_DESKTOP_CMD_INSECURE=1 volta ao
# comportamento antigo, para quem só opera localmente.
CMD_TOKEN = os.environ.get("HERMES_DESKTOP_CMD_TOKEN", "") or uuid.uuid4().hex
CMD_INSEGURO = os.environ.get("HERMES_DESKTOP_CMD_INSECURE", "") == "1"
LAST_TOKEN = ""  # último token lido do backend, só para redigir no log
VERSION = str(time.time())  # muda a cada correção → a página aberta recarrega sozinha
RELOAD_FLAGS: list = []  # sinalizadores das páginas abertas (canal SSE)
PENDENTES: list = []  # [(ts, id, json)] — comandos/recargas para as páginas abertas
# OBS: NÃO esvaziar a fila ao entregar. Uma conexão SSE morta (de uma carga antiga)
# ficava na lista, "comia" o comando e a página viva nunca recebia. Agora toda
# conexão reescreve a fila e a página descarta ids repetidos.

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".txt": "text/plain; charset=utf-8",
}


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%F %T')} {msg}\n")


def fetch_token() -> str:
    """Token de sessão do backend local (injetado no HTML do dashboard/serve)."""
    global LAST_TOKEN
    try:
        with urllib.request.urlopen(f"{BACKEND}/", timeout=6) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as exc:  # backend fora do ar
        log(f"fetch_token falhou: {exc!r}")
        return ""
    m = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        LAST_TOKEN = m.group(1)
        return LAST_TOKEN
    return ""


def redact(text: str) -> str:
    """Nunca deixa o token da sessão cair em log/arquivo em texto puro."""
    return text.replace(LAST_TOKEN, "<token>") if LAST_TOKEN else text


PONTE = """<script>
window.__HERMES_BACKEND__ = %(backend)s;
window.__HERMES_SESSION_TOKEN__ = %(token)s;
// captura de erro + telemetria de DOM: eu leio este log porque não vejo a tela do celular
(function () {
  const env = (level, msg) => { try { navigator.sendBeacon('/__err', JSON.stringify({level, msg: String(msg).slice(0, 4000)})); } catch (e) {} };
  // DIAGNÓSTICO: o app chama .trim() em algo que não é string e morre.
  // TEM de ser NÃO-ENUMERÁVEL: enumerável, o `for…in` do framer-motion itera a
  // chave herdada e quebra o app com erros de interop (this.events[e].clear etc.).
  try {
    Object.defineProperty(Object.prototype, 'trim', {
      value: function () {
        try { console.warn('[debug] .trim() recebeu um NAO-string: ' + JSON.stringify(this).slice(0, 200)); } catch (e) {}
        return String(this)
      },
      enumerable: false, configurable: true, writable: true
    })
  } catch (e) {}
  window.__probe = () => {
    try {
      const t = (document.body && document.body.innerText) || '';
      // textContent inclui o que está colapsado (o "Details" da tela de erro do app)
      const tudo = (document.body && document.body.textContent) || '';
      // geometria: com viewport larga o app acha que a tela é enorme e joga o
      // composer pra fora. Medir é mais rápido que adivinhar.
      const sb = document.querySelector('aside,[data-slot=sidebar],[class*="sidebar"]');
      if (sb) {
        // lista os pedaços internos da lateral: rail colapsado vs painel aberto
        const pedacos = [];
        const todos = document.querySelectorAll('[class*="sidebar"],[data-slot*="sidebar"],[data-sidebar]');
        for (let i = 0; i < todos.length && i < 7; i++) {
          const e = todos[i];
          const r = e.getBoundingClientRect();
          const cs = getComputedStyle(e);
          pedacos.push((e.tagName || '?').toLowerCase() + '.' + String(e.className).split(/\s+/).slice(0, 2).join('.') +
            '[x=' + Math.round(r.x) + ' w=' + Math.round(r.width) + ' h=' + Math.round(r.height) +
            ' pos=' + cs.position + ' tr=' + cs.transform.slice(0, 26) + ']');
        }
        env('lateral', pedacos.join(' || ').slice(0, 900));
      } else {
        env('lateral', 'nao achei elemento de sidebar');
      }
      const alvos = document.querySelectorAll('textarea,[contenteditable="true"],input[type="text"]');
      let geo = 'inputs=' + alvos.length;
      for (let i = 0; i < alvos.length && i < 3; i++) {
        const r = alvos[i].getBoundingClientRect();
        geo += ' |#' + i + ' x=' + Math.round(r.x) + ' y=' + Math.round(r.y) +
               ' w=' + Math.round(r.width) + ' h=' + Math.round(r.height);
      }
      // estado persistido do app (foi assim que a lateral ficou colapsada: o app
      // salvou o estado depois do meu disparo de resize). Fora do bloco do composer:
      // no estado atual o composer nem existe no DOM.
      try {
        const ls = [];
        for (let i = 0; i < localStorage.length && i < 40; i++) {
          const k = localStorage.key(i);
          ls.push(k + '=' + String(localStorage.getItem(k) || '').slice(0, 160));
        }
        env('local', ls.join(' ; ').slice(0, 4000));
      } catch (e) { env('local', 'ERRO ' + e); }
      // cadeia de ancestrais do composer: quem está estourando a altura?
      const alvo = alvos[1] || alvos[0];
      if (alvo) {
        let el = alvo, cadeia = [], n = 0;
        while (el && n < 20) {
          const cs = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          cadeia.push((el.tagName || '?').toLowerCase() +
            (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/).slice(0, 2).join('.') : '') +
            '[h=' + Math.round(r.height) + ',y=' + Math.round(r.y) +
            ',ov=' + cs.overflowY + ',pos=' + cs.position + ',hcss=' + cs.height +
            ',inline=' + ((el.getAttribute('style') || '').slice(0, 40) || '-') + ']');
          el = el.parentElement; n++;
        }
        env('cadeia', cadeia.join(' < '));
        env('alturas', 'innerH=' + window.innerHeight + ' clientH=' + document.documentElement.clientHeight +
          ' screenH=' + (window.screen ? window.screen.height : '?') +
          ' dvh=' + (window.visualViewport ? Math.round(window.visualViewport.height) : '?') +
          ' bodyH=' + Math.round(document.body.getBoundingClientRect().height) +
          ' rootH=' + Math.round((document.getElementById('root') || document.body.firstElementChild || document.body).getBoundingClientRect().height));
        // quem manda na altura do shell? (o app escreve estilo inline)
        const shell = document.querySelector('.relative.isolate');
        if (shell) {
          const cs = getComputedStyle(shell);
          env('shell', 'style="' + (shell.getAttribute('style') || '').slice(0, 300) + '"' +
            ' || h=' + Math.round(shell.getBoundingClientRect().height) +
            ' cssH=' + cs.height + ' maxH=' + cs.maxHeight + ' minH=' + cs.minHeight +
            ' || vars=' + ['--app-height', '--vh', '--window-height', '--composer-measured-height']
              .map((v) => v + '=' + (cs.getPropertyValue(v).trim() || '-')).join(' '));
        }
      }
      env('geo', geo + ' || janela=' + window.innerWidth + 'x' + window.innerHeight +
        ' | doc=' + document.documentElement.scrollWidth + 'x' + document.documentElement.scrollHeight +
        ' | visual=' + (window.visualViewport ? Math.round(window.visualViewport.width) + 'x' + Math.round(window.visualViewport.height) : 'n/a') +
        ' | rolagem=' + Math.round(window.scrollY) + '/' + Math.round(document.documentElement.scrollHeight - window.innerHeight));
      env('dom', 'title=' + document.title + ' | hash=' + location.hash +
        ' | nos=' + document.querySelectorAll('*').length +
        ' | texto=' + t.replace(/\s+/g, ' ').slice(0, 400) +
        ' | completo=' + tudo.replace(/\s+/g, ' ').slice(0, 900));
    } catch (e) { env('dom', 'probe falhou ' + e); }
  };
  window.addEventListener('error', e => env('error', (e.message || '') + ' @ ' + (e.filename || '') + ':' + (e.lineno || 0)));
  window.addEventListener('unhandledrejection', e => env('reject', (e.reason && (e.reason.stack || e.reason.message)) || e.reason));
  const ce = console.error.bind(console);
  console.error = (...a) => { env('console', a.map(x => (x && x.stack) || String(x)).join(' ')); ce(...a); };
  const cw = console.warn.bind(console);
  console.warn = (...a) => { env('warn', a.map(String).join(' ').slice(0, 800)); cw(...a); };
  setTimeout(window.__probe, 300);
  setTimeout(window.__probe, 6000);
  setTimeout(window.__probe, 15000);
  setTimeout(window.__probe, 30000);
  // pulso: sensor contínuo de uma linha, pra eu ver mudanças de estado sem precisar
  // de uma recarga a cada medida (barra lateral, composer, nº de nós)
  setInterval(() => {
    try {
      const sb = document.querySelector('[class*="sidebar-wrapper"],[class*="sidebar"]');
      const inp = document.querySelectorAll('textarea,[contenteditable="true"]');
      const r = inp[1] || inp[0];
      const rb = r ? r.getBoundingClientRect() : null;
      env('pulso', 'nos=' + document.querySelectorAll('*').length +
        ' | lateral=' + (sb ? Math.round(sb.getBoundingClientRect().width) : '-') +
        ' | composer=' + (rb ? Math.round(rb.y) + ' (janela ' + window.innerHeight + ')' : '-'));
    } catch (e) {}
  }, 10000);
  // aba em segundo plano congela timers: manda telemetria também quando ela volta pra frente
  document.addEventListener('visibilitychange', () => { if (!document.hidden) window.__probe(); });
  // manifest: deixa esta UI instalável como app próprio (ícone na tela inicial)
  try {
    const l = document.createElement('link');
    l.rel = 'manifest';
    l.href = '/__manifest.webmanifest';
    document.head.appendChild(l);
    const tc = document.createElement('meta');
    tc.name = 'theme-color';
    tc.content = '#0b0b0e';
    document.head.appendChild(tc);
    // service worker (sem cache): o Chrome exige um SW para oferecer "Instalar app"
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/__sw.js').catch(() => {});
    }
    // botão de instalação: o Chrome só oferece "Instalar app" no menu e às vezes só
    // depois de um tempo. Capturando o evento, um toque no botão abre o diálogo
    // nativo na hora. Some sozinho quando o app já está instalado (modo standalone).
    // ATENÇÃO: não usar `return` aqui — abortaria o resto da injeção (SSE, probes).
    const jaInstalado = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
    if (!jaInstalado) {
      let promptInstalar = null;
      window.addEventListener('beforeinstallprompt', (ev) => {
        ev.preventDefault();
        promptInstalar = ev;
        window.__hermesMostrarBotao();
      });
      window.addEventListener('appinstalled', () => {
        const b = document.getElementById('__hermes_instalar');
        if (b) b.remove();
        env('instalar', 'app instalado');
      });
      window.__hermesMostrarBotao = function mostrarBotao() {
        if (document.getElementById('__hermes_instalar')) return;
        const b = document.createElement('button');
        b.id = '__hermes_instalar';
        b.textContent = '⬇ Instalar app';
        b.style.cssText = 'position:fixed;top:8px;left:50%%;transform:translateX(-50%%);' +
          'z-index:2147483647;padding:10px 16px;border-radius:999px;border:1px solid #4a84fe;' +
          'background:#17243a;color:#e6edf3;font:600 13px system-ui,sans-serif;' +
          'box-shadow:0 4px 14px rgba(0,0,0,.5)';
        b.onclick = async () => {
          try {
            if (!promptInstalar) return;
            promptInstalar.prompt();
            const r = await promptInstalar.userChoice;
            env('instalar', 'escolha do usuario: ' + (r && r.outcome));
            if (r && r.outcome === 'accepted') b.remove();
          } catch (e) { env('instalar', 'erro: ' + e); }
        };
        document.body.appendChild(b);
      };
    }
  } catch (e) {}
  // auto-reload: canal aberto (SSE) — o servidor me empurra a recarga quando eu corrijo.
  // Melhor que polling: o Chrome congela timers de aba em segundo plano, mas não a rede.
  try {
    const es = new EventSource('/__events');
    const vistos = new Set();
    es.onopen = () => env('sse', 'conectado');
    es.onmessage = (e) => {
      let m = null;
      try { m = JSON.parse(e.data); } catch (err) { m = null; }
      if (!m) { if (e.data === 'reload') location.reload(); return; }
      if (m.id) { if (vistos.has(m.id)) return; vistos.add(m.id); }
      if (m.cmd === 'reload') { location.reload(); return; }
      if (m.cmd === 'js') {
        // canal de operação: eu mando JS daqui (clicar, ler DOM) e o resultado volta no log
        let r;
        try { r = eval(m.js); } catch (err) { r = 'ERRO: ' + ((err && err.stack) || err); }
        if (r && typeof r.then === 'function') {
          r.then((v) => env('eval', String(v).slice(0, 1500)))
           .catch((err) => env('eval', 'ERRO async: ' + ((err && err.stack) || err)));
        } else {
          env('eval', String(r).slice(0, 1500));
        }
      }
    };
  } catch (e) {}
  // ── 1) matchMedia: o app decide se a lateral fica na grade ou vira overlay de
  // borda por uma media query (SIDEBAR_COLLAPSE_MEDIA_QUERY = "(max-width: 639.98px)").
  // Numa tela de celular o layout viewport pode ser avaliado antes/depois da meta de
  // viewport e isso alterna entre cargas. Aqui a resposta é fixada em "não estreito".
  try {
    const _mm = window.matchMedia ? window.matchMedia.bind(window) : null;
    if (_mm) {
      window.matchMedia = function (q) {
        const mq = _mm(q);
        try {
          if (/max-width:\s*639\.98px/.test(String(q))) {
            Object.defineProperty(mq, 'matches', { get: () => false, configurable: true });
          }
        } catch (e) {}
        return mq;
      };
    }
  } catch (e) {}
  // ── 2) estado de LAYOUT do app: durante meus testes de resize o app gravou um
  // layout com a barra lateral colapsada (vira o rótulo "SESSIONS" na vertical) e
  // isso sobrevive a recargas. Zerar SÓ as chaves de layout devolve o padrão
  // (sessions | workspace | rail da direita) sem tocar em tema/fonte/preferências
  // — zerar tudo apagou preferência visual do usuário e isso foi um erro meu.
  try {
    const LAYOUT = [
      'hermes.desktop.paneStates.v1', 'hermes.desktop.layoutTree.v2',
      'hermes.desktop.layoutPreset.active', 'hermes.desktop.dismissedPanes.v1',
      'hermes.desktop.paneShare.v1', 'hermes.desktop.paneSharePartners.v1',
      'hermes.desktop.hiddenStripTabs.v1', 'hermes.desktop.userPlacedPanes.v1',
      'hermes.desktop.panesFlipped', 'hermes.desktop.collapsedTreeSides.v1',
      'hermes.desktop.floatingPanes.v1'
    ];
    const tiradas = LAYOUT.filter((k) => localStorage.getItem(k) !== null);
    if (tiradas.length) {
      tiradas.forEach((k) => localStorage.removeItem(k));
      env('fix', 'layout do app zerado (' + tiradas.length + ' chaves) → padrão');
    }
  } catch (e) {}
})();
</script>
<script src="/__stubs.js"></script>
<script src="/__shim.js"></script>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesDesktopWeb/1.0"

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/__err":
            return self._send(b"{}", "application/json", 404)
        try:
            n = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(n)[:8000]
            data = json.loads(raw or b"{}")
        except Exception:
            data = {"msg": "<payload invalido>"}
        level = str(data.get("level", "?"))
        msg = redact(str(data.get("msg", "")))[:2000]
        # ruído conhecido: não polui o log
        if not any(k in msg for k in ("favicon", "ResizeObserver loop")):
            log(f"[{level}] {msg}")
        return self._send(b'{"ok":true}', "application/json")

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import unquote, urlparse

        global VERSION
        path = urlparse(self.path).path
        # visibilidade: sem isso eu não sei se a página está viva e pedindo coisas
        if path in ("/", "/__events", "/__stubs.js", "/__shim.js", "/__health"):
            log(f"[req] {path}{'?…' if '?' in self.path else ''}")
        if path == "/__health":
            return self._send(b"ok", "text/plain")
        if path == "/__version":
            return self._send(VERSION.encode(), "text/plain")
        if path == "/__events":
            # canal aberto (SSE): eu empurro a recarga, funciona com aba em segundo plano
            import threading as _th

            flag = _th.Event()
            RELOAD_FLAGS.append(flag)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b": conectado\n\n")
                self.wfile.flush()
                for _ in range(600):  # até ~10 min
                    if flag.wait(timeout=1.0):
                        flag.clear()
                        # manda a fila INTEIRA para esta conexão (a página deduplica
                        # por id) e poda o que já está velho
                        agora = time.time()
                        PENDENTES[:] = [p for p in PENDENTES if agora - p[0] < 90]
                        for ts, cid, cmd in list(PENDENTES):
                            try:
                                payload = json.loads(cmd)
                            except Exception:
                                continue
                            payload["id"] = cid
                            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                            self.wfile.flush()
                        if PENDENTES:
                            log(f"[sse] entreguei {len(PENDENTES)} comando(s) para uma pagina")
                    else:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                if flag in RELOAD_FLAGS:
                    RELOAD_FLAGS.remove(flag)
            return
        if path == "/__bump":
            VERSION = str(time.time())
            PENDENTES.append((time.time(), uuid.uuid4().hex[:8], json.dumps({"cmd": "reload"})))
            n = 0
            for flag in list(RELOAD_FLAGS):
                flag.set()
                n += 1
            log(f"versao nova: {VERSION} — recarga empurrada para {n} pagina(s) aberta(s)")
            return self._send(VERSION.encode(), "text/plain")
        if path == "/__cmd":
            # operação remota da UI aberta: /__cmd?js=<urlencoded>&token=<CMD_TOKEN>
            from urllib.parse import parse_qs

            q = parse_qs(urlparse(self.path).query)
            if not CMD_INSEGURO and (q.get("token") or [""])[0] != CMD_TOKEN:
                log("[cmd] recusado: token ausente/errado (veja o token de startup "
                    "no log, ou defina HERMES_DESKTOP_CMD_TOKEN)")
                return self._send(b'{"ok":false,"erro":"token ausente ou invalido"}',
                                  "application/json", 403)
            js = (q.get("js") or [""])[0]
            if not js:
                return self._send(b'{"ok":false,"erro":"sem js"}', "application/json", 400)
            PENDENTES.append((time.time(), uuid.uuid4().hex[:8], json.dumps({"cmd": "js", "js": js})))
            n = 0
            for flag in list(RELOAD_FLAGS):
                flag.set()
                n += 1
            log(f"[cmd] enviado para {n} pagina(s): {js[:120]}")
            return self._send(json.dumps({"ok": True, "paginas": n}).encode(), "application/json")
        if path == "/__manifest.webmanifest":
            # instalável como app próprio (o PONTE é uma f-string, então as chaves vão dobradas)
            man = {
                "name": "Hermes (cara do PC)",
                "short_name": "Hermes PC",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#0b0b0e",
                "theme_color": "#0b0b0e",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                ],
            }
            return self._send(json.dumps(man).encode(), "application/manifest+json")
        if path == "/__sw.js":
            # Service worker mínimo exigido pelo Chrome para oferecer "Instalar app".
            # SEM cache: passa tudo direto pra rede (um SW com cache serviria versão
            # velha da UI e eu ficaria depurando fantasma).
            sw = (
                "self.addEventListener('install', () => self.skipWaiting());\n"
                "self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));\n"
                "self.addEventListener('fetch', () => {});\n"
            )
            return self._send(sw.encode(), "text/javascript; charset=utf-8")
        if path == "/__stubs.js":
            stubs = HERMES_HOME / "bin" / "shim-stubs.js"
            if not stubs.is_file():
                return self._send(b"// stubs ausentes", "text/javascript")
            return self._send(stubs.read_bytes(), "text/javascript; charset=utf-8")
        if path == "/__shim.js":
            if not SHIM.is_file():
                return self._send(b"// shim ausente", "text/javascript")
            return self._send(SHIM.read_bytes(), "text/javascript; charset=utf-8")

        rel = unquote(path).lstrip("/")
        # o bundle referencia os assets como "./assets/..." — normaliza
        while rel.startswith("./"):
            rel = rel[2:]
        target = (BUNDLE / rel).resolve() if rel else (BUNDLE / "index.html").resolve()
        try:
            target.relative_to(BUNDLE.resolve())
        except ValueError:
            return self._send(b"negado", "text/plain", 403)

        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA: qualquer rota desconhecida cai no index
            target = BUNDLE / "index.html"
        if not target.is_file():
            return self._send(b"bundle nao encontrado: " + str(BUNDLE).encode(), "text/plain", 404)

        data = target.read_bytes()
        if target.suffix == ".html":
            html = data.decode("utf-8", "replace")
            inj = PONTE % {"backend": json.dumps(BACKEND), "token": json.dumps(fetch_token())}
            # UI de desktop numa tela de celular: a viewport CSS tem ~400px e o layout
            # do desktop precisa de 1000+. Renderiza numa viewport larga e deixa o
            # Chrome reduzir — assim o layout fica inteiro (e dá pra dar zoom).
            largura = os.environ.get("HERMES_WEB_VIEWPORT_WIDTH", "")
            if largura:
                if re.search(r'<meta[^>]+name=["\']viewport["\'][^>]*>', html, flags=re.I):
                    html = re.sub(r'<meta[^>]+name=["\']viewport["\'][^>]*>',
                                  f'<meta name="viewport" content="width={largura}">',
                                  html, count=1, flags=re.I)
                else:
                    # só a meta aqui: o inj entra UMA vez no bloco do </head> mais
                    # abaixo (injetar aqui também duplicava o <script> da ponte —
                    # duas conexões SSE, dois probes, shim registrado 2x)
                    html = html.replace("</head>", f'<meta name="viewport" content="width={largura}"></head>', 1)
            # CSS extra do porte (lista separada por vírgula). Inclui as fontes
            # embutidas em data URI: o wordmark usa a Collapse com font-display:swap e,
            # quando a requisição dela falha, a tela fica na fonte de sistema.
            extra = os.environ.get("HERMES_WEB_EXTRA_CSS", "")
            for um in [x for x in extra.split(",") if x.strip()]:
                if Path(um.strip()).is_file():
                    css = Path(um.strip()).read_text(encoding="utf-8")
                    inj = f'<style id="hermes-port-extra">\n{css}\n</style>\n' + inj
            if "</head>" in html:
                html = html.replace("</head>", inj + "</head>", 1)
            else:
                html = inj + html
            data = html.encode()
        return self._send(data, MIME.get(target.suffix, "application/octet-stream"))


def main() -> None:
    log(f"servindo {BUNDLE} em http://127.0.0.1:{PORT} (backend {BACKEND})")
    if not CMD_INSEGURO:
        log(f"/__cmd exige token={CMD_TOKEN} (HERMES_DESKTOP_CMD_TOKEN fixa a env; "
            f"HERMES_DESKTOP_CMD_INSECURE=1 desliga a checagem)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
