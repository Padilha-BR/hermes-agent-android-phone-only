# 02 — O "app do Hermes" na tela inicial (dashboard como PWA) + watchdog

O que está na tela inicial do telefone como "Hermes" **não é um app nativo** — é o **dashboard
web** do Hermes (porta 9119) instalado como **WebAPK** pelo Chrome. O WebAPK guarda o `start_url`
no próprio manifesto (`base.apk` → `AndroidManifest.xml`, procurando `http://127.0.0.1:9119/`), e
numa bind loopback **não há login**: o HTML já vem com `__HERMES_SESSION_TOKEN__` injetado.

Isso funciona muito bem — até parar. E ele para por dois motivos, ambos com correção automática:

1. **O processo do dashboard morre.** O Android suspende o Termux; a sessão que subiu o dashboard
   termina; nada reiniciava o serviço → ícone na tela, servidor nenhum.
2. **O `hermes dashboard` reconstrói `hermes_cli/web_dist`** e, ao fazer isso, **apaga o patch que
   torna o dashboard instalável** (`manifest.webmanifest`, `icon-*.png`, tags no `index.html`).
   Sem o patch, o Chrome recebe HTML no lugar do manifesto. Detalhe que engana: `--skip-build`
   serve `hermes_cli/web_dist`, mas o build do Vite **também escreve ali** — mesmo diretório, então
   todo build reescreve o patch.

## Solução instalada (14/10/2026 — reversível, tudo em `~/.hermes/scripts/`)

| Script | Papel |
|---|---|
| [`dashboard-up.sh`](../scripts/dashboard-up.sh) | Idempotente: se `/api/health` responde, sai. Senão sobe com `--skip-build` (rápido, ~10s), **reaplica o patch PWA se faltar** e, se o skip falhar, cai para build completo. |
| [`dashboard-watchdog.sh`](../scripts/dashboard-watchdog.sh) | Laço de 60s chamando `dashboard-up.sh`, com guarda de instância única por `flock` (pidfile pid+starttime como reserva), backoff quando o `dashboard-up.sh` falha, segurando `termux-wake-lock`. Instalado em `~/.hermes/scripts/` pelo `install.sh` — é de lá que ele roda. |
| `~/.termux/boot/10-hermes-dashboard.sh` | Termux:Boot: `sleep 20` e então o watchdog. **Um único** script de boot — scripts duplicados disputam a mesma porta; neutralize os antigos (`mv` para fora do diretório + `chmod -x`). |

## As três regras que valem a pena

- **Solte o watchdog da árvore de processos do agente.** Use
  `subprocess.Popen(["setsid", script], start_new_session=True)` → PPID 1. Rodando como filho do
  Hermes, ele morre quando a sessão acaba. Confirme:

  ```bash
  grep PPid /proc/$(cut -d' ' -f1 ~/.hermes/state/dashboard-watchdog.pid)/status   # pidfile = "pid starttime"
  # PPid: 1        ← medido hoje, é isso que se quer
  ```

- **Teste derrubando de verdade, não deduzindo.**
  `hermes dashboard --stop`, apagar `web_dist/manifest.webmanifest` e as tags do `index.html`,
  esperar ~70s e conferir `200` em `/api/health` **e** `application/manifest+json` no manifesto.
  **Health 200 sem o manifesto significa app quebrado com servidor vivo** — é o modo de falha que
  mais engana.

- **Ajustes que só o usuário faz (1 toque cada):** Termux e Termux:Boot com bateria em
  **"Sem restrições"** (Ajustes → Apps → Bateria); e abrir o Termux:Boot **uma vez** depois de
  instalar, senão os scripts de boot nunca rodam.

## Quando o app abre antes do servidor: a página de resgate (16/10/2026)

O ponto cego que sobrou do desenho acima: **o app abria em tela preta** se você tocasse o ícone
enquanto o Termux ainda subia (depois de reiniciar o aparelho, o `Termux:Boot` levou ~4 min para
levantar as portas aqui). A requisição morria em `ERR_CONNECTION_REFUSED` e o service worker de
então era **inerte** (`fetch` vazio, sem cache) — ou seja, ele não tentava de novo: a tela preta
ficava até você fechar e reabrir.

Conserto no injetor (`/__sw.js`): o service worker passa a interceptar **só a navegação**
(`if (req.mode !== 'navigate') return;` — API, assets e SSE seguem direto para a rede) e, quando o
`fetch` falha, devolve uma página escura que explica em português que o servidor do celular ainda
está subindo, mostra o contador de tentativas e chama `/__health` a cada 1,5 s até ele responder —
aí recarrega sozinha. Provado derrubando o servidor de propósito: a página aparece; o servidor
voltou e a **mesma aba** virou o app, sem nenhum toque. Nada é guardado em cache (a UI carrega
token e shim frescos a cada carga).

Consequência prática: **a blindagem só vale depois de uma carga com o servidor no ar** — abra o
app uma vez depois de instalar/atualizar e ele fica coberto dali em diante.

Duas armadilhas de quem for testar isso:

- **Medir service worker exige navegador persistente.** O `webcdp` sobe e mata um Chromium por
  chamada (~5 s de vida): o worker não instala e o `Runtime.evaluate` dele devolve `{}` para
  Promise — você mede o nada e conclui errado. Use o `scripts/cdp-atual.py` (Chromium do 9222,
  perfil persistente): navegar → esperar `serviceWorker.controller` → derrubar o servidor → ver a
  página de resgate → subir de novo → conferir a autocura **sem navegar**.
- **`POST /__cmd` com a aba em segundo plano não responde.** O Chrome congela a página; o canal
  SSE continua aberto (`paginas: 1`) mas nenhum resultado volta. Meça com o app em primeiro plano,
  ou aceite que o canal está cego — não é defeito do canal.

## Abrir o app sem mentir: `hermes-voltar` (16/10/2026)

O `am start` do Android **não diz se abriu**: sai com código 0 e imprime `Starting: Intent {...}`
mesmo quando o sistema bloqueia o launch (app em segundo plano não pode iniciar activity). A
primeira versão do `hermes-voltar` acreditava nessa saída e anunciava "App do Hermes aberto" com a
página ainda em branco.

Agora ele **prova pelo efeito**: manda o intent VIEW (o caminho que o Chrome entrega ao WebAPK
instalado, mesmo com o Termux atrás), e confirma pelo contador de páginas conectadas no injetor
(`POST /__cmd` → `paginas`). O relato tem três casos, todos honestos: subiu (`0 → 1`), já estava
aberto (trouxe para a frente) ou **não confirmei**. O launch por componente explícito virou plano
B, e só quando nada conectou.

## Ruído que não é bug

O dashboard mostra `gateway_running: false` / `degraded` quando não há plataforma de mensagens
configurada no telefone. **Isso é esperado.** Não configure Telegram no celular "para arrumar":
o mesmo token em dois gateways derruba o outro.

## Estado medido hoje

```bash
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://127.0.0.1:9119/manifest.webmanifest
# 200 application/manifest+json
curl -s http://127.0.0.1:9119/api/health
# {"ok":true,"version":"unknown","displayVersion":"git.311155f.dirty","auth_required":false}
```

O PID do watchdog responde com `PPid: 1`, e as três portas do telefone (9119, 8890, 8891)
respondem `200`.

## Se precisar de outra superfície na tela inicial

O mesmo injetor (`~/.hermes/bin/hermes-desktop-web.py`) serve **qualquer** SPA local e injeta
token, telemetria e recarga por SSE — basta `HERMES_DESKTOP_WEB=<dir> HERMES_DESKTOP_WEB_PORT=<porta>`.
Foi assim que nasceram a UI do desktop em 8890 (doc 03) e o chat nativo em 8891 (doc 04).
