// build-desktop-web.mjs — builda a UI do app desktop do Hermes como bundle web,
// replicando EXATAMENTE o caminho oficial (scripts/build/desktop.mjs) menos o
// empacotamento Electron: mesmo carregador de config ('runner') e mesmo publicDir.
// Saída: $HERMES_DESKTOP_WEB ou <hermes-agent>/.hermes-port/renderer — o MESMO
// diretório que o hermes-desktop-web.py serve por padrão (antes saía em
// renderer2 e o servidor lia renderer: o build novo nunca entrava no ar).
// Uso: node build-desktop-web.mjs   (HERMES_AGENT_DIR muda o clone do Hermes)
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const root = process.env.HERMES_AGENT_DIR || path.join(os.homedir(), 'hermes-agent')
const app = path.join(root, 'apps/desktop')
const out = process.env.HERMES_DESKTOP_WEB || path.join(root, '.hermes-port/renderer')

const vite = await import(
  pathToFileURL(path.join(root, 'node_modules/vite/dist/node/index.js')).href
)

console.log('build →', out)
await vite.build({
  root: app,
  configLoader: 'runner',
  publicDir: path.join(app, 'public'),
  cacheDir: path.join(root, '.hermes-port/vite-cache'),
  build: { outDir: out, emptyOutDir: true }
})
console.log('build concluído')
