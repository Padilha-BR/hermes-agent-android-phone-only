#!/usr/bin/env python3
"""Seletor de fotos local do Hermes (miniaturas, igual app de celular).

Sobe SÓ em 127.0.0.1:8898 (nada exposto pra fora). O usuário abre no navegador,
toca nas fotos, escreve a pergunta e toca em "Mandar pro Hermes". A escolha vai
para ~/.hermes/state/picker-queue.json, que o comando `hermes-imagem` (no Termux)
lê para abrir o Hermes com a imagem anexada.

Sem dependências externas além do Pillow (que já existe no Termux) para as
miniaturas — se faltar, cai para a imagem original.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get("HOME", str(Path.home())))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(HOME / ".hermes")))
QUEUE = HERMES_HOME / "state" / "picker-queue.json"
THUMBS = HERMES_HOME / "cache" / "thumbs"
PORT = int(os.environ.get("HERMES_PICKER_PORT", "8898"))
MAX_PHOTOS = int(os.environ.get("HERMES_PICKER_MAX", "300"))
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

SOURCES = {
    "todas": None,
    "camera": [HOME / "storage/dcim/Camera"],
    "screens": [HOME / "storage/dcim/Screenshots"],
    "pictures": [HOME / "storage/pictures"],
    "downloads": [HOME / "storage/downloads"],
    "whatsapp": [
        HOME / "storage/shared/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
        HOME / "storage/shared/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/Sent",
    ],
    "telegram": [HOME / "storage/shared/Android/media/org.telegram.messenger/Telegram/Telegram Images"],
}

try:
    from PIL import Image

    _PIL = True
except Exception:  # pragma: no cover
    _PIL = False


def dirs_for(source: str) -> list[Path]:
    if source == "todas" or source not in SOURCES:
        out: list[Path] = []
        for k, v in SOURCES.items():
            if k == "todas":
                continue
            out.extend(v or [])
        return [d for d in out if d.is_dir()]
    return [d for d in (SOURCES[source] or []) if d.is_dir()]


def scan(source: str) -> list[Path]:
    found: list[tuple[float, Path]] = []
    for d in dirs_for(source):
        try:
            for p in d.rglob("*"):
                if p.is_file() and p.suffix.lower() in EXTS:
                    try:
                        found.append((p.stat().st_mtime, p))
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in found[:MAX_PHOTOS]]


def thumb_for(p: Path) -> bytes | None:
    """Miniatura em JPEG (cacheada em disco). None = não deu para gerar."""
    THUMBS.mkdir(parents=True, exist_ok=True)
    try:
        key = hashlib.sha1(f"{p}:{p.stat().st_mtime_ns}".encode()).hexdigest()[:24]
    except OSError:
        return None
    cache = THUMBS / f"{key}.jpg"
    if cache.is_file():
        try:
            return cache.read_bytes()
        except OSError:
            pass
    if not _PIL:
        return None
    try:
        with Image.open(p) as im:
            im = im.convert("RGB")
            im.thumbnail((360, 360))
            im.save(cache, "JPEG", quality=72)
        return cache.read_bytes()
    except Exception:
        return None


PAGE = """<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fotos para o Hermes</title><style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:#0b0b0e;color:#e8e8ee;font:15px/1.4 system-ui,-apple-system,Roboto,sans-serif;padding:10px 10px 96px}}
h1{{font-size:17px;margin:6px 4px 10px}}
select,textarea,button{{font:inherit;background:#17171d;color:#e8e8ee;border:1px solid #2c2c36;border-radius:10px;padding:10px}}
select{{width:100%;margin-bottom:10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:6px}}
.cell{{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;border:2px solid transparent;background:#141419}}
.cell img{{width:100%;height:100%;object-fit:cover;display:block}}
.cell.on{{border-color:#e0b64a}}
.cell.on:after{{content:"✓";position:absolute;top:4px;right:6px;background:#e0b64a;color:#1a1400;font-weight:700;border-radius:50%;width:20px;height:20px;display:grid;place-items:center;font-size:13px}}
.cell .faltou{{display:grid;place-items:center;height:100%;font-size:11px;color:#888;padding:4px;text-align:center}}
.foot{{position:fixed;left:0;right:0;bottom:0;background:#101015ee;border-top:1px solid #26262f;padding:8px 10px;display:flex;gap:8px;align-items:center}}
.foot textarea{{flex:1;height:44px;resize:none}}
.send{{background:#e0b64a;color:#1a1400;font-weight:700;border:0;white-space:nowrap}}
.vazio{{color:#999;padding:20px;text-align:center}}
</style></head><body>
<h1>Fotos para o Hermes</h1>
<select id="fonte">{opcoes}</select>
<div class="grid" id="grid">{celulas}</div>
<div class="foot">
  <textarea id="pergunta" placeholder="Pergunta (opcional)"></textarea>
  <button class="send" id="enviar">Mandar ({n})</button>
</div>
<script>
let sel=new Set();
const btn=document.getElementById('enviar');
function refresh(){{btn.textContent='Mandar ('+sel.size+')';}}
document.querySelectorAll('.cell').forEach(c=>{{
  c.addEventListener('click',()=>{{
    const id=c.dataset.id;
    if(sel.has(id)){{sel.delete(id);c.classList.remove('on');}}
    else{{sel.add(id);c.classList.add('on');}}
    refresh();
  }});
}});
document.getElementById('fonte').addEventListener('change',e=>{{
  location.href='/?fonte='+encodeURIComponent(e.target.value);
}});
btn.addEventListener('click',async()=>{{
  if(!sel.size){{btn.textContent='escolha uma foto';setTimeout(refresh,1200);return;}}
  btn.disabled=true;btn.textContent='mandando...';
  try{{
    const r=await fetch('/choose',{{method:'POST',headers:{{'content-type':'application/json'}},
      body:JSON.stringify({{ids:[...sel],question:document.getElementById('pergunta').value}})}});
    const j=await r.json();
    document.body.innerHTML='<div class="vazio"><h1>Pronto!</h1><p>'+j.count+' foto(s) enviada(s) pro Hermes.</p><p>Volte para o Termux — o Hermes já está abrindo com a imagem.</p></div>';
  }}catch(e){{btn.disabled=false;btn.textContent='deu erro, tente de novo';}}
}});
refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesPicker/1.0"
    photos: list[Path] = []

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(b"ok", "text/plain")
        if u.path.startswith("/thumb/"):
            idx = u.path.rsplit("/", 1)[-1]
            try:
                p = getattr(self.server, "photos", [])[int(idx)]
            except (ValueError, IndexError):
                return self._send(b"", "image/jpeg", 404)
            data = thumb_for(p)
            if data is None:
                return self._send(b"", "image/jpeg", 404)
            return self._send(data, "image/jpeg")
        if u.path == "/":
            fonte = (parse_qs(u.query).get("fonte", ["todas"])[0]) or "todas"
            # a lista vive no SERVIDOR: cada requisição cria um handler novo, então
            # guardar em self.photos faria /thumb/* responder 404 logo em seguida.
            self.server.photos = scan(fonte)
            cells = []
            for i, p in enumerate(self.server.photos):
                cells.append(
                    f'<div class="cell" data-id="{i}">'
                    f'<img loading="lazy" src="/thumb/{i}" alt="">'
                    f"</div>"
                )
            opcoes = "".join(
                f'<option value="{html.escape(k)}"{" selected" if k == fonte else ""}>{html.escape(k)}</option>'
                for k in SOURCES
            )
            corpo = PAGE.format(opcoes=opcoes, celulas="".join(cells) or '<div class="vazio">nenhuma foto encontrada nessa pasta</div>', n=len(self.photos))
            return self._send(corpo.encode(), "text/html; charset=utf-8")
        return self._send(b"nao encontrado", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/choose":
            return self._send(b"nao encontrado", "text/plain", 404)
        try:
            n = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(b'{"ok":false}', "application/json", 400)
        paths = []
        for i in payload.get("ids", []):
            try:
                paths.append(str(getattr(self.server, "photos", [])[int(i)]))
            except (ValueError, IndexError, TypeError):
                continue
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE.write_text(
            json.dumps(
                {"paths": paths, "question": str(payload.get("question", ""))[:2000], "ts": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return self._send(json.dumps({"ok": True, "count": len(paths)}).encode(), "application/json")

    def log_message(self, *a) -> None:  # silencio no log
        pass


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"seletor de fotos em http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
