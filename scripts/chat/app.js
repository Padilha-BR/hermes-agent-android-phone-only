/* app.js — chat NATIVO do Hermes para celular.
 * Fala o protocolo JSON-RPC/WebSocket do gateway (o mesmo do app desktop):
 *   envio:  {"jsonrpc":"2.0","id":N,"method":M,"params":{...}}
 *   recebo: {"method":"event","params":{"type":...,"session_id":...,"payload":{...},"seq":N}}
 * Protocolo validado fora do navegador antes deste app existir.
 */
(() => {
  'use strict'

  const BACKEND = window.__HERMES_BACKEND__ || 'http://127.0.0.1:9119'
  const TOKEN = window.__HERMES_SESSION_TOKEN__ || ''
  const WS_URL = BACKEND.replace(/^http/, 'ws') + '/api/ws?token=' + encodeURIComponent(TOKEN)

  const $ = (id) => document.getElementById(id)
  const main = $('main'), txt = $('txt'), btEnviar = $('btEnviar'), ponto = $('ponto')
  const banner = $('banner'), sub = $('sub'), titulo = $('titulo'), lista = $('lista')
  const gaveta = $('gaveta'), infoModelo = $('infoModelo'), infoUso = $('infoUso')
  const chips = $('chips'), arquivo = $('arquivo'), btAnexo = $('btAnexo')

  let ws = null, seq = 0, pend = new Map(), sid = null, rodando = false
  let bolhaIA = null, bufIA = '', pensando = null, tentativas = 0, ultimoTexto = ''
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
      // href = $2 (a URL, que precisa ser http/https e não pode ter aspa);
      // texto = $1. Estava INVERTIDO: href recebia o label — link navegava pra
      // um caminho relativo aleatório e o label podia ser javascript:...
      h = h.replace(/\[([^\]]+)\]\((https?:[^)\s"'<>]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
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
    el.innerHTML = '<div class="quem">' + (quem === 'user' ? 'você' : 'hermes') + '</div>' +
      '<div class="bolha">' + html + '</div>' + (extra || '')
    if (quem === 'ia') {
      const acoes = document.createElement('div')
      acoes.className = 'acoes'
      const bc = document.createElement('button')
      bc.textContent = 'copiar'
      bc.onclick = async () => {
        try {
          await navigator.clipboard.writeText(el.querySelector('.bolha').innerText.trim())
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
      pend.set(id, { resolve, reject })
      ws.send(JSON.stringify({ jsonrpc: '2.0', id: id, method: metodo, params: params || {} }))
      setTimeout(() => { if (pend.has(id)) { pend.delete(id); reject(new Error('tempo esgotado: ' + metodo)) } }, 60000)
    })
  }

  function conectar() {
    ligar(false)
    sub.textContent = 'conectando…'
    try { ws = new WebSocket(WS_URL) } catch (e) { agendarReconexao(); return }

    ws.onopen = async () => {
      tentativas = 0
      ligar(true)
      bannerLimpar()
      try { await rpc('client.capabilities', { server_requests: true }) } catch (e) {}
      if (!sid) await novaConversa(true)
      else sub.textContent = tituloSessao || 'conectado'
      carregarLista()
    }

    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch (e) { return }
      if (msg.id && pend.has(msg.id)) {
        const { resolve, reject } = pend.get(msg.id)
        pend.delete(msg.id)
        if (msg.error) reject(new Error(msg.error.message || 'erro no gateway'))
        else resolve(msg.result)
        return
      }
      if (msg.method === 'event') tratarEvento(msg.params || {})
    }

    ws.onclose = () => { ligar(false, true); sub.textContent = 'reconectando…'; agendarReconexao() }
    ws.onerror = () => ligar(false, true)
  }

  function agendarReconexao() {
    tentativas++
    const espera = Math.min(15000, 800 * Math.pow(1.7, Math.min(tentativas, 6)))
    bannerMostrar('Sem conexão com o Hermes — tentando de novo…')
    setTimeout(conectar, espera)
  }

  // ── eventos do gateway ─────────────────────────────────────────────────────
  function tratarEvento(p) {
    const tipo = p.type, carga = p.payload || {}
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
        ultimoTexto = bufIA
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
        const alvo = bolhaIA.querySelector('.bolha')
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
    const alvo = bolhaIA.querySelector('.bolha')
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
      bolhaIA = null; bufIA = ''; ultimoTexto = ''; pensando = null; anexos = []
      tituloSessao = ''
      titulo.firstChild.textContent = 'Hermes'
      sub.textContent = 'conectado'
      main.innerHTML = '<div class="vazio">Nova conversa. Manda a primeira mensagem.</div>'
      if (r.info && r.info.model) infoModelo.textContent = r.info.model
      atualizarBotao(); renderChips()
      if (!silencioso) txt.focus()
    } catch (e) { bannerMostrar('Não consegui abrir conversa: ' + e.message) }
  }

  async function enviar() {
    const texto = txt.value.trim()
    if (!texto && !anexos.length) return
    if (rodando) return
    txt.value = ''; autoajustar()
    addMensagem('user', md(texto || '(imagem)'))
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
      const msgs = r.messages || []
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
      const ult = msgs.filter((m) => (m.role || '') === 'assistant').pop()
      if (ult) ultimoTexto = textoDe(ult)
      rolar(true)
    } catch (e) {
      main.innerHTML = '<div class="vazio">Não consegui carregar: ' + esc(e.message) + '</div>'
    }
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
          headers: { 'content-type': 'application/json', 'X-Hermes-Session-Token': TOKEN },
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
