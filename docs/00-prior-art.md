# 00 — Estado da arte: o que já existia antes de a gente achar que era inédito

Levantamento feito em **14/10/2026**, buscando antes de publicar. A conclusão honesta é:
**"Hermes no Termux" não é inédito, e "UI do desktop no navegador" também não.** O que não
achamos publicado é o *conjunto* descrito no item "Nosso delta" no fim deste documento.

Regra que seguimos: primeiro procurar quem já fez, depois escrever "primeiro".

---

## 1. Hermes no Android/Termux — já é caminho oficial (Tier 2)

| Fonte | O que é | O que (não) resolve |
|---|---|---|
| [Docs oficiais — Android/Termux](https://hermes-agent.nousresearch.com/docs/getting-started/termux) | Caminho oficial: extra `.[termux]`, `constraints-termux.txt`, instalador `curl … install.sh \| bash`. | Tier 2, "best-effort". Em 25/09/2026 a própria página passou a exibir o aviso **"Termux is currently broken"** para o pacote APT. |
| [Repo APT assinado da Nous](https://hermes-assets.nousresearch.com/releases/termux/stable) (`pkg install hermes-agent`) | Pacote aarch64 oficial, canais `stable`/`canary`, runtime próprio (não usa o python do Termux). | Mesmo aviso de quebrado em set/2026; só aarch64, só prefixo padrão do Termux. |
| [adybag14-cyber/termux-hermes](https://github.com/adybag14-cyber/termux-hermes) | Empacotamento APT comunitário (origem do suporte a Termux no upstream, via PR #6834). | Comunitário, não auditado pela Nous. |
| [AbuZar-Ansarii/Hermes-Agent-On-Android](https://github.com/AbuZar-Ansarii/Hermes-Agent-On-Android) | Instalador one-liner via `curl \| bash`. | É bootstrap de instalação. Para no `hermes` funcionando no terminal. |
| [IceHeartGitH/Hermes-Agent-Android-Termux](https://github.com/IceHeartGitH/Hermes-Agent-Android-Termux) | Bootstrap que prepara o ambiente e chama o instalador oficial. | Idem. Requer F-Droid, Android 15+. |
| [didin-lab/hermes-termux-install](https://github.com/didin-lab/hermes-termux-install) | Guia prático (one-liner + manual + troubleshooting), espelho das docs oficiais. | Idem. Repete a faixa de Python das docs (`<3.14`), que estava desatualizada. |

**Conclusão:** instalar Hermes no Termux é território mapeado. Nosso doc 01 não reivindica
ineditismo — ele registra a **ordem que funcionou** e os 8 erros que custaram mais tempo,
incluindo dois que nenhuma dessas fontes menciona (o `curl` quebrado por skew de `libssl`, e o
`requires-python` real lido do `pyproject.toml`, e não das docs).

---

## 2. UI do app desktop no navegador — também já existe, em várias formas

| Projeto | Como faz | Diferença para o nosso |
|---|---|---|
| [walodayeet/Hermes-Desktop-Web-Port](https://github.com/walodayeet/Hermes-Desktop-Web-Port) | Copia o renderer verbatim e substitui o Electron por `web/src/web-bridge.ts` **escrito à mão** + proxy Node same-origin + login gate. | Assume um host/servidor; ponte manual; pensado para Docker. |
| [przbadu/hermes-ui](https://github.com/przbadu/hermes-ui) | `app/src/web-bridge/bridge.ts` **escrito à mão**, instalado como primeiro import do `main.tsx`, com stubs inertes para o que é Electron-only. | Mesma abordagem da ponte manual, em app Vite próprio. |
| [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) | **Não** usa o renderer do desktop: é uma UI própria em Python stdlib + JS puro, com paridade de CLI. | Outra UI. Otimizada para celular via Tailscale (`:8787`), com o host noutro lugar. |
| [NousResearch `apps/mobile/desktop-port`](https://github.com/NousResearch/hermes-agent/pull/64962) (PR #64962) | Leva o renderer para um app iOS (Capacitor/WKWebView) e troca a ponte por um shim **escrito à mão** (`hermes-web-shim.js`), modo remoto com `localStorage["hermes.remoteGateway"]`. | É um cliente iOS apontando para um gateway remoto. Tem até `shim/CONTRACT.md` com evidência `file:line` — excelente referência, feita à mão. |
| [Plugin `hermes-workbench`](https://hermes-agent.nousresearch.com/docs/plugins/hermes-workbench) | "The desktop app, in your browser": constrói o renderer com o Node gerenciado e injeta `hermes-web-shim.js` no dashboard. | Canônico e melhor caminho para headless/VPS/tablet. O alvo é um host, não o próprio telefone. |
| [hermes-webui #2364 / docs/remote-access.md](https://github.com/nesquena/hermes-webui/blob/master/docs/remote-access.md) | **Field report ARM64 Android**: Hermes + WebUI num **Debian 12 em VM via AVF** (Redmi Note 13 Pro, ~1,7 GB), Chrome no `localhost:8787`. | Prior art mais próximo de "o telefone é o host" — mas com VM Debian e outra UI, não Termux + renderer oficial. |
| [Hermes Android v1.0.0](https://www.reddit.com/r/hermesagent/comments/1tspb0a/hermes_android_v100_first_release_of_an_android/) | Cliente Android nativo, conecta no host Hermes por Wi-Fi/Tailscale. | Cliente, não host. |

**Conclusão:** portar a UI do desktop para o navegador é caminho conhecido, e todos os que
achamos **substituem a ponte à mão**. Nenhum deles faz isso *dentro do Termux do próprio
aparelho*, servindo o renderer em loopback para o navegador do mesmo telefone.

---

## 3. Analogia que ajuda a calibrar: Claude Code no Android

[ferrumclaudepilgrim/claude-code-android](https://github.com/ferrumclaudepilgrim/claude-code-android)
publica um port completo para Android com dois caminhos (Termux nativo com `glibc-runner` +
patchelf, e VM AVF), incluindo modelo de segurança, gate de biometria e guia de troubleshooting.
Serve de referência de **qualidade de documentação** para este tipo de projeto — e confirma que
"agente de IA rodando no celular" já tem campo estabelecido.

---

## Nosso delta (o que realmente não achamos publicado)

1. **Telefone é host E cliente.** O renderer oficial de `apps/desktop` é servido em
   `http://127.0.0.1:8890` **pelo Termux do próprio aparelho**, para o navegador do próprio
   aparelho. Sem segunda máquina no caminho: sem outro host, sem host de contêiner, sem túnel,
   sem segundo processo fora do telefone. Todos os ports que achamos põem algo entre o telefone e a UI.
2. **Ponte gerada do contrato de tipos, não escrita à mão.** `apps/desktop/src/global.d.ts` →
   [`gerar-shim-stubs.py`](../scripts/gerar-shim-stubs.py) → `shim-stubs.js` com **160 chaves
   inertes** (medido hoje: 9.688 bytes), sobre as quais o shim real implementa o que importa.
   Escrever a ponte à mão gerava *um erro novo por rodada*; gerar a superfície do contrato fechou
   o buraco de uma vez.
3. **O dashboard oficial como PWA instalável na tela inicial, no Termux**, com watchdog e
   `Termux:Boot`. Inclui um sintoma que não achamos documentado: o `hermes dashboard` **reconstrói
   `web_dist` e apaga o patch PWA** (manifesto, ícones, tags), então o ícone passa a receber HTML
   no lugar do manifesto e o app "quebra" com o servidor vivo.
4. **Playbook de otimização Android medido** — orçamento de memória que bloqueia *toda* gravação
   de memória em silêncio; custo real do índice de skills por mensagem (~100 chars/skill, ~13,7k
   com 137 skills); e o fato de `hermes plugins enable` compilar Rust **no telefone**.
5. **Recarga por SSE para depurar UI sem ver a tela**: `POST /__bump` empurra `location.reload()`
   para as páginas abertas e funciona em aba em segundo plano, onde `setInterval` **não** funciona
   (o Chrome congela timers de aba inativa).

## O que este repositório NÃO reivindica

- Não é o primeiro Hermes no Termux, nem o primeiro Hermes no Android.
- Não é o primeiro port do app desktop para o navegador.
- Não somos upstream de nada: não abrimos PR para a Nous com isso. Os scripts são nossos,
  sem versionamento contra internals, e leem caminhos (`hermes_cli/web_dist`,
  `apps/desktop/dist`) que o upstream pode mover a qualquer commit.
- Tudo aqui foi medido em **um** aparelho: Galaxy S24 Ultra (SM-S928B), Android 16 / One UI 8,
  arm64, 12 GB de RAM, Termux do
  F-Droid/GitHub (build debug). Outros aparelhos podem se comportar diferente.
