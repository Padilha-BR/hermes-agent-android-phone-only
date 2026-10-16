#!/data/data/com.termux/files/usr/bin/bash
# dashboard-watchdog.sh — mantém o Hermes Web UI (9119) vivo no celular.
# Guarda de instância única (flock, ou pidfile como reserva); checa a cada 60s e
# ressuscita se cair — com backoff quando o dashboard insiste em não subir.
export PATH=/data/data/com.termux/files/usr/bin:$PATH
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PIDFILE="$HERMES_HOME/state/dashboard-watchdog.pid"
LOCK="$HERMES_HOME/state/dashboard-watchdog.lock"
WLOG="$HERMES_HOME/logs/dashboard-watchdog.log"
mkdir -p "$HERMES_HOME/state" "$HERMES_HOME/logs"

# starttime do /proc (campo 22 = 20º token depois do ')'): identidade do
# processo, imune a reuso de PID. Sem isto, um pidfile antigo com o número
# reaproveitado fazia a guarda achar "já tem um watchdog" — e o dashboard ficava
# sem vigia em silêncio.
starttime() { awk '{n=split($0,a,")"); split(a[n],f," "); print f[20]}' "/proc/$1/stat" 2>/dev/null; }
# pidfile em formato LEGADO (só o pid) não tem starttime para comparar: nesse
# caso confirma pelo cmdline que o dono do pid é mesmo um watchdog.
e_watchdog() { tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null | grep -q 'dashboard-watchdog'; }

# Guarda preferida: flock. O kernel solta o lock quando o processo morre — também
# por SIGKILL (phantom process killer) e reboot, que não rodam trap — e o
# teste-e-trava é atômico. O fd 8 NÃO pode vazar para os filhos (dashboard e
# servidores 8890/8891 sobem desgarrados e vivem mais que o watchdog; herdando o
# fd, segurariam o lock e nenhum watchdog novo subiria): todo comando abaixo que
# pode deixar processo vivo roda com `8>&-`.
# Sem flock (pacote util-linux ausente), vale só a guarda por pidfile + starttime.
if command -v flock >/dev/null 2>&1; then
  exec 8>"$LOCK"
  flock -n 8 || exit 0   # já tem um watchdog rodando
fi
# pidfile: reserva sem flock — e, com flock, pega um watchdog de versão anterior
# (que não usa o lock) ainda vivo
other=""; other_st=""
[ -r "$PIDFILE" ] && read -r other other_st <"$PIDFILE"
if [ -n "$other" ] && [ "$other" != "$$" ] && kill -0 "$other" 2>/dev/null; then
  atual=$(starttime "$other")
  if [ -n "$other_st" ] && [ -n "$atual" ]; then
    [ "$atual" = "$other_st" ] && exit 0   # o MESMO processo, não só o mesmo pid
  elif e_watchdog "$other"; then
    exit 0                                 # pidfile legado: o pid É um watchdog
  fi
fi
# o pidfile continua sendo escrito: hermes-menu e phone-health leem dele
echo "$$ $(starttime $$)" >"$PIDFILE"
# o wake-lock era tomado aqui e NINGUÉM devolvia: depois que o watchdog morre,
# o aparelho nunca mais dormia. O trap cobre saída limpa e SIGTERM (SIGKILL não
# roda trap em lugar nenhum do sistema — por isso a guarda também valida pid)
trap 'rm -f "$PIDFILE"; termux-wake-unlock >/dev/null 2>&1' EXIT

termux-wake-lock >/dev/null 2>&1
UP="$HERMES_HOME/scripts/dashboard-up.sh"

# Backoff: se o dashboard nunca sobe, cada volta custava ~40 s de tentativa +
# `--stop` + build Vite completo, a cada ~60 s — bateria e aparelho quente.
# Depois de falhas seguidas, a próxima chamada ao dashboard-up espera 120, 240,
# 480 e 900 s (teto). O próprio dashboard-up limita o build completo à parte.
# As checagens leves (8890/8891) seguem a cada 60 s.
falhas=0
proximo=0
while true; do
  agora=$(date +%s)
  if [ "$agora" -ge "$proximo" ]; then
    if "$UP" 8>&-; then
      [ "$falhas" -gt 0 ] && echo "$(date '+%F %T') dashboard de volta depois de $falhas falha(s)" >>"$WLOG"
      falhas=0; proximo=0
    else
      falhas=$((falhas + 1))
      passo=$((falhas < 4 ? falhas : 4))
      espera=$((60 << passo)); [ "$espera" -gt 900 ] && espera=900
      proximo=$((agora + espera))
      echo "$(date '+%F %T') dashboard-up falhou ($falhas seguida(s)); próxima tentativa em ${espera}s" >>"$WLOG"
    fi
  fi
  # mantém também a UI do desktop (porta 8890) no ar
  [ -x "$HERMES_HOME/bin/hermes-desktop-up" ] && "$HERMES_HOME/bin/hermes-desktop-up" >/dev/null 2>&1 8>&-
  # e o sshd do Termux (porta 8022, so chave publica), config em $PREFIX/etc/ssh/sshd_config.d/*.conf
  # Só se o openssh estiver instalado: sem ele o laço logava "ressuscitando" todo minuto.
  # E só se o sshd foi CONFIGURADO de propósito (um *.conf em sshd_config.d, ou
  # HERMES_WATCHDOG_SSHD=1): o sshd padrão do Termux escuta em todas as interfaces
  # (e, conforme a versão do pacote, aceita senha) — quem instalou o openssh só pelo
  # cliente `ssh` ganhava um servidor na rede Wi-Fi a cada boot. HERMES_WATCHDOG_SSHD=0 desliga sempre.
  # `8>&-`: o sshd vira daemon e viveria segurando o lock do watchdog.
  sshd_quer="${HERMES_WATCHDOG_SSHD:-}"
  if [ -z "$sshd_quer" ]; then
    sshd_quer=0
    for c in "${PREFIX:-/data/data/com.termux/files/usr}"/etc/ssh/sshd_config.d/*.conf; do
      [ -f "$c" ] && { sshd_quer=1; break; }
    done
  fi
  if [ "$sshd_quer" = 1 ] && command -v sshd >/dev/null 2>&1 && ! pgrep -x sshd >/dev/null 2>&1; then
    echo "$(date '+%F %T') sshd ausente, ressuscitando" >>"$HERMES_HOME/logs/sshd.log"
    sshd >>"$HERMES_HOME/logs/sshd.log" 2>&1 8>&-
  fi
  # e o Chromium nativo com CDP na 9222 — motor do browser_exec do Hermes
  # (o Hermes não empacota Chromium arm64-android; browser.cdp_url aponta pra cá)
  # Só com o Chromium instalado: sem ele o `chrome-cdp up` esperava 25 s e falhava
  # a cada volta do laço, logando "ressuscitando" todo minuto.
  if [ -x "$HERMES_HOME/bin/chrome-cdp" ] && \
     [ -x "${CHROME_BIN:-${PREFIX:-/data/data/com.termux/files/usr}/bin/chromium-browser}" ] && \
     ! curl -s -o /dev/null --max-time 4 "http://127.0.0.1:${CHROME_CDP_PORT:-9222}/json/version"; then
    echo "$(date '+%F %T') chromium CDP ausente, ressuscitando" >>"$HERMES_HOME/logs/chrome-cdp.log"
    "$HERMES_HOME/bin/chrome-cdp" up >>"$HERMES_HOME/logs/chrome-cdp.log" 2>&1 8>&-
  fi
  # e o chat nativo (porta 8891), quando o app existir — pelo hermes-chat-up (guarda
  # contra servidor duplicado; log próprio, sem remendos do desktop e sem telemetria)
  if [ -f "$HERMES_HOME/chat/index.html" ]; then
    if ! curl -s -o /dev/null --max-time 3 "http://127.0.0.1:${HERMES_CHAT_PORT:-8891}/__health"; then
      if [ -x "$HERMES_HOME/bin/hermes-chat-up" ]; then
        "$HERMES_HOME/bin/hermes-chat-up" >/dev/null 2>&1 8>&-
      else
        ( cd "$HOME" && HERMES_DESKTOP_WEB="$HERMES_HOME/chat" HERMES_DESKTOP_WEB_PORT="${HERMES_CHAT_PORT:-8891}" \
          HERMES_WEB_LOG="$HERMES_HOME/logs/chat-telemetria.log" HERMES_WEB_DESKTOP_FIXES=0 \
          HERMES_WEB_TELEMETRY=0 HERMES_WEB_TELEMETRIA=0 \
          setsid nohup python "$HERMES_HOME/bin/hermes-desktop-web.py" >>"$HERMES_HOME/logs/chat-server.log" 2>&1 </dev/null 8>&- & )
      fi
    fi
  fi
  sleep 60
done
