#!/data/data/com.termux/files/usr/bin/bash
# Botão da tela inicial (Termux:Widget): abrir o Hermes para conversar.
# O widget NÃO lê o ~/.bashrc: PATH e caminho absolutos aqui, senão "command not found".
export PATH="$HOME/.hermes/bin:$HOME/hermes-agent/venv/bin:/data/data/com.termux/files/usr/bin:$PATH"
exec "$HOME/.hermes/bin/hermes-mobile"
