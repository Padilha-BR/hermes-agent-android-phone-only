# 01 — Instalação no Termux: a ordem que funciona

Registro de campo de 14/10/2026 (Galaxy S24 Ultra SM-S928B, Android 16 / API 36, One UI 8, arm64,
12 GB de RAM). Não é o caminho
oficial — é o caminho que **funcionou de verdade** naquele dia, com os erros que custaram tempo.

> Antes de tudo: o Termux (Android) é Tier 2 no upstream. O que está escrito nas docs
> envelhece rápido. A fonte da verdade é o `pyproject.toml` do checkout que você clonou.

## Escolha o build do Termux pelo que você precisa automatizar

- `adb shell run-as com.termux …` só funciona em APK **debuggable**. O build do F-Droid
  **não** é (`run-as: package not debuggable`). Use o **debug build do GitHub release**:
  `https://github.com/termux/termux-app/releases` →
  `termux-app_v<ver>+apt-android-7-github-debug_arm64-v8a.apk`.
  Pre-releases (`v0.119.0-beta.3`) trazem os mesmos assets — confira
  `releases?per_page=15`, porque `releases/latest` pula pre-release.
- Trocar de fonte exige `adb uninstall com.termux` antes (assinaturas diferentes →
  `INSTALL_FAILED_UPDATE_INCOMPATIBLE`). **Os dados do Termux são perdidos** — avise quem for fazer.
- Com build debuggable você nunca precisa da tela do telefone desbloqueada: o bootstrap extrai
  mesmo com o keyguard levantado, e `$PREFIX` aparece em ~10s.

## Chegando num shell de verdade: SSH pelo cabo

Digitar no Termux com `adb shell input text` é frágil (`%s` para espaço, sem metacaracteres) e
exige tela desbloqueada e focada. Caminho determinístico:

```bash
# no telefone, como o UID do Termux: escreva ~/.ssh/authorized_keys, então
sshd                      # escuta em 8022
# no computador, com o cabo ligado
adb forward tcp:8022 tcp:8022
ssh -i ./key -p 8022 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null <qualquer>@127.0.0.1
```

O `sshd` do Termux ignora o usuário; só autenticação por chave. Rode instalações longas dentro
de `tmux` — o daemon sobrevive à sessão SSH e à porta USB morrendo.

Injetar script no telefone via stdin do adb, sem digitar nada:

```bash
adb shell "run-as com.termux sh -c 'cat > /data/data/com.termux/files/home/boot.sh'" < boot.sh
```

Ao chamar binários do Termux por `run-as`, conserte o ambiente antes — `PREFIX`/`PATH` **não**
existem ali:

```bash
export PREFIX=/data/data/com.termux/files/usr
export HOME=/data/data/com.termux/files/home
export PATH=$PREFIX/bin:$PREFIX/bin/applets:$PATH
export TMPDIR=$PREFIX/tmp
```

## Ordem de instalação (cada passo existe porque a ordem ingênua quebra)

1. **`apt update && apt full-upgrade -y -o Dpkg::Options::=--force-confold` antes de qualquer coisa.**
   Um Termux recém-instalado pode vir com `libcurl` mais novo que o `libssl`, e isso mata o binário
   `curl` (`cannot locate symbol "SSL_set_quic_tls_early_data_enabled"`). Como o `pkg` usa `curl`
   para escolher espelho, o `pkg` morre em seguida com *"No mirror or mirror group selected"*.
   Conserte o skew com `apt` (não `pkg`) e só então o `pkg` volta a funcionar.
2. **Escolha espelho rápido antes dos downloads grandes.** Escreva `$PREFIX/etc/apt/sources.list`
   com `deb <mirror> stable main`. `mirror.mwt.me/termux/main` deu ~7 MB/s do Brasil contra ~1 MB/s
   da maioria. Meça bytes de verdade, não o arquivo `Release` (que é minúsculo e engana).
3. **Leia `requires-python` no `pyproject.toml` do clone — não confie na faixa das docs.**
   Em 14/10/2026 o repo dizia `>=3.11,<3.15` (**3.14 OK**) e o `constraints-termux.txt` **não
   existia mais**, enquanto as docs ainda falavam em `<3.14` e no arquivo de constraints.
   Prefira o `python` do repo principal do Termux (3.14): ele tem companheiros pré-compilados
   (`python-cryptography`, `python-lxml`, `python-numpy`, `python-pillow`) e o pip produz wheels
   com tag Android. O `python3.13` do TUR é o fallback documentado, não o padrão.
4. **Dependências de build:** `clang rust make pkg-config libffi openssl openssl-tool binutils
   cmake ninja patchelf libxml2 libxslt nodejs ripgrep ffmpeg`. O pacote `openssl` **não** traz
   CLI; quem dá o comando `openssl` é o `openssl-tool`.
5. **`python -m venv --system-site-packages venv`** para que as bibliotecas pré-compiladas do
   Termux sejam importáveis e o pip pare de tentar compilar elas.
6. **`python -m pip install -e '.[termux]'`.**

## As armadilhas que mais custaram tempo

- **Crates Rust compilam em série por padrão.** O `pydantic-core` traz
  `[profile.release] lto = "fat", codegen-units = 1` → **20-40 min num telefone** por pacote.
  O Cargo honra override por variável de ambiente, então exporte antes do `pip install`:
  `CARGO_PROFILE_RELEASE_LTO=off`, `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16`, `CARGO_BUILD_JOBS=8`.
  Mesmo resultado, minutos em vez de dezenas de minutos.
- **`pillow-heif`** (puxado pelo marcador de 3.14) falha com
  `Package libheif was not found in the pkg-config search path`. Conserto: `apt install -y libheif`.
- **O pip cacheia wheels** em `~/.cache/pip/wheels`; uma rodada morta retoma barato. Confira o
  cache antes de concluir que uma instalação interrompida perdeu tudo.
- **Nunca `pkill -f "<padrão>"` quando a sua própria linha de comando contém aquele padrão.**
  `pkill -f "pip install"`, `pkill -f "hermes dashboard"`, `pkill -f rustc` matam a shell que
  executa o próprio comando (exit -15/-9). Mate por PID, por sessão do tmux, ou com o comando
  próprio da ferramenta (`hermes dashboard --stop`). **Numa sessão de agente isso é fácil de
  violar duas vezes seguidas** — dentro do Hermes, mate processos pelo `process_manage`, que não
  consegue casar com a própria linha de comando.
- **`hermes version` não existe**; use `hermes --version`. O `hermes doctor` aponta o resto.
- **O Android mata processos "fantasma"**: desligue o assassino antes de começar:
  `adb shell settings put global settings_enable_monitor_phantom_procs false`.
- Telefone bloqueado tudo bem — e `adb exec-out screencap -p > shot.png` continua sendo como você
  lê a tela quando precisar confirmar um prompt de app.

## Verifique como o usuário vai usar

Não pare num pip verde. Prove dentro do app que a pessoa abre:

```bash
adb shell 'am start -n com.termux/.app.TermuxActivity'
adb shell 'input text "hermes%s--version"'; adb shell 'input keyevent 66'
adb exec-out screencap -p > shot.png   # e leia a imagem
```

E prove **tool calling**, não só conversa:

```bash
hermes -z "use the terminal tool to run uname -m and report it"
```

Um `PATH` que funciona por SSH pode ser diferente no shell do app. Teste o shell do app.

## Config de modelo no telefone

```bash
hermes config set model.provider … ; hermes config set model.default …
# (nunca edite config.yaml na mão)
```

Chaves vão num arquivo de ambiente dentro do `HERMES_HOME` com `chmod 600`, de preferência por
pipe (para nunca caírem num transcript). O nome da variável é o que o provedor que você escolher
exigir — exemplo com um nome genérico:

```bash
KEY=$(grep -m1 '^SUA_CHAVE_API=' "$HERMES_HOME/.env" | cut -d= -f2- | tr -d '\r')
printf 'SUA_CHAVE_API=%s\n' "$KEY" | ssh -i ./key -p 8022 … 'cat > ~/.hermes/.env'
```

Nunca versione o arquivo de ambiente, e nunca cole chave em transcript de chat ou em issue.

## Espaço em disco (medido no aparelho)

| Item | Tamanho |
|---|---|
| Base do Termux (`$PREFIX/usr`) | 1,9 GB |
| `hermes-agent` (checkout) | 1,8 GB — `venv` 151 MB, `node_modules` 1,1 GB, `apps/` 128 MB, `.git` 86 MB |
| `HERMES_HOME` (`~/.hermes`) | 1,3 GB — `cache/uv` 959 MB, `skills` 56 MB, resto é log/state/sessão |
| `$HOME/.cargo` | 1,0 GB — cache de build Rust, **purgável** |
| `$HOME/.cache/pip` | 17 MB |
| **Total instalado** | **~4,8 GB** (~5,8 GB contando o cache do cargo) |
| Livre no `/data` | 47 GB de 222 GB (79% em uso) — 175 GB já ocupados no aparelho |

Duas leituras úteis:

- Os 1,1 GB de `node_modules` só existem por causa do **build da UI do desktop** (Vite). Se você
  não for portar o app desktop, essa parte inteira desaparece.
- Limpar o cache do uv inteiro só quando faltar espaço — ele evita recompilar tudo de novo. Os
  sdists de uma build storm sozinhos valiam ~640 MB.
