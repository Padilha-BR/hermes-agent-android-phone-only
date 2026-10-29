// shim-desktop.js — implementa window.hermesDesktop no navegador, substituindo a
// ponte IPC do Electron, para rodar a UI do app DESKTOP do Hermes no celular.
//
// Base: relatório .hermes-port/ponte-desktop.md (contrato mapeado do renderer).
// Obrigatórios para bootar: getConnection, getGatewayWsUrl, getBootProgress,
// onBootProgress, onBackendExit, api. O resto é opcional (o renderer usa ?.) ou
// só-Electron (PTY/git/FS/diálogos) e fica inerte de propósito.
(function () {
  const BACKEND = window.__HERMES_BACKEND__ || 'http://127.0.0.1:9119';
  const TOKEN = window.__HERMES_SESSION_TOKEN__ || '';
  const PROFILE = window.__HERMES_PROFILE__ || 'default';
  const WS_URL = BACKEND.replace(/^http/, 'ws') + '/api/ws?token=' + encodeURIComponent(TOKEN);

  const log = (...a) => { try { console.info('[shim]', ...a); } catch (e) {} };

  // Rotas locais do injetor que gravam/leem disco (/__upload, /__save-text,
  // /__zoom, /__resolver-pasta, /__file) exigem o token da sessão: o loopback é
  // alcançável por qualquer app do aparelho, e só a página servida o conhece.
  const cabecalhoLocal = (extra) => Object.assign({ 'X-Hermes-Session-Token': TOKEN }, extra || {});

  // ── token obsoleto ───────────────────────────────────────────────────────
  // O token da sessão é lido UMA vez do HTML; quando o watchdog reinicia o
  // dashboard ele muda e o app ficava tentando com o velho para sempre. O
  // injetor expõe /__token (mesma origem): se o token mudou, recarrega a página
  // (o HTML novo já vem com o token certo). No máximo uma checagem a cada 30 s.
  let checandoToken = false, ultimaChecagem = 0;
  async function conferirToken() {
    if (checandoToken || Date.now() - ultimaChecagem < 30000) return;
    checandoToken = true; ultimaChecagem = Date.now();
    try {
      const r = await fetch('/__token', { cache: 'no-store', credentials: 'same-origin' });
      if (!r.ok) return; // backend ainda fora (503) ou injetor antigo (404)
      const j = await r.json();
      if (j && j.token && j.token !== TOKEN) { log('token do backend mudou → recarregando'); location.reload(); }
    } catch (e) {} finally { checandoToken = false; }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) conferirToken(); });
  // o WebSocket do gateway é aberto pelo próprio app (não passa pela ponte):
  // uma subclasse só observa o `close` do /api/ws para disparar a checagem.
  try {
    const WS0 = window.WebSocket;
    if (WS0) {
      window.WebSocket = class extends WS0 {
        constructor(url, protocolos) {
          super(url, protocolos);
          if (String(url).indexOf('/api/ws') !== -1) {
            this.addEventListener('close', () => setTimeout(conferirToken, 2000));
          }
        }
      };
    }
  } catch (e) {}

  const descriptor = () => ({
    baseUrl: BACKEND,
    token: TOKEN,
    wsUrl: WS_URL,
    mode: 'local',
    source: 'local',
    authMode: 'token',
    logs: [],
    nativeOverlayWidth: 0,
    isFullscreen: false,
    windowButtonPosition: null,
    customWindowControls: false,
    isMaximized: false,
    profile: PROFILE,
    connectionId: 'local',
    sharedPrimary: true
  });

  // ── progresso de boot ────────────────────────────────────────────────────
  const listeners = new Set();
  let last = null;
  const snapshot = (over) => ({
    error: null, fakeMode: false, message: 'conectado', phase: 'ready',
    progress: 100, running: true, timestamp: Date.now(), retryable: false, ...over
  });
  const emit = (p) => { last = p; listeners.forEach((cb) => { try { cb(p); } catch (e) {} }); };

  // ── transporte REST (equivale ao handler hermes:api do Electron) ─────────
  async function api(request) {
    const req = request || {};
    const path = req.path || '/api/status';
    const method = (req.method || 'GET').toUpperCase();
    const headers = { 'X-Hermes-Session-Token': TOKEN };
    let body;
    if (req.upload) {
      body = req.upload; // FormData — o navegador define o content-type
    } else if (req.body !== undefined && req.body !== null) {
      headers['content-type'] = 'application/json';
      body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
    }
    const ctrl = new AbortController();
    const timer = req.timeoutMs ? setTimeout(() => ctrl.abort(), req.timeoutMs) : null;
    try {
      const res = await fetch(BACKEND + path, { method, headers, body, signal: ctrl.signal, credentials: 'omit' });
      if (res.status === 401 || res.status === 403) conferirToken();
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.text()).slice(0, 300); } catch (e) {}
        throw new Error('HTTP ' + res.status + ' em ' + path + (detail ? ' :: ' + detail : ''));
      }
      const ct = res.headers.get('content-type') || '';
      if (ct.indexOf('application/json') !== -1) return await res.json();
      return await res.text();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  // ── ÁREA DE TRANSFERÊNCIA ────────────────────────────────────────────────
  // BUG QUE ISTO CONSERTA (medido no código, 15/10/2026): components/ui/
  // copy-button.tsx → writeClipboardText() tem early-return:
  //     if (window.hermesDesktop?.writeClipboard) { await ...; return }
  //     if (navigator.clipboard?.writeText) { ... }
  // Como o shim SEMPRE define writeClipboard (era `noopAsync`), o app nunca
  // caía no navigator.clipboard: copiava NADA e ainda pintava o check verde
  // "Copiado". Vale para o botão de copiar do bloco de código, do menu de
  // contexto (segurar o dedo), da mensagem e do terminal.
  // Aqui a escrita é REAL e, quando falha de verdade (documento sem foco — o
  // Android só libera o clipboard para o app em foco), LANÇA: o CopyButton
  // mostra o estado de erro em vez de mentir.
  function copiaLegado(texto) {
    try {
      const ta = document.createElement('textarea');
      ta.value = texto;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;' +
        'border:0;outline:0;box-shadow:none;background:transparent;opacity:0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (e) {
      return false;
    }
  }

  async function escreverClipboard(texto) {
    const t = String(texto == null ? '' : texto);
    if (!t) return true;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(t);
        return true;
      } catch (e) {
        // NotAllowedError = documento sem foco (aba em segundo plano) ou sem
        // gesto do usuário. Cai no caminho legado antes de desistir.
        log('clipboard: navigator falhou (' + (e && e.name) + '), tentando execCommand');
      }
    }
    if (copiaLegado(t)) return true;
    throw new Error('area de transferencia indisponivel (a pagina precisa estar em foco)');
  }

  async function lerClipboard() {
    try {
      if (navigator.clipboard && navigator.clipboard.readText) return await navigator.clipboard.readText();
    } catch (e) {}
    return '';
  }

  // ── ANEXOS: o navegador não dá CAMINHO, então os bytes viram arquivo ─────
  // No Electron, "anexar" abre o diálogo nativo e o app recebe um CAMINHO em
  // disco. Aqui `selectPaths` era stub e devolvia [], então os botões de foto e
  // de arquivo do composer eram só estéticos. Agora: seletor real do Android
  // (<input type=file>, que abre a galeria/seletor do sistema), os bytes sobem
  // para /__upload e VOLTA UM CAMINHO de verdade em ~/attachments — o mesmo
  // contrato que o app espera, e que o agente consegue ler (roda com cwd=$HOME).
  const EXT_IMAGEM = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'heic', 'heif'];

  const extensaoDe = (nome) => {
    const m = /\.([A-Za-z0-9]{1,8})$/.exec(String(nome || ''));
    return m ? m[1].toLowerCase() : '';
  };

  function b64DeBytes(bytes) {
    let saida = '';
    const passo = 0x8000;                    // em pedaços: apply() com arquivo grande estoura a pilha
    for (let i = 0; i < bytes.length; i += passo) {
      saida += String.fromCharCode.apply(null, bytes.subarray(i, i + passo));
    }
    return btoa(saida);
  }

  async function enviarBytes(bytes, ext, nome) {
    const r = await fetch('/__upload', {
      method: 'POST', headers: cabecalhoLocal({ 'content-type': 'application/json' }),
      body: JSON.stringify({ name: nome || '', ext: ext || '', data_b64: b64DeBytes(bytes) })
    });
    if (!r.ok) {
      if (r.status === 403) conferirToken();   // backend reiniciou: token novo
      let detalhe = '';
      try { detalhe = (await r.text()).slice(0, 200); } catch (e) {}
      throw new Error('nao salvei o anexo (' + r.status + ') ' + detalhe);
    }
    const j = await r.json();
    if (!j || !j.path) throw new Error('servidor nao devolveu caminho');
    return j.path;
  }

  async function enviarArquivo(arquivo) {
    return enviarBytes(new Uint8Array(await arquivo.arrayBuffer()), extensaoDe(arquivo.name), arquivo.name);
  }

  // Escolher PASTA: o navegador não tem API nenhuma que devolva o CAMINHO de
  // uma pasta (`showDirectoryPicker` não existe no Chrome Android, e
  // webkitdirectory no Android é ignorado). Mas a pasta é DESTE aparelho e o
  // agente lê o caminho — então perguntamos e CONFERIMOS no servidor. O campo
  // já vem com o cwd que o app mandou, e digitar só um nome também resolve
  // (o servidor procura dentro de $HOME: "docker" acha ~/repos/docker-termux).
  async function escolherPasta(o) {
    const base = o.defaultPath || '';
    let texto = null;
    try {
      texto = window.prompt((o.title || 'Pasta') + '\n\ncaminho (aceita ~ ou só um nome de pasta)',
                            base || '~');
    } catch (e) { texto = null; }
    if (texto === null || !String(texto).trim()) return [];
    try {
      const r = await fetch('/__resolver-pasta?nome=' + encodeURIComponent(String(texto).trim()) +
                            '&base=' + encodeURIComponent(base), { headers: cabecalhoLocal() });
      const j = await r.json();
      if (j && j.path) return [j.path];
      const cands = (j && j.candidatos) || [];
      log('pasta "' + texto + '": ' + (j && j.erro) + ' | candidatos: ' + cands.length);
      if (cands.length) {
        let escolha = null;
        try {
          escolha = window.prompt('Achei estas pastas:\n\n' +
            cands.map((c, i) => (i + 1) + ') ' + c).join('\n') +
            '\n\nNúmero (vazio cancela)', '1');
        } catch (e) { escolha = null; }
        const n = Number(String(escolha || '').trim());
        if (n >= 1 && n <= cands.length) return [cands[n - 1]];
      }
      return [];
    } catch (e) {
      log('escolherPasta falhou: ' + ((e && e.message) || e));
      return [];
    }
  }

  async function escolherPastaDeTrabalho() {
    const [dir] = await escolherPasta({ title: 'Pasta de trabalho' });
    return { canceled: !dir, dir: dir || null };
  }

  // Aviso visivel para falha de anexo: notificacao do sistema quando a pagina
  // tem permissao, e — sempre — o erro tambem vai para o log do injetor.
  function avisarAnexoFalhou(msg) {
    try {
      if (typeof Notification === 'function' && Notification.permission === 'granted') {
        new Notification('Anexo nao chegou', { body: String(msg).slice(0, 180) });
        return;
      }
      if (typeof Notification === 'function' && Notification.permission === 'default') {
        Notification.requestPermission().then((p) => {
          if (p === 'granted') new Notification('Anexo nao chegou', { body: String(msg).slice(0, 180) });
        }).catch(() => {});
      }
    } catch (e) { /* sem permissao: o log ja registrou */ }
  }

  function escolherArquivos(opcoes) {
    const o = opcoes || {};
    // Pasta tem caminho próprio; pasta de ARQUIVOS não (ver escolherPasta).
    if (o.directories) return escolherPasta(o);
    return new Promise((pronto) => {
      const entrada = document.createElement('input');
      entrada.type = 'file';
      entrada.multiple = o.multiple !== false;
      entrada.style.cssText = 'position:fixed;left:-10000px;top:0;width:1px;height:1px;opacity:0';
      const exts = [];
      (o.filters || []).forEach((f) => ((f && f.extensions) || []).forEach((e) => exts.push(String(e).toLowerCase())));
      if (exts.length) {
        // Só imagem → accept="image/*" faz o Android abrir a GALERIA (seletor de
        // fotos), que é o que se espera do botão de foto. Com a lista de
        // extensões crua o picker do Android tende a abrir o navegador de arquivos.
        entrada.accept = exts.every((e) => EXT_IMAGEM.indexOf(e) !== -1)
          ? 'image/*'
          : exts.map((e) => '.' + e).join(',');
      }
      let resolvido = false;
      const terminar = (lista) => {
        if (resolvido) return;
        resolvido = true;
        entrada.remove();
        pronto(lista);
      };
      entrada.onchange = async () => {
        const escolhidos = Array.from(entrada.files || []);
        if (!escolhidos.length) return terminar([]);
        const caminhos = [];
        for (const f of escolhidos) {
          try { caminhos.push(await enviarArquivo(f)); }
          catch (e) {
            // Falha de anexo NAO pode ser silenciosa: sem aviso o usuario ve o
            // seletor abrir, escolhe a foto e nada acontecer — foi exatamente o
            // que aconteceu quando /__upload morria com AttributeError.
            const msg = (e && e.message) || String(e);
            log('anexo falhou: ' + msg);
            avisarAnexoFalhou(msg);
          }
        }
        log('anexos: ' + escolhidos.length + ' escolhido(s) → ' + caminhos.length + ' caminho(s)');
        terminar(caminhos);
      };
      entrada.oncancel = () => terminar([]);   // Chrome 113+ — cancelamento real
      // Rede de segurança: `oncancel` não existe em todo build. Só conta como
      // cancelamento se a página REALMENTE perdeu o foco (o seletor abriu) e
      // voltou sem arquivo nenhum. Sem exigir o blur, o simples foco da janela
      // resolvia [] com o seletor ainda aberto — foi o que eu medi aqui: o app
      // chamava selectPaths e recebia lista vazia sem nunca escolher nada.
      let saiuDeFoco = false;
      window.addEventListener('blur', () => { saiuDeFoco = true; }, { once: true });
      const vigia = () => setTimeout(() => {
        if (!resolvido && saiuDeFoco && (!entrada.files || !entrada.files.length)) terminar([]);
      }, 1500);
      window.addEventListener('focus', vigia, { once: true });
      document.body.appendChild(entrada);
      entrada.click();
    });
  }

  async function lerArquivoComoDataUrl(caminho) {
    const r = await fetch('/__file?path=' + encodeURIComponent(String(caminho || '')), {
      headers: TOKEN ? { 'X-Hermes-Session-Token': TOKEN } : {}
    });
    if (!r.ok) return { error: 'nao consegui ler o arquivo (' + r.status + ')', path: caminho };
    const blob = await r.blob();
    return await new Promise((ok, falha) => {
      const fr = new FileReader();
      fr.onload = () => ok(String(fr.result));
      fr.onerror = () => falha(new Error('FileReader falhou'));
      fr.readAsDataURL(blob);
    });
  }

  // ── ZONA DE PEGA DO DIVISOR, sem roubar botão nenhum ────────────────────
  // O app põe controles a poucos px do fio (o "+" do composer a 25px, o botão de
  // status do turno a 20px, o "Minimize" a 4px do outro lado) — então alargar a
  // caixa que recebe o toque COME esses botões. Aqui a largura extra é dada sem
  // tocar no CSS: se o toque começa perto de um divisor e NÃO caiu em cima de um
  // controle, o gesto é REDIRECIONADO para o divisor, com um pointerdown
  // sintético que carrega o MESMO pointerId e as mesmas coordenadas. Isso
  // funciona porque startPointerDrag escuta pointermove no WINDOW
  // (lib/pointer-drag.ts) — uma vez iniciado, o arrasto segue o dedo em qualquer
  // lugar, e o delta é medido a partir do clientX/clientY reais (nada pula).
  const ZONA_FIO = 14;
  const CONTROLES = 'button,a,input,textarea,select,summary,label,' +
    '[role="button"],[role="menuitem"],[role="tab"],[role="switch"],[contenteditable="true"]';

  function instalarZonaDivisor() {
    document.addEventListener('pointerdown', (e) => {
      try {
        if (e.pointerType && e.pointerType !== 'touch') return;   // mouse/trackpad: comportamento do app
        if (e.defaultPrevented) return;
        const alvo = e.target;
        if (!alvo || !alvo.closest) return;
        if (alvo.closest('[data-sash-overlap]')) return;          // o toque já pegou o divisor
        if (alvo.closest(CONTROLES)) return;                      // é um controle do app: quem manda é ele
        const sashes = document.querySelectorAll('[data-sash-overlap]');
        for (let i = 0; i < sashes.length; i++) {
          const s = sashes[i];
          const r = s.getBoundingClientRect();
          if (r.width < 1 || r.height < 1) continue;
          if (getComputedStyle(s).pointerEvents === 'none') continue;
          const horizontal = r.height <= 12;                      // divisor de LINHA
          if (horizontal) {
            if (e.clientX < r.x - 8 || e.clientX > r.right + 8) continue;
            if (e.clientY < r.y - ZONA_FIO || e.clientY > r.bottom + ZONA_FIO) continue;
          } else {
            if (r.height < 40) continue;
            if (e.clientY < r.y || e.clientY > r.bottom) continue;
            if (e.clientX < r.x - ZONA_FIO || e.clientX > r.right + ZONA_FIO) continue;
          }
          s.dispatchEvent(new PointerEvent('pointerdown', {
            bubbles: true, cancelable: true, composed: true,
            pointerId: e.pointerId, pointerType: e.pointerType || 'touch',
            isPrimary: true, buttons: 1, button: 0,
            clientX: e.clientX, clientY: e.clientY
          }));
          return;
        }
      } catch (err) {
        log('zona do divisor falhou: ' + ((err && err.message) || err));
      }
    }, true);
  }

  // ── MICROFONE ────────────────────────────────────────────────────────────
  // use-mic-recorder.ts:274 pergunta à ponte ANTES de abrir o microfone; com o
  // stub fixado em `false` o app lançava "acesso negado" sem tentar — era essa
  // a mensagem que aparecia. Aqui a resposta é a verdade: pede a permissão de
  // fato (o Chrome mostra o diálogo do Android) e devolve o resultado, soltando
  // a trilha na hora — quem grava é o próprio app, logo em seguida.
  async function pedirMicrofone() {
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
      const fluxo = await navigator.mediaDevices.getUserMedia({ audio: true });
      fluxo.getTracks().forEach((t) => t.stop());
      return true;
    } catch (e) {
      log('microfone recusado: ' + ((e && e.name) || e));
      return false;
    }
  }

  // ── TAMANHO DA UI (Ajustes → Aparência → escala da UI) ───────────────────
  // Na UI do desktop quem manda no zoom é o processo principal do Electron
  // (webContents.setZoomLevel). Aqui o equivalente é a META DE VIEWPORT: o app
  // é servido numa viewport LARGA (o layout do desktop precisa de ~640px CSS) e
  // o Chrome REDUZ a página para caber na tela — logo, MENOR largura = texto
  // MAIOR. 100% = BASE_W, a largura que o servidor injetou.
  // Sem isto, o controle "escala da UI" de Ajustes→Aparência era um botão morto:
  // o stub universal devolvia um objeto e `zoom.get().then(({percent}) => …)`
  // recebia um stub no lugar do número.
  const BASE_W = Number(window.__HERMES_WEB_BASE_WIDTH__) || (function () {
    const m = document.querySelector('meta[name=viewport]');
    const achou = m && /width=(\d+)/.exec(m.content);
    return achou ? Number(achou[1]) : 800;
  })();
  // 660 é o piso: abaixo disso o layout do desktop fica espremido demais.
  const W_MIN = Math.round(BASE_W * 0.62);
  const W_MAX = Math.round(BASE_W * 1.15);
  const CHAVE_W = 'hermes.desktop.webViewportW.v1';
  const assinantesZoom = new Set();

  const metaViewport = () => document.querySelector('meta[name=viewport]');
  const larguraAtual = () => {
    const m = metaViewport();
    const achou = m && /width=(\d+)/.exec(m.content);
    return achou ? Number(achou[1]) : BASE_W;
  };
  const percentual = () => Math.round((BASE_W * 100) / larguraAtual());
  // Electron: fator = 1.2 ** nível  →  nível = log(fator)/log(1.2)
  const comoNivel = (percent) => Math.log(percent / 100) / Math.log(1.2);

  function aplicarLargura(w, persistir) {
    const alvo = Math.max(W_MIN, Math.min(W_MAX, Math.round(w)));
    const m = metaViewport();
    if (m) {
      // interactive-widget=resizes-content: sem isso o Chrome (108+) NÃO
      // redimensiona o layout viewport quando o teclado aparece e o composer
      // fica atrás do teclado. Mantido aqui também porque quem troca a meta
      // em tempo de execução precisa reescrever o content inteiro.
      m.setAttribute('content', 'width=' + alvo + ', interactive-widget=resizes-content');
    }
    if (persistir) {
      try { localStorage.setItem(CHAVE_W, String(alvo)); } catch (e) {}
      // avisa o servidor para o PRÓXIMO carregamento já nascer no tamanho certo
      try {
        fetch('/__zoom', {
          method: 'POST', headers: cabecalhoLocal({ 'content-type': 'application/json' }),
          body: JSON.stringify({ width: alvo }), keepalive: true
        }).catch(() => {});
      } catch (e) {}
    }
    const p = percentual();
    assinantesZoom.forEach((cb) => { try { cb({ level: comoNivel(p), percent: p }); } catch (e) {} });
    return alvo;
  }

  const noop = () => {};
  const noopAsync = async () => {};
  const falseAsync = async () => false;

  // openExternal: só http(s) e sem window.opener. Antes aceitava qualquer esquema
  // (javascript:, intent:, file:) vindo do app/markdown e a aba nova podia
  // navegar esta (reverse tabnabbing).
  const abrirFora = (url) => {
    try {
      const u = new URL(String(url), location.href);
      if (u.protocol === 'http:' || u.protocol === 'https:') {
        window.open(u.href, '_blank', 'noopener,noreferrer');
      } else {
        console.warn('[shim] openExternal recusou esquema ' + u.protocol);
      }
    } catch (e) {}
    return noopAsync();
  };

  // merge PROFUNDO: com Object.assign raso, um objeto do shim (git, terminal,
  // windowControls…) substituía o objeto gerado INTEIRO e os métodos do contrato
  // que eu não reescrevi sumiam — TypeError "is not a function", justamente o que
  // o gerador existe para evitar (o Proxy lá embaixo só cobre o 1º nível).
  // Só objetos literais se mesclam; função, null e array substituem.
  const literal = (v) => v !== null && typeof v === 'object' && Object.getPrototypeOf(v) === Object.prototype;
  const mesclar = (base, por) => {
    const out = literal(base) ? Object.assign({}, base) : {};
    Object.keys(por).forEach((k) => {
      out[k] = literal(out[k]) && literal(por[k]) ? mesclar(out[k], por[k]) : por[k];
    });
    return out;
  };

  const bridge = mesclar(
    // base: TODA a superfície do contrato (gerada de global.d.ts por gerar-shim-stubs.py).
    // Sem isso, cada chave ausente virava stub e quebrava o app de formas diferentes.
    (typeof window !== 'undefined' && window.__HERMES_STUBS__) || {},
    {
    // ── obrigatórios ──────────────────────────────────────────────────────
    getConnection: async () => descriptor(),
    // themes/backend-sync.ts:37-42 lê `localSkin` para decidir o perfil de boot.
    // Sem esta chave, o Proxy devolvia um stub e BOOT_PROFILE_KEY virava Promise
    // → o boot morria em `(e ?? "").trim is not a function`. null = sem skin local.
    localSkin: null,
    getGatewayWsUrl: async () => ({ ok: true, wsUrl: WS_URL }),
    getBootProgress: async () => last || snapshot(),
    onBootProgress: (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    onBackendExit: () => () => {},
    api,

    // ── opcionais úteis (um backend local, um perfil) ─────────────────────
    getConnectionFor: async () => descriptor(),
    getGatewayWsUrlFor: async () => ({ ok: true, wsUrl: WS_URL }),
    revalidateConnection: async () => ({ ok: true, rebuilt: false }),
    touchBackend: async () => ({ ok: true }),
    getAgentRoster: async () => ({ agents: [] }),
    profile: {
      get: async () => ({ profile: PROFILE }),
      getDefault: async () => ({ connectionId: null, profile: PROFILE }),
      set: async () => ({ profile: PROFILE }),
      remember: async () => ({ profile: PROFILE }),
      onDefaultChanged: () => () => {}
    },
    getProfileRoutes: async (profiles) =>
      ((profiles && profiles.length) ? profiles : [PROFILE]).map((name) => ({
        connectionId: 'local', mode: 'local', primary: true, profile: name, targetProfile: name
      })),
    connections: {
      list: async () => ({
        version: 2, primary: 'local', launchMode: 'primary', secureTokenStorage: false,
        connections: [{ id: 'local', kind: 'local', label: 'Este aparelho', tokenSet: true, tokenPreview: null }]
      }),
      onChanged: () => () => {}
    },

    // ── chrome / OS / notificações: no-ops seguros ────────────────────────
    notify: async () => true,
    setActiveWork: noop,
    setTitleBarTheme: noop,
    setNativeTheme: noop,
    setTranslucency: noop,
    setKeepAwake: noopAsync,
    // CONTRATO: estes dois são BOOLEANOS (global.d.ts:382-384), não funções.
    // Como função, o app lê "verdadeiro" e entra em caminho de vidro/translucidez
    // que não existe aqui — e depois chama algo que não é função ("W is not a function").
    glassSupported: false,
    translucencySupported: false,
    // CONTRATO: objeto com get/set (global.d.ts:312-314), não número.
    dataUrlReadMax: {
      get: async () => ({ defaultMaxMb: 20, maxBytes: 20 * 1024 * 1024, maxMb: 20 }),
      set: async (maxMb) => ({ defaultMaxMb: 20, maxBytes: Number(maxMb || 20) * 1024 * 1024, maxMb: Number(maxMb || 20) })
    },
    getOnBattery: async () => ({ onBattery: false }),
    onBatteryChanged: () => () => {},
    onPowerResume: () => () => {},
    onConnectionApplied: () => () => {},
    onPoolBackendRetiring: () => () => {},
    // remote-display-banner.tsx:12-17 — sem isto o banner monta com um stub e
    // quebra a conversão para primitivo ("Cannot convert object to primitive value").
    getRemoteDisplayReason: async () => null,
    // use-composer-screenshot.ts:28 — captura de tela é só-Electron; aqui é inerte.
    screenshot: {
      onStatus: () => () => {},
      onRequest: () => () => {},
      capture: async () => ({ ok: false })
    },
    // store/quick-entry.ts:516 — janela de Quick Entry é só-Electron. O resto do
    // contrato (getSettings, submit, dismiss, onShown…) vem dos stubs pelo merge.
    quickEntry: {
      onSubmit: () => () => {}
    },
    onWindowStateChanged: () => () => {},
    onDeepLink: () => () => {},
    getVersion: async () => ({ version: '0.0.0-mobile', name: 'Hermes', releaseDate: '', platform: 'android' }),
    getMachineProfile: async () => ({ os: 'android', arch: 'arm64', platform: 'android', cpus: 8 }),
    logLine: noop,
    revealLogs: noopAsync,
    openExternal: abrirFora,
    updates: {
      check: async () => ({ ok: false }), apply: noopAsync,
      // store/updates.ts:1345 chama isto SEM ?. — o main do Electron sempre expõe.
      onProgress: () => () => {}
    },
    // nomes do CONTRATO (global.d.ts): os antigos maximize/kill/status/diff/
    // branches/worktrees não existem lá — o app chamava toggleMaximize/dispose/
    // repoStatus… e caía no objeto do shim sem eles. themes (fetchMarketplace,
    // searchMarketplace) fica só com os stubs: a forma de retorno não é conhecida.
    windowControls: { custom: false, minimize: noopAsync, toggleMaximize: noopAsync, close: noopAsync },
    // superfícies só-Electron: presentes mas inertes, para nunca dar TypeError
    terminal: {
      start: noopAsync, attach: noopAsync, write: noopAsync, resize: noopAsync, cwd: async () => null,
      onData: () => () => {}, onExit: () => () => {}, dispose: noopAsync
    },
    git: {
      repoStatus: async () => null, fileDiff: async () => '', branchList: async () => [],
      worktreeList: async () => []
    },
    openDir: noopAsync,
    revealPath: noopAsync,
    findInPage: async () => ({ count: 0 }),
    stopFindInPage: noopAsync,
    onFoundInPage: () => () => {},
    hud: null,
    openWindow: noopAsync,
    openSessionWindow: noopAsync,
    openBrowserWindow: noopAsync,
    requestMicrophoneAccess: () => pedirMicrofone(),
    // ── formas específicas (o gerado devolve null; o app lê campos daqui) ──
    getBootstrapState: async () => ({ state: 'ready', phase: 'ready', running: false, log: [], error: null }),
    onBootstrapEvent: () => () => {},
    continueBootstrapLocal: async () => ({ ok: true }),
    probeLocalBackend: async () => ({ ok: true }),
    repairBootstrap: async () => ({ ok: true }),
    getSyncStatus: async () => ({ state: 'ready' }),
    getRecentLogs: async () => [],
    // store/session.ts:375-393 destrutura `.dir` deste retorno; null virava o aviso
    // "Failed to seed default workspace cwd". Devolve o home do Termux.
    sanitizeWorkspaceCwd: async (p) => ({ dir: (typeof p === 'string' && p) ? p : '/data/data/com.termux/files/home' }),
    // store/session.ts:353-363 faz `const { dir } = await settings()` — o gerado
    // devolvia null e virava o aviso "Failed to seed default workspace cwd".
    // Colocar pasta a partir da ponte: era só "pasta não dá". Agora pergunta o
    // caminho e confere (mesmo caminho de escolherPasta) — é isto que alimenta
    // "Folder…" do menu, "mudar workspace" e "adicionar projeto".
    // No CONTRATO (global.d.ts) pickDefaultProjectDir mora em `settings`; a cópia
    // no 1º nível fica por compatibilidade com quem já chamava por ali.
    pickDefaultProjectDir: () => escolherPastaDeTrabalho(),
    // literal simples: o merge PROFUNDO acima preserva setDefaultProjectDir e o
    // resto dos stubs de `settings` (o Object.assign com os stubs era redundante)
    settings: {
      getDefaultProjectDir: async () => ({ dir: '/data/data/com.termux/files/home' }),
      pickDefaultProjectDir: () => escolherPastaDeTrabalho(),
      // `set` gravava em lugar nenhum (stub): a escolha do usuário não voltava
      // na próxima abertura. Aqui persiste no localStorage da aba/app.
      setDefaultProjectDir: async (dir) => {
        try {
          if (dir) localStorage.setItem('hermes.desktop.defaultProjectDir.v1', String(dir));
          else localStorage.removeItem('hermes.desktop.defaultProjectDir.v1');
        } catch (e) {}
        return { dir: dir ? String(dir) : null };
      }
    },
    // ── chaves do contrato em que o app JÁ tem fallback de navegador ────────
    // O gerador cria um stub para TODA chave do contrato. Quando o app faz
    // `const native = bridge.X; if (native) native.call()`, o stub é TRUTHY e
    // derruba o caminho de fallback — foi assim que o composer passou a
    // imprimir "Invalid prepared submission journal" (o stub devolvia objeto no
    // lugar do JSON do diário). Aqui as que têm equivalente real no navegador.
    preparedSubmissions: (() => {
      const CHAVE = 'hermes.desktop.preparedSubmissions.v1';
      const ler = () => {
        try {
          const cru = JSON.parse(localStorage.getItem(CHAVE) || '{}');
          return cru && typeof cru === 'object' && !Array.isArray(cru) ? cru : {};
        } catch (e) { return {}; }
      };
      const gravar = (j) => { try { localStorage.setItem(CHAVE, JSON.stringify(j)); } catch (e) {} };
      return {
        read: async () => JSON.stringify(ler()),
        update: async (chave, entrada) => {
          const j = ler();
          if (entrada === null || entrada === undefined) delete j[chave]; else j[chave] = entrada;
          gravar(j);
        },
        // Atômico no Electron; aqui é o mesmo processo (aba), então basta
        // comparar-e-gravar sem corrida real.
        compareAndSet: async (chave, esperado, entrada) => {
          const j = ler();
          const atual = Object.prototype.hasOwnProperty.call(j, chave) ? j[chave] : null;
          if (atual !== (esperado === undefined ? null : esperado)) {
            return { applied: false, current: atual };
          }
          if (entrada === null || entrada === undefined) delete j[chave]; else j[chave] = entrada;
          gravar(j);
          return { applied: true, current: entrada === undefined ? null : entrada };
        }
      };
    })(),
    readFileDataUrlForAttach: (caminho) => lerArquivoComoDataUrl(caminho),
    // No navegador não existe caminho para um File do SO (webUtils.getPathForFile
    // é do Electron). Devolver '' é o contrato honesto: o app usa os bytes.
    getPathForFile: () => '',
    localModelsEnabled: false,
    gitRoot: async () => null,
    logsRoot: async () => '',
    agentPluginsRoot: async () => '',
    desktopPluginsRoot: async () => '',
    watchDirectory: async (dir) => ({ id: 'sem-vigia', path: String(dir || '') }),
    saveTextFileAs: async (pedido) => {
      const p = pedido || {};
      const texto = String(p.content !== undefined ? p.content : (p.text || ''));
      if (!texto) return { canceled: true, saved: false };
      const r = await fetch('/__save-text', {
        method: 'POST',
        headers: cabecalhoLocal({ 'content-type': 'application/json' }),
        body: JSON.stringify({ text: texto })
      });
      const j = await r.json().catch(() => null);
      if (!j || !j.ok) throw new Error('nao salvei o texto (' + r.status + ')');
      return { saved: true, path: j.path };
    },
    // Erro da renderer vai para o log do injetor (é o canal que eu leio).
    reportRendererError: (payload) => {
      try {
        const msg = typeof payload === 'string' ? payload : JSON.stringify(payload);
        navigator.sendBeacon('/__err', new Blob([JSON.stringify({ level: 'error', msg: msg })],
          { type: 'application/json' }));
      } catch (e) {}
      return undefined;
    },
    readDir: async () => [],
    readFileText: async () => '',
    selectPaths: (options) => escolherArquivos(options),
    selectSavePath: async () => null,

    // ── download de arquivo da conversa ──────────────────────────────────
    // O botão de download (card de anexo, menu do arquivo) chama isto e lê
    // `.saved` da resposta. Sem esta implementação a ponte caía no stub
    // inerte, devolvia null e o app estourava com
    // "Cannot read properties of null (reading 'saved')".
    // Aqui não existe diálogo "Salvar como": o arquivo vai para o Download
    // compartilhado do aparelho e o app recebe o caminho de volta.
    saveGatewayFile: async (opcoes) => {
      const o = opcoes || {};
      const caminho = o.path || o.caminho || '';
      const r = await fetch('/__save-file', {
        method: 'POST',
        headers: cabecalhoLocal({ 'content-type': 'application/json' }),
        body: JSON.stringify({ path: caminho, name: o.suggestedName || o.name || '' })
      });
      const d = await r.json().catch(() => null);
      if (d && d.saved) { log('download salvo em', d.saved); return d; }
      throw new Error((d && d.erro) || ('HTTP ' + r.status));
    },


    // ── anexos: caminho de verdade em troca dos bytes ─────────────────────
    // saveImageBuffer é o caminho de "colar/dropar imagem"; sem ele o app
    // recebia um objeto do stub e criava um anexo com caminho inválido.
    saveImageBuffer: (dados, ext, nome) => {
      const bytes = dados instanceof Uint8Array ? dados : new Uint8Array(dados || []);
      return enviarBytes(bytes, ext, nome);
    },
    // Miniatura/preview de anexo já salvo (é o que faz aparecer a imagem no chip).
    readFileDataUrl: (caminho) => lerArquivoComoDataUrl(caminho),

    // ── área de transferência: FUNCIONAL (era no-op silencioso) ────────────
    readClipboard: () => lerClipboard(),
    // o app pode mandar a string ou {text}: String({text}) copiaria "[object Object]"
    writeClipboard: (t) => escreverClipboard((t && typeof t === 'object' && 'text' in t) ? t.text : t),

    // Colar um texto MUITO grande vira anexo .txt (igual ao desktop). Só entra
    // em jogo acima do limite de paste inline; se o servidor falhar, devolve
    // null e o app mantém o texto inline (caminho de degradação do próprio app).
    savePastedText: async (texto) => {
      try {
        const r = await fetch('/__save-text', {
          method: 'POST', headers: cabecalhoLocal({ 'content-type': 'application/json' }),
          body: JSON.stringify({ text: String(texto || '') })
        });
        if (!r.ok) return null;
        const d = await r.json();
        return (d && d.path) || null;
      } catch (e) {
        return null;
      }
    },
    // Imagem da área de transferência → mesmo endpoint que o botão de clipe usa
    // (/api/chat/image-upload, corpo JSON com data_url). Sem permissão de
    // leitura do clipboard o navegador recusa e o app avisa — sem quebrar nada.
    saveClipboardImage: async () => {
      try {
        if (!navigator.clipboard || !navigator.clipboard.read) return null;
        const itens = await navigator.clipboard.read();
        for (const item of itens) {
          const tipo = (item.types || []).find((x) => x.indexOf('image/') === 0);
          if (!tipo) continue;
          const blob = await item.getType(tipo);
          const dataUrl = await new Promise((ok, falha) => {
            const fr = new FileReader();
            fr.onload = () => ok(fr.result);
            fr.onerror = falha;
            fr.readAsDataURL(blob);
          });
          const r = await fetch(BACKEND + '/api/chat/image-upload', {
            method: 'POST',
            headers: { 'content-type': 'application/json', 'X-Hermes-Session-Token': TOKEN },
            body: JSON.stringify({ data_url: dataUrl, filename: 'clipboard.png' })
          });
          if (!r.ok) return null;
          const d = await r.json();
          return (d && d.path) || null;
        }
        return null;
      } catch (e) {
        return null;
      }
    },

    // ── tamanho da UI: implementa o contrato do Electron sobre a viewport ──
    zoom: {
      get: async () => ({ level: comoNivel(percentual()), percent: percentual() }),
      // preview-pane.tsx:1299 multiplica coordenadas por isto — sem a função, o
      // stub do Proxy devolvia objeto e a conta de coordenadas do preview saía errada.
      factor: () => BASE_W / larguraAtual(),
      setPercent: (percent) => {
        const p = Number(percent) || 100;
        aplicarLargura((BASE_W * 100) / p, true);
        log('zoom → ' + p + '% (largura ' + larguraAtual() + 'px CSS)');
      },
      onChanged: (cb) => {
        assinantesZoom.add(cb);
        return () => assinantesZoom.delete(cb);
      }
    }
    }
  )

  // ── instrumentação: registra o que a ponte devolve (eu leio isso no log) ──
  const track = (nome, fn) => async (...a) => {
    try {
      const r = await fn(...a);
      try { console.info('[bridge] ' + nome + ' → ' + JSON.stringify(r).slice(0, 300)); } catch (e) {}
      return r;
    } catch (e) {
      console.error('[bridge] ' + nome + ' FALHOU: ' + (e && e.message));
      throw e;
    }
  };
  bridge.getConnection = track('getConnection', bridge.getConnection);
  bridge.getProfileRoutes = track('getProfileRoutes', bridge.getProfileRoutes);
  bridge.profile.get = track('profile.get', bridge.profile.get);
  bridge.profile.getDefault = track('profile.getDefault', bridge.profile.getDefault);
  bridge.connections.list = track('connections.list', bridge.connections.list);

  // ── auditoria da ponte (diagnóstico permanente) ──────────────────────────
  // Compara folha por folha o objeto final com a base GERADA do contrato: o que
  // continuou com a MESMA referência é stub (feature que existe na UI e não faz
  // nada); o resto foi implementado aqui. Sem isso, "o botão não faz nada" vira
  // adivinhação — e a lista muda a cada atualização do contrato.
  try {
    const achatar = (obj, prefixo) => Object.keys(obj || {}).reduce((acc, k) => {
      const caminho = prefixo ? prefixo + '.' + k : k;
      const v = obj[k];
      if (v && typeof v === 'object' && !Array.isArray(v) && Object.getPrototypeOf(v) === Object.prototype) {
        Object.assign(acc, achatar(v, caminho));
      } else {
        acc[caminho] = v;
      }
      return acc;
    }, {});
    const base = achatar((window.__HERMES_STUBS__) || {}, '');
    const final = achatar(bridge, '');
    const stubs = Object.keys(base).filter((k) => final[k] === base[k]);
    const implementados = Object.keys(base).filter((k) => k in final && final[k] !== base[k]);
    const extras = Object.keys(final).filter((k) => !(k in base));
    window.__HERMES_AUDIT__ = { stubs, implementados, extras };
    log(`ponte: ${implementados.length} implementados, ${stubs.length} stubs, ${extras.length} extras`);
  } catch (e) { /* auditoria nunca derruba o boot */ }

  // ── superfícies que NÃO existem no navegador: AUSENTES, não stub ─────────
  // O app decide "posso fazer X?" por PRESENÇA (`typeof bridge.X === 'function'`).
  // Com o stub universal toda chave do contrato existe, então o app achava que
  // estava dentro do Electron e mostrava "New window", "Open in terminal", HUD,
  // pet, screenshot, updater… — botões que abrem e não fazem nada. Apagar a
  // chave (e o Proxy devolver undefined) faz o app esconder/desabilitar a ação.
  // NÃO entram aqui os listeners (on*): código que faz `const off = bridge.onX(cb)`
  // sem `?.` estouraria com TypeError.
  const AUSENTES = new Set([
    'openWindow', 'openSessionWindow', 'openBrowserWindow', 'openSessionInTerminal',
    'hud', 'petOverlay', 'quickEntry', 'windowRelay', 'windowControls',
    'screenshot', 'minimizeToTray', 'nativeOverlayWidth',
    'updates', 'updateHold', 'uninstall', 'relaunchApp', 'recycleBackend', 'resetBootstrap',
    'cancelBootstrap', 'desktopMetrics', 'setDisableF12', 'setF12ShortcutActive',
    'capturePreview', 'watchPreviewFile', 'stopPreviewFileWatch',
    'sshConfigHosts', 'sshResolveHost', 'cloud', 'probeConnectionConfig',
    'getConnectionConfig', 'saveConnectionConfig', 'applyConnectionConfig', 'testConnectionConfig',
    'getSecretStorageEncryption', 'setSecretStorageEncryption',
    'oauthLoginConnectionConfig', 'oauthLogoutConnectionConfig',
    'installDesktopPlugin', 'removeDesktopPlugin', 'reconcileDesktopPlugins', 'probePluginRepo',
    'readPluginSource', 'desktopPluginsRoot', 'agentPluginsRoot',
    'claimAmbientCue', 'chatOnboarding', 'wakeIndicator', 'hudModifier',
    'themes', 'preparedSubmissionsProxyFallback',
  ]);
  AUSENTES.forEach((chave) => { delete bridge[chave]; });

  const faltando = new Set()
  window.hermesDesktop = new Proxy(bridge, {
    // Rede de segurança: qualquer método da ponte que eu não tenha previsto devolve
    // um stub universal (função que resolve em outro stub) em vez de dar TypeError.
    // E registra o nome: em build de produção o React não loga o erro capturado,
    // então a lista de chaves que o app PEDE e não existe é o único rastro do culpado.
    get(alvo, prop) {
      if (prop in alvo) {
        // Registra o uso de um stub do contrato: é assim que se descobre QUAL
        // botão da tela depende de algo que aqui não faz nada (antes, silêncio).
        try {
          if (typeof prop === 'string' && window.__HERMES_AUDIT__ &&
              window.__HERMES_AUDIT__.stubs.indexOf(prop) !== -1) {
            console.info('[bridge-stub] ' + prop);
          }
        } catch (e) {}
        return alvo[prop]
      }
      if (typeof prop === 'symbol') return undefined
      // Superfície que este host não tem: undefined de propósito (o app testa
      // presença para decidir se mostra o botão).
      if (AUSENTES.has(String(prop))) return undefined
      if (!faltando.has(prop) && String(prop).length < 40) {
        faltando.add(prop)
        try { console.info('[bridge-falta] ' + String(prop)) } catch (e) {}
      }
      const stub = new Proxy(function () {}, {
        get(_t, p) {
          // NUNCA sequestrar a conversão para primitivo: devolver função em
          // Symbol.toPrimitive/toString/valueOf quebra o app com
          // "Cannot convert object to primitive value".
          if (typeof p === 'symbol') return undefined;
          if (p === 'then') return undefined; // não é Promise
          if (p === 'toString' || p === 'valueOf' || p === 'toJSON') return undefined;
          return stub;
        },
        apply() { return Promise.resolve(stub); }
      });
      return stub;
    }
  });

  // ── ajustes do porte aplicados ANTES do app renderizar ───────────────────
  // 1) tamanho persistido (escolhido em Ajustes→Aparência ou por hermes-desktop-zoom).
  //    Este script roda no <head>, antes do bundle do app: mudar a meta aqui evita
  //    o "pulo" de escala no primeiro paint.
  try {
    const guardada = Number(localStorage.getItem(CHAVE_W));
    if (guardada && Math.abs(guardada - larguraAtual()) > 2) {
      aplicarLargura(guardada, true);
      log('largura aplicada do armazenamento local: ' + larguraAtual() + 'px CSS ('
        + percentual() + '%)');
    }
  } catch (e) {}

  // 2) texto do chat um pouco maior — o padrão do app é 110%, que num celular
  //    fica pequeno (medido: 8,6 dip de altura de texto contra ~16 dip do
  //    confortável em mobile). Este é o MESMO ajuste que existe em
  //    Ajustes → Aparência → "texto do chat".
  //    ARMADILHA MEDIDA: o app REMOVE esta chave quando o valor é o padrão dele
  //    (store/chat-text-scale.ts: persistString(KEY, valor === 110 ? null : …)),
  //    então "semear quando a chave não existe" re-aplicaria 150% por cima de
  //    quem escolheu 110%. Por isso a semeadura é registrada à parte: só
  //    acontece UMA vez, e a escolha do usuário vence depois disso.
  try {
    const CHAVE_TEXTO = 'hermes.desktop.chat-text-scale.v1';
    const JA_SEMEADO = 'hermes.desktop.webTextoPadraoSemeado.v1';
    if (localStorage.getItem(JA_SEMEADO) === null) {
      localStorage.setItem(JA_SEMEADO, '1');
      if (localStorage.getItem(CHAVE_TEXTO) === null) {
        localStorage.setItem(CHAVE_TEXTO, '150');
        log('escala do texto do chat semeada em 150% (era o padrão 110%)');
      }
    }
  } catch (e) {}

  // 3) zona de pega do divisor de painéis (o dedo não acerta 8px)
  try {
    instalarZonaDivisor();
  } catch (e) {}

  // 4) toque longo = selecionar texto + botão Copiar (o gesto nativo não funciona aqui)
  try {
    instalarSelecaoPorToque();
  } catch (e) { log('seleção por toque não instalou: ' + e); }

  // ── TEXTO: toque longo para selecionar e copiar ─────────────────────────
  // No Android, "segurar o dedo" é o gesto de selecionar texto — e nesta UI ele
  // NÃO seleciona nada: o shell do app desliga `user-select` no <body> e o gesto
  // acaba abrindo o menu de contexto do próprio app (o usuário relatou "aparece
  // aquela coisa de New session e nunca dá pra copiar"). Aqui o toque longo é
  // interpretado por nós: seleciona o parágrafo sob o dedo e oferece "Copiar"
  // num balão próprio, usando o writeClipboard que já copia de verdade.
  let selTimer = null, selAlvo = null, selBolha = null, selToque = null;

  function selecionavel(el) {
    while (el && el !== document.body) {
      if (/^(BUTTON|A|INPUT|TEXTAREA|SELECT|OPTION)$/.test(el.tagName)) return null;
      const cs = getComputedStyle(el);
      const us = cs.userSelect || cs.webkitUserSelect;
      if (us === 'text') return el;
      if (us === 'none' && el.nodeType === 1 && el !== document.body) {
        // continua subindo: um filho pode reabilitar (é o caso das mensagens)
      }
      el = el.parentElement;
    }
    return null;
  }

  function esconderBolha() {
    if (selBolha && selBolha.parentNode) selBolha.parentNode.removeChild(selBolha);
    selBolha = null;
  }

  function balaoCopiar(texto, x, y) {
    esconderBolha();
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = 'Copiar';
    b.setAttribute('aria-label', 'Copiar texto selecionado');
    b.style.cssText = [
      'position:fixed', 'z-index:2147483647', 'transform:translate(-50%,-140%)',
      'background:#1b1b21', 'color:#e6e6ea', 'border:1px solid #2c2c35',
      'border-radius:999px', 'padding:10px 18px', 'font:600 15px system-ui,sans-serif',
      'box-shadow:0 6px 20px rgba(0,0,0,.45)', 'touch-action:manipulation'
    ].join(';');
    b.style.left = Math.max(60, Math.min(window.innerWidth - 60, x)) + 'px';
    b.style.top = Math.max(60, y) + 'px';
    b.addEventListener('click', async (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      try {
        await bridge.writeClipboard(texto);
        b.textContent = 'Copiado';
        setTimeout(esconderBolha, 900);
      } catch (e) {
        b.textContent = 'Falhou';
        setTimeout(esconderBolha, 1200);
      }
    });
    document.body.appendChild(b);
    selBolha = b;
  }

  function selecionarNoToque(alvo, x, y) {
    if (!alvo) return;
    // não rouba seleção que o próprio navegador já fez (se um dia voltar a funcionar)
    const atual = (window.getSelection() || {}).toString().trim();
    if (atual.length > 2) return;
    try {
      const r = document.createRange();
      r.selectNodeContents(alvo);
      const s = window.getSelection();
      s.removeAllRanges();
      s.addRange(r);
      const texto = (s.toString() || '').trim();
      if (texto.length >= 3) balaoCopiar(texto, x, y);
    } catch (e) { log('seleção falhou: ' + e); }
  }

  function instalarSelecaoPorToque() {
    document.addEventListener('touchstart', (ev) => {
      if (ev.touches.length !== 1) return;
      const t = ev.touches[0];
      selToque = { x: t.clientX, y: t.clientY };
      selAlvo = selecionavel(ev.target);
      if (!selAlvo) { esconderBolha(); return; }
      clearTimeout(selTimer);
      selTimer = setTimeout(() => selecionarNoToque(selAlvo, selToque.x, selToque.y), 450);
    }, { passive: true, capture: true });

    const cancelar = () => { clearTimeout(selTimer); };
    document.addEventListener('touchend', cancelar, { passive: true, capture: true });
    document.addEventListener('touchcancel', cancelar, { passive: true, capture: true });
    document.addEventListener('touchmove', () => {
      cancelar();
      // arrastar o dedo depois do toque longo = ajustar a seleção: fecha o balão
      if (selBolha) esconderBolha();
    }, { passive: true, capture: true });
    // toque em qualquer outro lugar fecha o balão
    document.addEventListener('touchstart', (ev) => {
      if (selBolha && !selBolha.contains(ev.target)) esconderBolha();
    }, { passive: true, capture: true });

    // O MENU DE ABAS: segurar o dedo em qualquer texto abre o menu de contexto
    // da aba ("Reload / Close others / Close all…") — é o "New session" que o
    // usuário via. Ele nasce de pointerdown/long-press (Radix), não de
    // contextmenu, então barramos o pointerdown de toque em texto legível: o
    // app não vê o gesto e o menu não abre. Clique (mouse) segue intacto e os
    // botões da mensagem continuam funcionando (selecionavel() os exclui).
    document.addEventListener('pointerdown', (ev) => {
      if (ev.pointerType === 'mouse') return;
      if (selecionavel(ev.target)) ev.stopPropagation();
    }, true);

    // o menu de contexto do próprio app não pode abrir POR CIMA do balão quando
    // o toque longo já virou seleção nossa (era ele que aparecia antes de tudo)
    document.addEventListener('contextmenu', (ev) => {
      if (selBolha && selecionavel(ev.target)) {
        ev.stopImmediatePropagation();
        ev.preventDefault();
      }
    }, true);
  }

  log('ponte instalada → backend', BACKEND, '| perfil', PROFILE, '| token', TOKEN ? 'ok' : 'AUSENTE');
  emit(snapshot());
})();
