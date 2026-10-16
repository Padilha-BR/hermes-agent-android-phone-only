# Hermes Agent no Android — só com o celular, e com a UI do app desktop rodando no navegador do próprio aparelho

Relato de campo por **[Gabriel Padilha](https://github.com/Padilha-BR)**, **14/10/2026** — atualizado em **16/10/2026**.
Galaxy S24 Ultra (SM-S928B, arm64, 12 GB de RAM), firmware `S928BXXU6EZIF`, Termux, Python 3.14.6,
Hermes Agent `git.311155f.dirty`.
Medido no **Android 16 / API 36, One UI 8** (14/10/2026) e reconferido no **Android 17 / API 37** (16/10/2026).

**Em uma linha:** o Hermes Agent roda inteiro dentro do Termux do telefone — CLI, dashboard **e o
próprio renderer React do app desktop oficial**, servido ao navegador do telefone pelo telefone.
O telefone é host e cliente ao mesmo tempo — sem uma segunda máquina em lugar nenhum do caminho, sem host de contêiner, sem túnel, nada mais no meio. O
dashboard vira um PWA instalado na tela inicial, com um watchdog que o mantém vivo quando o Android
suspende o Termux.

> ⚠️ **É uma versão inicial — e não é um app.** Ainda **não existe APK oficial** disto: o que tem
> aqui é um PWA mais a UI do desktop portada, tudo segurado por scripts. Roda e é usável todo dia,
> mas **por ser versão inicial ainda tem bugs** — terminal in-app, git nativo, diálogos de arquivo,
> multi-janela/HUD e o pet overlay ficam inertes por design (são nativos do Electron), o Android pode
> suspender o Termux quando quiser, e o resto aparece na prática. **Toda e qualquer ajuda é bem-vinda:**
> issue, PR, relato de bug ou um "no meu aparelho deu isso" já vale. Leia as limitações honestas antes
> de tentar.

Não é fork e não é instalador. É um relato de campo + os scripts que a gente usa todo dia: o que
quebrou, por que quebrou, e a receita que consertou.

---

## Leia isto primeiro: o que **não** é inédito

Antes de escrever "primeiro do mundo" em qualquer lugar, a gente procurou (14/10/2026). Existe
bastante coisa. Crédito a quem é de direito:

| Já publicado | O que é |
|---|---|
| [Docs oficiais de Termux](https://hermes-agent.nousresearch.com/docs/getting-started/termux) + canal APT assinado | O caminho oficial "Hermes no Termux". Em set/2026 a página carregava o aviso **"Termux is currently broken"**; o caminho por pip a partir do código (abaixo) é o que funcionou pra gente. |
| [AbuZar-Ansarii/Hermes-Agent-On-Android](https://github.com/AbuZar-Ansarii/Hermes-Agent-On-Android), [IceHeartGitH/Hermes-Agent-Android-Termux](https://github.com/IceHeartGitH/Hermes-Agent-Android-Termux), [didin-lab/hermes-termux-install](https://github.com/didin-lab/hermes-termux-install) | Instaladores comunitários de bootstrap para Termux. |
| [walodayeet/Hermes-Desktop-Web-Port](https://github.com/walodayeet/Hermes-Desktop-Web-Port), [przbadu/hermes-ui](https://github.com/przbadu/hermes-ui), [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) | O renderer do desktop no navegador, ou uma UI web alternativa. Todos assumem um **servidor/NAS/host** e o telefone como cliente. |
| NousResearch `apps/mobile/desktop-port` ([PR #64962](https://github.com/NousResearch/hermes-agent/pull/64962)), plugin [hermes-workbench](https://hermes-agent.nousresearch.com/docs/plugins/hermes-workbench) | O renderer embrulhado num app iOS/Capacitor, ou servido por um host headless. Ambos substituem a ponte `window.hermesDesktop` — **escrita à mão**. |
| [Docs de acesso remoto do hermes-webui](https://github.com/nesquena/hermes-webui/blob/master/docs/remote-access.md), issue #2364 | Hermes + WebUI em ARM64 Android dentro de uma **VM Debian 12 via AVF** (Redmi Note 13 Pro, ~1,7 GB de footprint). O prior art mais próximo de "o telefone é o host". |
| [Hermes Android v1.0.0](https://www.reddit.com/r/hermesagent/comments/1tspb0a/hermes_android_v100_first_release_of_an_android/) | Um **cliente** Android de terceiros, que fala com um host Hermes na sua rede local. |

Detalhe, links e o que cada um faz de diferente: **[docs/00-prior-art.md](docs/00-prior-art.md)**.

## O que não achamos publicado em lugar nenhum

1. **O renderer oficial do desktop rodando no navegador do próprio telefone, hospedado pelo
   telefone.** Todo port publicado de `apps/desktop` põe algum host entre o telefone e a UI.
   Aqui o telefone é host **e** cliente, tudo dentro do Termux, em loopback
   (`http://127.0.0.1:8890`), sem camada nenhuma no meio.
2. **Ponte Electron gerada, não escrita à mão.** Em vez de implementar `window.hermesDesktop`
   método por método, geramos a superfície inteira a partir do contrato de tipos do próprio
   renderer (`apps/desktop/src/global.d.ts`) numa base inerte de **160 chaves**
   ([`gerar-shim-stubs.py`](scripts/gerar-shim-stubs.py)), e só então empilhamos as implementações
   reais em cima. Escrever na mão produzia **um erro novo por rodada**; gerar fechou o buraco de uma vez.
3. **O dashboard oficial virando PWA instalável na tela inicial do Termux**, com watchdog e entrada
   no `Termux:Boot`. Sintoma que ninguém documenta: o `hermes dashboard` **reconstrói `web_dist` e
   apaga o patch PWA** — o ícone passa a receber HTML no lugar do manifesto e o app "quebra" com o
   servidor vivo.
4. **Playbook de otimização Android medido** — o orçamento de memória que bloqueia *toda* gravação
   de memória em silêncio, o custo real do índice de skills por mensagem, e o fato de que
   `hermes plugins enable` compila Rust **no telefone**.
5. **Recarga por SSE** para o ciclo de depuração no celular: `POST /__bump` força `location.reload()`
   nas páginas abertas e funciona com a aba em segundo plano, onde `setInterval` **não** funciona
   (o Chrome congela timers de aba inativa).

## Verificado no aparelho hoje

| Afirmação | Evidência |
|---|---|
| Renderer do desktop servido pelo telefone | `GET :8890/` → `<title>Hermes</title>` + token de sessão injetado + `__stubs.js` + `__shim.js` + `manifest.webmanifest` |
| O app da tela inicial carrega a UI de verdade | WebAPK → `GET :8890/` → 3.385 nós no DOM, `window.hermesDesktop` presente, lista de sessões renderizada |
| O app se cura em vez de ficar preto | `/__sw.js` intercepta **só a navegação**: com o injetor fora ele serve uma página "o servidor está subindo" e se recarrega quando `/__health` responde — na mesma aba, sem toque |
| Superfície de chat nativa no ar | `GET :8891/` → 200 |
| Dashboard saudável, sem login em loopback | `GET :9119/api/health` → `{"ok":true,"auth_required":false}` |
| Dashboard é PWA de verdade | `GET :9119/manifest.webmanifest` → `200 application/manifest+json` |
| Superfície da ponte gerada | `shim-stubs.js` → 9.688 bytes, 160 chaves vindas do `global.d.ts` |
| Watchdog sobrevive à sessão do agente | `dashboard-watchdog.pid` → `PPid: 1` (desgarrado, não é filho do agente) |
| Bundle íntegro depois do build | `apps/desktop/dist` → 1.044 entradas em `assets`, 147 refs no `index.html` |

Testado por derrubada no mesmo dia: matar o servidor da :8890 de propósito → o watchdog o ressuscitou
em ~25 s, com :9119 e :8891 intactos. Layout fixado em viewport 800
(`~/.hermes/state/desktop-web-viewport`), que é o que deixa o texto legível e mantém o composer
dentro da área visível num telefone.

## As peças

```
Termux no telefone
├─ CLI do hermes (venv, Python 3.14, repo principal do Termux — sem TUR, sem camada de contêiner)
├─ :9119  hermes dashboard   → dashboard oficial, com patch para virar PWA instalável
├─ :8890  desktop-ui/web      → renderer React de apps/desktop + ponte gerada
├─ :8891  chat nativo         → SPA falando o protocolo JSON-RPC/WebSocket do gateway
├─ /__sw.js (service worker)  → página de resgate quando o injetor está fora, e se recarrega
│                               sozinha quando ele responde; de propósito não guarda UI em cache
└─ watchdog (PPid 1) + Termux:Boot → mantém as três de pé quando o Android suspende o Termux
```

Para medir um **service worker** você precisa de um navegador que continue vivo entre comandos:
`scripts/cdp-atual.py nav-js <url> '<expr>'` pilota o Chromium que já está no 9222, com perfil
persistente. O `webcdp` não serve: ele sobe e mata um Chromium a cada chamada, então o worker não
chega a instalar, e o `Runtime.evaluate` dele devolve `{}` para qualquer Promise.

## Não é uma janela de chat: o agente escreve e roda código no aparelho

Rodar o agente **no** aparelho é coisa diferente de conversar com um host a partir do aparelho. Aqui o
agente escreve e executa o próprio código localmente — Python 3.14.6, Node 26.4.0 e bash 5.3.20 estão
no telefone, e nada roda em outro lugar.

Todo script em [`scripts/`](scripts/) foi escrito pelo agente, no aparelho, durante este trabalho.
Vários existem porque **gerar** deu menos trabalho que escrever à mão:

| Script | O que faz | Por que existe |
|---|---|---|
| [`gerar-shim-stubs.py`](scripts/gerar-shim-stubs.py) | lê `apps/desktop/src/global.d.ts` e emite a superfície inteira de `window.hermesDesktop` como 160 chaves inertes | caçar método faltante um por um era lento e frágil — cada `on*` ausente devolvia Promise no lugar de cancelador e derrubava a UI |
| [`medir-ui-port.py`](scripts/medir-ui-port.py), [`medir-texto-port.py`](scripts/medir-texto-port.py) | medem o layout e o texto real das mensagens em cada escala, num Chromium headless emulando o aparelho | "parece que ficou bom" não é medição |
| [`cdp-atual.py`](scripts/cdp-atual.py) | pilota o Chromium que **já está no ar** em :9222 | service worker só se testa com um navegador que sobrevive entre comandos |
| [`testar-ponte-port.py`](scripts/testar-ponte-port.py), [`testar-anexos-port.py`](scripts/testar-anexos-port.py), [`teste-divisor-port.py`](scripts/teste-divisor-port.py) | provam as correções da ponte **pelo efeito**: clicam de verdade, leem a área de transferência de volta, contam `pointercancel` | um `cancel` que dispara na hora passa em teste ingênuo com a UI ainda quebrada |
| [`validar-injecao.py`](scripts/validar-injecao.py), [`verify-native-chat.py`](scripts/verify-native-chat.py) | afirmam o que a superfície **realmente** serve, pelo WebSocket do gateway | ícone pode estar vivo e servindo a coisa errada |

O mesmo padrão produziu as ferramentas do [repositório irmão](https://github.com/Padilha-BR/docker-termux)
(`webget`, `webprint`, `webplay`, `pdf`). Os números deste README foram consultados no aparelho enquanto
ele era escrito — não foram lembrados de uma sessão anterior.

## Pegada no aparelho

Medido em 14/10/2026, com tudo rodando:

| Item | Tamanho |
|---|---|
| Base do Termux (`$PREFIX/usr`) | 1,9 GB |
| Checkout `hermes-agent` | 1,8 GB — `venv` 151 MB, `node_modules` 1,1 GB, `apps/` 128 MB, `.git` 86 MB |
| `HERMES_HOME` (`~/.hermes`) | 1,3 GB — `cache/uv` 959 MB, `skills` 56 MB, o resto é log/state/sessão |
| `$HOME/.cargo` | 1,0 GB — cache de build Rust, **purgável** |
| `$HOME/.cache/pip` | 17 MB |
| **Total instalado** | **~4,8 GB** (~5,8 GB contando o cache do cargo) |
| RAM | 12 GB declarados — o kernel reporta `MemTotal: 11350704 kB` (≈ 10,8 GiB utilizáveis) |
| Swap | 12 GB (ZRAM) |
| Espaço no `/data` | 47 GB livres de 222 GB (79% em uso) |

Três coisas que valem saber antes de escolher um aparelho pra isso:

- Os 1,1 GB de `node_modules` existem **só** por causa do build da UI do desktop (Vite). Pulando o
  port do desktop, esse pedaço inteiro desaparece e a instalação fica perto de 3,7 GB.
- Os 12 GB de swap em ZRAM são o motivo de este aparelho ter sobrevivido a uma build storm que
  deixou a RAM livre em ~240 MB — sem isso, o OOM killer teria encerrado a instalação.
- O `HERMES_HOME` em si é pequeno. O peso é o checkout e os caches de Rust/uv, e os dois são
  descartáveis.

## Começo rápido

```bash
# 1. base do Termux — NÃO pule o apt upgrade primeiro
apt update && apt full-upgrade -y -o Dpkg::Options::=--force-confold

# 2. espelho, depois deps de build (o doc 01 explica por que openssl-tool e libheif importam)
pkg install -y clang rust make pkg-config libffi openssl openssl-tool binutils cmake ninja \
                patchelf libxml2 libxslt nodejs ripgrep ffmpeg libheif
# o python vem do repo principal do Termux (3.14) — existem wheels prontos para cryptography/lxml/numpy/pillow

# 3. clone + venv + install, com os overrides de perfil Rust que viram 40 minutos em 4
#    (os scripts foram verificados contra o commit 311155f do upstream — fixe-o se um main novo quebrar)
git clone https://github.com/NousResearch/hermes-agent.git && cd hermes-agent
# git checkout 311155f   # fixação opcional
python -m venv --system-site-packages venv && source venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
export CARGO_PROFILE_RELEASE_LTO=off CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 CARGO_BUILD_JOBS=8
python -m pip install -U pip setuptools wheel && python -m pip install -e '.[termux]'

# 4. instale os scripts deste repositório onde eles esperam estar (idempotente; rode de novo após git pull)
cd ~ && git clone https://github.com/Padilha-BR/hermes-agent-android-phone-only.git
bash hermes-agent-android-phone-only/install.sh --bashrc
#    → ~/.hermes/{bin,scripts,chat}, ~/.shortcuts (Termux:Widget), ~/.termux/boot (Termux:Boot)

# 5. suba as superfícies
hermes dashboard --skip-build --no-open                 # :9119
setsid ~/.hermes/scripts/dashboard-watchdog.sh &        # mantém de pé, desgarrado
```

O watchdog tem que rodar de `~/.hermes/scripts/` (ele chama os vizinhos de lá), não do clone.
Alguns artefatos **não** estão no repositório de propósito (patch e ícones do PWA, CSS do desktop,
fontes do chat) — o `install.sh` lista o que falta e o `scripts/README.md` diz o que é cada um.
Nada em `scripts/` contém credencial; os caminhos são o prefixo fixo do Termux.

## Documentos

| # | Doc | O que tem |
|---|---|---|
| 00 | [Estado da arte](docs/00-prior-art.md) | Todo projeto que achamos, o que faz, o que não faz, com links e datas. |
| 01 | [Instalação no Termux](docs/01-instalacao-no-termux.md) | A ordem que funciona e as 8 falhas que mais custaram tempo. |
| 02 | [O app na tela inicial](docs/02-app-na-tela-inicial.md) | Por que o ícone do dashboard quebra, o patch PWA, o watchdog, o Termux:Boot. |
| 03 | [UI do desktop no navegador do celular](docs/03-ui-desktop-no-navegador.md) | A receita completa: build sem Electron, contrato da ponte, gerador do shim, armadilhas de layout no celular. |
| 04 | [Chat nativo por WebSocket](docs/04-chat-nativo-websocket.md) | O protocolo JSON-RPC do gateway: métodos, eventos e como verificar antes de prometer que funciona. |
| 05 | [Otimização Android](docs/05-otimizacao-android.md) | Orçamento de memória, custo do índice de skills, armadilhas de plugin, o `pkill` que se automata. |
| 06 | [Operação só com o celular](docs/06-operacao-so-com-o-celular.md) | Teclado de tela sem Ctrl, ditado grátis, widgets, mandar foto pro agente. |
| 07 | [E isso abre porta para um app Android nativo?](docs/07-porta-para-app-android.md) | O que o upstream já resolve (wheels Android, pipeline de retag, gates de plataforma), por que a UI é separável, os bloqueios reais (Play Store, foreground service, armazenamento com escopo) e os três caminhos com preço. |
| 08 | [Como o agente abre uma página](docs/08-browser-no-android.md) | As três rotas por custo (HTTP puro ganha na maioria), por que nenhum Chromium roda em container aqui, de quem é o navegador na porta 9222, e como a *API* do Playwright funciona — por Node, com as duas chaves que ela exige. |
| 09 | [O que a ponte precisa implementar de verdade](docs/09-ponte-de-verdade.md) | As seis coisas que pareciam prontas e eram decorativas (anexo, escolher pasta, microfone, transcrição, o divisor no dedo, o teclado virtual): por que o navegador entrega **bytes** onde o Electron entregava **caminho**, as armadilhas que mais custaram (um `cancel` que dispara na hora no teste, um rótulo que muda com o estado do botão, um alvo alargado que roubou o botão vizinho) e os medidores usados para provar cada conserto. |

## Segurança e privacidade

- **Tudo escuta em `127.0.0.1`.** O injetor da UI do desktop fixa
  `ThreadingHTTPServer(("127.0.0.1", PORT), …)`, o dashboard usa loopback por padrão e o chat nativo
  reaproveita o mesmo servidor. Nada aqui é alcançável pela sua rede local nem pela internet — e
  nada aqui foi feito para ser.
- **Loopback significa sem login.** O injetor coloca o token de sessão direto no HTML que serve, e é
  isso que faz as superfícies funcionarem sem pedir senha. Isso é seguro **apenas** enquanto o bind
  continuar em loopback. **Nunca** ponha essas superfícies em `0.0.0.0`, nem faça port forward, nem
  jogue atrás de um túnel sem autenticação de verdade na frente — o token no HTML é o login inteiro.
- **Nenhuma credencial neste repositório.** Chave de API mora no arquivo de ambiente dentro do seu
  `HERMES_HOME` (`chmod 600`) e nunca num commit, num transcript ou numa issue. `scripts/` não
  referencia chave, token, hostname nem endereço externo.
- **Nenhum dado pessoal.** Sem nomes, contas, número de série de aparelho ou endereço de rede — só o
  endereço genérico de loopback e números de porta configuráveis.
- **Loopback não é fronteira de confiança no Android.** Além de você, três coisas alcançam estas
  portas: páginas abertas no Chrome do próprio celular (um GET simples já é CSRF), DNS rebinding (um
  domínio que passa a resolver para `127.0.0.1`) e qualquer outro app com a permissão INTERNET. Por
  isso os servidores locais respondem **403** a `Host` que não seja `127.0.0.1:<porta>`/
  `localhost:<porta>`, recusam `Origin`/`Sec-Fetch-Site` de outro site nas rotas que mudam estado,
  mantêm o canal de operação que executa JS (`/__cmd`) **desligado por padrão** e atrás de um token
  em arquivo (`0600`), dão ao seletor de fotos um token por execução e deixam a telemetria de
  depuração (que pode capturar texto da página) como opt-in. Contra outro app local só segredo
  ajuda — é para isso que esses dois tokens existem; o token de sessão no HTML continua legível por
  quem conseguir carregar a página, então a fronteira de segurança é o aparelho, não a porta.

## Android 17 (16/10/2026): o que a atualização mudou

O aparelho saiu do Android 16 / API 36 para o **Android 17 / API 37** em 16/10/2026. Duas coisas valem
saber antes de culpar a sua própria instalação:

- **O `termux-exec` parou de reescrever o shebang `/usr/bin/env`.** Script cuja primeira linha é
  `#!/usr/bin/env …` agora morre com `bad interpreter: No such file or directory`. Esconde bem: um
  launcher que só confere se o arquivo existe continua passando. Contornar é chamar o script por
  `sys.executable`, ou apontar o shebang para `$PREFIX/bin/env` — que é o que o `install.sh` deste
  repositório faz com todo script que instala, **a cada execução**, inclusive num aparelho que já tinha
  os scripts (o instalador do repositório irmão faz o mesmo com o binário do udocker).
- **O app pode ser aberto antes de as superfícies existirem.** O `Termux:Boot` roda na agenda do Android,
  não na do app, então tocar o ícone da tela inicial logo depois de reiniciar dava tela preta. Agora o
  service worker responde à navegação com uma página de resgate ("Acordando o Hermes") e se recarrega no
  instante em que o `/__health` responde: mesma aba, sem toque.

O caminho de instalação, as quatro portas, o patch de PWA e o watchdog sobreviveram à atualização, e as
conferências de superfície deste README foram rodadas de novo no Android 17 — 8890, 8891, 9119 e 9222
responderam 200.

## Limitações honestas

- **Android não é servidor.** O Termux é suspenso; desligar a otimização de bateria (Ajustes → Apps
  → Bateria → Sem restrições) tem que ser feito à mão, para o Termux e o Termux:Boot. O watchdog
  melhora isso, não derrota isso.
- **Sem voz local.** `faster-whisper → ctranslate2` não tem wheel para Android. Usamos o
  reconhecedor de fala do próprio sistema (doc 06).
- **Sem Docker de kernel, sem Playwright para Python.** Docker aqui é userspace (udocker + proot, e o
  repo irmão vai mais longe); automação de navegador roda no Chromium nativo do Termux via CDP, e o
  Playwright só pelo Node — `pip install playwright` não tem wheel para Android (doc 08).
- **A página de resgate não é retroativa.** O service worker assume depois de uma carga com o
  injetor de pé (justamente a carga que você faz ao instalar o app na tela inicial). Ele não guarda
  UI em cache de propósito, então com o injetor fora a página de resgate é tudo o que ele pode
  servir — e é esse o ponto.
- **O caminho de instalação é Tier 2 no upstream** — commits no `main` podem quebrar. E a faixa de
  Python suportada é a que estiver no `pyproject.toml` naquele dia, não a das docs (doc 01).
- **O port é um port, não é o Electron.** Terminal/PTY in-app, git nativo, diálogos de arquivo,
  multi-janela/HUD e o pet overlay ficam inertes no navegador, por design. Chat, sessões, config e
  ferramentas funcionam.
- **Duas superfícies é melhor que uma.** Se você só quer um chat bom no celular, a superfície
  nativa por WebSocket (doc 04) dá menos trabalho que portar a UI do desktop. A gente usa as duas.
- **Não subimos nada para o upstream.** Os scripts são nossos, sem versionamento contra internals,
  leem `hermes_cli/web_dist` e buildam o renderer do desktop em `.hermes-port/renderer` a partir
  de `apps/desktop` — caminhos e internals que o upstream pode mover (na dúvida, fixe o 311155f).

## Ajuda é bem-vinda

Isto é uma versão inicial e vai continuar incompleta sem gente usando. **Toda e qualquer ajuda conta**,
inclusive a mais barata:

- **Rodou no seu aparelho e quebrou?** Abra uma issue dizendo modelo, versão do Android, qual porta
  respondia (ou não) e o comando que você deu. Um relato de bug em aparelho diferente do nosso já é
  contribuição de verdade — aqui só existe UM telefone testado.
- **Achou um script que não roda, um doc que mente ou um passo faltando?** Issue ou PR, tanto faz.
- **Tem um port melhor, ou um APK em andamento?** Abra uma issue contando — o
  [doc 07](docs/07-porta-para-app-android.md) diz o que falta para um app nativo de verdade e por que
  isso é decisão de produto e de canal, não de código.
- **Inglês.** Os docs são em português de propósito (são nossas notas de trabalho); o README não.
  Correção de texto em inglês também é ajuda.

## Créditos

O Hermes Agent é [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT).
O Termux é [termux/termux-app](https://github.com/termux/termux-app). Os ports de desktop-web e os
relatos de campo listados no doc 00 são os ombros em que isso se apoia.

Dois PRs no upstream estão abertos, aguardando review: o fallback de instalação from-source no Termux
([#138739](https://github.com/NousResearch/hermes-agent/pull/138739)) e os docs de setup de navegador/CDP
no Android ([#139402](https://github.com/NousResearch/hermes-agent/pull/139402)). Os scripts continuam
nossos de qualquer forma — sem versionamento contra internals que o upstream pode mover.

MIT — veja [LICENSE](LICENSE).
