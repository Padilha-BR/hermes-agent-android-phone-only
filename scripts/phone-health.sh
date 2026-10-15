#!/data/data/com.termux/files/usr/bin/bash
# phone-health.sh — saúde do Hermes no CELULAR (Android/Termux). Criado 14/10/2026.
# Uso: bash ~/.hermes/scripts/phone-health.sh [--prune]
#   --prune : apaga logs com mais de 7 dias e rotaciona agent.log grande
set -u
H="${HERMES_HOME:-$HOME/.hermes}"
PRUNE=0; [ "${1:-}" = "--prune" ] && PRUNE=1
ok(){ printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }
bad(){ printf '  \033[31m✗\033[0m %s\n' "$1"; }

echo "=== Hermes no celular — saúde ($(date '+%d/%m/%Y %H:%M')) ==="

# 1. memória dentro do orçamento? (fora do orçamento = não grava memória nova)
# Os limites REAIS vêm do config.yaml (memory_char_limit / user_char_limit).
lim_mem=$(sed -n 's/^[[:space:]]*memory_char_limit:[[:space:]]*//p' "$H/config.yaml" 2>/dev/null | head -1)
lim_usr=$(sed -n 's/^[[:space:]]*user_char_limit:[[:space:]]*//p' "$H/config.yaml" 2>/dev/null | head -1)
for f in MEMORY.md USER.md; do
  case $f in MEMORY.md) lim=${lim_mem:-9000};; USER.md) lim=${lim_usr:-4500};; esac
  lim=${lim//[!0-9]/}; [ -n "$lim" ] || lim=9000
  n=$(python -c "import sys;print(len(open(sys.argv[1],encoding='utf-8').read()))" "$H/memories/$f" 2>/dev/null || wc -m <"$H/memories/$f" 2>/dev/null || echo 0)
  n=${n//[!0-9]/}; [ -n "$n" ] || n=0
  if [ "$n" -gt "$lim" ]; then bad "$f: $n/$lim chars — ESTOURADO, memória bloqueada"
  elif [ "$n" -gt $(( lim * 9 / 10 )) ]; then warn "$f: $n/$lim chars — perto do teto (>=90%), consolide entradas"
  else ok "$f: $n/$lim chars"; fi
done

# 2. skills: quantas entram no prompt de toda mensagem
n=$(find "$H/skills" -name SKILL.md 2>/dev/null | wc -l)
p=$(find "$H/skills-parked" -name SKILL.md 2>/dev/null | wc -l)
ok "skills ativas: $n (estacionadas: $p — restaurar: bash $H/scripts/skills-restore.sh)"

# 3. app do Hermes (dashboard :9119) + watchdog
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:9119/api/health 2>/dev/null)
[ "$code" = "200" ] && ok "dashboard/app respondendo (health 200)" || bad "dashboard fora do ar (health=$code) — corrija: bash $H/scripts/dashboard-up.sh"
wp=""; wp_st=""
[ -r "$H/state/dashboard-watchdog.pid" ] && read -r wp wp_st <"$H/state/dashboard-watchdog.pid"
if [ -n "$wp" ] && [ -d "/proc/$wp" ]; then
  atual=$(awk '{n=split($0,a,")"); split(a[n],f," "); print f[20]}' "/proc/$wp/stat" 2>/dev/null)
  if [ -z "$wp_st" ] || [ -z "$atual" ] || [ "$atual" = "$wp_st" ]; then
    ok "watchdog vivo (pid $wp, PPid $(awk '/^PPid/{print $2}' /proc/$wp/status))"
  else
    warn "pidfile do watchdog aponta para PID reaproveitado pelo sistema — suba de novo: setsid $H/scripts/dashboard-watchdog.sh &"
  fi
else warn "watchdog parado — suba: setsid $H/scripts/dashboard-watchdog.sh &"; fi

# 4. wake lock (sem ele o Android congela o Termux e o app morre)
# O lock pertence ao APP Termux (não conta tomadas) e não tem comando de status
# confável em toda versão do Termux:API — então aqui só mostramos a presença.
# Quem segura (e devolve no trap de saída) é o dashboard-watchdog.
if command -v termux-wake-lock >/dev/null 2>&1; then
  ok "termux-wake-lock instalado (segurado pelo watchdog enquanto ele rodar)"
else warn "termux-wake-lock não encontrado (instale Termux:API p/ segurar o lock)"; fi

# 5. espaço
df -h /data 2>/dev/null | awk 'NR==2{gsub(/G/,"",$4); printf "  espaço livre: %s (uso %s)\n", $4"G", $5}'

# 6. processos pesados acidentais (compilação Rust do gerenciador de plugins)
comp=$(pgrep -f "cargo/registry|uv/sdists" 2>/dev/null | wc -l)
[ "$comp" -eq 0 ] && ok "nenhuma compilação Rust rodando" || bad "$comp processos de compilação ativos (o gerenciador de plugins está buildando — mate por PID)"

# 7. logs
sz=$(du -sk "$H/logs" 2>/dev/null | awk '{print $1}')
ok "logs: $((sz/1024)) MB"
if [ "$PRUNE" = "1" ]; then
  find "$H/logs" -maxdepth 1 -type f -name "*.log*" -mtime +7 -delete 2>/dev/null && ok "logs >7 dias apagados"
  for f in agent.log errors.log; do
    if [ -f "$H/logs/$f" ] && [ "$(wc -c <"$H/logs/$f")" -gt 2000000 ]; then
      # trunca NO LUGAR (não `mv`): o processo que escreve tem o fd aberto e apontaria
      # pro inode antigo — o log pareceria limpo e o espaço NÃO seria liberado.
      tail -c 500000 "$H/logs/$f" > "$H/logs/$f.tmp" && cat "$H/logs/$f.tmp" > "$H/logs/$f" && rm -f "$H/logs/$f.tmp" && ok "$f rotacionado no lugar (>2MB)"
    fi
  done
fi
echo "=== fim ==="
