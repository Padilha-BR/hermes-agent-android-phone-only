#!/data/data/com.termux/files/usr/bin/bash
# Botão da tela inicial (Termux:Widget): abrir a UI do Hermes que o usuário realmente usa.
# Antes abria o dashboard (9119, painel de admin — caía na lista de Sessions).
# Agora: garante o backend/dashboard no ar E abre a UI do desktop portada (8890).
"$HOME/.hermes/scripts/dashboard-up.sh"          # backend que a UI consome
"$HOME/.hermes/bin/hermes-desktop-up" >/dev/null 2>&1   # sobe a UI portada se estiver fora
sleep 1
URL="http://127.0.0.1:8890"
H=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$URL/" 2>/dev/null)
if [ "$H" = "200" ]; then
  echo "Hermes: UI do desktop no ar ($URL)"
  termux-open-url "$URL" >/dev/null 2>&1
else
  echo "Hermes: a UI nao subiu (http=$H). Log: ~/.hermes/logs/desktop-web.log"
fi
printf "\n(toque Enter para fechar)\n"
read -r _
