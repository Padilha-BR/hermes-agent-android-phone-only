#!/data/data/com.termux/files/usr/bin/bash
# Devolve TODAS as skills estacionadas para ~/.hermes/skills (otimizacao Android 14/10/2026)
# Skill = diretório com SKILL.md, em QUALQUER profundidade (as de subcategoria ficam
# em skills-parked/<categoria>/<skill>/ e antes eram ignoradas). O caminho relativo é
# mantido. Se o destino já existe, PULA: `mv` para dentro de um diretório existente
# criava skills/x/x em silêncio.
set -u
H="${HERMES_HOME:-$HOME/.hermes}"
P="$H/skills-parked"
[ -d "$P" ] || { echo "nada estacionado em $P"; exit 0; }

n=0; pulei=0
# ordem C: o SKILL.md de uma skill vem antes do de uma skill aninhada dentro dela,
# e a aninhada viaja junto com a mãe (o [ -d ] abaixo a pula)
while IFS= read -r -d '' f; do
  d=$(dirname "$f")
  [ -d "$d" ] || continue
  rel=${d#"$P"/}
  [ "$rel" = "$d" ] && continue   # SKILL.md solto na raiz de skills-parked
  destino="$H/skills/$rel"
  if [ -e "$destino" ]; then
    echo "PULEI: $rel (já existe em skills/ — resolva à mão)"
    pulei=$((pulei + 1))
    continue
  fi
  mkdir -p "$(dirname "$destino")"
  if mv "$d" "$destino"; then
    echo "restaurada: $rel"
    n=$((n + 1))
  fi
done < <(find "$P" -name SKILL.md -print0 | LC_ALL=C sort -z)

# categorias que ficaram vazias
find "$P" -mindepth 1 -type d -empty -delete 2>/dev/null
echo "$n skill(s) restaurada(s), $pulei pulada(s)"
[ "$pulei" -eq 0 ]
