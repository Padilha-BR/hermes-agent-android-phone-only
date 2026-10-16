#!/data/data/com.termux/files/usr/bin/bash
# install.sh — copia os scripts deste repositório para onde eles esperam estar no
# telefone. Idempotente: pode rodar de novo a cada `git pull`.
#
#   scripts/*.sh, build-desktop-web.mjs → $HERMES_HOME/scripts/  (o watchdog chama daqui)
#   comandos, .py e shim*.js            → $HERMES_HOME/bin/
#   scripts/desktop-port.css            → $HERMES_HOME/desktop-port.css (CSS do porte, 8890)
#   scripts/chat/                       → $HERMES_HOME/chat/
#   scripts/shortcuts/*                 → ~/.shortcuts/      (Termux:Widget)
#   scripts/10-hermes-dashboard.sh      → ~/.termux/boot/    (Termux:Boot)
#
# Uso: bash install.sh [--bashrc] [--dry-run]
#   --bashrc  : acrescenta $HERMES_HOME/bin ao PATH no ~/.bashrc (uma vez, com marcador)
#   --dry-run : só mostra o que faria
# Arquivo existente com conteúdo diferente é guardado antes de ser trocado em
# $HERMES_HOME/backup/install/<caminho>.<data>.bak (0600, sem execução) — FORA dos
# diretórios de destino: um .bak executável em ~/.termux/boot rodaria no boot e em
# ~/.shortcuts viraria um atalho duplicado. Não toca em config, chaves, memórias nem logs.
set -u
cd "$(dirname "$0")" || exit 1
H="${HERMES_HOME:-$HOME/.hermes}"
BASHRC=0; DRY=0
for a in "$@"; do
  case "$a" in
    --bashrc) BASHRC=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "opcao desconhecida: $a (veja --help)"; exit 1 ;;
  esac
done

copiados=0; iguais=0
QUANDO="$(date +%Y%m%d-%H%M%S)"
BACKUP="$H/backup/install"
# guarda a versão atual de <destino> fora do diretório dele, sem bit de execução
guardar() {
  local dst="$1" rel
  case "$dst" in
    "$HOME"/*) rel="${dst#"$HOME"/}" ;;
    *) rel="${dst#/}" ;;
  esac
  mkdir -p "$(dirname "$BACKUP/$rel")" &&
    cp "$dst" "$BACKUP/$rel.$QUANDO.bak" && chmod 600 "$BACKUP/$rel.$QUANDO.bak"
}
# instala <origem> em <destino> com o modo dado; pula se já é idêntico
instalar() {
  local src="$1" dst="$2" modo="$3"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    iguais=$((iguais+1))
    [ "$DRY" = 1 ] || chmod "$modo" "$dst"
    return 0
  fi
  if [ "$DRY" = 1 ]; then echo "  copiaria $src → $dst"; copiados=$((copiados+1)); return 0; fi
  mkdir -p "$(dirname "$dst")" || return 1
  if [ -f "$dst" ] && ! guardar "$dst"; then
    echo "FALHA ao guardar a versão atual de $dst — não troquei"; return 1
  fi
  # .tmp + mv: um script em execução (o watchdog, por exemplo) não lê arquivo pela metade
  cp "$src" "$dst.tmp.$$" && chmod "$modo" "$dst.tmp.$$" && mv -f "$dst.tmp.$$" "$dst" || {
    rm -f "$dst.tmp.$$"; echo "FALHA ao instalar $dst"; return 1; }
  echo "  $dst"
  copiados=$((copiados+1))
}

falhas=0
echo "== $H/scripts"
for f in dashboard-up.sh dashboard-watchdog.sh phone-health.sh skills-restore.sh; do
  instalar "scripts/$f" "$H/scripts/$f" 755 || falhas=$((falhas+1))
done
instalar scripts/build-desktop-web.mjs "$H/scripts/build-desktop-web.mjs" 644 || falhas=$((falhas+1))

echo "== $H/bin"
for f in scripts/*; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in
    # .sh/.mjs vão para scripts/, shims logo abaixo; .css não é comando (vai para $H)
    *.sh|*.md|*.mjs|*.js|*.css) continue ;;
  esac
  instalar "$f" "$H/bin/$(basename "$f")" 755 || falhas=$((falhas+1))
done
instalar scripts/shim-desktop.js "$H/bin/shim-desktop.js" 644 || falhas=$((falhas+1))
# os stubs dependem da versão do Hermes instalada: a cópia do repo é só ponto de
# partida — se já existe um gerado no aparelho, ele fica
if [ -f "$H/bin/shim-stubs.js" ]; then
  echo "  (mantido $H/bin/shim-stubs.js — regenere com gerar-shim-stubs.py se o Hermes mudou)"
else
  instalar scripts/shim-stubs.js "$H/bin/shim-stubs.js" 644 || falhas=$((falhas+1))
fi

echo "== $H (CSS do porte, lido pelo hermes-desktop-up via HERMES_WEB_EXTRA_CSS)"
instalar scripts/desktop-port.css "$H/desktop-port.css" 644 || falhas=$((falhas+1))

echo "== $H/chat"
for f in scripts/chat/*; do
  [ -f "$f" ] || continue
  instalar "$f" "$H/chat/$(basename "$f")" 644 || falhas=$((falhas+1))
done
# fontes do chat: não estão no repo (licença/peso), mas o bundle do desktop as tem
BUNDLE="${HERMES_DESKTOP_WEB:-$HOME/hermes-agent/.hermes-port/renderer}"
for nome in JetBrainsMono-Regular JetBrainsMono-Bold; do
  [ -f "$H/chat/fonts/$nome.woff2" ] && continue
  achado=$(find "$BUNDLE/assets" -maxdepth 1 -name "$nome-????????.woff2" 2>/dev/null | head -1)
  if [ -n "$achado" ]; then
    instalar "$achado" "$H/chat/fonts/$nome.woff2" 644 || falhas=$((falhas+1))
  else
    echo "  aviso: $nome.woff2 fora do repo e nao achado em $BUNDLE/assets — o chat cai na fonte mono do sistema"
  fi
done

echo "== shebangs (Android 17 parou de reescrever /usr/bin/env)"
# Até o Android 16 o `termux-exec` reescrevia `#!/usr/bin/env python3` para o env
# do Termux. No Android 17 isso PAROU: o script morre com
#   bad interpreter: No such file or directory
# mesmo existindo, executável, com o interpretador instalado — o erro aponta
# para o lugar errado e custa tempo. Aqui o shebang passa a apontar para o env
# do próprio Termux.
# Roda SEMPRE, e não só quando um arquivo acabou de ser copiado: correção presa
# ao ramo de "instalação nova" nunca roda em quem ATUALIZA o aparelho, e o
# defeito sobrevive à atualização sem ninguém perceber.
PREFIXO="${PREFIX:-/data/data/com.termux/files/usr}"
consertados=0
for f in "$H/bin"/* "$H/scripts"/*; do
  [ -f "$f" ] || continue
  head -1 "$f" 2>/dev/null | grep -qE '^#!/(usr/)?bin/env ' || continue
  if [ "$DRY" = 1 ]; then echo "  consertaria o shebang de $f"; continue; fi
  if sed -i "1s|^#![^[:space:]]*/env |#!$PREFIXO/bin/env |" "$f"; then
    echo "  $f"
    consertados=$((consertados+1))
  else
    echo "  FALHA no shebang de $f"
    falhas=$((falhas+1))
  fi
done
[ "$consertados" -eq 0 ] && echo "  (todos já apontavam para o env do Termux)"

echo "== ~/.shortcuts (Termux:Widget)"
for f in scripts/shortcuts/*.sh; do
  [ -f "$f" ] || continue
  instalar "$f" "$HOME/.shortcuts/$(basename "$f")" 700 || falhas=$((falhas+1))
done

echo "== ~/.termux/boot (Termux:Boot)"
instalar scripts/10-hermes-dashboard.sh "$HOME/.termux/boot/10-hermes-dashboard.sh" 700 || falhas=$((falhas+1))

if [ "$BASHRC" = 1 ]; then
  MARCA="# hermes-agent-android-phone-only: PATH"
  if grep -qF "$MARCA" "$HOME/.bashrc" 2>/dev/null; then
    echo "== ~/.bashrc já tem a linha do PATH"
  elif [ "$DRY" = 1 ]; then
    echo "== acrescentaria a linha do PATH no ~/.bashrc"
  else
    printf '\n%s\nexport PATH="%s/bin:$PATH"\n' "$MARCA" "$H" >>"$HOME/.bashrc"
    echo "== PATH acrescentado no ~/.bashrc (abra um shell novo)"
  fi
fi

echo
echo "instalados: $copiados  ·  já iguais: $iguais  ·  shebangs corrigidos: $consertados  ·  falhas: $falhas"
[ "$DRY" = 1 ] && exit 0

# o que NÃO está no repo e o resto do sistema procura (só avisa)
for f in "$HOME/hermes-app/patch_dashboard_pwa.py" "$HOME/hermes-app/icon-192.png" \
         "$HOME/hermes-app/icon-512.png" "$H/desktop-look.css" "$H/desktop-fonts.css"; do
  [ -e "$f" ] || echo "ausente (fora do repo, opcional): $f"
done
echo "veja scripts/README.md → 'O que não está no repositório'."
# processos de verdade (argv0 = python) rodando <script>: `pgrep -f` sozinho casa
# qualquer shell que cite o nome
pids_python() {
  local pid argv0 lista=""
  for pid in $(pgrep -f "$1" 2>/dev/null); do
    # 2> antes do <: processo que morreu no meio não suja a saída
    argv0=$(tr '\0' '\n' 2>/dev/null < "/proc/$pid/cmdline" | head -1)
    case "$argv0" in *python*) lista="$lista $pid" ;; esac
  done
  echo "${lista# }"
}
if [ "$copiados" -gt 0 ]; then
  if pgrep -f dashboard-watchdog.sh >/dev/null 2>&1; then
    # pelo pidfile, não por `pkill -f`: rodado via `bash -c` o pkill casa a própria shell
    echo "o watchdog em execução é a versão anterior — reinicie:"
    echo "  kill \$(cut -d' ' -f1 $H/state/dashboard-watchdog.pid); setsid $H/scripts/dashboard-watchdog.sh &"
  fi
  web=$(pids_python hermes-desktop-web.py)
  if [ -n "$web" ]; then
    echo "servidores da UI/chat (8890/8891) rodando o código anterior (pid $web) — reinicie:"
    echo "  kill $web   # o watchdog sobe os dois de novo em até 60 s (ou: hermes-desktop-up; hermes-chat)"
  fi
  pk=$(pids_python hermes-picker.py)
  if [ -n "$pk" ]; then
    echo "seletor de fotos rodando o código anterior (pid $pk) — encerre: kill $pk (o hermes-imagem sobe o novo)"
  fi
fi
[ "$falhas" -eq 0 ]
