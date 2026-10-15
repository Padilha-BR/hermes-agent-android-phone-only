#!/usr/bin/env python3
"""Prova de ponta a ponta do chat NATIVO do Hermes pelo WebSocket do gateway.

Roda no celular (Termux) contra o backend local. Precisa da lib `websockets`
(vem no venv do Hermes: ~/hermes-agent/venv/bin/python).

Uso:  verify-native-chat.py ["prompt"]
      DUMP=1 verify-native-chat.py     # imprime os frames crus

Saída esperada: session.create → session_id, prompt.submit → status streaming,
e os deltas de message.delta até message.complete.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.request

import websockets

BACKEND = os.environ.get("HERMES_BACKEND", "http://127.0.0.1:9119")
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "Responda apenas: OK"


def token() -> str:
    with urllib.request.urlopen(f"{BACKEND}/", timeout=6) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*["\']([^"\']+)["\']', html)
    if not m:
        raise SystemExit("nao achei o token no HTML do backend")
    return m.group(1)


async def main() -> int:
    url = BACKEND.replace("http", "ws") + "/api/ws?token=" + token()
    print("conectando em", url.split("?")[0])
    texto: list[str] = []
    vistos: list[str] = []

    def tratar(msg: dict) -> None:
        # o tipo real vive em params.type — nao em method (que e sempre "event")
        if (msg.get("method") or "") != "event":
            return
        p = msg.get("params") or {}
        tipo = p.get("type") or ""
        carga = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        if os.environ.get("DUMP"):
            print("  RAW:", json.dumps(msg)[:300])
        if not tipo:
            return
        vistos.append(tipo)
        t = carga.get("text")
        if tipo == "message.delta" and isinstance(t, str) and t:
            texto.append(t)
            print("  <", t[:200].replace("\n", " "))
        elif tipo == "message.complete" and isinstance(t, str) and t:
            if not texto:
                texto.append(t)
            print("  [fim]", t[:200].replace("\n", " "))

    async with websockets.connect(url, max_size=None) as ws:
        async def pedir(metodo: str, params: dict, rid: int) -> dict:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": metodo, "params": params}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("id") == rid:
                    return msg
                tratar(msg)

        await pedir("client.capabilities", {"server_requests": True}, 1)
        r = await pedir("session.create", {}, 2)
        if "error" in r:
            print("session.create ERRO:", r["error"])
            return 2
        res = r.get("result") or {}
        sid = res.get("session_id") or res.get("id")
        print("session.create →", json.dumps(res)[:160])
        if not sid:
            return 2

        r = await pedir("prompt.submit", {"session_id": sid, "text": PROMPT}, 3)
        print("prompt.submit →", json.dumps(r.get("result"))[:160] if "result" in r else r.get("error"))

        limite = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < limite:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            except asyncio.TimeoutError:
                break
            tratar(msg)
            if (msg.get("params") or {}).get("type") == "message.complete":
                break

    print()
    print("eventos vistos:", ", ".join(sorted(set(vistos))) or "(nenhum)")
    print("TEXTO DO AGENTE:", ("".join(texto)[:400] or "(vazio)"))
    return 0 if texto else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
