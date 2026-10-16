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

import base64
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
# Telemetria/instrumentação de debug: DESLIGADA por padrão (ligue com
# HERMES_WEB_TELEMETRIA=1 ao subir o injetor). Ligada, a página manda pulso/probe/
# console a cada 10s e o log cresce ~4 MB/dia — foi reclamação do usuário (15/10/2026).
TELEMETRIA = os.environ.get("HERMES_WEB_TELEMETRIA", "").lower() in ("1", "true", "yes", "on")
DEBUG = TELEMETRIA or os.environ.get("HERMES_WEB_DEBUG", "").lower() in ("1", "true", "yes", "on")
# Largura de viewport que corresponde a "100%" na escala da UI do app
# (Ajustes → Aparência). O porte nasceu em 800px CSS; a escala que o app mostra
# é percent = 100 * BASE_W / largura, então mexer nesta constante REMAPEIA os
# presets do app (90…175%) — não mexa sem querer.
BASE_W = int(os.environ.get("HERMES_WEB_BASE_WIDTH", "800"))
# Colar um texto gigante no composer vira anexo .txt (savePastedText da ponte).
# Teto de tamanho: o endpoint é alcançável por qualquer app do aparelho no
# loopback e não pode virar torneira de disco.
PASTES = HERMES_HOME / "composer-pastes"
LIMITE_PASTE = 2 * 1024 * 1024
# Anexos (foto/arquivo escolhido no seletor do Android). O navegador não entrega
# caminho de arquivo: a ponte sobe os BYTES e recebe de volta um caminho em
# disco, que é o que o app (e o agente, com cwd=$HOME) sabe usar.
ANEXOS = HOME / "attachments"
LIMITE_ANEXO = 24 * 1024 * 1024          # por arquivo, antes do base64
LIMITE_CORPO = 40 * 1024 * 1024          # teto do corpo recebido (base64 infla 4/3)
# /__cmd executa JS arbitrário nas páginas abertas. Sem exigir token, qualquer
# página web aberta no Chrome alcança a porta fixa por GET (CSRF, sem CORS) e
# dirige a UI que carrega o token da sessão — no Android o loopback também é
# alcançável por qualquer app com permissão de rede. Ordem do token: env
# HERMES_DESKTOP_CMD_TOKEN (estável, para scripts) → arquivo 0600 em
# state/desktop-cmd-token → aleatório por processo (impresso no log de startup).
# HERMES_DESKTOP_CMD_INSECURE=1 volta ao comportamento antigo.
def _ler_token_arquivo() -> str:
    try:
        return (HERMES_HOME / "state" / "desktop-cmd-token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


CMD_TOKEN = os.environ.get("HERMES_DESKTOP_CMD_TOKEN", "") or _ler_token_arquivo()
CMD_TOKEN_ORIGEM = "env" if os.environ.get("HERMES_DESKTOP_CMD_TOKEN") else ("arquivo 0600" if CMD_TOKEN else "aleatorio")
if not CMD_TOKEN:
    CMD_TOKEN = uuid.uuid4().hex
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


LIMITE_LOG = 1_000_000  # 1 MB: rotação própria, pra log nenhum virar bola de neve
GUARDA_LOG = 200_000    # guarda os últimos 200 KB quando rotaciona


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LOG.stat().st_size > LIMITE_LOG:
            with LOG.open("rb") as fh:
                fh.seek(max(0, LOG.stat().st_size - GUARDA_LOG))
                resto = fh.read()
            LOG.write_bytes(resto)
    except OSError:
        pass
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
// Largura de viewport que o shim trata como "100%%" na escala da UI (Ajustes → Aparência).
window.__HERMES_WEB_BASE_WIDTH__ = %(base_w)s;
// Telemetria de debug (pulso/probe/console): só quando o servidor manda
// (HERMES_WEB_TELEMETRIA=1). Desligada, a página não manda nada e não varre o DOM.
const __TELEMETRIA__ = %(telemetria)s;
(function () {
  const relatar = (level, msg) => { try { navigator.sendBeacon('/__err', JSON.stringify({level, msg: String(msg).slice(0, 4000)})); } catch (e) {} };
  const env = __TELEMETRIA__ ? relatar : () => {};
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
    if (!__TELEMETRIA__) return;
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
          pedacos.push((e.tagName || '?').toLowerCase() + '.' + String(e.className).split(/\\s+/).slice(0, 2).join('.') +
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
            (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\\s+/).slice(0, 2).join('.') : '') +
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
        ' | texto=' + t.replace(/\\s+/g, ' ').slice(0, 400) +
        ' | completo=' + tudo.replace(/\\s+/g, ' ').slice(0, 900));
    } catch (e) { env('dom', 'probe falhou ' + e); }
  };
  if (__TELEMETRIA__) {
  window.addEventListener('error', e => env('error', (e.message || '') + ' @ ' + (e.filename || '') + ':' + (e.lineno || 0)));
  window.addEventListener('unhandledrejection', e => env('reject', (e.reason && (e.reason.stack || e.reason.message)) || e.reason));
  const ce = console.error.bind(console);
  console.error = (...a) => { env('console', a.map(x => (x && x.stack) || String(x)).join(' ')); ce(...a); };
  const cw = console.warn.bind(console);
  console.warn = (...a) => { env('warn', a.map(String).join(' ').slice(0, 800)); cw(...a); };
  }
  if (__TELEMETRIA__) {
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
  }
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
          r.then((v) => relatar('eval', String(v).slice(0, 1500)))
           .catch((err) => relatar('eval', 'ERRO async: ' + ((err && err.stack) || err)));
        } else {
          relatar('eval', String(r).slice(0, 1500));
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
          if (/max-width:\\s*639\\.98px/.test(String(q))) {
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

    def log_message(self, fmt, *args):  # noqa: N802
        # log de acesso só em modo debug: sem isso crescia ~1 MB/dia à toa
        if DEBUG:
            super().log_message(fmt, *args)

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

    def _zoom(self, data) -> None:
        """Persiste a largura de viewport escolhida na escala da UI do app.

        O app (Ajustes → Aparência → escala) chama window.hermesDesktop.zoom
        .setPercent(); o shim traduz para largura e avisa aqui, para o PRÓXIMO
        carregamento da página já nascer no tamanho certo (sem "pulo" de
        escala). O valor vivo de cada aba continua no localStorage dela.
        """
        try:
            w = int(data.get("width"))
        except (TypeError, ValueError):
            return self._send(b'{"ok":false,"erro":"width invalido"}', "application/json", 400)
        if not 400 <= w <= 2400:
            return self._send(b'{"ok":false,"erro":"width fora de faixa"}', "application/json", 400)
        arq = HERMES_HOME / "state" / "desktop-web-viewport"
        try:
            arq.parent.mkdir(parents=True, exist_ok=True)
            arq.write_text(str(w), encoding="utf-8")
        except OSError as exc:
            return self._send(json.dumps({"ok": False, "erro": str(exc)}).encode(),
                              "application/json", 500)
        log(f"[zoom] largura de viewport persistida: {w}px CSS")
        return self._send(json.dumps({"ok": True, "width": w}).encode(), "application/json")

    def _save_text(self, data) -> None:
        """Cola-texto-grande → arquivo, para o anexo "@file:" do composer.

        Mesmo comportamento do savePastedText do Electron; o app só usa isto
        acima do limite de paste inline e degrada sozinho (mantém inline) se
        devolvermos vazio.
        """
        texto = data.get("text")
        if not isinstance(texto, str) or not texto:
            return self._send(b'{"ok":false,"erro":"sem texto"}', "application/json", 400)
        if len(texto.encode("utf-8")) > LIMITE_PASTE:
            return self._send(b'{"ok":false,"erro":"texto grande demais"}', "application/json", 413)
        try:
            PASTES.mkdir(parents=True, exist_ok=True)
            nome = "paste-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".txt"
            destino = PASTES / nome
            destino.write_text(texto, encoding="utf-8")
        except OSError as exc:
            return self._send(json.dumps({"ok": False, "erro": str(exc)}).encode(),
                              "application/json", 500)
        log(f"[paste] texto colado salvo em {destino} ({len(texto)} chars)")
        return self._send(json.dumps({"ok": True, "path": str(destino)}).encode(),
                          "application/json")

    def _upload(self, data) -> None:
        """Recebe os BYTES de um anexo e devolve um caminho em disco.

        Por que existe: no Electron quem escolhe arquivo é o diálogo nativo e o
        app recebe um CAMINHO. No navegador o seletor devolve um File (sem
        caminho) — então a ponte sobe os bytes aqui, o arquivo nasce em
        $HOME/attachments/ e o caminho volta para o app, que o usa como
        `@file:<caminho>` para o agente (que roda com cwd=$HOME e lê de verdade).
        """
        nome = str(data.get("name") or "")[:120]
        ext = re.sub(r"[^A-Za-z0-9]", "", str(data.get("ext") or ""))[:8]
        bruto = data.get("data_url") or data.get("data_b64") or ""
        if not isinstance(bruto, str) or not bruto:
            return self._send(b'{"ok":false,"erro":"sem dados"}', "application/json", 400)
        if "," in bruto and bruto.lstrip().startswith("data:"):
            bruto = bruto.split(",", 1)[1]
        try:
            conteudo = base64.b64decode(bruto, validate=False)
        except Exception:
            return self._send(b'{"ok":false,"erro":"base64 invalido"}', "application/json", 400)
        if len(conteudo) == 0:
            return self._send(b'{"ok":false,"erro":"arquivo vazio"}', "application/json", 400)
        if len(conteudo) > LIMITE_ANEXO:
            return self._send(
                json.dumps({"ok": False, "erro": f"arquivo maior que {LIMITE_ANEXO // 1048576}MB"}).encode(),
                "application/json", 413)
        # nome seguro: nada de barra, ponto-ponto ou caractere de controle
        limpo = re.sub(r"[^A-Za-z0-9._-]", "_", nome).strip("._") or "arquivo"
        if ext and not limpo.lower().endswith("." + ext.lower()):
            limpo += "." + ext
        carimbo = time.strftime("%Y%m%d-%H%M%S")
        destino = ANEXOS / f"{carimbo}-{limpo}"
        try:
            ANEXOS.mkdir(parents=True, exist_ok=True)
            if destino.exists():
                destino = ANEXOS / f"{carimbo}-{uuid.uuid4().hex[:4]}-{limpo}"
            destino.write_bytes(conteudo)
        except OSError as exc:
            return self._send(json.dumps({"ok": False, "erro": str(exc)}).encode(),
                              "application/json", 500)
        log(f"[anexo] {len(conteudo)} bytes → {destino}")
        return self._send(json.dumps({"ok": True, "path": str(destino)}).encode(),
                          "application/json")

    def _servir_arquivo(self, q) -> None:
        """Serve um arquivo do aparelho para a página (miniatura/preview).

        Exige o token da sessão (o mesmo que o HTML injeta) e só aceita caminho
        dentro de $HOME: a porta é loopback, mas qualquer app do aparelho a
        alcança, e isto aqui lê disco.
        """
        from urllib.parse import unquote as _unq

        pedido = _unq((q.get("path") or [""])[0])
        if not pedido:
            return self._send(b'{"ok":false,"erro":"sem path"}', "application/json", 400)
        veio = self.headers.get("x-hermes-session-token", "")
        if not CMD_INSEGURO and LAST_TOKEN and veio != LAST_TOKEN:
            return self._send(b'{"ok":false,"erro":"token invalido"}', "application/json", 403)
        try:
            alvo = (Path(pedido) if Path(pedido).is_absolute() else (HOME / pedido)).resolve()
            alvo.relative_to(HOME.resolve())
        except ValueError:
            return self._send(b'{"ok":false,"erro":"fora de $HOME"}', "application/json", 403)
        except OSError:
            return self._send(b'{"ok":false,"erro":"caminho invalido"}', "application/json", 400)
        if not alvo.is_file():
            return self._send(b'{"ok":false,"erro":"nao existe"}', "application/json", 404)
        try:
            if alvo.stat().st_size > LIMITE_ANEXO:
                return self._send(b'{"ok":false,"erro":"arquivo grande demais"}',
                                  "application/json", 413)
            corpo = alvo.read_bytes()
        except OSError as exc:
            return self._send(json.dumps({"ok": False, "erro": str(exc)}).encode(),
                              "application/json", 500)
        return self._send(corpo, MIME.get(alvo.suffix.lower(), "application/octet-stream"))

    def _resolver_pasta(self, q) -> None:
        """Transforma o que o usuário digitou num caminho de pasta existente.

        Por que existe: escolher PASTA no navegador é impossível (nenhuma API
        entrega o caminho de uma pasta; `showDirectoryPicker` nem existe no
        Chrome Android). Mas aqui a pasta é deste aparelho e o agente lê esse
        caminho — então basta pedir o caminho ao usuário e CONFERIR que existe.
        Aceita `~`, caminho relativo à base (o cwd que o app mandou) e, se não
        achar, procura por NOME dentro de $HOME (digitar "docker" acha
        ~/repos/docker-termux). Devolve {path} ou {erro, candidatos}.
        """
        from urllib.parse import unquote as _unq

        bruto = _unq((q.get("nome") or [""])[0]).strip().strip('"').strip("'")
        base = _unq((q.get("base") or [""])[0]).strip() or str(HOME)
        if bruto.startswith("~"):
            bruto = str(HOME) + bruto[1:]
        alvo = Path(bruto) if bruto.startswith("/") else (Path(base) / bruto)
        try:
            alvo = alvo.resolve()
        except OSError:
            alvo = Path(base)
        if alvo.is_dir():
            return self._send(json.dumps({"ok": True, "path": str(alvo), "exato": True}).encode(),
                              "application/json")
        # não achou: procura pelo NOME dentro de $HOME (busca limitada de propósito)
        if not bruto:
            return self._send(b'{"ok":false,"erro":"vazio"}', "application/json", 400)
        nome = Path(bruto.rstrip("/")).name.lower()
        PULAR = {".git", "node_modules", ".cargo", ".cache", ".rustup", "venv", ".venv",
                 "site-packages", "__pycache__", ".npm", ".gradle", "build", "dist"}

        def varre(maxprof, orcamento):
            """Passa pelas pastas de $HOME até `maxprof` níveis, com teto de visitas.

            Ordena e limita a largura de propósito: sem isso uma árvore gigante
            (hermes-agent, com milhares de diretórios) consome o orçamento ANTES
            de chegar em ~/repos — foi assim que "docker" deixou de achar
            ~/repos/docker-termux na primeira versão.
            """
            exatos, parciais, vistos = [], [], 0
            for raiz, dirs, _arqs in os.walk(HOME):
                try:
                    prof = len(Path(raiz).relative_to(HOME).parts)
                except ValueError:
                    continue
                if prof >= maxprof:
                    dirs[:] = []
                dirs[:] = sorted(d for d in dirs if d not in PULAR and not d.startswith("."))[:60]
                for d in dirs:
                    vistos += 1
                    if vistos > orcamento:
                        return exatos, parciais
                    baixo = d.lower()
                    if baixo == nome:
                        exatos.append(str(Path(raiz) / d))
                    elif nome in baixo:
                        parciais.append(str(Path(raiz) / d))
            return exatos, parciais

        exatos, parciais = varre(2, 2000)        # raso primeiro: cobre o caso comum
        achados = exatos or parciais
        if not achados:
            exatos, parciais = varre(4, 8000)    # só então desce mais fundo
            achados = exatos or parciais
        if len(achados) == 1:
            return self._send(json.dumps({"ok": True, "path": achados[0], "exato": False}).encode(),
                              "application/json")
        return self._send(json.dumps({"ok": False, "erro": "nao achei",
                                      "candidatos": achados[:6]}).encode(), "application/json")

    def do_POST(self) -> None:  # noqa: N802
        from urllib.parse import urlparse as _parse

        caminho = _parse(self.path).path
        if caminho not in ("/__err", "/__zoom", "/__save-text", "/__upload"):
            return self._send(b"{}", "application/json", 404)
        try:
            n = int(self.headers.get("content-length", "0"))
            teto = LIMITE_CORPO if caminho == "/__upload" else LIMITE_PASTE + 65536
            raw = self.rfile.read(min(n, teto)) if n > 0 else b""
            data = json.loads(raw or b"{}")
        except Exception:
            data = {"msg": "<payload invalido>"}
        if not isinstance(data, dict):
            data = {"msg": "<payload invalido>"}
        if caminho == "/__zoom":
            return self._zoom(data)
        if caminho == "/__save-text":
            return self._save_text(data)
        if caminho == "/__upload":
            return self._upload(data)
        level = str(data.get("level", "?"))
        msg = redact(str(data.get("msg", "")))[:2000]
        # ruído conhecido: não polui o log
        if (DEBUG or level in ("eval", "cmd")) and not any(k in msg for k in ("favicon", "ResizeObserver loop")):
            log(f"[{level}] {msg}")
        return self._send(b'{"ok":true}', "application/json")

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import unquote, urlparse

        global VERSION
        path = urlparse(self.path).path
        # visibilidade: sem isso eu não sei se a página está viva e pedindo coisas
        if DEBUG and path in ("/", "/__events", "/__stubs.js", "/__shim.js", "/__health"):
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
                log("[cmd] recusado: token ausente/errado (token em state/desktop-cmd-token "
                    "ou na env HERMES_DESKTOP_CMD_TOKEN)")
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
            # Service worker do app no celular. SEM cache de UI (a injeção carrega
            # token/shim frescos e um SW com cache serviria versão velha), com UMA
            # missão a mais: RESGATE. Se o servidor local ainda não subiu — o caso
            # normal nos primeiros minutos depois de reiniciar o aparelho — em vez
            # da tela de erro do Chrome o app abre uma página que explica e tenta
            # sozinha até o servidor responder (autocura, sem fechar o app).
            sw = """const RESGATE = `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes acordando</title>
<style>
  :root { color-scheme: dark }
  html,body { height:100%; margin:0 }
  body { background:#0b0b0e; color:#e6e6ea; padding:24px;
         font:16px/1.55 system-ui,-apple-system,Roboto,sans-serif;
         display:flex; align-items:center; justify-content:center }
  .c { max-width:520px; text-align:center }
  h1 { font-size:20px; font-weight:600; margin:0 0 14px }
  p { margin:0 0 10px; color:#a9a9b6 }
  .d { font-family:ui-monospace,monospace; font-size:13px; color:#8a8a97;
       margin-top:18px; white-space:pre-wrap }
  .b { margin-top:18px; background:#1b1b21; border:1px solid #2c2c35;
       color:#e6e6ea; border-radius:10px; padding:10px 16px; font-size:15px }
  .p { display:inline-block; width:8px; height:8px; border-radius:50%;
       background:#f5a623; margin-right:8px; animation:pu 1.1s infinite }
  @keyframes pu { 0%,100%{opacity:.2} 50%{opacity:1} }
</style>
</head>
<body><div class="c">
  <h1><span class="p"></span>Acordando o Hermes</h1>
  <p>O servidor do Hermes aqui no celular ainda nao respondeu. Isso e normal nos
     primeiros minutos depois de reiniciar o aparelho &mdash; ele esta subindo.</p>
  <p>Esta pagina tenta sozinha e abre o app assim que ele estiver de pe. Pode deixar aberta.</p>
  <div class="d" id="d">tentando</div>
  <button class="b" id="b">Tentar agora</button>
</div>
<script>
  var d = document.getElementById('d');
  var b = document.getElementById('b');
  var n = 0, t0 = Date.now();
  var tenta = function () {
    n++;
    d.textContent = 'tentativa ' + n + ' - ' + Math.round((Date.now() - t0) / 1000) + 's';
    fetch('/__health', { cache: 'no-store' }).then(function (r) {
      if (r && r.ok) { d.textContent = 'servidor no ar - abrindo o app'; location.reload(); }
      else { setTimeout(tenta, 1500); }
    }).catch(function () { setTimeout(tenta, 1500); });
  };
  b.addEventListener('click', tenta);
  tenta();
</script>
</body></html>`;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  const req = e.request;
  // só a NAVEGAÇÃO é interceptada: API, assets e SSE seguem direto pra rede
  if (req.mode !== 'navigate') return;
  e.respondWith(
    fetch(req).catch(() => new Response(RESGATE, {
      status: 200,
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store'
      }
    }))
  );
});
"""
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
        if path == "/__file":
            # miniatura/preview de um anexo já salvo (imagem, PDF, texto…).
            # Exige o token da sessão e caminho dentro de $HOME — ver _servir_arquivo.
            from urllib.parse import parse_qs

            return self._servir_arquivo(parse_qs(urlparse(self.path).query))
        if path == "/__resolver-pasta":
            # "escolher pasta" no navegador: o usuário digita o caminho e aqui a
            # gente confere/descobre. Ver _resolver_pasta.
            from urllib.parse import parse_qs

            return self._resolver_pasta(parse_qs(urlparse(self.path).query))

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
            inj = PONTE % {"backend": json.dumps(BACKEND), "token": json.dumps(fetch_token()),
                       "telemetria": "true" if TELEMETRIA else "false",
                       "base_w": json.dumps(BASE_W)}
            # UI de desktop numa tela de celular: a viewport CSS tem ~400px e o layout
            # do desktop precisa de 1000+. Renderiza numa viewport larga e deixa o
            # Chrome reduzir — assim o layout fica inteiro (e dá pra dar zoom).
            #
            # interactive-widget=resizes-content é OBRIGATÓRIO aqui, não enfeite:
            # desde o Chrome 108 o teclado virtual do Android NÃO redimensiona mais
            # o layout viewport (ele só cobre o conteúdo). Como esta UI tem altura
            # fixa (100svh) com rolagem interna, o composer — que fica ancorado na
            # base — sumia atrás do teclado. Com resizes-content o ICB encolhe junto
            # com o teclado, as unidades de viewport acompanham e o composer sobe.
            largura = os.environ.get("HERMES_WEB_VIEWPORT_WIDTH", "")
            if largura:
                meta_nova = (f'<meta name="viewport" content="width={largura}, '
                             'interactive-widget=resizes-content">')
                if re.search(r'<meta[^>]+name=["\']viewport["\'][^>]*>', html, flags=re.I):
                    html = re.sub(r'<meta[^>]+name=["\']viewport["\'][^>]*>',
                                  meta_nova, html, count=1, flags=re.I)
                else:
                    # só a meta aqui: o inj entra UMA vez no bloco do </head> mais
                    # abaixo (injetar aqui também duplicava o <script> da ponte —
                    # duas conexões SSE, dois probes, shim registrado 2x)
                    html = html.replace("</head>", meta_nova + "</head>", 1)
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
        if CMD_TOKEN_ORIGEM == "aleatorio":
            log(f"/__cmd exige token={CMD_TOKEN} (defina HERMES_DESKTOP_CMD_TOKEN ou crie "
                f"state/desktop-cmd-token com chmod 600 para fixar)")
        else:
            log(f"/__cmd exige token lido de {CMD_TOKEN_ORIGEM} (state/desktop-cmd-token); "
                f"HERMES_DESKTOP_CMD_INSECURE=1 desliga a checagem")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
