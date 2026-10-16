# scripts/ — o que é cada arquivo

Estes são os scripts que rodam **no telefone**, em `~/.hermes/` (o `HERMES_HOME` do Termux).
A instalação é o `install.sh` da raiz (idempotente — rode de novo depois de cada `git pull`):
`.sh`/`.mjs` → `~/.hermes/scripts/` (chamados pelo watchdog), comandos/`.py`/shims →
`~/.hermes/bin/` (no `PATH` pelo `~/.bashrc`; `install.sh --bashrc` acrescenta a linha),
`chat/` → `~/.hermes/chat/`, `shortcuts/` → `~/.shortcuts/` e `10-hermes-dashboard.sh` →
`~/.termux/boot/`. A versão anterior de um arquivo trocado vai para
`~/.hermes/backup/install/<caminho>.<data>.bak` (0600) — nunca ao lado do original, porque
um `.bak` em `~/.termux/boot/` rodaria no boot. Nada aqui contém credencial — chaves
ficam no arquivo de ambiente do `HERMES_HOME`, com `chmod 600`.

> `$PREFIX/bin` é protegido para escrita pelo agente no Termux: escreva o script em
> `~/.hermes/bin` e crie o link com `ln -s`, senão a escrita é recusada.

> ⚠️ **Os `.py` que falam com o Hermes rodam com o python do venv**, não com o `python3` do
> sistema: `~/hermes-agent/venv/bin/python`. Eles importam `websockets` e outras dependências que só
> existem no venv — com o `python3` do Termux o erro é
> `ModuleNotFoundError: No module named 'websockets'`, que parece script quebrado e é só
> interpretador errado. Vale para `verify-native-chat.py`, `testar-chat-*.py`, `hermes-desktop-web.py`
> e `validar-injecao.py`. Já os `.sh` são do Termux e rodam com `bash`.

> ⚠️ **Nunca ponha o injetor em `0.0.0.0`.** O `hermes-desktop-web.py` fixa
> `ThreadingHTTPServer(("127.0.0.1", PORT), …)` e injeta o token da sessão no HTML que serve —
> em loopback isso é conveniente e seguro, porque só o próprio aparelho alcança. Fora do loopback
> o token do HTML **é** o login, e não existe segundo fator. Se precisar de acesso remoto, use
> túnel/autenticação de verdade na frente, nunca este servidor.

> ⚠️ **Loopback não é fronteira de confiança no Android.** Três vetores alcançam estas portas:
> sites abertos no Chrome do próprio celular (requisição GET simples = CSRF), DNS rebinding
> (um domínio que passa a resolver para `127.0.0.1`) e qualquer outro app com permissão
> INTERNET. Por isso o injetor e o seletor de fotos respondem **403** a `Host` que não seja
> `127.0.0.1:<porta>`/`localhost:<porta>` e recusam `Origin`/`Sec-Fetch-Site` de outro site nas
> rotas que mudam estado (`/__cmd`, `/__bump`, `/__err`, `/__token`, `/choose`). Contra o terceiro
> vetor (app local) só segredo ajuda — é o que o token do `/__cmd` e o do seletor fazem.

> 🔑 **O canal de operação `/__cmd` vem DESLIGADO** (ele executa JS arbitrário com `eval` na
> página aberta, que carrega o token da sessão). Liga com `HERMES_DESKTOP_CMD=1` no ambiente de
> quem sobe o servidor — o segredo é gerado em `~/.hermes/state/desktop-cmd-token` (0600) se
> ainda não existir — ou automaticamente quando esse arquivo (ou a env
> `HERMES_DESKTOP_CMD_TOKEN`) já existe, que é o fluxo antigo. `HERMES_DESKTOP_CMD=0` desliga
> sempre. Desligado, a página nem tem o ramo do `eval`. O token **não** vai mais para o log.
> Uso (POST, preferido):
> `curl -s http://127.0.0.1:8890/__cmd -H 'content-type: application/json'
> -H "X-Hermes-Cmd-Token: $(cat ~/.hermes/state/desktop-cmd-token)" -d '{"js":"document.title"}'`.
> O GET antigo (`-G --data-urlencode "js=…" --data-urlencode "token=…"`) continua aceito.
> `HERMES_DESKTOP_CMD_INSECURE=1` liga sem token — não use.

> 📝 **Telemetria é opt-in** (`HERMES_WEB_TELEMETRY`): sem a env, o log recebe só erros do
> navegador (`error`, `unhandledrejection`, `console.error/warn`). `=1` soma geometria, pulso de
> 10 s e as **chaves** do `localStorage`; `=dom` soma o **texto da página** e os valores do
> `localStorage` (é trecho de conversa — use só para depurar a UI do desktop). O chat (8891) sobe
> sempre com telemetria desligada. O log gira sozinho em 2 MB (`HERMES_WEB_LOG_MAX`).
> Os remendos do porte (`Object.prototype.trim`, `matchMedia` fixado, reset das chaves de
> layout) só entram na UI do desktop (`HERMES_WEB_DESKTOP_FIXES=1`, padrão só na 8890).

## Superfícies (subir e manter de pé)

| Arquivo | O que faz |
|---|---|
| `dashboard-up.sh` | Idempotente. Se `/api/health` responde, sai. Senão sobe o dashboard com `--skip-build`, **reaplica o patch PWA** se faltar e cai para build completo se o skip falhar. Serializado por `flock` (`state/dashboard-up.lock`): watchdog e widget não se atropelam mais. Build completo que falha entra em backoff (5→10→20→40 min, teto 1 h, estado em `state/dashboard-build-falhas`); `HERMES_FORCE_BUILD=1` ignora o backoff. |
| `dashboard-watchdog.sh` | Laço de 60 s chamando `dashboard-up.sh`, guarda de instância única por `flock` (`state/dashboard-watchdog.lock`) com o pidfile (pid + starttime) como reserva e para reconhecer um watchdog da versão anterior, segurando `termux-wake-lock` — e o devolvendo no trap de saída. Quando o `dashboard-up.sh` falha, espaça as tentativas (2→4→8→15 min). No mesmo laço mantém a UI do desktop (8890), o chat (8891, pelo `hermes-chat-up`), o `sshd` do Termux (8022 — só se o openssh estiver instalado **e** configurado de propósito: um `*.conf` em `$PREFIX/etc/ssh/sshd_config.d/` ou `HERMES_WATCHDOG_SSHD=1`; `=0` desliga) e o Chromium com CDP (9222, motor do `browser_exec` — só se `chrome-cdp` e o `chromium-browser` estiverem instalados). Rode-o desgarrado (`setsid`) para ele virar PPid 1 e sobreviver à sessão do agente. |
| `10-hermes-dashboard.sh` | Script de boot (`~/.termux/boot/`, via app **Termux:Boot**): espera 20 s e sobe o watchdog. **Um só** script de boot — duplicados disputam a mesma porta. |
| `hermes-app` | Sobe o backend e abre **o app dele**: o ícone "Hermes" da tela inicial é um WebAPK apontando para a UI portada em 8890, não para o dashboard. Abrir a 9119 como "o app" joga o usuário numa aba nova do Chrome, no painel errado — quem abre o certo é o `hermes-voltar`. |
| `hermes-voltar` | Acha o WebAPK da tela inicial **pela porta do app** (o hash do pacote muda se ele reinstalar o atalho) e abre por componente explícito, que é o único jeito: WebAPK não abre por pacote. `status` mostra o alvo. |
| `hermes-desktop-web.py` | O injetor: servidor stdlib que serve um SPA local e injeta (a) o token da sessão, (b) o shim da ponte, (c) captura de erro em `~/.hermes/logs/desktop-web.log` (+ telemetria de DOM, **opt-in**: `HERMES_WEB_TELEMETRY` ou `HERMES_WEB_TELEMETRIA`; o log de acesso só sai com `HERMES_WEB_DEBUG=1`, sempre sem query) com rotação própria, (d) canal **SSE** `/__events` — um POST (ou GET) em `/__bump` empurra `location.reload()` para as páginas abertas, o que funciona com a aba em segundo plano (o Chrome congela `setInterval` de aba inativa), (e) `/__token`, que devolve o token **atual** do backend para a página se recuperar quando o dashboard reinicia. Reutilizável: `HERMES_DESKTOP_WEB=<dir> HERMES_DESKTOP_WEB_PORT=<porta>` serve qualquer SPA local; uma página que traga `<meta http-equiv="Content-Security-Policy">` com `__HERMES_NONCE__` recebe nonce novo por resposta (é o caso do chat). Serve também os endpoints que a ponte usa: `/__zoom` (persiste a escala da UI escolhida no app), `/__upload` (recebe o anexo em base64 e devolve o **caminho** em disco), `/__file` (serve o anexo para a miniatura — só dentro de `$HOME`), `/__save-text` (o paste gigante vira anexo `.txt`) e `/__resolver-pasta`. Todos exigem o token da sessão no header `X-Hermes-Session-Token` (o shim manda) e os POST, a mesma origem. A meta de viewport sai com `interactive-widget=resizes-content`: sem isso o teclado virtual cobre o composer (Chrome 108+ não redimensiona mais o layout viewport sozinho). Serve ainda `/__sw.js`: service worker do app instalado, **sem cache de UI** — intercepta só a navegação e, quando o fetch falha (o caso normal nos primeiros minutos depois de reiniciar o aparelho, com o servidor ainda subindo), devolve uma página de resgate que se recarrega sozinha assim que ele responder. |
| `hermes-desktop-up` / `hermes-desktop` | Sobe e abre a UI do desktop portada (8890). |
| `hermes-desktop-zoom` | Escolhe o tamanho do texto da UI portada. O tamanho é o **inverso** da largura de viewport injetada (`width=N`; N menor = texto maior). Fica gravado em `~/.hermes/state/desktop-web-viewport` (sem arquivo, o `hermes-desktop-up` cai no 900). |
| `hermes-desktop-reload` | Recarrega a UI de forma confiável (abrir a mesma URL numa aba já aberta só foca a aba). |
| `hermes-chat` | Abre a superfície de chat **nativa** (8891), que fala o protocolo WebSocket do gateway. |
| `hermes-chat-up` | Sobe o injetor servindo a SPA do chat nativo (`~/.hermes/chat`), sem remendos do desktop e sem telemetria. Separado do `hermes-chat` para o watchdog poder chamá-lo. |
| `chrome-cdp` | Sobe/mata/consulta o Chromium nativo do Termux com CDP em 9222 (com UA de Chrome normal: o UA do headless faz o Google devolver captcha). É o motor do `browser_exec` e dos medidores deste repo. O CDP escuta **só em 127.0.0.1** — e, como todo loopback no Android, qualquer app do aparelho o alcança: CDP é controle total do navegador (cookies e sessões do perfil dele). |
| `build-desktop-web.mjs` | Build do renderer do desktop sem Electron (chama o Vite direto). **Nunca** use `npm run build`, que monta o pacote Electron. Sai em `~/hermes-agent/.hermes-port/renderer` — o mesmo diretório que o `hermes-desktop-up` serve (`HERMES_DESKTOP_WEB` muda os dois; `HERMES_AGENT_DIR` muda o clone). |

## A ponte (portar a UI do desktop para o navegador)

| Arquivo | O que faz |
|---|---|
| `gerar-shim-stubs.py` | **A peça que destravou tudo.** Lê o contrato de tipos (`apps/desktop/src/global.d.ts`) e gera `shim-stubs.js` com a superfície inteira da ponte em stubs inertes do tipo certo (~160 chaves) — membros em forma de propriedade (`nome: T`) e de método (`nome(a): R`). Valida o resultado com `node --check` antes de trocar o arquivo em uso. É a fonte da superfície — mexeu na ponte, rode o gerador de novo. |
| `shim-stubs.js` | Gerado (não edite à mão). Base inerte do `window.hermesDesktop`. |
| `shim-desktop.js` | A ponte de verdade: base gerada + implementações reais, **mescladas em profundidade** (um objeto do shim não apaga mais os métodos gerados do mesmo grupo). REST com `X-Hermes-Session-Token`, WebSocket com `?token=`, renovação do token via `/__token` quando o backend reinicia, `openExternal` só para `http(s):` com `noopener`; os nomes seguem o contrato (`windowControls.toggleMaximize`, `git.repoStatus`…). Depois do doc 09 ela deixou de ser "só o que o app precisa para bootar": **clipboard que copia de verdade** (com fallback e erro honesto em vez de mentir "Copiado"), **escala da UI** (Ajustes → Aparência, que estava morta), **anexo por bytes → caminho** (`selectPaths`/`saveImageBuffer`/`readFileDataUrl`), **escolher pasta** (pergunta o caminho e confere), **microfone** (`getUserMedia` de verdade em vez de "sem acesso"), **texto do chat** semeado uma vez, e a **zona de pega do divisor** que não rouba botão. Notificações seguem no-op. |
| `desktop-port.css` | O CSS do porte, injetado só na 8890: conserto de altura (`h-screen` → `100svh`), toque no Android (realce neutro, sem os 300 ms, sem rolagem encadeada), alvo do divisor de painéis e os botões de dedo do composer (via as variáveis de CSS do próprio app). Nada aqui muda cor, fonte ou desenho — só comportamento e tamanho de alvo. O `install.sh` o copia para `~/.hermes/desktop-port.css`. |
| `validar-injecao.py` | Formata o template da injeção **fora** do servidor e roda `node --check` no JS resultante. Pega o `%` literal (tipo `left:50%`) que faz o servidor fechar a conexão sem resposta e sem traceback — o que parece "servidor morto" e não bug de template. |
| `gerar-fontes-embutidas.py` | Regenera o CSS com as fontes do bundle em data URI. Rodar **depois de cada rebuild**, porque o hash do arquivo muda. |
| `patch-dashboard-look.py` | Injeta o bloco `<style id="hermes-desktop-look">` (CSS da cara de desktop) no `index.html` do dashboard. O PWA de verdade (manifesto, ícones, tags) é o `patch_dashboard_pwa.py`, que fica em `~/hermes-app/` (fora do repo) e é chamado pelo `dashboard-up.sh`. |

## Chat nativo

| Arquivo | O que faz |
|---|---|
| `chat/index.html`, `chat/app.js` | A SPA do chat nativo, falando JSON-RPC 2.0 por WebSocket com streaming de verdade. |
| `verify-native-chat.py` | Verifica o protocolo do gateway (métodos e eventos) **antes** de prometer que funciona. |
| `testar-chat-metodos.py` | Sonda de `session.list/resume/history/activate`, `image.attach` e `session.interrupt`. **Cria uma sessão no histórico real**: só roda com `HERMES_TESTE_CONFIRMO=1`; `image.attach` só com `FOTO=<arquivo>`; imprime a forma das respostas, não o conteúdo das conversas. |

## Diagnóstico e saúde

| Arquivo | O que faz |
|---|---|
| `phone-health.sh [--prune]` | Resume tudo: orçamento de memória, skills ativas/estacionadas, dashboard + watchdog, wake lock, espaço, compilações acidentais e rotação de logs. |
| `skills-restore.sh` | Devolve as skills estacionadas de `~/.hermes/skills-parked/` para `skills/`, em qualquer profundidade (subcategorias inclusive). Não sobrescreve skill que já existe no destino (avisa e sai 1). |

## Medição e verificação (o que prova as afirmações deste repo)

Medir sem ver a tela, e sem depender do usuário para testar. Todos rodam com o **python do venv**
(`~/hermes-agent/venv/bin/python`), exigem o Chromium com CDP no 9222 (`chrome-cdp up`) e o injetor
no ar. **Não precisam do `/__cmd`** (nem de `HERMES_DESKTOP_CMD=1`): falam CDP com o Chromium, e as
chamadas às rotas locais (`/__zoom`, `/__upload`, `/__save-text`, `/__file`) partem da própria página,
com o token da sessão e a mesma origem que o injetor exige. Só a leitura da página **viva do
aparelho** (o Chrome do usuário, não o headless) usa o `/__cmd` — ver o aviso no topo.

| Arquivo | O que faz |
|---|---|
| `eval-port.py` | Roda um JS na página do Chromium (CDP puro) e imprime o resultado. `--emular` aplica a geometria do aparelho (largura em dip + toque), `--nav` carrega a URL antes. É o canivete das outras medições. |
| `cdp-atual.py` | Piloto CDP contra o Chromium **já no ar** (9222, perfil persistente): `nav <url> [s]`, `js '<expr>'` e `nav-js <url> '<expr>' [s]`, todos com `awaitPromise`. Existe porque o `webcdp` lança e **mata** um Chromium a cada chamada (~5 s de vida) — service worker nunca instala nesse tempo e Promise volta `{}`. É o único caminho para medir SW de verdade: navegar → esperar `serviceWorker.controller` → derrubar o servidor → ver o resgate → subir de novo → conferir a autocura **sem navegar**. Rode com o python do venv. |
| `medir-ui-port.py` | Mede o porte em várias combinações de largura/escala: fator de ajuste do Chrome, tamanho do texto **em dip** (não em px CSS, que engana), lateral, composer e overflow horizontal. |
| `medir-texto-port.py` | Abre uma sessão de verdade e mede o **texto das mensagens** por escala da UI — o número que o usuário sente como "está pequeno". Medir a tela de nova sessão engana: nela não existe mensagem. |
| `testar-ponte-port.py` | Prova clipboard, escala da UI e `savePastedText` de ponta a ponta: clica no botão de copiar com evento de verdade e **lê de volta** a área de transferência. Sem a leitura de volta não há prova nenhuma. |
| `testar-anexos-port.py` | Prova o anexo **pela interface**: botão "+" → menu → "Images…" → arquivo escolhido → chip no composer. Precisa interceptar o seletor (`Page.setInterceptFileChooserDialog` + `DOM.setFileInputFiles`): sem isso o Chrome headless cancela na hora e o teste mede o cancelamento, não o fluxo. |
| `teste-divisor-port.py` | Mede o divisor de painéis num arrasto de **toque** (`Input.dispatchTouchEvent`): quanto o fio anda por dedo e quantos `pointercancel` acontecem. Foi assim que apareceu o `touch-action` faltando. |

## Comandos de uso no dia a dia

| Comando | O que faz |
|---|---|
| `hermes-mobile` (alias `h`) | Start leve: `CARGO_BUILD_JOBS=2`, LTO off, banner com como parar. `--continue` (alias `hc`) retoma a última conversa. |
| `hermes-menu` (alias `hm`) | Menu numerado: navegação só com toques. |
| `hermes-calm` (alias `hcalm`) | `renice 19` em rustc/cargo/maturin quando um build trava o aparelho. **Não mata nada** — devolveu ~3 GB de RAM disponível na hora. |
| `hermes-falar` | Ditado grátis: `termux-speech-to-text` (voz do próprio Android) → `hermes-mobile -z "<texto>"`. |
| `hermes-imagem` | Popup de miniaturas das fotos do celular + pergunta, e manda pro Hermes com a imagem anexada. |
| `hermes-picker.py` | O servidor do popup acima (stdlib + Pillow, 127.0.0.1:8898, filtro por pasta). Cada execução gera um token (`state/picker-token`, 0600) que toda rota exige (`?t=`; o `hermes-imagem` já abre a URL com ele); a escolha é um POST JSON. Sai sozinho depois da escolha ou após `HERMES_PICKER_IDLE` segundos parado (padrão 900). |

## O que não está no repositório

O resto do sistema procura estes arquivos, mas eles ficam fora do repo (o `install.sh` avisa
quais faltam). Sem eles tudo sobe; só a parte correspondente fica de fora.

| Arquivo | Quem usa | Sem ele |
|---|---|---|
| `~/hermes-app/patch_dashboard_pwa.py`, `icon-192.png`, `icon-512.png` | `dashboard-up.sh` (PWA do dashboard) | o dashboard funciona, mas não instala como app |
| `~/.hermes/desktop-look.css` | `patch-dashboard-look.py` via `dashboard-up.sh` | o dashboard fica com a cara original |
| `~/.hermes/desktop-fonts.css` | `hermes-desktop-up` (`HERMES_WEB_EXTRA_CSS`) — gerado por `gerar-fontes-embutidas.py` depois de cada build | wordmark e código caem na fonte do sistema |
| `~/.hermes/chat/fonts/JetBrainsMono-{Regular,Bold}.woff2` | `chat/index.html` | o `install.sh` copia do bundle do desktop se achar; senão o chat usa a mono do sistema |

## `shortcuts/`

Os scripts de botão da tela inicial (app **Termux:Widget** copia para `~/.shortcuts/`; o
`install.sh` já faz isso). O widget **não** lê o `~/.bashrc`: cada atalho exporta o `PATH`
(`~/.hermes/bin`, venv do Hermes, `$PREFIX/bin`) e chama os comandos por caminho absoluto.
Cada um roda com o Termux em primeiro plano — o que é justamente o que faz
`termux-open-url` funcionar (em segundo plano o Android bloqueia lançamento de atividade e o
comando sai com exit 0 sem abrir nada).
