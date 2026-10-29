#!/data/data/com.termux/files/usr/bin/bash
# dashboard-up.sh — garante que o Hermes Web UI (dashboard, porta 9119) esteja no ar.
# Idempotente: se já responde, sai sem fazer nada.
# Estratégia: start rápido (--skip-build); se não subir em 40s, tenta build completo
# (com backoff: build completo que falha não se repete a cada minuto).
export PATH=/data/data/com.termux/files/usr/bin:$PATH
# Mesma razão do watchdog: PYTHONPATH herdado fixa o code tree antigo e o
# dashboard passa a servir o web_dist da geração anterior.
unset PYTHONPATH
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
LOG="$HERMES_HOME/logs/dashboard.log"
URL="http://127.0.0.1:9119"
CURL=/data/data/com.termux/files/usr/bin/curl
# estado do backoff do build completo: "<epoch da última falha> <falhas seguidas>"
BUILD_ST="$HERMES_HOME/state/dashboard-build-falhas"

mkdir -p "$HERMES_HOME/logs" "$HERMES_HOME/state"

# Lock: o watchdog e o widget/hermes-app podiam rodar este script ao mesmo tempo,
# e um derrubava a subida do outro com `--stop`. Com flock o segundo ESPERA o
# primeiro (até 5 min) e depois só confere a saúde. O fd 9 é fechado (`9>&-`) em
# tudo que fica vivo — herdando o fd, o dashboard seguraria o lock para sempre.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$HERMES_HOME/state/dashboard-up.lock"
  if ! flock -w 300 9; then
    echo "$(date '+%F %T') outro dashboard-up segurou o lock por 5 min — desisti" >>"$LOG"
    exit 1
  fi
fi

up() { "$CURL" -s -o /dev/null --max-time 4 "$URL/api/health"; }

# O build do web_dist apaga o patch que torna o dashboard um app instalável
# (manifest.webmanifest + ícones + tags no index.html). Reaplica se faltar.
ensure_pwa() {
  D="$HOME/hermes-agent/hermes_cli/web_dist"
  P="$HOME/hermes-app"
  [ -f "$D/index.html" ] || return 0
  if [ ! -s "$D/manifest.webmanifest" ] || ! grep -q 'manifest.webmanifest' "$D/index.html" 2>/dev/null; then
    if cp -f "$P/icon-192.png" "$P/icon-512.png" "$D/" 2>>"$LOG"; then :; else
      echo "$(date '+%F %T') aviso: icones PWA nao encontrados em $P (estao fora do repo)" >>"$LOG"
    fi
    if "$HOME/hermes-agent/venv/bin/python" "$P/patch_dashboard_pwa.py" "$D" >>"$LOG" 2>&1; then
      echo "$(date '+%F %T') patch PWA reaplicado em $D" >>"$LOG"
    else
      echo "$(date '+%F %T') FALHA ao reaplicar patch PWA ($P/patch_dashboard_pwa.py) — instalacao do PWA pode ter ficado quebrada" >>"$LOG"
    fi
  fi
  # visual "cara de desktop" (CSS): rebuild do web_dist apaga o bloco, então reaplica
  if [ -f "$HERMES_HOME/desktop-look.css" ] && ! grep -q 'hermes-desktop-look' "$D/index.html" 2>/dev/null; then
    if "$HOME/hermes-agent/venv/bin/python" "$HERMES_HOME/bin/patch-dashboard-look.py" "$D" >>"$LOG" 2>&1; then
      echo "$(date '+%F %T') CSS desktop-look reaplicado" >>"$LOG"
    else
      echo "$(date '+%F %T') FALHA ao reaplicar CSS desktop-look (veja $LOG)" >>"$LOG"
    fi
  fi
}

# devolve o wake-lock tomado abaixo — mas só se ninguém de longa duração estiver
# segurando: o lock é do APP Termux (não conta tomadas) e quem segura normalmente
# é o dashboard-watchdog; derrubar o lock dele aqui deixaria o app sem guarda
falhou() {
  wp=""
  [ -r "$HERMES_HOME/state/dashboard-watchdog.pid" ] && wp=$(awk '{print $1}' "$HERMES_HOME/state/dashboard-watchdog.pid" 2>/dev/null)
  if [ -z "$wp" ] || ! kill -0 "$wp" 2>/dev/null; then
    termux-wake-unlock >/dev/null 2>&1
  fi
  exit 1
}

ensure_pwa
up && exit 0

termux-wake-lock >/dev/null 2>&1
cd "$HOME" || exit 1

# tentativa 1: start rápido (web UI já buildada)
setsid nohup hermes dashboard --skip-build --no-open >>"$LOG" 2>&1 </dev/null 9>&- &
for _ in $(seq 1 20); do
  sleep 2
  if up; then ensure_pwa; echo "$(date '+%F %T') dashboard UP (skip-build)" >>"$LOG"; exit 0; fi
done

# tentativa 2: start completo (build da web UI) — com backoff. Depois de N falhas
# seguidas o próximo build espera 5, 10, 20, 40 min e então 1 h (teto): no máximo
# um build completo por hora enquanto continuar falhando. HERMES_FORCE_BUILD=1 força.
b_ts=0; b_n=0
[ -r "$BUILD_ST" ] && read -r b_ts b_n <"$BUILD_ST"
case "${b_ts}x${b_n}" in *[!0-9x]*|x*|*x) b_ts=0; b_n=0 ;; esac
if [ "$b_n" -gt 0 ] && [ "${HERMES_FORCE_BUILD:-0}" != "1" ]; then
  passo=$((b_n < 5 ? b_n : 5))
  espera=$((300 << (passo - 1))); [ "$espera" -gt 3600 ] && espera=3600
  falta=$((b_ts + espera - $(date +%s)))
  if [ "$falta" -gt 0 ]; then
    echo "$(date '+%F %T') skip-build falhou; build completo adiado por mais ${falta}s ($b_n falha(s) seguidas; HERMES_FORCE_BUILD=1 força)" >>"$LOG"
    falhou
  fi
fi
echo "$(date '+%F %T') skip-build falhou, tentando build completo" >>"$LOG"
hermes dashboard --stop >>"$LOG" 2>&1 9>&-
setsid nohup hermes dashboard --no-open >>"$LOG" 2>&1 </dev/null 9>&- &
for _ in $(seq 1 60); do
  sleep 2
  if up; then
    ensure_pwa; rm -f "$BUILD_ST"
    echo "$(date '+%F %T') dashboard UP (build completo)" >>"$LOG"; exit 0
  fi
done

echo "$(date +%s) $((b_n + 1))" >"$BUILD_ST"
echo "$(date '+%F %T') FALHA: dashboard nao subiu (build completo falhou $((b_n + 1))x seguidas)" >>"$LOG"
falhou
