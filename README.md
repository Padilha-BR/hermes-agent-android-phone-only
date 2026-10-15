# Hermes Agent on Android — phone-only, with the desktop UI running in the phone's own browser

Field report by **[Gabriel Padilha](https://github.com/Padilha-BR)**, **2026-10-14**. Galaxy S24 Ultra (SM-S928B, Android 16 / API 36, One UI 8, arm64,
12 GB RAM), Termux, Python 3.14.6,
Hermes Agent `git.311155f.dirty`.

**Português:** [leia em português](README.pt-BR.md).

**One line:** Hermes Agent runs entirely inside Termux on the phone — CLI, dashboard, *and the official
desktop app's own React renderer*, served to the phone's browser by the phone itself. No PC, no VM,
no Docker, no Tailscale, no second machine anywhere in the loop. The dashboard is pinned to the home
screen as an installable PWA, and a watchdog keeps it alive when Android suspends Termux.

> ⚠️ **It works, but it's a workaround — not an app.** There is no APK here and this is not a
> product: it is a PWA plus a ported desktop UI, held together by scripts. It runs and it is usable
> every day, but **some features still don't work 100%** — the in-app terminal, native git, file
> dialogs, multi-window/HUD and the pet overlay are inert by design (they are Electron-native), and
> Android may suspend Termux whenever it feels like it. Read [Honest
> limitations](#honest-limitations) before you try it.

This is not a fork and not an installer. It is a field report plus the actual scripts we run daily:
what broke, why it broke, and the recipe that fixed it.

---

## Read this first: what is *not* new

Before writing "first ever" anywhere, we searched (2026-10-14). Plenty exists. Credit where due:

| Already published | What it is |
|---|---|
| [Upstream Termux docs](https://hermes-agent.nousresearch.com/docs/getting-started/termux) + signed APT channel | The official "Hermes on Termux" path. Docs carried a *"Termux is currently broken"* banner in Sep 2026; the pip-from-source path below is what worked for us. |
| [AbuZar-Ansarii/Hermes-Agent-On-Android](https://github.com/AbuZar-Ansarii/Hermes-Agent-On-Android), [IceHeartGitH/Hermes-Agent-Android-Termux](https://github.com/IceHeartGitH/Hermes-Agent-Android-Termux), [didin-lab/hermes-termux-install](https://github.com/didin-lab/hermes-termux-install) | Community Termux bootstrap installers for Hermes. |
| [walodayeet/Hermes-Desktop-Web-Port](https://github.com/walodayeet/Hermes-Desktop-Web-Port), [przbadu/hermes-ui](https://github.com/przbadu/hermes-ui), [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) | The desktop renderer in a browser, or a replacement web UI. All of them assume a **server/NAS/host the phone connects to** as a client. |
| NousResearch `apps/mobile/desktop-port` ([PR #64962](https://github.com/NousResearch/hermes-agent/pull/64962)), [hermes-workbench](https://hermes-agent.nousresearch.com/docs/plugins/hermes-workbench) plugin | The desktop renderer bundled in an iOS/Capacitor shell, or served by a headless host. Both replace the `window.hermesDesktop` bridge — but hand-written. |
| [hermes-webui remote-access docs](https://github.com/nesquena/hermes-webui/blob/master/docs/remote-access.md), issue #2364 | Hermes + WebUI on ARM64 Android inside a **Debian 12 VM via Android Virtualization Framework** (Redmi Note 13 Pro, ~1.7 GB footprint). Closest prior art to "the phone is the host". |
| [Hermes Android v1.0.0](https://www.reddit.com/r/hermesagent/comments/1tspb0a/hermes_android_v100_first_release_of_an_android/) | A third-party Android *client* that talks to a Hermes host on your LAN. |

Detail, links and what each one does differently: **[docs/00-prior-art.md](docs/00-prior-art.md)**.

## What we could not find published anywhere

1. **The official desktop renderer running in the phone's own browser, hosted by the phone.**
   Every published port of `apps/desktop` puts a server, container or VM between the phone and the UI.
   Here the phone is both host *and* client, entirely inside Termux on loopback
   (`http://127.0.0.1:8890`), with no VM and no Debian layer.
2. **A generated Electron-bridge shim.** Instead of hand-writing `window.hermesDesktop` method by
   method, we generate the whole bridge surface from the renderer's own type contract
   (`apps/desktop/src/global.d.ts`) into a 160-key inert stub base
   ([`gerar-shim-stubs.py`](scripts/gerar-shim-stubs.py)), then layer the real implementations on top.
   Hand-writing it produced a new missing key on every single round; generating it closed the gap at once.
3. **The upstream dashboard turned into an installable home-screen PWA on Termux**, with a watchdog and
   a `Termux:Boot` entry so Android suspending Termux cannot leave a dead icon behind.
   Symptom nobody documents: `hermes dashboard` **rebuilds `web_dist` and wipes the PWA patch**, so the
   app icon silently starts receiving HTML instead of a manifest.
4. **A measured Android optimization playbook** — the memory budget that silently blocks all memory
   writes, the real cost of the skill index per message, and the fact that
   `hermes plugins enable` compiles Rust *on the phone*.
5. **SSE push-reload** for the mobile dev loop: `POST /__bump` forces open pages to reload, which
   works in a backgrounded tab where `setInterval` polling does not (Chrome freezes inactive timers).

## Verified on the device today

| Claim | Evidence |
|---|---|
| Desktop renderer bundle served from the phone | `GET :8890/` → `<title>Hermes</title>` + injected session token + `__stubs.js` + `__shim.js` + `manifest.webmanifest` |
| Native chat surface up | `GET :8891/` → 200 |
| Dashboard healthy, no auth gate on loopback | `GET :9119/api/health` → `{"ok":true,"auth_required":false}` |
| Dashboard is a real PWA | `GET :9119/manifest.webmanifest` → `200 application/manifest+json` |
| Generated bridge surface | `shim-stubs.js` → 9,688 bytes, 160 keys from `global.d.ts` |
| Watchdog survives the agent session | `dashboard-watchdog.pid` → `PPid: 1` (detached, not a child of the agent) |
| Bundle intact after build | `apps/desktop/dist` → 1,044 asset entries, 147 refs in `index.html` |

Restart-tested earlier the same day: killing the :8890 server on purpose → watchdog brought it back in ~25 s,
with :9119 and :8891 untouched. Layout pinned at viewport 800 (`~/.hermes/state/desktop-web-viewport`),
which is what makes the text readable and keeps the composer inside the visible area on a phone.

## The moving parts

```
Termux on the phone
├─ hermes CLI (venv, Python 3.14, Termux main repo — no TUR, no VM)
├─ :9119  hermes dashboard      → official web dashboard, patched into an installable PWA
├─ :8890  desktop-ui/web         → apps/desktop React renderer + generated bridge shim
├─ :8891  native chat            → SPA speaking the gateway's JSON-RPC/WebSocket protocol
└─ watchdog (PPid 1) + Termux:Boot → keeps all three alive across Android suspending Termux
```

## Footprint on the phone

Measured 2026-10-14, with everything running:

| Item | Size |
|---|---|
| Termux base (`$PREFIX/usr`) | 1.9 GB |
| `hermes-agent` checkout | 1.8 GB — `venv` 151 MB, `node_modules` 1.1 GB, `apps/` 128 MB, `.git` 86 MB |
| `HERMES_HOME` (`~/.hermes`) | 1.3 GB — `cache/uv` 959 MB, `skills` 56 MB, the rest is logs/state/sessions |
| `$HOME/.cargo` | 1.0 GB — Rust build cache, **purgeable** |
| `$HOME/.cache/pip` | 17 MB |
| **Total installed** | **~4.8 GB** (~5.8 GB counting the cargo cache) |
| RAM | 12 GB declared — the kernel reports `MemTotal: 11350704 kB` (≈ 10.8 GiB usable) |
| Swap | 12 GB (ZRAM) |
| Space on `/data` | 47 GB free of 222 GB (79% used) |

Three things worth knowing before you size a phone for this:

- The 1.1 GB of `node_modules` exists **only** for the desktop-UI build (Vite). If you skip the
  desktop port, that whole chunk disappears and the install lands near 3.7 GB.
- The 12 GB of ZRAM swap is why this phone survived a build storm that had free RAM down to
  ~240 MB — without it, the OOM killer would have ended the install.
- `HERMES_HOME` itself is small. The weight is the checkout and the Rust/uv caches, and both are
  disposable.

## Quickstart

```bash
# 1. Termux base — do NOT skip the apt upgrade first
apt update && apt full-upgrade -y -o Dpkg::Options::=--force-confold

# 2. mirror, then build deps (see docs/01 for why openssl-tool and libheif matter)
pkg install -y clang rust make pkg-config libffi openssl openssl-tool binutils cmake ninja \
                patchelf libxml2 libxslt nodejs ripgrep ffmpeg libheif
# python comes from the Termux main repo (3.14) — prebuilt wheels exist for cryptography/lxml/numpy/pillow

# 3. clone + venv + install, with the Rust profile overrides that turn 40 minutes into 4
git clone https://github.com/NousResearch/hermes-agent.git && cd hermes-agent
python -m venv --system-site-packages venv && source venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
export CARGO_PROFILE_RELEASE_LTO=off CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 CARGO_BUILD_JOBS=8
python -m pip install -U pip setuptools wheel && python -m pip install -e '.[termux]'

# 4. bring the surfaces up
hermes dashboard --skip-build --no-open      # :9119
bash scripts/dashboard-watchdog.sh &         # keeps it up, detached
```

Then read the docs in order. Nothing in `scripts/` contains credentials; paths are Termux's fixed prefix.

## Docs

| # | Doc | What's in it |
|---|---|---|
| 00 | [Prior art](docs/00-prior-art.md) | Every project we found, what it does, what it does not, with links and dates. |
| 01 | [Installing on Termux](docs/01-instalacao-no-termux.md) | The install order that works, and the 8 failures that cost us the most time. |
| 02 | [The app on the home screen](docs/02-app-na-tela-inicial.md) | Why the dashboard icon breaks, the PWA patch, the watchdog, Termux:Boot. |
| 03 | [Desktop UI in the phone's browser](docs/03-ui-desktop-no-navegador.md) | The full port recipe: build without Electron, the bridge contract, the shim generator, mobile layout traps. |
| 04 | [Native chat over WebSocket](docs/04-chat-nativo-websocket.md) | The gateway's JSON-RPC protocol: methods, events, and how to verify it before promising it works. |
| 05 | [Android optimization](docs/05-otimizacao-android.md) | Memory budget, skill index cost, plugin traps, the `pkill` self-kill. |
| 06 | [Phone-only operation](docs/06-operacao-so-com-o-celular.md) | On-screen keyboard without Ctrl, free dictation, widgets, sending photos to the agent. |
| 07 | [Is a native Android port next?](docs/07-porta-para-app-android.md) | What upstream already solves (Android wheels, retag pipeline, platform gates), why the UI is separable, the real blockers (Play Store, foreground service, scoped storage) and the three paths with their price. |

Docs are in Portuguese (they are our working notes); this README and doc 00's tables are in English.

## Security and privacy notes

- **Everything binds to `127.0.0.1`.** The desktop-ui injector hard-codes
  `ThreadingHTTPServer(("127.0.0.1", PORT), …)`, the dashboard defaults to loopback, and the chat
  surface reuses the same server. Nothing in this setup is reachable from your LAN or the internet,
  and nothing here is designed to be.
- **Loopback means no login.** The injector puts the session token straight into the HTML it serves,
  which is what makes the surfaces work without an auth prompt. That is safe *only* while the bind
  stays on loopback. **Never** bind these surfaces to `0.0.0.0`, port-forward them, or put them
  behind a tunnel without real auth in front — the token in the HTML is the whole login.
- **No credentials in this repo.** API keys belong in the env file inside your `HERMES_HOME`
  (`chmod 600`) and never in a commit, a transcript or an issue. `scripts/` references no key,
  no token, no hostname and no external address.
- **No personal data.** No names, no accounts, no device serials, no network addresses — only the
  generic loopback address and configurable port numbers.
- **Android's own caveat:** anything else running on the phone can reach loopback too. That is
  Android's app model, not something this setup changes — but it is why you should treat the phone
  itself as the security boundary, not the port.

## Honest limitations

- **Android is not a server.** Termux gets suspended; battery optimisation must be disabled by hand
  (Settings → Apps → Battery → Unrestricted) for Termux and Termux:Boot. The watchdog improves this,
  it does not defeat it.
- **No local voice.** `faster-whisper → ctranslate2` has no Android wheel. We use the OS speech
  recogniser instead (doc 06).
- **No Docker, no local browser automation** inside Termux.
- **The installer path is Tier 2 upstream** — commits to `main` can break it. Python version support is
  whatever `pyproject.toml` says that day, not what the docs say (doc 01).
- **The port is a port, not Electron.** In-app PTY/terminal, native git, file dialogs, multi-window/HUD
  and the pet overlay are inert in a browser, by design. Chat, sessions, config and tools work.
- **Two surfaces beat one.** If all you want is a good chat on the phone, the native WebSocket surface
  (doc 04) is less work than porting the desktop UI. We run both.
- We did **not** upstream anything; the scripts are ours, unversioned against upstream internals, and
  they read `hermes_cli/web_dist` and `apps/desktop/dist` — paths that upstream can move.

## Credits

Hermes Agent is [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT).
Termux is [termux/termux-app](https://github.com/termux/termux-app). The desktop-web ports and the
field reports listed in doc 00 are the shoulders this stands on.

MIT — see [LICENSE](LICENSE).
