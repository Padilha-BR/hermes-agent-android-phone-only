/* app.js — chat NATIVO do Hermes para celular.
 * Fala o protocolo JSON-RPC/WebSocket do gateway (o mesmo do app desktop):
 *   envio:  {"jsonrpc":"2.0","id":N,"method":M,"params":{...}}
 *   recebo: {"method":"event","params":{"type":...,"session_id":...,"payload":{...},"seq":N}}
 * Protocolo validado fora do navegador antes deste app existir.
 */
(() => {
  'use strict'

  const BACKEND = window.__HERMES_BACKEND__ || 'http://127.0.0.1:9119'
  // o token MUDA quando o backend reinicia (o watchdog faz isso): não é const.
  // Depois de algumas falhas seguidas o app pede o atual em /__token.
  let TOKEN = window.__HERMES_SESSION_TOKEN__ || ''
  const wsUrl = () => BACKEND.replace(/^http/, 'ws') + '/api/ws?token=' + encodeURIComponent(TOKEN)

  const $ = (id) => document.getElementById(id)
  const main = $('main'), txt = $('txt'), btEnviar = $('btEnviar'), ponto = $('ponto')
  const banner = $('banner'), sub = $('sub'), titulo = $('titulo'), lista = $('lista')
  const gaveta = $('gaveta'), infoModelo = $('infoModelo'), infoUso = $('infoUso')
  const chips = $('chips'), arquivo = $('arquivo'), btAnexo = $('btAnexo')

  // sid = id VIVO (prompt.submit usa este); sidSalvo = id da conversa salva, o
  // que o session.resume aceita depois de uma reconexão/restart do backend.
  let ws = null, seq = 0, pend = new Map(), sid = null, sidSalvo = null, rodando = false
  let bolhaIA = null, bufIA = '', pensando = null, tentativas = 0
  let autoScroll = true, anexos = [], tituloSessao = ''

  // ── utilidades ─────────────────────────────────────────────────────────────
  // aspas também: a saída vira innerHTML e um " crua dentro de href="…" quebra
  // o atributo e deixa injetar event handler
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
  const perto = () => main.scrollHeight - main.scrollTop - main.clientHeight < 90

  function bannerMostrar(msg) { banner.textContent = msg; banner.classList.add('mostra') }
  function bannerLimpar() { banner.classList.remove('mostra') }

  function ligar(v, erro) {
    ponto.className = 'ponto' + (v ? ' on' : erro ? ' err' : '')
  }

  // ── markdown mínimo (sem biblioteca) ───────────────────────────────────────
  function md(texto) {
    const blocos = String(texto || '').split(/```/)
    return blocos.map((parte, i) => {
      if (i % 2 === 1) {
        const linhas = parte.replace(/^[a-zA-Z0-9+-]*\n/, '')
        return '<pre><code>' + esc(linhas) + '</code></pre>'
      }
      let h = esc(parte)
      h = h.replace(/`([^`]+)`/g, '<code>$1</code>')
      h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      h = h.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      // href = $2 (a URL, que precisa ser http(s):// e não pode ter aspa);
      // texto = $1. Estava INVERTIDO: href recebia o label — link navegava pra
      // um caminho relativo aleatório e o label podia ser javascript:...
      h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s"'<>]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      h = h.replace(/^### (.*)$/gm, '<h3>$1</h3>')
      h = h.replace(/^## (.*)$/gm, '<h2>$1</h2>')
      h = h.replace(/^# (.*)$/gm, '<h1>$1</h1>')
      const linhas = h.split('\n').map((l) => {
        if (/^\s*[-*] /.test(l)) return '<li>' + l.replace(/^\s*[-*] /, '') + '</li>'
        if (/^\s*\d+\. /.test(l)) return '<li>' + l.replace(/^\s*\d+\. /, '') + '</li>'
        return l
      }).join('\n')
      const comListas = linhas.replace(/(?:<li>[\s\S]*?<\/li>\n?)+/g, (m) => '<ul>' + m + '</ul>')
      return comListas.split(/\n{2,}/).map((p) => (/^\s*<(h\d|ul|pre|li)/.test(p) ? p : '<p>' + p.replace(/\n/g, '<br>') + '</p>')).join('')
    }).join('')
  }

  function rolar(final) {
    if (final || autoScroll) main.scrollTop = main.scrollHeight
  }

  function limparVazio() { const v = $('vazio'); if (v) v.remove() }

  function addMensagem(quem, html, extra) {
    limparVazio()
    const el = document.createElement('div')
    el.className = 'msg ' + quem
    // .ferramentas e .texto separados: o streaming reescreve só o .texto, então as
    // marcas de ferramenta (⚙ / ✓) não somem a cada delta
    el.innerHTML = '<div class="quem">' + (quem === 'user' ? 'você' : 'hermes') + '</div>' +
      '<div class="bolha"><div class="ferramentas"></div><div class="texto">' + html + '</div></div>' + (extra || '')
    if (quem === 'ia') {
      const acoes = document.createElement('div')
      acoes.className = 'acoes'
      const bc = document.createElement('button')
      bc.textContent = 'copiar'
      bc.onclick = async () => {
        try {
          await navigator.clipboard.writeText(el.querySelector('.bolha .texto').innerText.trim())
          bc.textContent = 'copiado'
          setTimeout(() => { bc.textContent = 'copiar' }, 1400)
        } catch (e) { bc.textContent = 'falhou' }
      }
      acoes.appendChild(bc)
      el.appendChild(acoes)
    }
    main.appendChild(el)
    rolar()
    return el
  }

  // ── WebSocket + JSON-RPC ───────────────────────────────────────────────────
  function rpc(metodo, params) {
    return new Promise((resolve, reject) => {
      if (!ws || ws.readyState !== 1) return reject(new Error('sem conexão'))
      const id = ++seq
      const timer = setTimeout(() => { if (pend.has(id)) { pend.delete(id); reject(new Error('tempo esgotado: ' + metodo)) } }, 60000)
      pend.set(id, { resolve, reject, timer })
      ws.send(JSON.stringify({ jsonrpc: '2.0', id: id, method: metodo, params: params || {} }))
    })
  }

  // conexão caiu: quem esperava resposta nunca vai receber — rejeita JÁ (antes
  // ficavam penduradas 60 s e o botão continuava em "parar")
  function derrubarPendentes(motivo) {
    pend.forEach(({ reject, timer }) => { clearTimeout(timer); try { reject(new Error(motivo)) } catch (e) {} })
    pend.clear()
    if (pensando) { pensando.remove(); pensando = null }
    if (bolhaIA) { pintarIA(false); bolhaIA = null }
    bufIA = ''
    rodando = false
    atualizarBotao()
  }

  function conectar() {
    ligar(false)
    sub.textContent = 'conectando…'
    let meu
    try { meu = new WebSocket(wsUrl()) } catch (e) { agendarReconexao(); return }
    ws = meu

    meu.onopen = async () => {
      // NÃO zera `tentativas` aqui: se o gateway aceita o upgrade e fecha por
      // token inválido, o contador nunca chegava a 3 e o renovarToken() não
      // rodava. Só conta como conexão boa depois que o 1º RPC responde.
      ligar(true)
      bannerLimpar()
      try {
        await rpc('client.capabilities', { server_requests: true })
        tentativas = 0
      } catch (e) {
        // erro DO gateway (método recusado etc.) com o socket ainda aberto também
        // prova que a conexão está de pé; rejeição por queda não zera
        if (ws === meu && meu.readyState === WebSocket.OPEN) tentativas = 0
      }
      // reconexão: o sid vivo pode ter sido recolhido pelo gateway (ou o backend
      // reiniciou) — retoma pela conversa SALVA em vez de mandar no sid velho
      if (!sid) await novaConversa(true)
      else await retomar(true)
      carregarLista()
    }

    meu.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch (e) { return }
      if (msg.id && pend.has(msg.id)) {
        const { resolve, reject, timer } = pend.get(msg.id)
        clearTimeout(timer)
        pend.delete(msg.id)
        if (msg.error) reject(new Error(msg.error.message || 'erro no gateway'))
        else resolve(msg.result)
        return
      }
      if (msg.method === 'event') tratarEvento(msg.params || {})
    }

    meu.onclose = () => {
      if (ws !== meu) return // socket antigo, já substituído
      ligar(false, true); sub.textContent = 'reconectando…'
      derrubarPendentes('conexão caiu')
      agendarReconexao()
    }
    meu.onerror = () => ligar(false, true)
  }

  function agendarReconexao() {
    tentativas++
    const espera = Math.min(15000, 800 * Math.pow(1.7, Math.min(tentativas, 6)))
    bannerMostrar('Sem conexão com o Hermes — tentando de novo…')
    // backend reiniciado = token novo; o velho falha para sempre. A partir da 3ª
    // falha seguida, pergunta o token atual ao servidor que serviu a página.
    if (tentativas >= 3) renovarToken()
    setTimeout(conectar, espera)
  }

  async function renovarToken() {
    try {
      const r = await fetch('/__token', { cache: 'no-store', credentials: 'same-origin' })
      if (r.status === 404) {
        // servidor antigo, sem /__token: recarregar a página traz o token novo no
        // HTML. No máximo uma vez a cada 5 min, para não virar laço.
        let ult = 0
        try { ult = Number(sessionStorage.getItem('__hermes_chat_reload') || 0) } catch (e) {}
        if (Date.now() - ult > 300000) {
          try { sessionStorage.setItem('__hermes_chat_reload', String(Date.now())) } catch (e) {}
          location.reload()
        }
        return
      }
      if (!r.ok) return // backend ainda fora (503): tenta de novo na próxima falha
      const j = await r.json()
      if (j && j.token && j.token !== TOKEN) { TOKEN = j.token; tentativas = 0 }
    } catch (e) { /* servidor da página fora também: a próxima tentativa refaz */ }
  }

  // ── eventos do gateway ─────────────────────────────────────────────────────
  // eventos globais (não pertencem a uma conversa)
  const GLOBAIS = new Set(['gateway.ready', 'sessions.changed'])

  function tratarEvento(p) {
    const tipo = p.type, carga = p.payload || {}
    // filtra por conversa: streaming de uma sessão antiga (outra aba, conversa
    // trocada no meio do turno) não pode cair na conversa aberta
    if (p.session_id && sid && !GLOBAIS.has(tipo) && p.session_id !== sid && p.session_id !== sidSalvo) return
    switch (tipo) {
      case 'gateway.ready':
        aplicarSkin(carga.skin)
        break
      case 'session.info':
        if (carga.model) infoModelo.textContent = carga.model + (carga.provider ? ' · ' + carga.provider : '')
        break
      case 'session.title':
        tituloSessao = carga.title || ''
        if (tituloSessao) { titulo.firstChild.textContent = tituloSessao; sub.textContent = 'conectado' }
        break
      case 'message.start':
        bolhaIA = addMensagem('ia', '<span class="cursor"></span>')
        bufIA = ''
        break
      case 'thinking.delta':
        if (!bufIA && carga.text) {
          if (!pensando) pensando = addMensagem('ia', '<span class="pensando">pensando…</span>')
        }
        break
      case 'message.delta':
        if (!bolhaIA) bolhaIA = addMensagem('ia', '')
        if (pensando) { pensando.remove(); pensando = null }
        bufIA += (carga.text || '')
        pintarIA()
        break
      case 'message.complete':
        if (pensando) { pensando.remove(); pensando = null }
        bufIA = carga.text || bufIA
        if (!bolhaIA) bolhaIA = addMensagem('ia', '')
        pintarIA(false)
        if (carga.usage) mostrarUso(carga.usage)
        rodando = false
        atualizarBotao()
        bolhaIA = null
        break
      case 'tool.start':
      case 'tool.generating': {
        if (!bolhaIA) bolhaIA = addMensagem('ia', '')
        const nome = carga.name || carga.tool || 'ferramenta'
        const alvo = bolhaIA.querySelector('.bolha .ferramentas')
        if (!alvo.querySelector('.ferramenta:last-child') || alvo.dataset.ultimaFerramenta !== nome) {
          const f = document.createElement('div')
          f.className = 'ferramenta'
          f.innerHTML = '<span class="tag">⚙ ' + esc(nome) + '</span>'
          alvo.appendChild(f)
          alvo.dataset.ultimaFerramenta = nome
          rolar()
        }
        break
      }
      case 'tool.complete': {
        if (bolhaIA) {
          const tags = bolhaIA.querySelectorAll('.ferramenta .tag')
          const ult = tags[tags.length - 1]
          if (ult && !ult.classList.contains('ok')) {
            ult.classList.add('ok')
            ult.textContent = '✓ ' + ult.textContent.replace('⚙ ', '')
          }
        }
        break
      }
      case 'session.usage':
        mostrarUso(carga)
        break
      case 'sessions.changed':
        if (gaveta.classList.contains('aberta')) carregarLista()
        break
      case 'session.reclaimed':
        // o gateway recolheu a sessão viva (ex.: ws_orphan_reap): o sid não vale
        // mais; retoma pela conversa salva antes do próximo envio
        if (ws && ws.readyState === 1) retomar(true)
        break
      case 'error':
        bannerMostrar('Erro do Hermes: ' + (carga.message || carga.error || JSON.stringify(carga)))
        rodando = false
        atualizarBotao()
        break
      default:
        break
    }
  }

  function pintarIA(final) {
    if (!bolhaIA) return
    const alvo = bolhaIA.querySelector('.bolha .texto')
    alvo.innerHTML = md(bufIA) + (final === false ? '' : '<span class="cursor"></span>')
    rolar()
  }

  function mostrarUso(u) {
    const partes = []
    if (u.model) partes.push(u.model)
    if (u.total) partes.push(u.total + ' tokens')
    if (u.context_percent) partes.push('contexto ' + u.context_percent + '%')
    if (partes.length) infoUso.textContent = partes.join(' · ')
  }

  function aplicarSkin(skin) {
    if (!skin || !skin.colors) return
    const c = skin.colors
    const raiz = document.documentElement.style
    if (c.ui_accent) raiz.setProperty('--acento', c.ui_accent)
    if (c.ui_error) raiz.setProperty('--erro', c.ui_error)
    if (c.ui_ok) raiz.setProperty('--ok', c.ui_ok)
  }

  // ── ações ──────────────────────────────────────────────────────────────────
  async function novaConversa(silencioso) {
    try {
      const r = await rpc('session.create', {})
      sid = r.session_id || r.id
      sidSalvo = r.stored_session_id || sid
      bolhaIA = null; bufIA = ''; pensando = null; anexos = []
      tituloSessao = ''
      titulo.firstChild.textContent = 'Hermes'
      sub.textContent = 'conectado'
      main.innerHTML = '<div class="vazio">Nova conversa. Manda a primeira mensagem.</div>'
      if (r.info && r.info.model) infoModelo.textContent = r.info.model
      atualizarBotao(); renderChips()
      if (!silencioso) txt.focus()
    } catch (e) { bannerMostrar('Não consegui abrir conversa: ' + e.message) }
  }

  // Reabre a conversa atual pelo id SALVO (session.resume devolve o id vivo novo e
  // o histórico). Usado na reconexão e no session.reclaimed. Se o gateway não
  // conhecer a conversa (nova e ainda vazia, por exemplo), abre outra.
  async function retomar(redesenhar) {
    const alvo = sidSalvo || sid
    if (!alvo) return novaConversa(true)
    try {
      const r = await rpc('session.resume', { session_id: alvo, source: 'desktop' })
      sid = (r && r.session_id) || sid
      if (redesenhar && r && Array.isArray(r.messages) && r.messages.length && !rodando) {
        renderHistorico(r.messages)
      }
      sub.textContent = tituloSessao || 'conectado'
    } catch (e) {
      bannerMostrar('Não consegui retomar a conversa (' + e.message + ') — abri uma nova.')
      await novaConversa(true)
    }
  }

  async function enviar() {
    const texto = txt.value.trim()
    if (!texto && !anexos.length) return
    if (rodando) return
    txt.value = ''; autoajustar()
    const bolhaUser = addMensagem('user', md(texto || '(imagem)'))
    const anexosEnviar = anexos.slice()
    anexos = []; renderChips()
    rodando = true; atualizarBotao(); bolhaIA = null; bufIA = ''
    try {
      for (const a of anexosEnviar) {
        try { await rpc('image.attach', { session_id: sid, path: a.path }) }
        catch (e) { bannerMostrar('Falha ao anexar imagem: ' + e.message) }
      }
      await rpc('prompt.submit', { session_id: sid, text: texto || 'Olhe a imagem anexada.' })
    } catch (e) {
      bannerMostrar('Falha ao enviar: ' + e.message)
      rodando = false; atualizarBotao()
      // não chegou ao gateway: devolve o texto ao composer (antes ele sumia) e os
      // anexos aos chips, e tira a bolha que fingia ter enviado
      if (!txt.value) { txt.value = texto; autoajustar() }
      if (anexosEnviar.length && !anexos.length) { anexos = anexosEnviar; renderChips() }
      bolhaUser.remove()
    }
  }

  async function parar() {
    try { await rpc('session.interrupt', { session_id: sid }); bannerMostrar('Turno interrompido.') }
    catch (e) { bannerMostrar('Não consegui parar: ' + e.message) }
    rodando = false; atualizarBotao()
  }

  function atualizarBotao() {
    btEnviar.textContent = rodando ? '■' : '➤'
    btEnviar.setAttribute('aria-label', rodando ? 'Parar' : 'Enviar')
    btEnviar.classList.toggle('primario', !rodando)
  }

  async function carregarLista() {
    try {
      const r = await rpc('session.list', { limit: 60 })
      const sessoes = (r && r.sessions) || []
      lista.innerHTML = sessoes.length ? '' : '<div class="vazio">Nenhuma conversa salva.</div>'
      sessoes.forEach((s) => {
        const el = document.createElement('div')
        el.className = 'item' + (s.id === sid ? ' atual' : '')
        el.innerHTML = '<div class="t">' + esc(s.title || '(sem título)') + '</div>' +
          '<div class="s">' + esc(s.preview || '') + '</div>'
        el.onclick = () => { gaveta.classList.remove('aberta'); abrirSessao(s) }
        lista.appendChild(el)
      })
    } catch (e) { /* silencioso: lista é acessório */ }
  }

  async function abrirSessao(s) {
    const id = s.resolved_id || s.id
    tituloSessao = s.title || ''
    titulo.firstChild.textContent = tituloSessao || 'Hermes'
    main.innerHTML = '<div class="vazio">Carregando conversa…</div>'
    try {
      // session.resume é o método que abre conversa SALVA (activate/history exigem sessão viva).
      // Ele devolve o id vivo + o histórico; a partir daí o prompt.submit usa esse id novo.
      const r = await rpc('session.resume', { session_id: id, source: 'desktop' })
      sid = r.session_id || id
      sidSalvo = id
      renderHistorico(r.messages || [])
    } catch (e) {
      main.innerHTML = '<div class="vazio">Não consegui carregar: ' + esc(e.message) + '</div>'
    }
  }

  function renderHistorico(msgs) {
    main.innerHTML = ''
    if (!msgs.length) main.innerHTML = '<div class="vazio">Conversa vazia.</div>'
    msgs.forEach((m) => {
      const papel = (m.role || m.type || '').toLowerCase()
      const corpo = textoDe(m)
      if (!corpo) return
      if (papel === 'user') addMensagem('user', md(corpo))
      else if (papel === 'assistant' || papel === 'ai') addMensagem('ia', md(corpo))
    })
    bolhaIA = null; bufIA = ''
    rolar(true)
  }

  function textoDe(m) {
    if (typeof m.text === 'string') return m.text
    if (typeof m.content === 'string') return m.content
    if (Array.isArray(m.content)) return m.content.map((c) => (typeof c === 'string' ? c : c.text || '')).join('')
    return ''
  }

  // ── anexos ─────────────────────────────────────────────────────────────────
  function renderChips() {
    chips.innerHTML = ''
    anexos.forEach((a, i) => {
      const el = document.createElement('div')
      el.className = 'chip'
      el.innerHTML = '<span>🖼 ' + esc(a.nome) + '</span>'
      const b = document.createElement('button')
      b.textContent = '×'
      b.onclick = () => { anexos.splice(i, 1); renderChips() }
      el.appendChild(b)
      chips.appendChild(el)
    })
  }

  btAnexo.onclick = () => arquivo.click()
  arquivo.onchange = () => {
    const f = arquivo.files && arquivo.files[0]
    arquivo.value = ''
    if (!f) return
    const leitor = new FileReader()
    leitor.onload = async () => {
      try {
        const r = await fetch(BACKEND + '/api/chat/image-upload', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'X-Hermes-Session-Token': TOKEN }, // TOKEN atual (pode ter sido renovado)
          body: JSON.stringify({ data_url: leitor.result, filename: f.name || 'foto.jpg' })
        })
        if (!r.ok) throw new Error('HTTP ' + r.status)
        const j = await r.json()
        if (!j.path) throw new Error('resposta sem caminho')
        anexos.push({ path: j.path, nome: f.name || 'foto.jpg' })
        renderChips()
      } catch (e) { bannerMostrar('Não consegui subir a imagem: ' + e.message) }
    }
    leitor.readAsDataURL(f)
  }

  // ── composer ───────────────────────────────────────────────────────────────
  function autoajustar() {
    txt.style.height = 'auto'
    txt.style.height = Math.min(txt.scrollHeight, Math.round(window.innerHeight * 0.34)) + 'px'
  }
  txt.addEventListener('input', autoajustar)
  txt.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && window.innerWidth > 700) { e.preventDefault(); enviar() }
  })
  btEnviar.onclick = () => (rodando ? parar() : enviar())
  $('btNovo').onclick = () => novaConversa()
  $('btMenu').onclick = () => { gaveta.classList.add('aberta'); carregarLista() }
  gaveta.onclick = (e) => { if (e.target === gaveta) gaveta.classList.remove('aberta') }

  main.addEventListener('scroll', () => { autoScroll = perto() })

  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => { if (autoScroll) rolar(true) })
  }

  if (!TOKEN) {
    bannerMostrar('Sem token de sessão — abra esta página pelo comando hermes-chat.')
  }
  autoajustar()
  conectar()
})()
