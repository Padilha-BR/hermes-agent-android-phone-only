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
    settings: Object.assign({}, ((window.__HERMES_STUBS__ || {}).settings) || {}, {
      getDefaultProjectDir: async () => ({ dir: '/data/data/com.termux/files/home' })
    }),
    readDir: async () => [],
    readFileText: async () => '',
    selectPaths: async () => [],
    selectSavePath: async () => null,
    readClipboard: async () => '',
    writeClipboard: noopAsync
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
