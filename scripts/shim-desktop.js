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
    requestMicrophoneAccess: falseAsync,
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
    // (o merge profundo preserva pickDefaultProjectDir/setDefaultProjectDir dos stubs)
    settings: {
      getDefaultProjectDir: async () => ({ dir: '/data/data/com.termux/files/home' })
    },
    // área de transferência de verdade: 127.0.0.1 é contexto seguro, então a
    // Clipboard API funciona (com a permissão/gesto que o Chrome exigir)
    readClipboard: async () => {
      try { return await navigator.clipboard.readText(); } catch (e) { return ''; }
    },
    writeClipboard: async (t) => {
      const texto = (t && typeof t === 'object' && 'text' in t) ? t.text : t;
      try { await navigator.clipboard.writeText(String(texto == null ? '' : texto)); } catch (e) {}
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

  log('ponte instalada → backend', BACKEND, '| perfil', PROFILE, '| token', TOKEN ? 'ok' : 'AUSENTE');
  emit(snapshot());
})();
