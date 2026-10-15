#!/data/data/com.termux/files/usr/bin/bash
# dashboard-up.sh — garante que o Hermes Web UI (dashboard, porta 9119) esteja no ar.
# Idempotente: se já responde, sai sem fazer nada.
# Estratégia: start rápido (--skip-build); se não subir em 40s, tenta build completo.
export PATH=/data/data/com.termux/files/usr/bin:$PATH
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
LOG="$HERMES_HOME/logs/dashboard.log"
URL="http://127.0.0.1:9119"
CURL=/data/data/com.termux/files/usr/bin/curl

mkdir -p "$HERMES_HOME/logs"

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
  if [ -f "$HOME/.hermes/desktop-look.css" ] && ! grep -q 'hermes-desktop-look' "$D/index.html" 2>/dev/null; then
    if "$HOME/hermes-agent/venv/bin/python" "$HOME/.hermes/bin/patch-dashboard-look.py" "$D" >>"$LOG" 2>&1; then
      echo "$(date '+%F %T') CSS desktop-look reaplicado" >>"$LOG"
    else
      echo "$(date '+%F %T') FALHA ao reaplicar CSS desktop-look (veja $LOG)" >>"$LOG"
    fi
  fi
}

ensure_pwa
up && exit 0

termux-wake-lock >/dev/null 2>&1
cd "$HOME" || exit 1

# tentativa 1: start rápido (web UI já buildada)
setsid nohup hermes dashboard --skip-build --no-open >>"$LOG" 2>&1 </dev/null &
for _ in $(seq 1 20); do
  sleep 2
  if up; then ensure_pwa; echo "$(date '+%F %T') dashboard UP (skip-build)" >>"$LOG"; exit 0; fi
done

# tentativa 2: start completo (build da web UI)
echo "$(date '+%F %T') skip-build falhou, tentando build completo" >>"$LOG"
hermes dashboard --stop >>"$LOG" 2>&1
setsid nohup hermes dashboard --no-open >>"$LOG" 2>&1 </dev/null &
for _ in $(seq 1 60); do
  sleep 2
  if up; then ensure_pwa; echo "$(date '+%F %T') dashboard UP (build completo)" >>"$LOG"; exit 0; fi
done

echo "$(date '+%F %T') FALHA: dashboard nao subiu" >>"$LOG"
# devolve o wake-lock tomado acima — mas só se ninguém de longa duração estiver
# segurando: o lock é do APP Termux (não conta tomadas) e quem segura normalmente
# é o dashboard-watchdog; derrubar o lock dele aqui deixaria o app sem guarda
wp=""
[ -r "$HERMES_HOME/state/dashboard-watchdog.pid" ] && wp=$(awk '{print $1}' "$HERMES_HOME/state/dashboard-watchdog.pid" 2>/dev/null)
if [ -z "$wp" ] || ! kill -0 "$wp" 2>/dev/null; then
  termux-wake-unlock >/dev/null 2>&1
fi
exit 1
