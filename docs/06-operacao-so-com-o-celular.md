# 06 — Operação 100% pelo celular (sem teclado físico, sem PC)

Objetivo declarado: **toda** tarefa tem que ser possível só com o aparelho — teclado de tela e,
quando der, toques na tela. Três frentes.

## Toques → o app

O dashboard web (9119) é a superfície clicável: chat, sessões, config, MCP. Mantenha no ar com o
watchdog (doc 02) e **ensine o caminho do ícone na tela inicial**, não comandos.

## Teclado de tela → termos que não dependem de modificador

O que resolve a dor "não tenho Ctrl+C":

- **Mandar qualquer mensagem + Enter interrompe o turno.** `display.busy_input_mode` já é
  `interrupt` por padrão no CLI — esse é o cancelamento natural no celular (digitar e mandar
  redireciona o turno).
- `Ctrl+C` limpa o rascunho primeiro e só interrompe com o campo vazio; duplo toque em 2s força a
  saída. Sem tecla CTRL, **não conte com ele**.
- **A barra de teclas extras do Termux é a única forma de ter ESC/CTRL/setas.** Em
  `~/.termux/termux.properties`:

  ```
  extra-keys = [['ESC','/','-','HOME','UP','END','PGUP'],['TAB','CTRL','ALT','LEFT','DOWN','RIGHT','PGDN']]
  ```

  Depois `termux-reload-settings` (aplica na hora, sem reiniciar o app). Não mexa em
  `back-key=escape` sem pensar: o botão voltar deixa de fechar o teclado.
- **Painel de raciocínio escondido**: `hermes config set display.show_reasoning false`.
- Quase tudo que exigia atalho tem comando digitável — prefira sempre a variante digitável ao
  atalho na hora de instruir alguém: `/copy` (copiar resposta), `/paste` (colar imagem),
  `/image <caminho>` (anexar arquivo), `/btw` (pergunta paralela), `/agents` (ver tarefas, no lugar
  do Ctrl+T), `/queue`, `/prompt`.

## Kit de comandos (`~/.hermes/bin/`, no PATH pelo `~/.bashrc`)

| Comando | O que faz |
|---|---|
| `hermes-mobile` (alias `h`) | start leve: `CARGO_BUILD_JOBS=2`, LTO off, banner com como parar |
| `hermes-mobile --continue` (alias `hc`) | continua a última conversa |
| `hermes-app` (alias `happ`) | sobe o dashboard e abre a tela clicável |
| `hermes-menu` (alias `hm`) | menu numerado — navegação só com toques |
| `hermes-calm` (alias `hcalm`) | `renice 19` em rustc/cargo/maturin quando um build trava o aparelho |
| `hermes-chat` | abre a superfície de chat nativa (8891) |
| `hermes-falar` | ditado (ver abaixo) |
| `hermes-imagem` | seletor de fotos (ver abaixo) |

**Armadilha de escrita:** `$PREFIX/bin` é protegido para escrita pelo agente Termux. Escreva o
script em `~/.hermes/bin` e crie o link com `ln -s` — senão a escrita é recusada.

Botões na tela inicial (app **Termux:Widget**, scripts em `~/.shortcuts/`): Hermes,
Hermes continuar, Hermes APP, Aliviar celular, Hermes chat, Hermes foto, Hermes falar.

## Ditado sem digitar nada (grátis)

`hermes-falar` = `termux-speech-to-text` (reconhecimento de voz do próprio Android, **sem API
paga**) → manda o texto com `hermes-mobile -z "<texto>"`. É o substituto barato do `/voice` do
Hermes, que no Termux exige `pkg install portaudio python-numpy` + `sounddevice` + provedor de STT.

## Mandar foto pro Hermes (dois caminhos)

**Pelo app (preferir):** o dashboard **já tem anexo de imagem nativo** — o botão do clipe abre o
seletor de fotos do Android e o SPA posta em `POST /api/chat/image-upload` com corpo **JSON**
`{"data_url":"data:image/jpeg;base64,…","filename":"x.jpg"}` e header `X-Hermes-Session-Token`
(extraído do HTML servido: `__HERMES_SESSION_TOKEN__`). Devolve
`{"ok":true,"path":"<hermes home>/images/dashboard_*.jpg"}`. Um POST multipart nesse endpoint dá
**500** — o formato é JSON, não form-data. O Chrome resolve o acesso às fotos; o Termux não precisa
de nada disso.

**Pelo terminal:** `hermes-picker.py` (servidor stdlib em 127.0.0.1:8898: grade de miniaturas via
Pillow, filtro por pasta — Camera, Screenshots, Pictures, Downloads, WhatsApp, Telegram —, seleção
múltipla e campo de pergunta) + `hermes-imagem`, que abre a página, espera a escolha em
`~/.hermes/state/picker-queue.json` e chama
`hermes-mobile chat -q "<pergunta>" --image "<path>"`.

**Bug que vale lembrar:** num `BaseHTTPRequestHandler` cada requisição cria uma instância nova —
guardar a lista de fotos em `self.photos` faz `/thumb/N` responder 404 na requisição seguinte.
Guarde no servidor (`self.server.photos`).

## Abrir app ou URL a partir do Termux (quando é o agente que "clica")

`am start -a android.intent.action.VIEW -d <url>` e `termux-open-url <url>` **só entregam o intent
com o Termux em primeiro plano.** Em segundo plano o Android bloqueia lançamento de atividade: o
comando imprime `Starting: Intent {…}`, sai com código **0**, e **nada abre**. Não confie no exit
code — confirme pelo efeito colateral.

- Log do seu servidor vazio = o intent não saiu (bloqueio de segundo plano), não URL errada. Não
  tente por três caminhos diferentes (intent padrão, componente explícito do Chrome,
  `termux-open-url`) — todos falham pelo mesmo motivo.
- Para pedir o clique quando o usuário está noutro app: copie o alvo com `termux-clipboard-set` e
  avise com `termux-notification --title … --content "cole no Chrome: <url>"`.
- O botão de tela inicial (Termux:Widget) resolve: o script roda com o Termux em primeiro plano e o
  `termux-open-url` funciona.

## Ver a tela do celular (sem cabo, sem root)

- `screencap` pelo Termux **falha** (`Failed to take screenshot`): captura de tela exige privilégio
  que o Termux não tem. Não insista por aí — nem com o app Termux:API (ele dá câmera, não tela).
- Caminho que funciona: **o usuário tira print (Power + Volume baixo)** e o agente lê o arquivo
  direto de `~/storage/dcim/Screenshots/` (precisa da permissão de arquivos) e analisa com visão.
  Confirmado: os prints abrem (1440x3120 JPEG) e são legíveis.
- Com cabo num computador, `adb exec-out screencap -p` continua sendo o caminho autônomo.

## Instalar APK quando o usuário só tem o celular

`termux-open <arquivo>.apk` e `termux-setup-storage` retornaram exit 0 e **nenhum diálogo
apareceu** no Android 16 com o build debug — o hand-off de intent do Termux não chega ao
instalador. O caminho que funcionou: subir um servidor HTTP local
(`python -m http.server 8899 --bind 127.0.0.1` em `~/apks`) e abrir a URL com
`am start -a android.intent.action.VIEW -d http://127.0.0.1:8899/<arquivo>.apk` — o Chrome baixa e
o próprio Chrome instala (pedindo "permitir deste app" uma vez).

Isso depende da regra da seção anterior (**Termux em primeiro plano**): confirme pelo log do
`http.server` que a requisição chegou — exit 0 sem requisição significa intent bloqueado, não URL
errada — e depois confirme com `pm list packages | grep com.termux.api`.

Capacidades que só existem com o app **Termux:API** instalado (o pacote `termux-api` do apt só
fornece o comando; sem o app ele falha): clipboard (`/copy` depende disso),
`termux-speech-to-text`, `termux-share`, `termux-notification`.
