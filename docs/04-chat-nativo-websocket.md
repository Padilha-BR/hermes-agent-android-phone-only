# 04 — Chat nativo no celular: o protocolo WebSocket do gateway

> Portado do nosso caderno de campo. Verificado em 14/10/2026.


Protocolo validado de ponta a ponta conversando com o gateway local (resposta
recebida em streaming, com eventos de ferramenta, uso de contexto e título).
É o caminho que o app desktop usa e o que qualquer chat nativo precisa falar.

## Por que isso importa: o chat do dashboard NÃO é um chat nativo

O `ChatPage` do dashboard web usa **xterm + `hermes.pty.attach.${id}`** — ele
renderiza o TUI do Hermes dentro do navegador, ou seja, é um terminal. Consequência
prática: **nenhum CSS transforma esse chat num chat com bolhas/markdown estilo
desktop.** Quando o pedido é "deixa o chat parecido com o desktop", a resposta
certa é construir/usar a superfície nativa abaixo, não estilizar o terminal.
(Conferir antes de prometer: `grep -l 'hermes.pty.attach' <web_dist>/assets/*.js`.)

## Conexão e autenticação

- Token de sessão: ler do HTML do backend (`__HERMES_SESSION_TOKEN__`) — ele é
  aleatório a cada start do servidor.
- WebSocket: `ws://127.0.0.1:9119/api/ws?token=<token>` (trocar o esquema do
  backend por `ws`). Autentica **por query**, não por header.
- REST: header `X-Hermes-Session-Token`. O backend reflete origem de loopback no
  CORS e aceita WS de outra porta local — não precisa de proxy.
- Um host só aceita um backend (`Refusing to start: this host is already served
  by PID ...`): reutilize o que está no ar.

## Envelope (JSON-RPC 2.0)

O que o cliente envia:
```json
{"jsonrpc":"2.0","id":N,"method":"<metodo>","params":{...}}
```
Eventos chegam SEMPRE num envelope genérico — o tipo real está em `params.type`,
não em `method` (errar isso faz o cliente "ver" só o tipo `event` e nenhum texto):
```json
{"jsonrpc":"2.0","method":"event",
 "params":{"type":"message.delta","session_id":"...","payload":{"text":"..."},"seq":N}}
```

## Métodos e params confirmados

| método | params | resultado |
|---|---|---|
| `client.capabilities` | `{"server_requests": true}` | lista de requests que o servidor pode fazer ao cliente |
| `session.create` | `{}` | `result.session_id`, `result.stored_session_id`, `result.info.model` |
| `prompt.submit` | `{"session_id": sid, "text": "..."}` | `{"status":"streaming", "user_row_id": N}` |
| `image.attach` | `{"session_id": sid, "path": "..."}` | anexa arquivo já no disco |
| `session.interrupt` | `{"session_id": sid}` | interrompe o turno |
| `session.list` | `{"limit": N}` | `result.sessions[]` com `{id, resolved_id, title, preview, started_at, message_count, source}` |
| `session.resume` | `{"session_id": <id SALVO>, "source": "desktop"}` | **abre conversa salva**: `{session_id: <id vivo>, resumed, message_count, messages[]}` |

### Abrir uma conversa SALVA (a pegadinha que virou bug)

`session.activate` e `session.history` só enxergam sessão **viva no processo** —
com o id que veio de `session.list` os dois respondem
`{"error":{"code":4001,"message":"session not found"}}`. O método certo é
`session.resume`: ele carrega a sessão salva, devolve o **id vivo** (que passa a
ser o `session_id` de todo `prompt.submit` seguinte) e já manda o histórico em
`messages[]`.

Nas linhas de histórico o texto está em **`text`** (não `content`), com `role`
(`user`/`assistant`), `timestamp` e `row_id` — renderizar `content` mostra
conversa vazia sem erro nenhum.

Outros métodos do catálogo (confirmar o handler em `tui_gateway/server.py` antes de
usar): `session.list`, `session.history`, `session.title`, `session.activate`,
`session.close`, `session.compress`, `session.branch`, `session.usage`,
`prompt.background`, `session.steer`, `commands.catalog`, `command.resolve`,
`reload.mcp`, `reload.env`, `process.stop`, `terminal.resize`, `clipboard.paste`.

## Eventos observados (com o que trazem)

- `gateway.ready` — `payload.skin.colors` (a paleta do skin atual).
- `session.info` — modelo, provider, tools, cwd.
- `message.start` · `thinking.delta` (`payload.text`) · `message.delta`
  (`payload.text` = pedaço do texto, é o streaming de verdade).
- `message.complete` — `payload.text` (texto final) + `payload.usage`
  (`input`, `output`, `total`, `context_used`, `context_max`, `context_percent`).
- `tool.start` · `tool.generating` · `tool.complete` — atividade de ferramenta
  (renderizar como linha discreta).
- `session.title` (`payload.title`) · `session.usage` · `sessions.changed` ·
  `session.reclaimed` (motivo, ex.: `ws_orphan_reap`).

## Anexar imagem por upload (caminho do navegador)

O navegador não tem caminho de arquivo, então: upload → recebe o caminho → anexa.
```
POST {backend}/api/chat/image-upload      (header X-Hermes-Session-Token)
body JSON: {"data_url":"data:image/jpeg;base64,...","filename":"x.jpg"}
→ {"ok":true,"path":"<hermes home>/images/dashboard_*.jpg"}
```
Depois `image.attach {session_id, path}`. Multipart nesse endpoint dá **500** — o
corpo é JSON.

## Verificação (rodar antes de confiar)

`scripts/verify-native-chat.py` abre uma sessão, manda um prompt e imprime os
deltas e o texto final. É o teste que separa "o protocolo funciona" de "o app
parece funcionar". Use `DUMP=1` para ver os frames crus quando o parser não pegar
algo — foi assim que o envelope `params.type` apareceu.
