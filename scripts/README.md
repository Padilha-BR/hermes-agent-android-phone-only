# scripts/ — o que é cada arquivo

Estes são os scripts que rodam **no telefone**, em `~/.hermes/` (o `HERMES_HOME` do Termux).
Copiar para lá é a instalação: `~/.hermes/bin/` vai no `PATH` pelo `~/.bashrc` e
`~/.hermes/scripts/` é chamado pelo watchdog. Nada aqui contém credencial — chaves ficam no
arquivo de ambiente do `HERMES_HOME`, com `chmod 600`.

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

> 🔑 **O canal de operação `/__cmd` exige token** (executa JS arbitrário na página aberta, que
> carrega o token da sessão). Ordem de resolução: env `HERMES_DESKTOP_CMD_TOKEN` → arquivo
> `~/.hermes/state/desktop-cmd-token` (chmod 600) → aleatório por processo, impresso no log de
> startup. Então: `curl -s -G 'http://127.0.0.1:8890/__cmd' --data-urlencode "js=<expr>"
> --data-urlencode "token=$(cat ~/.hermes/state/desktop-cmd-token)"`. Sem token a resposta é
> **403**. `HERMES_DESKTOP_CMD_INSECURE=1` volta ao comportamento antigo — não use com a porta
> aberta para o resto do aparelho.

## Superfícies (subir e manter de pé)

| Arquivo | O que faz |
|---|---|
| `dashboard-up.sh` | Idempotente. Se `/api/health` responde, sai. Senão sobe o dashboard com `--skip-build`, **reaplica o patch PWA** se faltar e cai para build completo se o skip falhar. |
| `dashboard-watchdog.sh` | Laço de 60 s chamando `dashboard-up.sh`, guarda de instância única por pidfile (pid + starttime, imune a reuso de PID), segurando `termux-wake-lock` — e o devolvendo no trap de saída. No mesmo laço ressuscita o `sshd` do Termux (8022, só chave pública) e o Chromium headless com CDP (9222, motor do `browser_exec`). Rode-o desgarrado (`setsid`) para ele virar PPid 1 e sobreviver à sessão do agente. |
| `10-hermes-dashboard.sh` | Script de boot (`~/.termux/boot/`, via app **Termux:Boot**): espera 20 s e sobe o watchdog. **Um só** script de boot — duplicados disputam a mesma porta. |
| `hermes-app` | Sobe o backend e abre **o app dele**: o ícone "Hermes" da tela inicial é um WebAPK apontando para a UI portada em 8890, não para o dashboard. Abrir a 9119 como "o app" joga o usuário numa aba nova do Chrome, no painel errado — quem abre o certo é o `hermes-voltar`. |
| `hermes-voltar` | Acha o WebAPK da tela inicial **pela porta do app** (o hash do pacote muda se ele reinstalar o atalho) e abre por componente explícito, que é o único jeito: WebAPK não abre por pacote. `status` mostra o alvo. |
| `hermes-desktop-web.py` | O injetor: servidor stdlib que serve um SPA local e injeta (a) o token da sessão, (b) o shim da ponte, (c) captura de erro + telemetria de DOM em `~/.hermes/logs/desktop-web.log` — **desligada por padrão** (`HERMES_WEB_TELEMETRIA=1` liga; o log de acesso só sai em debug) e com rotação própria de 1 MB, (d) canal **SSE** `/__events` — um POST em `/__bump` empurra `location.reload()` para as páginas abertas, o que funciona com a aba em segundo plano (o Chrome congela `setInterval` de aba inativa). Reutilizável: `HERMES_DESKTOP_WEB=<dir> HERMES_DESKTOP_WEB_PORT=<porta>` serve qualquer SPA local. Serve também os endpoints que a ponte usa: `/__zoom` (persiste a escala da UI escolhida no app), `/__upload` (recebe o anexo em base64 e devolve o **caminho** em disco), `/__file` (serve o anexo para a miniatura — exige o token da sessão e só aceita caminho dentro de `$HOME`), `/__save-text` (o paste gigante vira anexo `.txt`) e `/__resolver-pasta`. A meta de viewport sai com `interactive-widget=resizes-content`: sem isso o teclado virtual cobre o composer (Chrome 108+ não redimensiona mais o layout viewport sozinho). |
| `hermes-desktop-up` / `hermes-desktop` | Sobe e abre a UI do desktop portada (8890). |
| `hermes-desktop-zoom` | Escolhe o tamanho do texto da UI portada. O tamanho é o **inverso** da largura de viewport injetada (`width=N`; N menor = texto maior). Fica gravado em `~/.hermes/state/desktop-web-viewport` (sem arquivo, o `hermes-desktop-up` cai no 900). |
| `hermes-desktop-reload` | Recarrega a UI de forma confiável (abrir a mesma URL numa aba já aberta só foca a aba). |
| `hermes-chat` | Abre a superfície de chat **nativa** (8891), que fala o protocolo WebSocket do gateway. |
| `hermes-chat-up` | Sobe o injetor servindo a SPA do chat nativo (`~/.hermes/chat`). Separado do `hermes-chat` para o watchdog poder chamá-lo. |
| `chrome-cdp` | Sobe/mata/consulta o Chromium nativo do Termux com CDP em 9222 (com UA de Chrome normal: o UA do headless faz o Google devolver captcha). É o motor do `browser_exec` e dos medidores deste repo. |
| `build-desktop-web.mjs` | Build do renderer do desktop sem Electron (chama o Vite direto). **Nunca** use `npm run build`, que monta o pacote Electron. |

## A ponte (portar a UI do desktop para o navegador)

| Arquivo | O que faz |
|---|---|
| `gerar-shim-stubs.py` | **A peça que destravou tudo.** Lê o contrato de tipos (`apps/desktop/src/global.d.ts`) e gera `shim-stubs.js` com a superfície inteira da ponte em stubs inertes do tipo certo (~160 chaves). É a fonte da superfície — mexeu na ponte, rode o gerador de novo. |
| `shim-stubs.js` | Gerado (não edite à mão). Base inerte do `window.hermesDesktop`. |
| `shim-desktop.js` | A ponte de verdade: base gerada + implementações reais. Depois do doc 09 ela deixou de ser "só o que o app precisa para bootar": **clipboard que copia de verdade** (com fallback e erro honesto em vez de mentir "Copiado"), **escala da UI** (Ajustes → Aparência, que estava morta), **anexo por bytes → caminho** (`selectPaths`/`saveImageBuffer`/`readFileDataUrl`), **escolher pasta** (pergunta o caminho e confere), **microfone** (`getUserMedia` de verdade em vez de "sem acesso"), **texto do chat** semeado uma vez, e a **zona de pega do divisor** que não rouba botão. |
| `desktop-port.css` | O CSS do porte, injetado só na 8890: conserto de altura (`h-screen` → `100svh`), toque no Android (realce neutro, sem os 300 ms, sem rolagem encadeada), alvo do divisor de painéis e os botões de dedo do composer (via as variáveis de CSS do próprio app). Nada aqui muda cor, fonte ou desenho — só comportamento e tamanho de alvo. |
| `validar-injecao.py` | Formata o template da injeção **fora** do servidor e roda `node --check` no JS resultante. Pega o `%` literal (tipo `left:50%`) que faz o servidor fechar a conexão sem resposta e sem traceback — o que parece "servidor morto" e não bug de template. |
| `gerar-fontes-embutidas.py` | Regenera o CSS com as fontes do bundle em data URI. Rodar **depois de cada rebuild**, porque o hash do arquivo muda. |
| `patch-dashboard-look.py` | Injeta o bloco `<style id="hermes-desktop-look">` (CSS da cara de desktop) no `index.html` do dashboard. O PWA de verdade (manifesto, ícones, tags) é o `patch_dashboard_pwa.py`, que fica em `~/hermes-app/` (fora do repo) e é chamado pelo `dashboard-up.sh`. |

## Chat nativo

| Arquivo | O que faz |
|---|---|
| `chat/index.html`, `chat/app.js` | A SPA do chat nativo, falando JSON-RPC 2.0 por WebSocket com streaming de verdade. |
| `verify-native-chat.py` | Verifica o protocolo do gateway (métodos e eventos) **antes** de prometer que funciona. |
| `testar-chat-gateway.py`, `testar-chat-metodos.py` | Sondas usadas para descobrir o contrato. |

## Diagnóstico e saúde

| Arquivo | O que faz |
|---|---|
| `phone-health.sh [--prune]` | Resume tudo: orçamento de memória, skills ativas/estacionadas, dashboard + watchdog, wake lock, espaço, compilações acidentais e rotação de logs. |
| `skills-restore.sh` | Devolve as skills estacionadas de `~/.hermes/skills-parked/` para `skills/`. |

## Medição e verificação (o que prova as afirmações deste repo)

Medir sem ver a tela, e sem depender do usuário para testar. Todos rodam com o **python do venv**
(`~/hermes-agent/venv/bin/python`), exigem o Chromium com CDP no 9222 (`chrome-cdp up`) e o injetor
no ar.

| Arquivo | O que faz |
|---|---|
| `eval-port.py` | Roda um JS na página do Chromium (CDP puro) e imprime o resultado. `--emular` aplica a geometria do aparelho (largura em dip + toque), `--nav` carrega a URL antes. É o canivete das outras medições. |
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
| `hermes-picker.py` | O servidor do popup acima (stdlib + Pillow, 127.0.0.1:8898, filtro por pasta). |

## `shortcuts/`

Os scripts de botão da tela inicial (app **Termux:Widget** copia para `~/.shortcuts/`).
Cada um roda com o Termux em primeiro plano — o que é justamente o que faz
`termux-open-url` funcionar (em segundo plano o Android bloqueia lançamento de atividade e o
comando sai com exit 0 sem abrir nada).
