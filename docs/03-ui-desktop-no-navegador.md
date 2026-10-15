# 03 — Rodar a UI do app desktop do Hermes num navegador (sem Electron)

> Portado do nosso caderno de campo. Tudo abaixo foi verificado no aparelho em 14/10/2026.


Alvo: ter a cara do app DESKTOP (não a do dashboard) numa superfície sem Electron
— navegador do celular, quiosque, qualquer host sem empacotador.

## Os dois UIs são coisas diferentes

| | o que é | backend |
|---|---|---|
| **Dashboard** (`web/` → `hermes_cli/web_dist`) | SPA administrativa, servida pelo próprio backend web | mesma origem, token injetado no HTML |
| **Desktop** (`apps/desktop`) | app React **próprio**, dentro de um shell Electron | `hermes serve` via JSON-RPC/WebSocket + REST |

O desktop **nunca** abre o dashboard: ele sobe o próprio backend e espera a ponte
de preload. É por isso que servir o bundle dele num navegador exige shim.

## Build (sem Electron, sem empacotador)

```bash
cd apps/desktop
npm install --ignore-scripts --no-audit --no-fund   # --ignore-scripts evita baixar o binário do Electron
nice -n 10 node ../../node_modules/vite/bin/vite.js build   # → apps/desktop/dist (~45 MB)
```

- **Não use `npm run build`**: ele chama `scripts/build.mjs` → `stage-native-deps`
  + `bundleElectronMain`, isto é, monta o app Electron inteiro. O único passo que
  interessa é o `vite.build` interno — rode o vite direto.
- Nenhuma env obrigatória; modo produção automático. Cache frio do React Compiler
  ~1m20; quente ~11s (vale reusar `node_modules/.cache/hermes-react-compiler`).
- O bundle referencia os assets como `./assets/...` — o servidor que for servi-lo
  precisa normalizar o `./` inicial, senão todo asset dá 404.

## Ponte a reimplementar (`window.hermesDesktop`)

Sem o objeto, o boot falha de propósito (`failDesktopBoot('Desktop IPC bridge is
unavailable.')`). Obrigatórios: `getConnection`, `getGatewayWsUrl`,
`getBootProgress`, `onBootProgress`, `onBackendExit` e `api` (transporte REST).
Todo o resto é lido com `?.` ou tem fallback — pode ficar de fora.

Descritor que `getConnection()` devolve (campos usados pelo boot):
`{ baseUrl, token, wsUrl, mode:'local', source:'local', authMode:'token', logs,
profile, connectionId }`. `getBootProgress` responde
`{error:null, fakeMode:false, message, phase, progress, running, timestamp}` —
ele só alimenta a tela de boot e o teste de "retryable"; não bloqueia.

`api(request)` = `fetch(baseUrl + request.path, ...)` com o header de sessão;
`request.upload` é `FormData` (deixe o navegador definir o content-type),
`request.body` é JSON. O WebSocket **não** passa pela ponte: o renderer faz
`new WebSocket(url)` direto.

## Autenticação e origem (o que dispensa proxy)

- REST: header `X-Hermes-Session-Token: <token de sessão>`.
- WebSocket: `ws(s)://<host>/api/ws?token=<mesmo token>` — responde
  `101 Switching Protocols`.
- **O backend reflete origem loopback no CORS**
  (`access-control-allow-origin: http://127.0.0.1:<outra porta>`) e aceita o WS
  vindo de outra origem loopback. Ou seja: dá para servir o bundle numa porta
  própria e apontar direto pro backend — **não precisa de proxy reverso**.
- O token de sessão em bind loopback é aleatório a cada start do backend e não
  aparece em nenhum arquivo: leia-o do HTML que o backend serve
  (`__HERMES_SESSION_TOKEN__`) a cada carregamento de página e injete.
- Um backend por host: não tente subir um segundo em outra porta para isolar a
  UI — reutilize o que está no ar.

## Servir com shim + telemetria (padrão para UI que você não vê)

O agente não vê a tela; então o servidor que serve a UI deve ser também o canal
de diagnóstico:

1. Injete no `<head>` do `index.html`, servido na hora: o backend, o token, o
   shim e um bloco de telemetria.
2. A telemetria escuta `error`, `unhandledrejection`, `console.error/warn` e
   manda por `navigator.sendBeacon('/__err', ...)` para o próprio servidor, que
   **redige o token antes de gravar** e escreve num log (`logs/desktop-web.log`).
3. Some um probe de DOM em 2-3 momentos (`document.title`, `location.hash`,
   contagem de nós, `body.innerText` cortado) — é o que distingue "tela branca
   por exceção" de "app renderizado mas vazio".
4. O shim é servido como arquivo separado (`/__shim.js`) para iterar sem
   reconstruir o bundle.

Padrão reutilizável para qualquer UI web que precise ser depurada sem ver
pixels: **telemetria > tentativa de adivinhar**. E o corolário: se o log do
servidor está vazio, o navegador não chegou — o problema é o lançamento, não o JS.

## O que fica inerte no navegador (por design)

PTY/terminal in-app, git local, diálogos nativos de arquivo, FS nativo,
multi-janela/HUD/pet, OAuth/cloud/SSH e instalador/updater. Chat, sessões, model
picker, config e cron servidos pelo backend funcionam. Torne essas superfícies
no-ops em vez de deixar `undefined` — assim uma chamada sem `?.` não vira
TypeError.

## Armadilhas do shim e da injeção (custaram bugs reais)

- **Stub universal não pode sequestrar conversão para primitivo.** A tentação é
  fazer a ponte devolver um stub para QUALQUER propriedade ausente (evita
  TypeError em cascata). Se esse stub responde também a `Symbol.toPrimitive`,
  `toString` e `valueOf`, todo lugar que faça `String(x)` num valor da ponte
  quebra com **`Cannot convert object to primitive value`** — erro que aparece
  longe da causa. Regra: no stub, `typeof prop === 'symbol'` → `undefined`, e
  `toString`/`valueOf`/`toJSON` → `undefined`; só o resto devolve stub.
- **Confira o estilo de formatação do template antes de mexer nas chaves.** Um
  template Python `%`-formatado trata `{` e `}` como literais; duplicar chaves
  (hábito de f-string) injeta `{{` no JS entregue ao navegador e quebra o
  script inteiro, silenciosamente do lado do agente. Depois de injetar, confira
  o HTML servido (`curl | grep -c '{{'` → 0).
- **`%` literal na injeção derruba o servidor SEM traceback.** O mesmo template
  `%`-formatado trata todo `%` solto como verbo de formatação: um CSS normal
  (`left:50%`, `translateX(-50%)`) faz `PONTE % {...}` estourar com
  `TypeError: not enough arguments for format string`. O sintoma engana: o cliente
  recebe `RemoteDisconnected` (conexão fechada sem resposta) e **o log não mostra
  exceção nenhuma** — parece servidor morto, não bug de template. Regra: todo `%`
  literal vira `%%`; e valide ANTES de abrir a página com
  `scripts/validar-injecao.py`, que reproduz a formatação fora do servidor e roda
  `node --check` no `<script>` resultante (foi o que isolou o erro em um comando,
  depois de várias rodadas de "a página não abre").
- **Recarga da página aberta: empurre, não pergunte.** `setInterval` de polling
  NÃO roda com a aba em segundo plano (o Chrome congela timers de aba inativa),
  então a página nunca se atualiza sozinha. Use um canal aberto (SSE):
  `EventSource('/__events')` na página + um endpoint que empurra `data: reload`
  quando o agente muda o shim. Rede não é congelada; timers são.

## Gere a ponte a partir do contrato (a regra que destravou)

Preencher `window.hermesDesktop` método por método **não converge**: cada chave
faltante devolve um stub que quebra o app de um jeito diferente, e você roda em
círculo. Gere a superfície inteira do contrato de tipos do próprio app:

```bash
python ~/.hermes/bin/gerar-shim-stubs.py    # lê apps/desktop/src/global.d.ts
# → ~/.hermes/bin/shim-stubs.js  (window.__HERMES_STUBS__, ~160 chaves)
```

O servidor serve isso como `/__stubs.js` **antes** do `/__shim.js`, e o shim usa
`Object.assign(window.__HERMES_STUBS__, { ...implementações reais })` — a base
gerada cobre o que você não pensou, suas implementações vencem no que importa.

### Regras de contrato que o gerador precisa respeitar

- **Inscrição (`on*`) devolve CANCELADOR, não Promise.** O app guarda o retorno e
  chama `cleanup?.()` — um stub `async` devolve Promise, que é objeto não
  chamável → `X is not a function` no cleanup. Gere `() => () => {}` para todo
  `on*` (e para retornos tipados como `Unsubscribe`).
- **Booleano é booleano, não função.** `glassSupported` / `translucencySupported`
  são `boolean` no contrato; como função são *sempre verdadeiras*, o app entra em
  caminho de vidro/translucidez que não existe e explode depois. Mesma classe:
  `dataUrlReadMax` é objeto `{get,set}`, não número.
- **O que o app destrutura precisa de forma real.** `getBootstrapState` →
  `{state, phase, running, log:[], error:null}` (sem `log` o app lê
  `r.log.length` e morre); `settings.getDefaultProjectDir` e
  `sanitizeWorkspaceCwd` → `{dir}` (senão vem o aviso "Failed to seed default
  workspace cwd"); `getVersion`/`getMachineProfile` → objeto, não string.
- **`localSkin` precisa existir** (pode ser `null`). Ausente, o stub faz
  `BOOT_PROFILE_KEY` virar Promise e o boot morre em
  `(e ?? "").trim is not a function`.
- **Parser de `global.d.ts` por indentação.** Procurar `nome:` em todo o texto
  captura **parâmetros** de assinatura (`(profile?: string, opts?: {...})`) como
  se fossem membros. Filtre pela indentação do nível (6 espaços no bloco,
  mais fundo nos aninhados) e deduplique.

### Diagnóstico do que ainda falta (quando algo quebra)

- Em build de produção o React **não loga** o erro que a boundary captura: a tela
do app mostra só a mensagem (`X is not a function`), sem pilha no console. Para
tirar a pilha: envolva `console.error/warn` na injeção (pega quando o app loga) e,
quando não pega, **leia o bundle minificado no `arquivo:linha:coluna` da pilha** —
imprima ~110 chars em volta do offset e a expressão culpada aparece literal
(`[r.log.length,a]`, `e?.()`). Foi assim que cada causa foi cravada, sem chute.
- Instrumente a ponte para **registrar toda chave pedida e ausente**
  (`console.info('[bridge-falta] ' + prop)` no `get` do Proxy). A lista, na ordem
de acesso, diz qual superfície o app tentou usar.
- Não persiga diferença de build: refazer o build pelo caminho oficial do repo
  (`configLoader:'runner'` + `publicDir`) sai **byte a byte idêntico**. O que
  faltava era a ponte.

## Layout no celular: viewport, altura e a barra lateral

A UI do desktop espera janela larga; no navegador do celular tudo depende de como
você a serve. Estes cinco pontos foram medidos, não deduzidos:

1. **Tamanho do texto = largura da viewport, ao contrário.** Injete
   `<meta name="viewport" content="width=N">`; o Chrome encolhe a UI pra caber,
   então **N menor = texto maior**. O app mantém a barra lateral na grade só
   acima de `SIDEBAR_DOCK_MIN_WIDTH_PX = 640` (`app/layout-constants.ts`).
   Medido: `N=1180` → texto ~40% do tamanho (ilegível), `N=900` → ~30% maior e a
   lateral dockada. Comando: `hermes-desktop-zoom <N>` (grava
   `~/.hermes/state/desktop-web-viewport`, que `hermes-desktop-up` lê).
2. **A barra lateral colapsa por media query, não por CSS.** O app decide isso em
   `components/pane-shell/tree/store.ts` (`$narrowViewport` → panes `collapsible`
   saem da grade e viram overlay de borda; o rótulo vertical "SESSIONS" é o
   `components/ui/pane-tab.tsx`). A query é `(max-width: 639.98px)`. Numa tela de
   celular essa avaliação oscila entre cargas; fixe a resposta na injeção:
   envolva `window.matchMedia` e devolva `matches = false` **só** para essa query.
3. **O composer cai fora da tela por causa da barra de URL.** O app dimensiona a
   raiz com `h-screen` = `100vh`, que o Chrome calcula como se a barra de URL não
   existisse (medido: `dvh`=2405 vs janela visível=2233), e ancora o composer na
   base dessa medida → ~180px abaixo do corte, sem rolagem (o shell tem
   `overflow:hidden`). Conserto: `.h-screen { height: 100svh !important }`
   (`svh` = viewport pequena, com a barra de URL = o que está na tela).
   **Classe exata, nunca curinga** — ver a regra 1 das armadilhas abaixo.
4. **NUNCA dispare `resize`.** O app reage a resize colapsando a barra lateral e
   **grava** esse estado; o usuário vê a lateral sumir e ela não volta sozinha.
5. **NUNCA zere o `localStorage` inteiro.** Isso apaga preferência de tema/fonte
   do usuário junto com o layout (erro cometido e cobrado). Zere só as chaves de
   layout — a lista exata está em `lib/layout-persistence.ts` (`LAYOUT_KEYS`):
   `paneStates.v1`, `layoutTree.v2`, `layoutPreset.active`, `dismissedPanes.v1`,
   `paneShare*.v1`, `hiddenStripTabs.v1`, `userPlacedPanes.v1`, `panesFlipped`,
   `collapsedTreeSides.v1`, `floatingPanes.v1`.

### Depurar layout sem ver a tela

- **Meça, não adivinhe**: injete um probe que devolva `getBoundingClientRect()`
  dos alvos (composer, lateral), a **cadeia de ancestrais** (tag/classe, altura,
  `overflow`, `position`, `style` inline) e as alturas do ambiente
  (`innerHeight`, `visualViewport.height`, `screen.height`). Foi a cadeia que
  mostrou `h-screen` = 2451 e o shell 164px maior que a janela.
- Um **pulso** a cada 10s (uma linha: nº de nós, largura da lateral, `y` do
  composer) dá sensor contínuo sem exigir uma recarga por medida.
- **`am start -a VIEW -d <mesma url>` numa aba já aberta só foca a aba** — não
  recarrega. Use URL com `?v=<timestamp>` (`hermes-desktop-reload`). E aba em
  segundo plano tem o JS **congelado**: o servidor entrega, a página não executa.
  Se a telemetria parou, é a aba, não o código — peça UM toque de recarga ao
  usuário em vez de repetir tentativas.
- **Canal de operação `POST /__cmd` `{"js": …}`** (SSE → `eval` → resultado no log)
  permite clicar e ler a página daqui. Vem **desligado**: liga com `HERMES_DESKTOP_CMD=1` (gera o
  segredo em `~/.hermes/state/desktop-cmd-token`, chmod 600) ou sozinho quando esse arquivo / a
  env `HERMES_DESKTOP_CMD_TOKEN` já existe. O token vai no header `X-Hermes-Cmd-Token` (ou no
  corpo; o GET antigo `?js=…&token=…` segue aceito); **sem token a resposta é 403**, e `Host`,
  `Origin` ou `Sec-Fetch-Site` de outro site também dão 403 — sem isso qualquer página aberta no
  navegador dispara o canal por GET (CSRF) ou lê o token por DNS rebinding, porque o loopback não
  é fronteira de confiança. O resultado do `eval` volta pelo `/__err` mesmo com a telemetria
  desligada; os probes/pulso é que são opt-in (`HERMES_WEB_TELEMETRY=1`, ou `=dom` para incluir
  o texto da página). Armadilha: **não esvazie a fila ao
  entregar** — uma conexão SSE morta de uma carga antiga fica na lista, "come" o
  comando e a página viva nunca recebe. Entregue a fila inteira a cada conexão e
  deixe a página descartar ids repetidos — **inclusive entre recargas** (a página guarda os ids
  vistos no `sessionStorage`), senão um `reload` antigo recarrega de novo em laço.
- **Recarga confiável tem duas portas**: `?v=<ts>` para carga fria e SSE
  (`/__bump`) para a aba viva; nenhuma funciona com o Termux em segundo plano
  (bloqueio de lançamento de atividade — ver a seção de intents no SKILL.md).

## Fidelidade visual: a fonte do wordmark vem embutida

O título "HERMES AGENT" é o **wordmark** (`components/chat/wordmark.tsx`,
classe `.wordmark`), renderizado na fonte **Collapse** com
`font-display: swap` e peso 700. `swap` significa: aparece a fonte de sistema e
só depois troca — se a requisição da fonte atrasa ou falha, a tela **fica** na
fonte de sistema e o usuário lê isso como "a fonte do app mudou".

Conserto que fecha o assunto (não depende de rede, cache nem corrida): embuta a
fonte na injeção como `@font-face` com **data URI** e `font-display: block` —
57 KB de woff2 viram ~79 KB de base64, irrelevante para uma página local. Um
`@font-face` declarado depois do CSS do app vence o do bundle para a mesma
família/peso. A injeção aceita **lista** de CSS (`HERMES_WEB_EXTRA_CSS` separado
por vírgula), então o arquivo de fontes é gerado à parte e somado ao de ajustes.

Gere esse CSS com `scripts/gerar-fontes-embutidas.py` e **rode de novo depois de
cada rebuild do bundle**: os nomes dos arquivos carregam hash de conteúdo
(`Collapse-Bold-<hash>.woff2`), então um CSS gerado à mão passa a apontar para
arquivo inexistente e a UI volta para a fonte de sistema — silenciosamente, que é
o pior jeito de descobrir.

Não persiga "a fonte mudou" como bug de código antes de checar estas duas coisas:

- **O texto da tela inicial é SORTEADO por carga.** `components/chat/intro-copy.jsonl`
  tem dezenas de variantes com um campo `personality` ("hype" traz título em
  CAIXA ALTA e tom exagerado; "none" é o sóbrio). A mesma URL mostra headline e
  corpo diferentes a cada recarga — é escolha do app, não regressão sua.
- A fonte de UI (`--font-sans`) é a pilha de sistema: no Android resolve Roboto,
  no Electron/Windows resolve Segoe UI. Essa diferença é esperada; a que precisa
  de conserto é a do wordmark.

## Instalar como app na tela inicial (PWA do porte)

O usuário pede isso naturalmente — em modo app não existe barra de URL, sobra
espaço vertical e o composer fica mais folgado. Requisitos que o Chrome cobra,
e como atender:

1. **Manifesto** (`/__manifest.webmanifest`, `application/manifest+json`) com
   `name`, `short_name`, `start_url`, `display: standalone` e ícones.
2. **Ícones com o tamanho REAL declarado.** Declarar `192x192`/`512x512` para um
   PNG de 1024 é mentira no manifesto — gere os tamanhos de verdade a partir do
   original (`PIL.Image.resize`, LANCZOS) e declare `purpose: any` / `any maskable`.
3. **Service worker** registrado pela injeção. Use um SW **sem cache**:
   `fetch` handler vazio deixa o navegador fazer a requisição normal. Um SW com
   cache serviria UI velha e você depuraria fantasma.
4. **Botão de instalação próprio.** O Chrome só oferece "Instalar app" no menu e
   às vezes só depois de um tempo; capture `beforeinstallprompt`, guarde o evento
   e mostre um botão que chama `prompt()` — o diálogo nativo abre na hora.
   Esconda o botão em `display-mode: standalone` e remova em `appinstalled`.
5. **`short_name` DISTINTO do app que já existe.** O dashboard também se chama
   "Hermes"; dois ícones com o mesmo nome na tela inicial confundem o usuário.

**Armadilha:** ao guardar o código do botão com `if (!instalado) { ... }`, nunca
use `return` dentro do IIFE da injeção — ele aborta o que vem depois (SSE, probes,
reset de layout) e a página perde a telemetria silenciosamente. Use um `if`.

## Estado da arte

**Verificado**: bundle builda; os 146 refs do `index.html` respondem 200; REST com
header 200 e WS com `?token=` 101 de outra origem loopback; **a UI desktop boota,
conecta no backend e renderiza a tela de nova sessão** (`NEW SESSION / HERMES
AGENT / … / Backend ready`), com o workspace carregado, zero erros e zero avisos
na telemetria.

**Layout resolvido**: com a viewport em 900 a barra lateral fica dockada (260px)
e o composer **dentro** da área visível (medido `y=1638` numa janela de `1703`;
antes `y=2386` numa janela de `2233`, ou seja, fora). O usuário ainda achou o
texto pequeno e fixou **800** — esse valor fica em
`~/.hermes/state/desktop-web-viewport`, sobrevive a reinício e é o que
`hermes-desktop-up` lê. Erros/avisos: zero. Fontes do wordmark embutidas e
manifesto/SW/ícones prontos para instalar como app.

**Resistência comprovada**: matar o servidor da 8890 de propósito → o watchdog o
ressuscita em ~25s, com 9119 e 8891 intactos.

Fica inerte por design (é navegador, não Electron): PTY/terminal in-app, git,
diálogos de arquivo, FS nativo, multi-janela/HUD/pet. Chat, sessões, config e
ferramentas funcionam.

Se o objetivo é só um chat bonito e leve no celular, a alternativa nativa
(protocolo do gateway) é mais simples que portar a UI do desktop — ver
`references/native-chat-protocol.md`.

Artefatos: `~/.hermes/bin/gerar-shim-stubs.py` (gera a ponte do contrato →
`~/.hermes/bin/shim-stubs.js`), `~/.hermes/bin/hermes-desktop-web.py` (servidor +
injeção, serve `/__stubs.js` antes de `/__shim.js`), `~/.hermes/bin/shim-desktop.js`
(ponte: base gerada + implementações reais), `hermes-desktop` /
`hermes-desktop-up` (comandos), `~/.hermes/scripts/dashboard-watchdog.sh` mantém a
porta viva. **Ao mexer na ponte, rode o gerador de novo** — ele é a fonte da
superfície, não o arquivo gerado.
