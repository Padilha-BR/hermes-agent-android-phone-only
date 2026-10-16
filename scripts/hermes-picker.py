#!/usr/bin/env python3
"""Seletor de fotos local do Hermes (miniaturas, igual app de celular).

Sobe SÓ em 127.0.0.1:8898 (nada exposto pra fora). O usuário abre no navegador,
toca nas fotos, escreve a pergunta e toca em "Mandar pro Hermes". A escolha vai
para ~/.hermes/state/picker-queue.json, que o comando `hermes-imagem` (no Termux)
lê para abrir o Hermes com a imagem anexada.

Sem dependências externas além do Pillow (que já existe no Termux) para as
miniaturas — se faltar, cai para a imagem original.

Segurança: as miniaturas são as fotos do usuário, e QUALQUER app com permissão
INTERNET (mesmo sem permissão de armazenamento) alcança 127.0.0.1. Por isso:
  - token aleatório por execução, gravado em ~/.hermes/state/picker-token (0600);
    o `hermes-imagem` lê o arquivo e abre a URL com ?t=<token>; toda rota exceto
    /health exige o token;
  - Host tem de ser o próprio loopback (anti DNS rebinding); POST /choose exige
    content-type JSON, Origin/Sec-Fetch-Site da própria página e o token no header;
  - o servidor NÃO fica residente: encerra logo depois da escolha ou após
    HERMES_PICKER_IDLE segundos sem requisição (padrão 900).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get("HOME", str(Path.home())))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(HOME / ".hermes")))
QUEUE = HERMES_HOME / "state" / "picker-queue.json"
TOKEN_ARQ = HERMES_HOME / "state" / "picker-token"
THUMBS = HERMES_HOME / "cache" / "thumbs"
PORT = int(os.environ.get("HERMES_PICKER_PORT", "8898"))
MAX_PHOTOS = int(os.environ.get("HERMES_PICKER_MAX", "300"))
OCIOSO = int(os.environ.get("HERMES_PICKER_IDLE", "900"))
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
TOKEN = secrets.token_urlsafe(24)
HOSTS_OK = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
ORIGENS_OK = {"http://" + h for h in HOSTS_OK}
ULTIMA = [time.time()]  # última requisição (para o encerramento por ociosidade)

SOURCES = {
    "todas": None,
    "camera": [HOME / "storage/dcim/Camera"],
    "screens": [HOME / "storage/dcim/Screenshots"],
    "pictures": [HOME / "storage/pictures"],
    "downloads": [HOME / "storage/downloads"],
    # só a pasta-mãe: o rglob já desce em "Sent" (listar as duas duplicava as fotos)
    "whatsapp": [HOME / "storage/shared/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images"],
    "telegram": [HOME / "storage/shared/Android/media/org.telegram.messenger/Telegram/Telegram Images"],
}


def gravar_atomico(destino: Path, dados: bytes, modo: int = 0o644) -> None:
    """Grava em .tmp e troca com os.replace: quem lê ao vivo (o hermes-imagem
    esperando a fila, outro request lendo a miniatura) nunca vê arquivo pela metade."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(f".{destino.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modo)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(dados)
        os.replace(tmp, destino)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

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
    vistos: set[str] = set()  # pastas que se sobrepõem não repetem a mesma foto
    for d in dirs_for(source):
        try:
            for p in d.rglob("*"):
                if p.is_file() and p.suffix.lower() in EXTS:
                    try:
                        chave = str(p.resolve())
                        if chave in vistos:
                            continue
                        vistos.add(chave)
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
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72)
        data = buf.getvalue()
        gravar_atomico(cache, data)  # duas threads gerando a mesma miniatura não se atropelam
        return data
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
  <input type="hidden" id="gen" value="{gen}">
</div>
<script>
const TOK='{tok}';
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
  location.href='/?fonte='+encodeURIComponent(e.target.value)+'&t='+encodeURIComponent(TOK);
}});
btn.addEventListener('click',async()=>{{
  if(!sel.size){{btn.textContent='escolha uma foto';setTimeout(refresh,1200);return;}}
  btn.disabled=true;btn.textContent='mandando...';
  try{{
    const r=await fetch('/choose',{{method:'POST',headers:{{'content-type':'application/json','x-picker-token':TOK}},
      body:JSON.stringify({{ids:[...sel],gen:document.getElementById('gen').value,question:document.getElementById('pergunta').value}})}});
    const j=await r.json();
    if(!r.ok||j.stale){{
      document.body.innerHTML='<div class="vazio"><h1>A lista mudou</h1><p>As fotos foram reescaneadas depois que esta tela abriu — nada foi enviado.</p><p><a href="/?t='+encodeURIComponent(TOK)+'">recarregar a lista</a></p></div>';
      return;
    }}
    document.body.innerHTML='<div class="vazio"><h1>Pronto!</h1><p>'+j.count+' foto(s) enviada(s) pro Hermes.</p><p>Volte para o Termux — o Hermes já está abrindo com a imagem.</p></div>';
  }}catch(e){{btn.disabled=false;btn.textContent='deu erro, tente de novo';}}
}});
refresh();
</script></body></html>"""


NEGADO = ('<!doctype html><meta charset="utf-8"><title>Fotos para o Hermes</title>'
          '<body style="background:#0b0b0e;color:#e8e8ee;font:15px system-ui;padding:24px">'
          "<h1>Abra pelo comando hermes-imagem</h1>"
          "<p>Este seletor só abre com o endereço que o <code>hermes-imagem</code> gera "
          "(ele leva um código de uso único).</p></body>")


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesPicker/1.0"

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("referrer-policy", "no-referrer")  # o token vai na URL
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _host_ok(self) -> bool:
        return (self.headers.get("host") or "").strip().lower() in HOSTS_OK

    def _token_ok(self, recebido: str) -> bool:
        return bool(recebido) and hmac.compare_digest(recebido.encode(), TOKEN.encode())

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        if not self._host_ok():
            return self._send(b"host invalido", "text/plain", 403)
        ULTIMA[0] = time.time()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return self._send(b"ok", "text/plain")
        if not self._token_ok((q.get("t") or [""])[0]):
            return self._send(NEGADO.encode(), "text/html; charset=utf-8", 403)
        if u.path.startswith("/thumb/"):
            idx = u.path.rsplit("/", 1)[-1]
            try:
                p = getattr(self.server, "photos", [])[int(idx)]
            except (ValueError, IndexError):
                return self._send(b"", "image/jpeg", 404)
            data = thumb_for(p)
            if data is None:
                # a docstring promete "cai para a imagem original": é aqui.
                # Só acontece sem Pillow ou com arquivo que o PIL não abre.
                try:
                    data = p.read_bytes()
                except OSError:
                    return self._send(b"", "image/jpeg", 404)
                tipo = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
                        "webp": "webp", "bmp": "bmp"}.get(p.suffix.lstrip(".").lower(), "jpeg")
                return self._send(data, f"image/{tipo}")
            return self._send(data, "image/jpeg")
        if u.path == "/":
            fonte = (q.get("fonte", ["todas"])[0]) or "todas"
            # a lista vive no SERVIDOR: cada requisição cria um handler novo, então
            # guardar no handler faria /thumb/* responder 404 logo em seguida.
            self.server.photos = scan(fonte)
            # geração da lista: o POST /choose compara com a da página aberta.
            # A lista é reescaneada a cada GET / (outra aba, foto nova) e os ids
            # do cliente são ÍNDICES — sem esta checagem, o que você marcou podia
            # virar OUTRA foto na hora de mandar pro Hermes.
            self.server.gen = secrets.token_hex(8)
            cells = []
            for i, _ in enumerate(self.server.photos):
                cells.append(
                    f'<div class="cell" data-id="{i}">'
                    f'<img loading="lazy" src="/thumb/{i}?t={TOKEN}" alt="">'
                    f"</div>"
                )
            opcoes = "".join(
                f'<option value="{html.escape(k)}"{" selected" if k == fonte else ""}>{html.escape(k)}</option>'
                for k in SOURCES
            )
            corpo = PAGE.format(opcoes=opcoes, celulas="".join(cells) or '<div class="vazio">nenhuma foto encontrada nessa pasta</div>',
                                n=0, gen=self.server.gen, tok=TOKEN)
            return self._send(corpo.encode(), "text/html; charset=utf-8")
        return self._send(b"nao encontrado", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(b"host invalido", "text/plain", 403)
        ULTIMA[0] = time.time()
        if self.path != "/choose":
            return self._send(b"nao encontrado", "text/plain", 404)
        # CSRF: um site aberto no Chrome podia mandar POST text/plain (sem preflight)
        # e injetar a `question` que o hermes-imagem repassa ao agente
        origem = self.headers.get("origin")
        sfs = (self.headers.get("sec-fetch-site") or "").strip().lower()
        if ((origem is not None and origem.strip().lower() not in ORIGENS_OK)
                or (sfs and sfs not in ("same-origin", "none"))
                or "application/json" not in (self.headers.get("content-type") or "").lower()
                or not self._token_ok(self.headers.get("x-picker-token") or "")):
            return self._send(b'{"ok":false,"erro":"recusado"}', "application/json", 403)
        try:
            n = max(0, min(int(self.headers.get("content-length", "0")), 64000))
            payload = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except Exception:
            return self._send(b'{"ok":false}', "application/json", 400)
        if str(payload.get("gen", "")) != str(getattr(self.server, "gen", "")):
            # página aberta antes do último scan: recusar em vez de mandar a
            # foto errada (os ids são índices numa lista que mudou)
            return self._send(json.dumps(
                {"ok": False, "stale": True,
                 "erro": "a lista de fotos mudou — recarregue a pagina"}
            ).encode(), "application/json", 409)
        paths = []
        ids = payload.get("ids", [])
        for i in ids if isinstance(ids, list) else []:
            try:
                paths.append(str(getattr(self.server, "photos", [])[int(i)]))
            except (ValueError, IndexError, TypeError):
                continue
        gravar_atomico(
            QUEUE,
            json.dumps(
                {"paths": paths, "question": str(payload.get("question", ""))[:2000], "ts": time.time()},
                ensure_ascii=False,
            ).encode("utf-8"),
            0o600,
        )
        self._send(json.dumps({"ok": True, "count": len(paths)}).encode(), "application/json")
        # escolha feita: o servidor não fica residente servindo as fotos
        threading.Timer(1.5, self.server.shutdown).start()

    def log_message(self, *a) -> None:  # silencio no log
        pass


def _vigia_ociosidade(srv: ThreadingHTTPServer) -> None:
    while True:
        time.sleep(15)
        if time.time() - ULTIMA[0] > OCIOSO:
            print(f"{OCIOSO}s sem uso — encerrando o seletor", flush=True)
            srv.shutdown()
            return


def _apagar_meu_token() -> None:
    try:
        if TOKEN_ARQ.read_text(encoding="utf-8").strip() == TOKEN:
            TOKEN_ARQ.unlink()
    except OSError:
        pass


def main() -> None:
    # o token vai num arquivo 0600 que só o usuário do Termux lê; o hermes-imagem
    # monta a URL com ele. Nunca no log nem no stdout.
    # Ordem: bind → token → serve_forever. O hermes-imagem só considera o seletor
    # pronto quando /health RESPONDE, o que exige o serve_forever — ou seja, o
    # token novo já está gravado (e ele apaga o arquivo velho de um SIGKILL antes
    # de subir). Gravar ANTES do bind seria pior: com a porta ocupada por outro
    # seletor vivo, o token dele seria sobrescrito e a URL dele deixaria de abrir.
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    gravar_atomico(TOKEN_ARQ, TOKEN.encode(), 0o600)
    threading.Thread(target=_vigia_ociosidade, args=(srv,), daemon=True).start()
    print(f"seletor de fotos em http://127.0.0.1:{PORT} (encerra após a escolha ou {OCIOSO}s ocioso)", flush=True)
    try:
        srv.serve_forever()
    finally:
        _apagar_meu_token()
        srv.server_close()


if __name__ == "__main__":
    main()
