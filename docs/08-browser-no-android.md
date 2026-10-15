# 08 — Como o agente abre uma página no celular?

**Resposta curta:** o navegador que ele tentaria baixar **não roda aqui** — mas o do Termux roda, e
o agente fala com ele por CDP. E para ler texto de página, navegador nenhum é necessário na maioria
das vezes.

Os outros sete documentos cobrem instalação, dashboard, UI, chat e operação. Este cobre a peça que
faltava: **como o agente lê uma página quando precisa**. Tudo aqui foi medido no aparelho (Galaxy S24
Ultra, Android 16 / API 36, arm64, sem root), dentro do Termux.

---

## Primeiro, o que NÃO funciona (e por quê)

| Tentativa | O que acontece |
|---|---|
| `pip install playwright` | `No matching distribution found` — não existe wheel para `aarch64-linux-android`. O Playwright em **Python** não é instalável, ponto |
| O Chromium que o Playwright baixaria | é build **glibc**; o Android é **bionic** → `Trace/breakpoint trap` (SIGTRAP) ou `Target crashed` |
| Qualquer Chromium **dentro de container** | `proot` intercepta syscalls por **ptrace**, e o crashpad do Chromium depende do mesmo mecanismo. Não é flag: testados `--no-sandbox`, `--single-process`, `--no-zygote`, `--headless=old` e `GLIBC_TUNABLES=glibc.pthread.rseq=0`, nenhum resolve |
| Página sem texto no DOM (canvas puro) | **nenhum** nível extrai: nem HTTP, nem navegador |

A hipótese que parecia boa e foi descartada: o bug do `PartitionAlloc` em aarch64 com página de
**64 KB**. Neste aparelho o `SC_PAGE_SIZE` é **4096**, então essa explicação não se aplica aqui.

---

## O que funciona: três níveis, escolhidos por custo

Medido no aparelho, três rodadas concordando:

| Nível | O que é | Página estática | SPA (só JS) | RAM de pico |
|---|---|---|---|---|
| 1 | HTTP puro (parser, sem navegador) | **1,7 s** | falha (volta vazio) | **~34 MB** |
| 2 | Chromium **nativo** do Termux | 2,8 s | 4,7-6,2 s, renderizado | ~1 GB |
| 3 | CDP puro (sem chromedriver) | **1,9-2,5 s** | renderizado | ~1 GB |
| 3b | Playwright **via Node** | 0,5 s (arranque) | **1,4 s** | ~830 MB |

**Comece sempre pelo nível 1.** Ele extrai 8.097 caracteres do `react.dev` e 3.559 do `vercel.com` —
"página moderna" não significa "precisa de navegador", porque muito site já entrega o HTML pronto.
Subir para navegador custa ~1 GB de RAM contra 34 MB; vale quando você precisa de **pixel**
(PNG/PDF), de **clique**, ou de algo que só existe depois do JS rodar e não vem no HTML.

O Chromium nativo é um pacote do Termux, em bionic, sem proot:

```sh
pkg install x11-repo && pkg install chromium        # binário: chromium-browser
```

---

## Como o `browser_exec` fala com ele

O Hermes no celular aponta o browser para um Chromium **nativo** mantido no ar com
`--remote-debugging-port` e perfil próprio (`~/.hermes/browser-cdp`) — é o `chrome-cdp` deste
repositório, e o `scripts/dashboard-watchdog.sh` o ressuscita junto com o dashboard. É o mesmo
mecanismo do `/browser connect`: um Chromium expondo CDP, dirigido de fora.

Para subir na mão:

```sh
chromium-browser \
  --headless=new \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.hermes/browser-cdp" \
  --no-first-run --no-default-browser-check &
```

Detalhe que custa tempo: **confira de quem é o navegador antes de dirigir.** Se você abrir um
navegador na porta fixa 9222 enquanto outro já está lá, o `/json/list` devolve o browser **alheio**
e o seu script navega e executa JS na sessão de outro — sem erro nenhum. Foi exatamente o que
aconteceu aqui uma vez (o "resultado" veio de uma página que não era a que eu pedi).

---

## E o Playwright? Funciona — por Node, não por Python

Ele recusa subir com `Unsupported platform: android` (só conhece darwin/linux/win32), e a mensagem
vem do próprio código dele. Duas coisas resolvem:

1. **`PLAYWRIGHT_BROWSERS_PATH=0`** — sem isso ele tenta calcular um diretório de cache e morre no
   arranque;
2. **`executablePath`** apontando para o Chromium **do Termux** — o que ele baixaria é glibc.

```sh
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i playwright-core   # não baixe o que não roda
```

```js
const { chromium } = require("playwright-core");
const b = await chromium.launch({
  executablePath: "/data/data/com.termux/files/usr/bin/chromium-browser",
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
});
```

Medido: navegador no ar em **0,5 s**, `react.dev` renderizado em **1,4 s** (6.583 caracteres de
texto), screenshot 1280×720 válido, `locator()` com auto-wait em 0,2-0,4 s, pico de ~830 MB.
Auto-wait, locators e `page.evaluate` funcionam normal.

**Ressalva honesta:** custa a mesma RAM do Chromium nativo, então um navegador por vez — e o script
tem de ser em **JavaScript**, porque o pacote é o de Node. Para texto puro, o nível 1 continua
ganhando feio (1,7 s e 34 MB contra 3,0 s e 830 MB).

---

## O que este documento não afirma

- **Não** testamos VNC/headful no celular (só headless).
- **Não** testamos outro navegador além do pacote oficial do Termux.
- Os tempos são deste aparelho, com a rede dele; em outro chip/estado de bateria variam.
- Nada disso exige root, e nada disso funciona **com** o navegador dentro de um container.

---

Detalhamento, medições completas e a bateria reproduzível
(`bash verificar.sh`, 17 itens, ~2 min): **[docker-termux](https://github.com/Padilha-BR/docker-termux)**
— ver `CAPACIDADES.md` §3.6.3.
