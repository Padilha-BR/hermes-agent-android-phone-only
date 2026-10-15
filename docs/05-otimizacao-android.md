# 05 — Otimização para Android (Termux): o que é medido, não achismo

Feito em 14/10/2026. Cada mensagem no telefone carrega SOUL + MEMORY.md + USER.md + o índice de
skills. Naquele dia isso custava **~4,9k tokens por mensagem** — e a memória estava **quebrada**
sem avisar na tela. Se for reotimizar, siga a ordem.

1. **O orçamento de memória do celular é o menor que existir**: `MEMORY.md` 2200 chars,
   `USER.md` 1375. Sintoma de estouro (aparece **só** em `logs/errors.log`, nada na tela):
   `MEMORY.md exceeds its char limit on load: N/2200 chars ... further additions are blocked`
   — e enquanto não voltar ao orçamento o agente **não grava memória nova, calado**.

2. **Meça antes de confiar**: `wc -c ~/.hermes/memories/*`. O limite conta **caracteres**, então
   bytes um pouco acima do limite ainda podem estar OK.

3. **Deixe ~10% de folga.** O `add` é all-or-nothing: em 2150/2200 a entrada seguinte é recusada
   inteira (`free at least N chars more`) — inclusive quando vem `patch` no mesmo batch.

4. **O scanner de ameaças bloqueia entradas em silêncio**: `tools/threat_patterns.py` (scope
   `strict`) casa caminhos de arquivo de ambiente do Hermes e marca a entrada como
   `[BLOCKED: …]` **no system prompt** — o texto cru continua no arquivo e segue contando no
   orçamento. Escreva "o arquivo de ambiente do HERMES_HOME", nunca o caminho literal. Valide com
   `scan_for_threats(entry, scope="strict")` no venv antes de confiar num rewrite.

5. **O índice de skills custa ~100 chars por skill em TODA mensagem** (~13,7k com 137 skills — e
   137 é o que vem quando as skills de outra máquina são copiadas junto). Estacionar as impossíveis/inúteis
   no Android em `~/.hermes/skills-parked/` (fora de `skills/`) baixou para **~9,7k**. Manifesto em
   `skills-parked/MANIFEST.json`; voltar: [`skills-restore.sh`](../scripts/skills-restore.sh).
   `hermes update` / `skills repair-official` pode re-semear built-ins — reconferir depois.
   Não existe knob de config para o tamanho do índice (só `skills.auto_load`, que pina skills
   **inteiras** — o oposto do que se quer).

6. **NUNCA usar `hermes plugins enable|disable` no Termux.** O toggle resolve dependências pelo
   gerenciador de plugins, que **compila Rust no próprio celular** (`pydantic-core`, `jiter`,
   `cryptography`, `firecrawl-anydoc`). Tentamos: >7 min travado, ~1,6 GB de cache e bateria/CPU
   queimando — parece "comando pendurado", mas é build. Faça pelo config:

   ```bash
   hermes config set plugins.disabled '["wecom-platform", …]'
   # deny-list vence a allow-list (plugins.enabled); confirme com:
   hermes plugins list --plain
   ```

7. **Ao matar build, nunca `pkill -f rustc`**: a própria linha de comando contém "rustc" e a shell
   se mata (exit -9). Percorra `/proc`, pule o próprio pid, mate por pid. O plugin host fica com
   PPid 1 (`pm/worker.py`) e segura `.install.lock`.

8. **A verificação que vale:**

   ```bash
   hermes -z "diga apenas: ok"
   grep -iE "memory|BLOCKED" ~/.hermes/logs/errors.log
   ```

   Sem aviso de orçamento = ok.

9. **`bash ~/.hermes/scripts/phone-health.sh [--prune]`** resume tudo: orçamento de memória,
   skills ativas/estacionadas, dashboard+watchdog, wake lock, espaço, compilações acidentais e
   rotação de logs.

10. **`skill-router`** (instalado, desabilitado) cortaria o índice para os 2-3 nomes relevantes por
    turno, mas exige o mesmo passo de dependências do item 6 — só testar com as deps instaladas à
    mão, um turno de cada vez, e desabilitar na hora se o roteamento errar.

11. **Space hogs medidos:** `~/.cargo` 1,0 GB, `~/.hermes/cache/uv` 1,6 GB (caiu para 959 MB
    depois de limpar os sdists da build storm), checkout `hermes-agent` 927 MB. Purbar o cache do
    uv inteiro só quando faltar espaço.

## Quando o celular "trava"

Sintoma real (14/10/2026): dezenas de `rustc`/`cargo` de `~/.hermes/cache/uv/sdists-v9/...` comendo
CPU, memória disponível em ~240 MB, **7 GB de swap**. Origem: `pydantic-core`, `jiter`,
`firecrawl-anydoc`, `watchfiles` compilados do zero — disparados por `hermes-agent/pm/worker.py` →
`uv sync --frozen --all-packages` (provisionamento do runtime/plugins, PPID 1).

Remédio imediato (**não mata nada**): `renice -n 19` na árvore — devolveu ~3 GB de memória
disponível na hora. O `hermes-calm` ([scripts/hermes-mobile](../scripts/hermes-mobile)) faz isso.
Para não repetir: manter `CARGO_BUILD_JOBS=2` e os plugins de plataforma/browser desnecessários em
`plugins.disabled` é o que impede o aparelho de virar forno.

## Chaves de config que importam

```bash
hermes config set memory.memory_char_limit <N>   # tools/memory_tool.py lê daqui
hermes config set memory.user_char_limit <N>
hermes config set display.show_reasoning false   # terminal limpo (efeito na sessão seguinte)
hermes config set plugins.disabled '[…]'
```
