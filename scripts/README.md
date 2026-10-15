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

## Superfícies (subir e manter de pé)

| Arquivo | O que faz |
|---|---|
| `dashboard-up.sh` | Idempotente. Se `/api/health` responde, sai. Senão sobe o dashboard com `--skip-build`, **reaplica o patch PWA** se faltar e cai para build completo se o skip falhar. |
| `dashboard-watchdog.sh` | Laço de 60 s chamando `dashboard-up.sh`, guarda de instância única por pidfile (pid + starttime, imune a reuso de PID), segurando `termux-wake-lock` — e o devolvendo no trap de saída. Rode-o desgarrado (`setsid`) para ele virar PPid 1 e sobreviver à sessão do agente. |
| `10-hermes-dashboard.sh` | Script de boot (`~/.termux/boot/`, via app **Termux:Boot**): espera 20 s e sobe o watchdog. **Um só** script de boot — duplicados disputam a mesma porta. |
| `hermes-app` | Garante que o dashboard (9119) esteja no ar e abre a tela clicável. |
| `hermes-desktop-web.py` | O injetor: servidor stdlib que serve um SPA local e injeta (a) o token da sessão, (b) o shim da ponte, (c) captura de erro + telemetria de DOM em `~/.hermes/logs/desktop-web.log`, (d) canal **SSE** `/__events` — um POST em `/__bump` empurra `location.reload()` para as páginas abertas, o que funciona com a aba em segundo plano (o Chrome congela `setInterval` de aba inativa). Reutilizável: `HERMES_DESKTOP_WEB=<dir> HERMES_DESKTOP_WEB_PORT=<porta>` serve qualquer SPA local. |
| `hermes-desktop-up` / `hermes-desktop` | Sobe e abre a UI do desktop portada (8890). |
| `hermes-desktop-zoom` | Escolhe o tamanho do texto da UI portada. O tamanho é o **inverso** da largura de viewport injetada (`width=N`; N menor = texto maior). Fica gravado em `~/.hermes/state/desktop-web-viewport` (sem arquivo, o `hermes-desktop-up` cai no 900). |
| `hermes-desktop-reload` | Recarrega a UI de forma confiável (abrir a mesma URL numa aba já aberta só foca a aba). |
| `hermes-chat` | Abre a superfície de chat **nativa** (8891), que fala o protocolo WebSocket do gateway. |
| `build-desktop-web.mjs` | Build do renderer do desktop sem Electron (chama o Vite direto). **Nunca** use `npm run build`, que monta o pacote Electron. |

## A ponte (portar a UI do desktop para o navegador)

| Arquivo | O que faz |
|---|---|
| `gerar-shim-stubs.py` | **A peça que destravou tudo.** Lê o contrato de tipos (`apps/desktop/src/global.d.ts`) e gera `shim-stubs.js` com a superfície inteira da ponte em stubs inertes do tipo certo (~160 chaves). É a fonte da superfície — mexeu na ponte, rode o gerador de novo. |
| `shim-stubs.js` | Gerado (não edite à mão). Base inerte do `window.hermesDesktop`. |
| `shim-desktop.js` | A ponte de verdade: base gerada + implementações reais (REST com `X-Hermes-Session-Token`, WebSocket com `?token=`, clipboard, `Notification`, no-ops seguros para o que é Electron-only). |
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
