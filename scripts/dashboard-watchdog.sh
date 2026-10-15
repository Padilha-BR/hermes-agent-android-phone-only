#!/data/data/com.termux/files/usr/bin/bash
# dashboard-watchdog.sh — mantém o Hermes Web UI (9119) vivo no celular.
# Guarda de instância única por pidfile; checa a cada 60s e ressuscita se cair.
export PATH=/data/data/com.termux/files/usr/bin:$PATH
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PIDFILE="$HERMES_HOME/state/dashboard-watchdog.pid"
mkdir -p "$HERMES_HOME/state" "$HERMES_HOME/logs"

if [ -f "$PIDFILE" ]; then
  other=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    exit 0   # já tem um watchdog rodando
  fi
fi
echo $$ >"$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

termux-wake-lock >/dev/null 2>&1
UP="$HERMES_HOME/scripts/dashboard-up.sh"

while true; do
  "$UP"
  # mantém também a UI do desktop (porta 8890) no ar
  [ -x "$HERMES_HOME/bin/hermes-desktop-up" ] && "$HERMES_HOME/bin/hermes-desktop-up" >/dev/null 2>&1
  # e o chat nativo (porta 8891), quando o app existir
  if [ -f "$HERMES_HOME/chat/index.html" ]; then
    curl -s -o /dev/null --max-time 3 "http://127.0.0.1:${HERMES_CHAT_PORT:-8891}/__health" || \
      ( cd "$HOME" && HERMES_DESKTOP_WEB="$HERMES_HOME/chat" HERMES_DESKTOP_WEB_PORT="${HERMES_CHAT_PORT:-8891}" \
        setsid nohup python "$HERMES_HOME/bin/hermes-desktop-web.py" >>"$HERMES_HOME/logs/chat-server.log" 2>&1 </dev/null & )
  fi
  sleep 60
done
