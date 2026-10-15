#!/usr/bin/env python3
"""Exercita os métodos que o app de chat usa e que eu ainda NÃO tinha testado:
session.list, session.history, session.activate, image.attach.

Assim o app não estreia com parâmetro errado na frente do usuário.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
from pathlib import Path

import websockets

BACKEND = "http://127.0.0.1:9119"


def token() -> str:
    with urllib.request.urlopen(f"{BACKEND}/", timeout=6) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*["\']([^"\']+)["\']', html)
    if not m:
        raise SystemExit("sem token")
    return m.group(1)


async def main() -> int:
    tok = token()
    url = BACKEND.replace("http", "ws") + "/api/ws?token=" + tok
    seq = 0
    pend: dict[int, asyncio.Future] = {}

    async with websockets.connect(url, max_size=None) as ws:
        async def rpc(metodo: str, params: dict, espera: float = 45) -> dict:
            nonlocal seq
            seq += 1
            meu = seq
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            pend[meu] = fut
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": meu, "method": metodo, "params": params}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=espera))
                if msg.get("id") in pend:
                    f = pend.pop(msg["id"])
                    if not f.done():
                        f.set_result(msg)
                if msg.get("id") == meu:
                    return msg

        await rpc("client.capabilities", {"server_requests": True})
        criada = await rpc("session.create", {})
        sid = (criada.get("result") or {}).get("session_id")
        print("session.create →", sid)

        r = await rpc("session.list", {"limit": 5})
        res = r.get("result") or {}
        sessoes = res.get("sessions") or []
        print(f"session.list → {len(sessoes)} sessões; campos da 1ª:",
              sorted(sessoes[0].keys())[:8] if sessoes else "(vazio)")

        if sessoes:
            alvo = sessoes[0].get("resolved_id") or sessoes[0].get("id")
            print("  título da 1ª:", (sessoes[0].get("title") or "")[:60])
            print("  id salvo:", alvo, "| resolved_id:", sessoes[0].get("resolved_id"))
            # session.resume é o método que ABRE uma conversa salva (activate/history exigem sessão viva)
            rr = await rpc("session.resume", {"session_id": alvo, "source": "desktop"})
            print("  RAW resume:", json.dumps(rr)[:700])
            rr_res = rr.get("result") or {}
            vivo = rr_res.get("session_id") or rr_res.get("live_session_id")
            msgs_res = rr_res.get("messages") or []
            print("  id vivo:", vivo, "| mensagens no resume:", len(msgs_res))
            if msgs_res:
                print("  exemplo:", msgs_res[0].get("role"), str(msgs_res[0].get("content"))[:60])
            r = await rpc("session.history", {"session_id": vivo}) if vivo else {"result": {}}
            h = r.get("result") or {}
            msgs = h.get("messages") or []
            print(f"session.history → {h.get('count')} mensagens; campos:",
                  sorted(msgs[0].keys())[:8] if msgs else "(vazio)")
            if msgs:
                print("  exemplo role/content:", msgs[0].get("role"), str(msgs[0].get("content"))[:60])

        r = await rpc("session.activate", {"session_id": sid})
        print("session.activate →", json.dumps(r.get("result") or r.get("error"))[:160])

        # image.attach com uma foto real do aparelho
        fotos = sorted((Path.home() / "storage/dcim/Camera").glob("*.jpg"))
        if fotos:
            r = await rpc("image.attach", {"session_id": sid, "path": str(fotos[0])})
            print("image.attach →", json.dumps(r.get("result") or r.get("error"))[:200])
        else:
            print("image.attach → (sem foto para testar)")

        r = await rpc("session.interrupt", {"session_id": sid})
        print("session.interrupt →", json.dumps(r.get("result") or r.get("error"))[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
