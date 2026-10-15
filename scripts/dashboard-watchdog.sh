#!/data/data/com.termux/files/usr/bin/bash
# dashboard-watchdog.sh — mantém o Hermes Web UI (9119) vivo no celular.
# Guarda de instância única por pidfile; checa a cada 60s e ressuscita se cair.
export PATH=/data/data/com.termux/files/usr/bin:$PATH
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PIDFILE="$HERMES_HOME/state/dashboard-watchdog.pid"
mkdir -p "$HERMES_HOME/state" "$HERMES_HOME/logs"

# starttime do /proc (campo 22 = 20º token depois do ')'): identidade do
# processo, imune a reuso de PID. Sem isto, um pidfile antigo com o número
# reaproveitado fazia a guarda achar "já tem um watchdog" — e o dashboard ficava
# sem vigia em silêncio.
starttime() { awk '{n=split($0,a,")"); split(a[n],f," "); print f[20]}' "/proc/$1/stat" 2>/dev/null; }
# pidfile em formato LEGADO (só o pid) não tem starttime para comparar: nesse
# caso confirma pelo cmdline que o dono do pid é mesmo um watchdog.
e_watchdog() { tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null | grep -q 'dashboard-watchdog'; }

other=""; other_st=""
[ -r "$PIDFILE" ] && read -r other other_st <"$PIDFILE"
if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
  atual=$(starttime "$other")
  if [ -n "$other_st" ] && [ -n "$atual" ]; then
    [ "$atual" = "$other_st" ] && exit 0   # o MESMO processo, não só o mesmo pid
  elif e_watchdog "$other"; then
    exit 0                                 # pidfile legado: o pid É um watchdog
  fi
fi
echo "$$ $(starttime $$)" >"$PIDFILE"
# o wake-lock era tomado aqui e NINGUÉM devolvia: depois que o watchdog morre,
# o aparelho nunca mais dormia. O trap cobre saída limpa e SIGTERM (SIGKILL não
# roda trap em lugar nenhum do sistema — por isso a guarda também valida pid)
trap 'rm -f "$PIDFILE"; termux-wake-unlock >/dev/null 2>&1' EXIT

termux-wake-lock >/dev/null 2>&1
UP="$HERMES_HOME/scripts/dashboard-up.sh"

while true; do
  "$UP"
  # mantém também a UI do desktop (porta 8890) no ar
  [ -x "$HERMES_HOME/bin/hermes-desktop-up" ] && "$HERMES_HOME/bin/hermes-desktop-up" >/dev/null 2>&1
  # e o sshd do Termux (porta 8022, so chave publica), config em $PREFIX/etc/ssh/sshd_config.d/*.conf
  if ! pgrep -x sshd >/dev/null 2>&1; then
    echo "$(date '+%F %T') sshd ausente, ressuscitando" >>"$HERMES_HOME/logs/sshd.log"
    sshd >>"$HERMES_HOME/logs/sshd.log" 2>&1
  fi
  # e o Chromium nativo com CDP na 9222 — motor do browser_exec do Hermes
  # (o Hermes não empacota Chromium arm64-android; browser.cdp_url aponta pra cá)
  if ! curl -s -o /dev/null --max-time 4 "http://127.0.0.1:${CHROME_CDP_PORT:-9222}/json/version"; then
    echo "$(date '+%F %T') chromium CDP ausente, ressuscitando" >>"$HERMES_HOME/logs/chrome-cdp.log"
    "$HERMES_HOME/bin/chrome-cdp" up >>"$HERMES_HOME/logs/chrome-cdp.log" 2>&1
  fi
  # e o chat nativo (porta 8891), quando o app existir
  if [ -f "$HERMES_HOME/chat/index.html" ]; then
    curl -s -o /dev/null --max-time 3 "http://127.0.0.1:${HERMES_CHAT_PORT:-8891}/__health" || \
      ( cd "$HOME" && HERMES_DESKTOP_WEB="$HERMES_HOME/chat" HERMES_DESKTOP_WEB_PORT="${HERMES_CHAT_PORT:-8891}" \
        setsid nohup python "$HERMES_HOME/bin/hermes-desktop-web.py" >>"$HERMES_HOME/logs/chat-server.log" 2>&1 </dev/null & )
  fi
  sleep 60
done
