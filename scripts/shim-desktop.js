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
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: nome || '', ext: ext || '', data_b64: b64DeBytes(bytes) })
    });
    if (!r.ok) {
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
                            '&base=' + encodeURIComponent(base));
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
          catch (e) { log('anexo falhou: ' + ((e && e.message) || e)); }
        }
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
          method: 'POST', headers: { 'content-type': 'application/json' },
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

  const bridge = Object.assign(
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
    // store/quick-entry.ts:516 — janela de Quick Entry é só-Electron.
    quickEntry: {
      onSubmit: () => () => {},
      onDismiss: () => () => {},
      hide: async () => ({ ok: false }),
      show: async () => ({ ok: false })
    },
    onWindowStateChanged: () => () => {},
    onDeepLink: () => () => {},
    getVersion: async () => ({ version: '0.0.0-mobile', name: 'Hermes', releaseDate: '', platform: 'android' }),
    getMachineProfile: async () => ({ os: 'android', arch: 'arm64', platform: 'android', cpus: 8 }),
    logLine: noop,
    revealLogs: noopAsync,
    openExternal: (url) => { try { window.open(url, '_blank'); } catch (e) {} return noopAsync(); },
    dataUrlReadMaxLegacy: 0,
    updates: {
      check: async () => ({ ok: false }), apply: noopAsync, onEvent: () => () => {},
      // store/updates.ts:1345 chama isto SEM ?. — o main do Electron sempre expõe.
      onProgress: () => () => {}
    },
    windowControls: { minimize: noop, maximize: noop, close: noop, isMaximized: falseAsync, onMaximizedChanged: () => () => {} },
    themes: { list: async () => [], install: noopAsync, remove: noopAsync, onChanged: () => () => {} },
    // superfícies só-Electron: presentes mas inertes, para nunca dar TypeError
    terminal: {
      start: noopAsync, attach: noopAsync, write: noop, resize: noop, cwd: async () => null,
      onData: () => () => {}, onExit: () => () => {}, list: async () => [], kill: noopAsync
    },
    git: {
      status: async () => null, diff: async () => '', branches: async () => [],
      worktrees: async () => [], isRepo: falseAsync
    },
    selectPaths: async () => [],
    selectSavePath: async () => null,
    readDir: async () => [],
    readFileText: async () => '',
    readFileDataUrl: async () => null,
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
    pickDefaultProjectDir: async () => {
      const [dir] = await escolherPasta({ title: 'Pasta de trabalho' });
      return { canceled: !dir, dir: dir || null };
    },
    settings: Object.assign({}, ((window.__HERMES_STUBS__ || {}).settings) || {}, {
      getDefaultProjectDir: async () => ({ dir: '/data/data/com.termux/files/home' })
    }),
    readDir: async () => [],
    readFileText: async () => '',
    selectPaths: (options) => escolherArquivos(options),
    selectSavePath: async () => null,

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
    writeClipboard: (texto) => escreverClipboard(texto),

    // Colar um texto MUITO grande vira anexo .txt (igual ao desktop). Só entra
    // em jogo acima do limite de paste inline; se o servidor falhar, devolve
    // null e o app mantém o texto inline (caminho de degradação do próprio app).
    savePastedText: async (texto) => {
      try {
        const r = await fetch('/__save-text', {
          method: 'POST', headers: { 'content-type': 'application/json' },
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

  const faltando = new Set()
  window.hermesDesktop = new Proxy(bridge, {
    // Rede de segurança: qualquer método da ponte que eu não tenha previsto devolve
    // um stub universal (função que resolve em outro stub) em vez de dar TypeError.
    // E registra o nome: em build de produção o React não loga o erro capturado,
    // então a lista de chaves que o app PEDE e não existe é o único rastro do culpado.
    get(alvo, prop) {
      if (prop in alvo) return alvo[prop]
      if (typeof prop === 'symbol') return undefined
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

  log('ponte instalada → backend', BACKEND, '| perfil', PROFILE, '| token', TOKEN ? 'ok' : 'AUSENTE');
  emit(snapshot());
})();
