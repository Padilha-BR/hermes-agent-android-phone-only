# 07 — Isso abre porta para um app Android nativo?

**Resposta curta:** abre, mas não pelo lado que parece. O gargalo de um app Android **não é rodar o
Hermes no Android** — isso o upstream já resolveu. O que falta é decisão de produto, canal de
distribuição e alguém para manter.

O que este repositório entrega nessa direção é evidência e um padrão reusável, não um app. Aqui não
tem APK (ver o aviso no topo do README).

---

## Fato: o runtime no Android já é resolvido lá dentro

Levantado no commit `311155fa` (o mesmo do relato), não em suposição:

| Achado | Onde |
|---|---|
| Pipeline que **re-etiqueta wheels para Android** | `scripts/termux/retag_wheel.py` |
| Testes dedicados ao wheelhouse e à linkage do Python no Termux | `tests/scripts/test_termux_wheelhouse_cache.py`, `test_termux_python_linkage.py`, `test_termux_retag_wheel.py` |
| CI constrói as wheels nativas; o alvo é `android_24_arm64_v8a` | doc oficial de Termux ("CI builds the native Python wheels…", "The wheels target Android API 24") |
| `pyproject.toml` **Android-aware de propósito** | `psutil` pinado a um commit específico quando `sys_platform == 'android'`; `uvloop` excluído no Android; comentário sobre PEP 738 e kernels GKI (`-androidNN-` no release do kernel) |

Traduzindo: **a parte que parece o monstro — o Python e as dependências nativas no Android — já tem
solução mantida pelo upstream.** Eles têm gates explícitos de plataforma e wheels publicadas.

## Fato: a UI é separável — e nós provamos o caminho que faltava

- `apps/desktop` depende de **`@hermes/shared` (`file:../shared`)**, o **mesmo** pacote que o
  dashboard do navegador usa.
- O renderer conversa com o backend por **um único objeto**: `window.hermesDesktop`.
- Existe um **PR de port para iOS** (comunidade, não o time principal) que segue exatamente esse
  padrão, com um shell Capacitor/WKWebView.
- No commit `311155fa`, **`apps/mobile` não existe** no repositório: a porta está desenhada, ninguém
  atravessou do lado Android.

**O que este repo acrescenta e não estava publicado:**

1. O renderer roda **sem Electron e sem app instalado** — servido em loopback, no navegador do
   próprio aparelho (doc 03). É a terceira opção entre "Electron no desktop" e "app Capacitor".
2. A **ponte gerada do contrato de tipos** (`global.d.ts` → 160 chaves inertes, ver doc 03). O
   `CONTRACT.md` do port iOS é escrito à mão; gerar a superfície é o que faz um shell novo deixar de
   ser arqueologia.
3. O **envelope do Android medido** (doc 05): orçamento de memória que bloqueia gravação em silêncio,
   ~100 chars por skill por mensagem, o toggle de plugin compilando Rust no aparelho, e o build do
   dashboard apagando o `web_dist`.

## Os bloqueios reais — e nenhum deles é do Hermes

| Bloqueio | O que exige |
|---|---|
| **Play Store** | Um app que executa comando de shell arbitrário não passa na política do Google. O canal realista é F-Droid/sideload — igual ao pacote Termux oficial, que também não é Play. |
| **Processo em segundo plano** | `Foreground Service` com notificação fixa. É o equivalente app do nosso watchdog + `termux-wake-lock` (doc 02). |
| **Armazenamento com escopo** | SAF (seletor de arquivo) no lugar de caminhos. Nossos scripts assumem `~` e `~/storage/*`; um app precisa pedir cada árvore. |
| **A ferramenta `terminal`** | Rodar shell dentro do sandbox do app é trabalho real — ou o app define um subconjunto de ferramentas. A doc do upstream já diz que Docker e automação de navegador ficam fora no Android. |
| **Manter uma quarta UI** | Desktop, dashboard, CLI/TUI e mobile são quatro superfícies para o mesmo backend. É por isso que o caminho barato é **reusar** o renderer, não reescrevê-lo. |

## Os três caminhos, com preço

| Caminho | O que custa | Veredito |
|---|---|---|
| **PWA na tela inicial** (o que este repo entrega) | zero instalação, zero loja, zero manutenção de app; limitações: terminal in-app, git nativo, diálogos e HUD ficam inertes | **80% da experiência por 0% do custo** |
| **Shell Capacitor/WebView** reusando o renderer + `apps/shared` | reusar a ponte (aqui: gerada), `Foreground Service`, SAF, empacotar e distribuir | **o caminho curto para um app de verdade** — e o nosso gerador remove a parte chata |
| **UI nativa (Compose) do zero** | reescrever o renderer e perder a paridade com o upstream | caro e **contra a arquitetura do Hermes**: o renderer é compartilhado e versionado por design |

## O que eu levaria para o upstream

O achado com valor de contribuição não é o relato em si — é o **gerador da ponte**. O
`CONTRACT.md` do port iOS já é, na prática, a especificação que falta ser mecanizada. Se o upstream
gerar a ponte do contrato, **todo cliente mobile futuro** (iOS, Android, WebView) deixa de escrever
shim à mão e ganha stubs com o tipo certo de graça.

## O que é fato e o que é leitura

- **Fato:** os arquivos, testes, gates de plataforma e a ausência de `apps/mobile` no commit citado —
  tudo acima foi conferido no código daquele commit.
- **Leitura minha:** os custos e vereditos da tabela de caminhos, e a avaliação de política da Play
  Store. São julgamento, não medição.
- Não testamos nenhum shell Capacitor nem empacotamos nada. O que testamos está nos docs 01–06.
