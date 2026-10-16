# 09 — O que a ponte precisa implementar de verdade

**Resposta curta:** a UI do desktop portada boota e conversa, mas **seis coisas ficaram decorativas**
— anexar foto/arquivo, escolher pasta, microfone, transcrição, o divisor de painéis no dedo e o
teclado virtual. Não era o app: era a ponte que substitui o Electron, respondendo "não tenho isso"
para tudo que é diálogo ou API de sistema. Este documento é o mapa do que era enfeite, por que era, e
o que foi preciso implementar para virar de verdade — com as medições.

Tudo aqui foi medido no aparelho (Galaxy S24 Ultra, Android 16 / API 36, arm64, sem root), dentro do
Termux, com a UI servida em `127.0.0.1:8890`.

---

## A regra que organiza todos os consertos

No Electron, "escolher arquivo" abre um diálogo nativo e a ponte devolve um **caminho** — e o app
inteiro foi escrito nesse contrato: ele guarda o caminho, mostra o caminho, e o agente lê o caminho
(`@file:<caminho>`).

No navegador não existe caminho: o seletor devolve um `File` (os **bytes**), e nenhuma API entrega o
caminho de uma pasta (`showDirectoryPicker` não existe no Chrome Android, e `webkitdirectory` é
ignorado no Android). Então toda implementação da ponte segue a mesma forma:

> recebe bytes → grava num lugar que o agente alcança → **devolve o caminho**.

Isso é possível porque aqui o app, o agente e o arquivo estão **no mesmo aparelho**. O truque não
seria possível num app servido de fora.

---

## 1. Anexar foto e arquivo

O botão "+" do composer abre um menu com seis itens. O que o app chama é
`selectPaths(options)` (e, para imagem colada/arrastada, `saveImageBuffer`; para a miniatura,
`readFileDataUrl`). Os três eram stubs: o app pedia um caminho, recebia vazio, e o botão ficava
bonito e inerte.

Implementado:

- `<input type="file">` criado na hora e clicado — é o que abre o seletor do Android;
- `accept="image/*"` quando o filtro é só de imagem: assim abre a **galeria** (seletor de fotos). Com
  a lista crua de extensões (`png,jpg,…`) o Android abre o navegador de arquivos, o que não é o que
  se espera do botão de foto;
- os bytes sobem em base64 para `POST /__upload`, o arquivo nasce em `~/attachments/`, e o caminho
  absoluto volta para o app;
- a miniatura vem de `GET /__file?path=…`, que **exige o token da sessão** e só aceita caminho dentro
  de `$HOME` (travessia testada: `/etc/passwd` → **403**).

Três armadilhas que custaram rodadas:

1. **O `cancel` do input dispara na hora num teste headless** — não existe diálogo para o usuário
   cancelar. Sem interceptar o seletor (`Page.setInterceptFileChooserDialog` +
   `DOM.setFileInputFiles`) o que se mede é o cancelamento, e a conclusão errada é "a ponte não foi
   chamada".
2. **Rede de segurança de cancelamento por `focus` aborta a escolha.** Resolver "lista vazia" quando a
   janela volta ao foco **sem exigir que ela tenha perdido o foco antes** derruba a escolha com o
   seletor ainda aberto. Tem de exigir `blur` primeiro.
3. Um clique **duplo** em item de menu Radix abre e fecha o menu. Clique uma vez.

## 2. Escolher pasta (e o que isso destrava)

Nenhuma API de navegador devolve o caminho de uma pasta. Como o caminho é deste aparelho, a ponte
pergunta: `window.prompt` com o diretório de trabalho já preenchido, e a resolução no servidor
(`GET /__resolver-pasta`). Aceita `~`, caminho relativo à base e **só o nome** — digitar
`docker-termux` acha sozinho.

A busca por nome precisa de **duas passadas rasas antes de qualquer mergulho**: uma árvore grande
(`hermes-agent`, com milhares de diretórios) consome o orçamento de visitas antes de chegar em
`~/repos`, e foi assim que `docker` deixou de achar `~/repos/docker-termux`. Busca de pasta é busca
de vizinhança, não de profundidade.

Vale mais que o item de menu: a **mesma** chamada alimenta "Folder…" do anexo, **mudar workspace** e
**adicionar projeto**. Era um item aparentemente decorativo segurando três recursos.

## 3. Microfone

`use-mic-recorder.ts` pergunta à ponte **antes** de abrir o microfone: se ela devolve `false`, o app
lança "acesso negado" sem nem tentar. Com o stub fixado em `false`, era exatamente isso que aparecia.

Correção: `requestMicrophoneAccess()` chama `getUserMedia({audio:true})` de verdade — o Chrome mostra
o diálogo do Android — e solta as trilhas na hora (quem grava é o app, logo depois).

**Medido, e contraria o que eu esperava:** o manifesto do WebAPK instalado no aparelho **não declara**
`android.permission.RECORD_AUDIO` (só `REORDER_TASKS` e `POST_NOTIFICATIONS`) — e a gravação
**funciona**: permissão aceita, medidor de áudio reagindo à voz. A captura acontece no processo do
Chrome, que tem a permissão; a ausência no manifesto do WebAPK não impede. Não vale prometer falha
por causa disso.

## 4. Transcrição (STT): aqui o limite é do aparelho, não da ponte

Com o microfone funcionando, o erro seguinte veio do backend:
`HTTP 400 /api/audio/transcribe :: No STT provider available`.

O primeiro caminho sugerido pela própria mensagem — o motor local grátis (`faster-whisper`) — **não
roda neste aparelho**:

```
$ pip download ctranslate2 --no-deps
ERROR: Could not find a version that satisfies the requirement ctranslate2 (from versions: none)
```

O `faster_whisper` baixa (`py3-none-any`), mas não roda sem o `ctranslate2`, que é o motor dele e
**não tem distribuição para Android/arm64**. Não é configuração: não existe binário.

Sobram dois caminhos, e nenhum é da ponte:

- **`HERMES_LOCAL_STT_COMMAND`** apontando para um CLI de whisper (whisper.cpp compilado no próprio
  aparelho) + o modelo baixado — grátis, offline, sem conta. `cmake`, `make` e `clang++` já estão no
  Termux;
- **chave de nuvem** (`groq`, `openai`, `mistral`, `xai` — todas com nível grátis), ou o registro
  `stt.providers.<nome>` no `config.yaml`, que usa a mesma gramática de placeholders.

## 5. Botões de dedo no composer

Pedido literal: *"os botões de enviar e do microfone estão bem pequenos pra ficar clicando com o dedo"*.
Medido antes: 24–26px CSS com glifo de 14px. Com o ajuste de 0,6 do porte isso dá ~14 dip ≈ 5 mm,
contra ~9 mm de um dedo.

O app dimensiona essa fileira por **variável de CSS** (`--composer-control-size: 1.5rem`,
`--composer-control-primary-size: 1.625rem`, em `styles.css`, usadas por `ICON_BTN`/`PRIMARY_ICON_BTN`)
— então o aumento é feito definindo a variável **no próprio botão**: só ele cresce, e a fileira não
rouba largura do campo de texto (alargar a fileira inteira encolheria o campo).

Duas armadilhas:

- **o MESMO botão troca de rótulo com o estado** — enviar vira `Stop` durante o turno e o microfone
  vira `Stop dictation` enquanto dita. Se um dos estados ficar fora do seletor, o botão **encolhe no
  meio do uso**, que é pior que não ter mexido;
- **o glifo não acompanha**: o app fixa o ícone (14px) e o quadradinho de "parar" é um `span` de 10px
  no código. Sem aumentar o glifo, o botão vira um anel vazio.

Resultado medido no aparelho: `+` 34×34, microfone 34×34, enviar/parar 36×36, glifos em 18×18.

## 6. Divisor de painéis no dedo (o slider lateral)

Sintoma: *"deliza muito pouco por vez e às vezes vai de uma vez e eu tenho que voltar de pouco em
pouco"*. Duas causas, as duas no gesto — a matemática do app está certa (o arrasto é 1:1 em px CSS e
usa `setPointerCapture`):

1. **`touch-action: auto` na faixa de pega**: o Chrome interpreta o arrasto como rolagem e **cancela o
   ponteiro** no meio. Medido, num arrasto de 60px CSS: `pointercancel` no 3º movimento e o fio
   praticamente parado. Conserto: `touch-action: none`.
2. **Faixa de 8px CSS** (≈6 dip ≈ 1 mm, com o fio visível em 10% de opacidade). Um dedo tem ~8 mm. Os
   arrastos abortados viram **toques**, e dois toques no divisor disparam o duplo-clique do app
   ("reset do painel") — é daí o "vai de uma vez".

Na segunda parte eu errei ao corrigir: alarguei o alvo com um `::after` de 30px e **cobri o botão "+"
do composer**, que fica a 25px do fio. O clique ia para o divisor e o botão de anexo virou enfeite
aos olhos do usuário. Antes de alargar qualquer alvo, enumere o que existe perto: neste fio há o "+"
a 25px e o botão de status do turno a 20px de um lado, e o "Minimize" a 4px do outro.

Solução que ficou: alvo curto por CSS (−3/+10px) e a largura restante **por JS**, com um `pointerdown`
sintético que só redireciona o gesto quando o toque **não** caiu em cima de um controle. Funciona
porque `startPointerDrag` escuta `pointermove` no `window` — iniciado o arrasto, ele segue o dedo
onde estiver.

## 7. Teclado virtual

Desde o **Chrome 108** o teclado do Android **não redimensiona mais o layout viewport** — ele cobre.
Como esta UI tem altura fixa (`100svh`) com rolagem interna e o composer ancorado na base, o campo de
digitação ficava **atrás do teclado**. Conserto: `interactive-widget=resizes-content` na meta de
viewport, que faz o ICB encolher junto com o teclado (e as unidades de viewport acompanharem).

Quem reescreve a meta em tempo de execução tem de reescrever o `content` **inteiro**, senão derruba o
atributo — foi assim que a escala da UI (que também mexe na meta) apagaria o conserto do teclado.

## 8. Como cada afirmação daqui foi medida

Sem ver a tela, e sem depender do usuário para testar:

- **Chromium headless emulando o aparelho** (`chrome-cdp up` + CDP puro): `Emulation.setDeviceMetricsOverride`
  com a geometria da tela do aparelho e `Emulation.setTouchEmulationEnabled`, para medir em **dip**
  (não em px CSS, que engana por causa do ajuste). É assim que se mede "8,6 dip" e "34 dip" com
  honestidade.
- **Arrasto de toque de verdade**: `Input.dispatchTouchEvent` (touchStart/move/end) e contagem de
  `pointercancel`; leitura da posição do fio antes e depois.
- **Escolha de arquivo de verdade**: `Page.setInterceptFileChooserDialog` +
  `DOM.setFileInputFiles` — o teste faz o papel do usuário.
- **Estado que só existe em uso real**: em vez de provocar um turno (que sujaria a sessão do
  usuário), o botão é **clonado** na DOM com o outro `aria-label` e medido. Valida o seletor sem
  disparar nada.
- **A página viva do aparelho**: o injetor tem um canal de operação (`/__cmd`) que executa leitura na
  página aberta e devolve o resultado no log — é por onde se confere, no aparelho real, que os botões
  medem o que deveriam. Ele vem **desligado**: suba o injetor com `HERMES_DESKTOP_CMD=1` (por exemplo
  `HERMES_DESKTOP_CMD=1 hermes-desktop-up` com a 8890 parada), o que grava o segredo em
  `~/.hermes/state/desktop-cmd-token` (0600); dali em diante ele liga sozinho enquanto o arquivo
  existir. Chame por POST, com o segredo no header (fora da URL e do log):
  `curl -s http://127.0.0.1:8890/__cmd -H 'content-type: application/json'
  -H "X-Hermes-Cmd-Token: $(cat ~/.hermes/state/desktop-cmd-token)" -d '{"js":"document.title"}'`.
  Os medidores CDP abaixo **não** precisam dele: dirigem a própria página, que já tem o token da
  sessão e a mesma origem. **Enquanto o app está em segundo plano o Chrome congela a
  aba**: o socket do SSE continua aberto e o comando é "entregue", mas nada executa. Meça com o app na
  frente.
- Os medidores usados estão em `scripts/`: `eval-port.py`, `medir-ui-port.py`, `medir-texto-port.py`,
  `testar-ponte-port.py`, `testar-anexos-port.py`, `teste-divisor-port.py`.

## 9. O que continua sem funcionar (e por quê)

- **Escolher pasta por toque**: só pelo caminho digitado. Nenhuma API de navegador entrega o caminho
  de uma pasta no Android.
- **Diálogo de "salvar como"** (`selectSavePath`): não existe no navegador. Degrada para cancelado.
- **Terminal PTY, git, árvore de arquivos e diálogos nativos do Electron**: continuam inertes de
  propósito (a ponte devolve vazio e o app mostra o estado vazio).
- **Transcrição**: depende do que a seção 4 descreve — modelo local compilado ou chave de nuvem.
- E o mesmo limite de antes: isto não é um APK. É uma página servida em loopback, com a mesma "cara".
