#!/usr/bin/env python3
"""Serve a UI do desktop (bundle estático) num navegador comum, com:
  - injeção do token da sessão (pra falar com o backend local),
  - injeção do shim window.hermesDesktop (ver shim-desktop.js),
  - captura de erro do navegador em ~/.hermes/logs/desktop-web.log (debug sem ver a tela).

Uso:  hermes-desktop-web.py [DIR_DO_BUNDLE]
  env: HERMES_DESKTOP_WEB_PORT (default 8890), HERMES_BACKEND (default http://127.0.0.1:9119)
       HERMES_DESKTOP_WEB    bundle (default ~/hermes-agent/.hermes-port/renderer)
       HERMES_WEB_TELEMETRY  0 (padrão: só erros) | 1 (geometria, pulso, chaves do
                             localStorage) | dom (+ texto da página e valores do
                             localStorage — só para depurar a UI do desktop)
       HERMES_WEB_DESKTOP_FIXES  1 = remendos do porte (trim, matchMedia, reset de
                             layout); padrão: 1 só na porta 8890
       HERMES_DESKTOP_CMD    1 liga o canal /__cmd (eval remoto); 0 desliga; vazio =
                             liga só se já houver token configurado (env ou arquivo)
       HERMES_WEB_LOG_MAX    bytes antes de girar o log (padrão 2000000)

Nada é exposto pra fora: bind só em 127.0.0.1. Mas loopback NÃO é fronteira de
confiança no Android: sites abertos no Chrome do próprio aparelho (CSRF, DNS
rebinding) e qualquer app com INTERNET alcançam esta porta. Por isso toda
requisição passa por _host_ok() e as rotas que mudam estado por _origem_ok().
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HOME = Path(os.environ.get("HOME", str(Path.home())))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(HOME / ".hermes")))
# mesmo padrão do hermes-desktop-up, do build-desktop-web.mjs e do
# gerar-fontes-embutidas.py: UM destino de bundle, configurável por HERMES_DESKTOP_WEB
BUNDLE = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "HERMES_DESKTOP_WEB", str(HOME / "hermes-agent" / ".hermes-port" / "renderer")))
SHIM = Path(os.environ.get("HERMES_DESKTOP_SHIM", str(HERMES_HOME / "bin" / "shim-desktop.js")))
PORT = int(os.environ.get("HERMES_DESKTOP_WEB_PORT", "8890"))
BACKEND = os.environ.get("HERMES_BACKEND", "http://127.0.0.1:9119")
LOG = Path(os.environ.get("HERMES_WEB_LOG", str(HERMES_HOME / "logs" / "desktop-web.log")))
LOG_MAX = int(os.environ.get("HERMES_WEB_LOG_MAX", "") or "2000000")

# Host aceito: só o próprio loopback NESTA porta. Um site que faz DNS rebinding
# para 127.0.0.1 manda `Host: evil.com:8890` — sem esta checagem ele lia `/` (com
# o token da sessão no HTML) e dirigia o backend inteiro.
HOSTS_OK = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
ORIGENS_OK = {"http://" + h for h in HOSTS_OK}

# telemetria: desligada por padrão. Ligada sempre, ela mandava ao log trechos da
# CONVERSA (body.textContent) e valores do localStorage, com pulso a cada 10 s,
# em toda página — inclusive o chat. Agora é opt-in e o texto só vem com "dom".
_tel = os.environ.get("HERMES_WEB_TELEMETRY", "0").strip().lower()
TELEMETRIA = _tel in ("1", "dom")
TELEMETRIA_DOM = _tel == "dom"
# remendos que só fazem sentido no app do desktop portado (8890): Object.prototype.trim,
# matchMedia fixado e reset das chaves de layout. No chat (8891) eram poluição pura.
# NÃO tire do 8890: o boot do app portado depende do trim (ver PR #1).
PORTE_DESKTOP = os.environ.get("HERMES_WEB_DESKTOP_FIXES", "1" if PORT == 8890 else "0") == "1"

# /__cmd executa JS arbitrário nas páginas abertas (eval). Qualquer app do
# aparelho alcança 127.0.0.1 (no Android o loopback NÃO exige permissão de rede)
# e a página carrega o token da sessão. Por isso o canal vem DESLIGADO e só liga
# por opt-in:
#   - HERMES_DESKTOP_CMD=1 liga (gera o segredo em state/desktop-cmd-token, 0600, se faltar);
#   - sem a env, liga só se JÁ existir token configurado (env HERMES_DESKTOP_CMD_TOKEN
#     ou o arquivo) — é o fluxo de quem já operava o canal pelo arquivo;
#   - HERMES_DESKTOP_CMD=0 desliga sempre.
# HERMES_DESKTOP_CMD_INSECURE=1 liga SEM token (sobram só Host/Origin) — não use.
CMD_ARQ = HERMES_HOME / "state" / "desktop-cmd-token"


def _ler_token_arquivo() -> str:
    try:
        return CMD_ARQ.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _ler_token_esperando() -> str:
    """Lê o arquivo que OUTRO servidor acabou de criar: pode estar vazio por um
    instante (criado, ainda não escrito). Espera até 2 s em vez de ler ""."""
    for _ in range(20):
        tok = _ler_token_arquivo()
        if tok:
            return tok
        time.sleep(0.1)
    return ""


def _criar_token_arquivo() -> str:
    """Gera o segredo e grava com 0600 já na criação (sem janela de leitura).

    8890 e 8891 sobem juntos pelo watchdog: o arquivo só aparece COMPLETO —
    escrito num temporário 0600 e publicado com os.link (atômico, falha se já
    existe). Se o link não for permitido (SELinux de algumas versões do Android),
    cai no O_EXCL + escrita, e quem perde a corrida lê esperando conteúdo."""
    CMD_ARQ.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    tmp = CMD_ARQ.with_name(f".{CMD_ARQ.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok)
        try:
            os.link(tmp, CMD_ARQ)
            return tok
        except FileExistsError:  # o outro servidor (8890 x 8891) publicou primeiro
            return _ler_token_esperando()
        except OSError:
            pass  # sem hard link: segue pelo O_EXCL abaixo
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        fd = os.open(str(CMD_ARQ), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _ler_token_esperando()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok)
    return tok


CMD_INSEGURO = os.environ.get("HERMES_DESKTOP_CMD_INSECURE", "") == "1"
_cmd_env = os.environ.get("HERMES_DESKTOP_CMD", "").strip()
CMD_TOKEN = os.environ.get("HERMES_DESKTOP_CMD_TOKEN", "") or (_ler_token_esperando() if CMD_ARQ.exists() else "")
CMD_TOKEN_ORIGEM = "env" if os.environ.get("HERMES_DESKTOP_CMD_TOKEN") else ("arquivo" if CMD_TOKEN else "")
if _cmd_env == "0":
    CMD_LIGADO = False
elif _cmd_env == "1" or CMD_INSEGURO:
    CMD_LIGADO = True
    if not CMD_TOKEN and not CMD_INSEGURO:
        CMD_TOKEN, CMD_TOKEN_ORIGEM = _criar_token_arquivo(), "arquivo (gerado agora)"
else:
    CMD_LIGADO = bool(CMD_TOKEN)

LAST_TOKEN = ""  # último token lido do backend, só para redigir no log
VERSION = str(time.time())  # muda a cada correção → a página aberta recarrega sozinha
RELOAD_FLAGS: list = []  # sinalizadores das páginas abertas (canal SSE)
PENDENTES: list = []  # [(ts, id, json)] — comandos/recargas para as páginas abertas
# OBS: NÃO esvaziar a fila ao entregar. Uma conexão SSE morta (de uma carga antiga)
# ficava na lista, "comia" o comando e a página viva nunca recebia. Agora toda
# conexão reescreve a fila e a página descarta ids repetidos — inclusive entre
# recargas (sessionStorage), senão um `reload`/`js` velho rodava de novo.
SSE_PING = 15  # s — o ping de 1 s custava CPU e rádio à toa
SSE_VIDA = 600  # s — depois disso a página reconecta sozinha (EventSource)
_LOG_LOCK = threading.Lock()

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
    with _LOG_LOCK:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        # rotação por tamanho: o log crescia sem limite e o `phone-health --prune`
        # não o pegava. Só este processo escreve nele, então girar por rename é seguro.
        try:
            if LOG.stat().st_size > LOG_MAX:
                os.replace(LOG, LOG.with_name(LOG.name + ".1"))
        except OSError:
            pass
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%F %T')} {msg}\n")


def uma_linha(texto: str) -> str:
    """Uma entrada = uma linha: \\n/\\r vindos do navegador forjavam linhas de log."""
    return texto.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def js_literal(valor) -> str:
    """json.dumps para dentro de <script>: um `</script>` no valor fecharia o bloco."""
    return (json.dumps(valor).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


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
  // flags decididas no SERVIDOR (env): telemetria, remendos do porte, canal /__cmd
  const FLAGS = %(flags)s;
  const env = (level, msg) => { try { navigator.sendBeacon('/__err', JSON.stringify({level, msg: String(msg).slice(0, 4000)})); } catch (e) {} };
  // DIAGNÓSTICO: o app chama .trim() em algo que não é string e morre.
  // TEM de ser NÃO-ENUMERÁVEL: enumerável, o `for…in` do framer-motion itera a
  // chave herdada e quebra o app com erros de interop (this.events[e].clear etc.).
  // Só no app do desktop portado (FLAGS.desktop): o boot dele depende disto.
  if (FLAGS.desktop) {
    try {
      Object.defineProperty(Object.prototype, 'trim', {
        value: function () {
          try { console.warn('[debug] .trim() recebeu um NAO-string: ' + JSON.stringify(this).slice(0, 200)); } catch (e) {}
          return String(this)
        },
        enumerable: false, configurable: true, writable: true
      })
    } catch (e) {}
  }
  window.__probe = () => {
    // telemetria é opt-in (HERMES_WEB_TELEMETRY): sem ela, só erros vão ao log
    if (!FLAGS.telemetria) return;
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
      // Valores só com FLAGS.dom: o localStorage guarda rascunho e preferência do usuário.
      try {
        const ls = [];
        for (let i = 0; i < localStorage.length && i < 40; i++) {
          const k = localStorage.key(i);
          const v = String(localStorage.getItem(k) || '');
          ls.push(k + '=' + (FLAGS.dom ? v.slice(0, 160) : '<' + v.length + ' chars>'));
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
      // texto da página = trecho da CONVERSA: só com FLAGS.dom (opt-in explícito)
      env('dom', 'title=' + document.title + ' | hash=' + location.hash +
        ' | nos=' + document.querySelectorAll('*').length +
        (FLAGS.dom
          ? ' | texto=' + t.replace(/\s+/g, ' ').slice(0, 400) +
            ' | completo=' + tudo.replace(/\s+/g, ' ').slice(0, 900)
          : ' | texto=<' + t.length + ' chars>'));
    } catch (e) { env('dom', 'probe falhou ' + e); }
  };
  window.addEventListener('error', e => env('error', (e.message || '') + ' @ ' + (e.filename || '') + ':' + (e.lineno || 0)));
  window.addEventListener('unhandledrejection', e => env('reject', (e.reason && (e.reason.stack || e.reason.message)) || e.reason));
  const ce = console.error.bind(console);
  console.error = (...a) => { env('console', a.map(x => (x && x.stack) || String(x)).join(' ')); ce(...a); };
  const cw = console.warn.bind(console);
  console.warn = (...a) => { env('warn', a.map(String).join(' ').slice(0, 800)); cw(...a); };
  if (FLAGS.telemetria) {
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
  }
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
    // ids vistos sobrevivem à recarga (sessionStorage da aba): o servidor reenvia a
    // fila de 90 s inteira a cada conexão, e sem isto um `reload` antigo recarregava
    // a página de novo (laço) e um `js` antigo rodava outra vez.
    const CHAVE = '__hermes_cmd_vistos';
    let vistos = [];
    try { vistos = JSON.parse(sessionStorage.getItem(CHAVE) || '[]') || []; } catch (e) { vistos = []; }
    const marcar = (id) => {
      vistos.push(id);
      vistos = vistos.slice(-60);
      try { sessionStorage.setItem(CHAVE, JSON.stringify(vistos)); } catch (e) {}
    };
    const CARREGADA = Date.now();
    es.onopen = () => env('sse', 'conectado');
    es.onmessage = (e) => {
      let m = null;
      try { m = JSON.parse(e.data); } catch (err) { m = null; }
      if (!m) { if (e.data === 'reload') location.reload(); return; }
      if (m.id) { if (vistos.indexOf(m.id) !== -1) return; marcar(m.id); }
      if (m.cmd === 'reload') {
        // pedido de recarga anterior a esta carga: a página já é a versão nova
        if (m.ts && m.ts * 1000 < CARREGADA) return;
        location.reload(); return;
      }
      // eval só existe com o canal ligado no servidor (HERMES_DESKTOP_CMD)
      if (m.cmd === 'js' && FLAGS.cmd) {
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
  // Só no desktop portado: no chat a media query é do próprio layout responsivo.
  if (FLAGS.desktop) try {
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
  if (FLAGS.desktop) try {
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


def _origens_backend() -> str:
    """Origens do backend para o connect-src da CSP do chat (http + ws)."""
    u = urlparse(BACKEND)
    if not u.netloc:
        return ""
    ws = "wss" if u.scheme == "https" else "ws"
    return f"{u.scheme}://{u.netloc} {ws}://{u.netloc}"


# assets com hash de conteúdo no nome (Vite: `nome-AbC123_x.js`) nunca mudam:
# podem ficar em cache. O resto (HTML, shim, stubs) segue no-store.
_ASSET_HASH = re.compile(r"-[A-Za-z0-9_-]{8}\.(?:js|mjs|css|woff2?|ttf|png|jpe?g|gif|webp|svg|wasm)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesDesktopWeb/1.0"

    def _send(self, body: bytes, ctype: str, code: int = 200, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj: dict, code: int = 200) -> None:
        return self._send(json.dumps(obj).encode(), "application/json", code)

    def _so_caminho(self) -> str:
        """Path SEM a query, para log: a query do GET /__cmd leva `token=`."""
        return uma_linha(urlparse(getattr(self, "path", "") or "").path)[:200]

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 (assinatura da stdlib)
        # O padrão do BaseHTTPRequestHandler grava a linha da requisição INTEIRA
        # (com `?token=…` do GET /__cmd) no stderr, que vai para
        # desktop-web-server.log / chat-server.log. Aqui sai só método + path.
        msg = fmt % args if args else fmt
        linha = getattr(self, "requestline", "") or ""
        if linha:
            msg = msg.replace(linha, f"{getattr(self, 'command', None) or '-'} {self._so_caminho()}")
        msg = re.sub(r"\?[^\s\"']*", "?…", msg)  # rede de segurança: nenhuma query no log
        sys.stderr.write(f"{self.log_date_time_string()} {uma_linha(msg)}\n")

    # ── guardas (loopback não é fronteira de confiança no Android) ───────────
    def _host_ok(self) -> bool:
        """Anti DNS rebinding: o Host tem de ser o próprio loopback desta porta."""
        if (self.headers.get("host") or "").strip().lower() in HOSTS_OK:
            return True
        log(f"[seg] Host recusado: {uma_linha(str(self.headers.get('host')))[:80]} em {self._so_caminho()[:80]}")
        return False

    def _origem_ok(self) -> bool:
        """Anti CSRF nas rotas que mudam estado ou entregam segredo.

        `Origin` (quando vem) tem de ser esta mesma origem; e `Sec-Fetch-Site`
        (o Chrome manda em toda requisição, inclusive `new Image().src` de outro
        site, que não leva Origin) tem de ser same-origin ou none (curl/termux
        não mandam nenhum dos dois)."""
        origem = self.headers.get("origin")
        if origem is not None and origem.strip().lower() not in ORIGENS_OK:
            log(f"[seg] Origin recusado: {uma_linha(origem)[:80]} em {self._so_caminho()[:80]}")
            return False
        sfs = (self.headers.get("sec-fetch-site") or "").strip().lower()
        if sfs and sfs not in ("same-origin", "none"):
            log(f"[seg] Sec-Fetch-Site={uma_linha(sfs)[:20]} recusado em {self._so_caminho()[:80]}")
            return False
        return True

    def _ler_corpo(self, limite: int = 8000) -> bytes:
        """Content-Length limitado: sem teto, um valor enorme estourava a RAM e um
        negativo travava a thread em rfile.read(-1) até o cliente fechar."""
        try:
            n = int(self.headers.get("content-length", "0"))
        except ValueError:
            n = 0
        n = max(0, min(n, limite))
        return self.rfile.read(n) if n else b""

    def _enfileirar(self, cmd: dict) -> int:
        PENDENTES.append((time.time(), uuid.uuid4().hex[:8], json.dumps(cmd)))
        n = 0
        for flag in list(RELOAD_FLAGS):
            flag.set()
            n += 1
        return n

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(b"host invalido", "text/plain", 403)
        path = urlparse(self.path).path
        if path == "/__err":
            if not self._origem_ok():
                return self._json({"ok": False}, 403)
            try:
                data = json.loads(self._ler_corpo(8000) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
            except Exception:
                data = {"msg": "<payload invalido>"}
            level = uma_linha(str(data.get("level", "?")))[:24]
            msg = uma_linha(redact(str(data.get("msg", ""))))[:2000]
            # ruído conhecido: não polui o log
            if not any(k in msg for k in ("favicon", "ResizeObserver loop")):
                log(f"[{level}] {msg}")
            return self._json({"ok": True})
        if path == "/__bump":
            # a doc sempre disse POST /__bump; antes só o GET existia (POST dava 404)
            return self._bump()
        if path == "/__cmd":
            return self._cmd(post=True)
        return self._send(b"{}", "application/json", 404)

    # ── rotas de operação ────────────────────────────────────────────────────
    def _bump(self) -> None:
        global VERSION
        if not self._origem_ok():
            return self._send(b"origem recusada", "text/plain", 403)
        VERSION = str(time.time())
        n = self._enfileirar({"cmd": "reload", "ts": time.time()})
        log(f"versao nova: {VERSION} — recarga empurrada para {n} pagina(s) aberta(s)")
        return self._send(VERSION.encode(), "text/plain")

    def _cmd(self, post: bool) -> None:
        """Operação remota da UI aberta (eval na página). Desligado por padrão.

        POST /__cmd  {"js": "...", "token": "..."}  (ou header X-Hermes-Cmd-Token)
        GET  /__cmd?js=<urlencoded>&token=<...>     (compatível com o fluxo antigo)
        """
        if not CMD_LIGADO:
            return self._json({"ok": False, "erro": "canal /__cmd desligado (HERMES_DESKTOP_CMD=1 liga)"}, 404)
        if not self._origem_ok():
            return self._json({"ok": False, "erro": "origem recusada"}, 403)
        if post:
            if "application/json" not in (self.headers.get("content-type") or "").lower():
                # exige preflight de qualquer origem que não seja esta
                return self._json({"ok": False, "erro": "content-type deve ser application/json"}, 415)
            try:
                corpo = json.loads(self._ler_corpo(64000) or b"{}")
                if not isinstance(corpo, dict):
                    raise ValueError
            except Exception:
                return self._json({"ok": False, "erro": "json invalido"}, 400)
            js = str(corpo.get("js") or "")
            tok = str(self.headers.get("x-hermes-cmd-token") or corpo.get("token") or "")
        else:
            q = parse_qs(urlparse(self.path).query)
            js = (q.get("js") or [""])[0]
            tok = str(self.headers.get("x-hermes-cmd-token") or (q.get("token") or [""])[0])
        if not CMD_INSEGURO and not (CMD_TOKEN and hmac.compare_digest(tok.encode(), CMD_TOKEN.encode())):
            log("[cmd] recusado: token ausente/errado (o token fica em state/desktop-cmd-token)")
            return self._json({"ok": False, "erro": "token ausente ou invalido"}, 403)
        if not js:
            return self._json({"ok": False, "erro": "sem js"}, 400)
        n = self._enfileirar({"cmd": "js", "js": js, "ts": time.time()})
        log(f"[cmd] enviado para {n} pagina(s): {uma_linha(js[:120])}")
        return self._json({"ok": True, "paginas": n})

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(b"host invalido", "text/plain", 403)
        path = urlparse(self.path).path
        # visibilidade: sem isso eu não sei se a página está viva e pedindo coisas
        if path in ("/", "/__events", "/__stubs.js", "/__shim.js", "/__health"):
            log(f"[req] {path}{'?…' if '?' in self.path else ''}")
        if path == "/__health":
            return self._send(b"ok", "text/plain")
        if path == "/__version":
            return self._send(VERSION.encode(), "text/plain")
        if path == "/__token":
            # token NOVO do backend: ele muda a cada restart do dashboard (o watchdog
            # reinicia) e a página aberta ficava tentando com o velho para sempre.
            # Mesmo segredo que já vai no HTML de `/`, com as mesmas guardas + origem.
            if not self._origem_ok():
                return self._json({"ok": False}, 403)
            tok = fetch_token()
            return self._json({"ok": bool(tok), "token": tok}, 200 if tok else 503)
        if path == "/__events":
            return self._eventos()
        if path == "/__bump":
            return self._bump()
        if path == "/__cmd":
            return self._cmd(post=False)
        if path == "/__manifest.webmanifest":
            # instalável como app próprio. (O PONTE é `%`-formatado, não f-string:
            # aqui é JSON gerado em Python, sem chave dobrada nenhuma.)
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
        return self._estatico(path)

    def _eventos(self) -> None:
        # canal aberto (SSE): eu empurro a recarga, funciona com aba em segundo plano
        flag = threading.Event()
        RELOAD_FLAGS.append(flag)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "keep-alive")
        self.end_headers()
        fim = time.time() + SSE_VIDA
        try:
            self.wfile.write(b": conectado\n\n")
            self.wfile.flush()
            while time.time() < fim:
                if flag.wait(timeout=SSE_PING):
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

    def _estatico(self, path: str) -> None:
        rel = unquote(path).lstrip("/")
        if "\0" in rel:
            # `\0` no caminho fazia resolve()/is_file() estourar ValueError (sem resposta)
            return self._send(b"caminho invalido", "text/plain", 400)
        # o bundle referencia os assets como "./assets/..." — normaliza
        while rel.startswith("./"):
            rel = rel[2:]
        try:
            target = (BUNDLE / rel).resolve() if rel else (BUNDLE / "index.html").resolve()
            target.relative_to(BUNDLE.resolve())
        except (ValueError, OSError):
            return self._send(b"negado", "text/plain", 403)

        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA: rota desconhecida cai no index — mas SÓ rota (sem extensão).
            # Asset ausente (.js/.woff2) devolvendo index.html com 200 virava
            # "Unexpected token <" longe da causa.
            if Path(rel).suffix:
                return self._send(b"nao encontrado", "text/plain", 404)
            target = BUNDLE / "index.html"
        if not target.is_file():
            return self._send(b"bundle nao encontrado: " + str(BUNDLE).encode(), "text/plain", 404)

        data = target.read_bytes()
        if target.suffix == ".html":
            return self._send(self._injetar(data.decode("utf-8", "replace")).encode(), MIME[".html"])
        cache = "public, max-age=31536000, immutable" if _ASSET_HASH.search(target.name) else "no-store"
        return self._send(data, MIME.get(target.suffix, "application/octet-stream"), cache=cache)

    def _injetar(self, html: str) -> str:
        flags = {"telemetria": TELEMETRIA, "dom": TELEMETRIA_DOM,
                 "desktop": PORTE_DESKTOP, "cmd": CMD_LIGADO}
        inj = PONTE % {"backend": js_literal(BACKEND), "token": js_literal(fetch_token()),
                       "flags": js_literal(flags)}
        if not PORTE_DESKTOP:
            # a ponte window.hermesDesktop é do app do desktop; no chat ela só
            # pesava (e o embrulho de WebSocket do shim recarregava a página
            # junto com a renovação de token que o próprio chat já faz)
            inj = inj.replace('<script src="/__stubs.js"></script>\n', "").replace(
                '<script src="/__shim.js"></script>\n', "")
        # CSP com nonce: páginas que pedem (o chat traz <meta> com __HERMES_NONCE__)
        # recebem nonce novo por resposta, e o <script> inline da injeção leva o
        # mesmo nonce. Sem o placeholder, nada muda (o bundle do desktop).
        if "__HERMES_NONCE__" in html:
            nonce = secrets.token_urlsafe(16)
            html = (html.replace("__HERMES_NONCE__", nonce)
                    .replace("__HERMES_CSP_BACKEND__", _origens_backend())
                    .replace("__HERMES_CSP_EVAL__", "'unsafe-eval'" if CMD_LIGADO else ""))
            inj = inj.replace("<script>", f'<script nonce="{nonce}">')
        # UI de desktop numa tela de celular: a viewport CSS tem ~400px e o layout
        # do desktop precisa de 1000+. Renderiza numa viewport larga e deixa o
        # Chrome reduzir — assim o layout fica inteiro (e dá pra dar zoom).
        largura = os.environ.get("HERMES_WEB_VIEWPORT_WIDTH", "")
        if largura.isdigit():
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
            return html.replace("</head>", inj + "</head>", 1)
        return inj + html


def main() -> None:
    log(f"servindo {BUNDLE} em http://127.0.0.1:{PORT} (backend {BACKEND})")
    log(f"telemetria={'dom' if TELEMETRIA_DOM else ('on' if TELEMETRIA else 'off')} "
        f"remendos-desktop={'on' if PORTE_DESKTOP else 'off'}")
    if not CMD_LIGADO:
        log("/__cmd desligado (HERMES_DESKTOP_CMD=1 liga; o segredo fica em state/desktop-cmd-token)")
    elif CMD_INSEGURO:
        log("/__cmd LIGADO SEM TOKEN (HERMES_DESKTOP_CMD_INSECURE=1) — só Host/Origin protegem")
    else:
        # o token NÃO vai para o log: quem opera lê o arquivo 0600 (ou a env)
        log(f"/__cmd ligado; token de {CMD_TOKEN_ORIGEM} ({CMD_ARQ if 'arquivo' in CMD_TOKEN_ORIGEM else 'HERMES_DESKTOP_CMD_TOKEN'})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
