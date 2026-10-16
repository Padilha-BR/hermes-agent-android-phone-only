#!/data/data/com.termux/files/usr/bin/bash
# Botão da tela inicial (Termux:Widget): aliviar o celular de builds pesados.
# O widget NÃO lê o ~/.bashrc: PATH e caminho absolutos aqui, senão "command not found".
export PATH="$HOME/.hermes/bin:$HOME/hermes-agent/venv/bin:/data/data/com.termux/files/usr/bin:$PATH"
"$HOME/.hermes/bin/hermes-calm"
printf "\n(toque Enter para fechar)\n"
read -r _
