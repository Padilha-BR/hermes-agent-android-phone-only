#!/data/data/com.termux/files/usr/bin/bash
# Termux:Boot — mantém o app do Hermes (dashboard web, porta 9119) vivo no celular.
# Instalado por Hermes em 14/10/2026. Log: ~/.hermes/logs/boot.log
export PATH=/data/data/com.termux/files/usr/bin:$PATH
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME/logs"
sleep 20   # deixa o Android terminar de subir
echo "$(date '+%F %T') boot: iniciando watchdog do dashboard" >>"$HERMES_HOME/logs/boot.log"
setsid nohup "$HERMES_HOME/scripts/dashboard-watchdog.sh" >>"$HERMES_HOME/logs/dashboard-watchdog.log" 2>&1 </dev/null &
