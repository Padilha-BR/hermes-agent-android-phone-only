#!/data/data/com.termux/files/usr/bin/bash
# Devolve TODAS as skills estacionadas para ~/.hermes/skills (otimizacao Android 14/10/2026)
set -e
H="${HERMES_HOME:-$HOME/.hermes}"
for d in "$H"/skills-parked/*/; do
  rel="${d#$H/skills-parked/}"; rel="${rel%/}"
  [ -f "$d/SKILL.md" ] || continue
  mkdir -p "$H/skills/$(dirname "$rel")"
  mv "$d" "$H/skills/$rel"
  echo "restaurada: $rel"
done
