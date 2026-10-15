#!/usr/bin/env python3
"""Prova de ponta a ponta do chat NATIVO do Hermes pelo WebSocket do gateway.

É o caminho que o app desktop usa (e que o dashboard NÃO usa — o dashboard é um
terminal via PTY). Se isto conversa, um chat nativo "com cara de desktop" pode
ser construído para o celular.

Uso: testar-chat-gateway.py ["prompt"]
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.request

import websockets

BACKEND = "http://127.0.0.1:9119"
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "Responda apenas: OK"


def token() -> str:
    with urllib.request.urlopen(f"{BACKEND}/", timeout=6) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*["\']([^"\']+)["\']', html)
    if not m:
        raise SystemExit("nao achei o token no HTML do backend")
    return m.group(1)


async def main() -> int:
    tok = token()
    url = BACKEND.replace("http", "ws") + "/api/ws?token=" + tok
    print("conectando em", BACKEND.replace("http", "ws") + "/api/ws (token ok)")
    texto_final = []
    vistos = []

    async with websockets.connect(url, max_size=None) as ws:
        async def pedir(metodo: str, params: dict, rid: int) -> dict:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": metodo, "params": params}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("id") == rid:
                    return msg
                tratar(msg)

        def tratar(msg: dict) -> None:
            # envelope real: {"method":"event","params":{"type":...,"payload":{...},"seq":N}}
            if (msg.get("method") or "") != "event":
                return
            p = msg.get("params") or {}
            tipo = p.get("type") or ""
            carga = p.get("payload") or {}
            if os.environ.get("DUMP"):
                print("  RAW:", json.dumps(msg)[:300])
            if tipo:
                vistos.append(tipo)
                texto = carga.get("text") if isinstance(carga, dict) else None
                if tipo == "message.delta" and isinstance(texto, str) and texto:
                    texto_final.append(texto)
                    print("  <", texto[:200].replace("\n", " "))
                elif tipo == "message.complete" and isinstance(texto, str) and texto:
                    if not texto_final:
                        texto_final.append(texto)
                    print("  [fim]", texto[:200].replace("\n", " "))

        # 1) anuncia capacidades (exigido pelo gateway em conexões novas)
        try:
            r = await pedir("client.capabilities", {"server_requests": True}, 1)
            print("client.capabilities →", str(r.get("result"))[:120])
        except Exception as e:
            print("client.capabilities falhou:", e)

        # 2) cria sessão
        r = await pedir("session.create", {}, 2)
        if "error" in r:
            print("session.create ERRO:", r["error"])
            return 2
        res = r.get("result") or {}
        sid = res.get("session_id") or res.get("id") or res.get("sid")
        print("session.create →", json.dumps(res)[:200])
        if not sid:
            print("não veio session_id")
            return 2

        # 3) manda o prompt
        r = await pedir("prompt.submit", {"session_id": sid, "text": PROMPT}, 3)
        print("prompt.submit →", json.dumps(r.get("result"))[:200] if "result" in r else r.get("error"))

        # 4) espera o turno terminar
        limite = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < limite:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            except asyncio.TimeoutError:
                break
            tratar(msg)
            ev = msg.get("method") or ""
            if ev == "event":
                tipo = (msg.get("params") or {}).get("type") or ""
                if tipo in ("message.complete", "turn.complete", "session.idle", "prompt.complete"):
                    break

    print()
    print("eventos vistos:", ", ".join(sorted(set(vistos))) or "(nenhum)")
    print("TEXTO DO AGENTE:", ("".join(texto_final)[:400] or "(vazio)"))
    return 0 if texto_final else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
