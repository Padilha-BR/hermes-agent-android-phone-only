// build-desktop-web.mjs — builda a UI do app desktop do Hermes como bundle web,
// replicando EXATAMENTE o caminho oficial (scripts/build/desktop.mjs) menos o
// empacotamento Electron: mesmo carregador de config ('runner') e mesmo publicDir.
// Saída: .hermes-port/renderer2 (servida pelo hermes-desktop-web.py).
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const root = '/data/data/com.termux/files/home/hermes-agent'
const app = path.join(root, 'apps/desktop')
const out = path.join(root, '.hermes-port/renderer2')

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
