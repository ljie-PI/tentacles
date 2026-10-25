import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer as createTcpServer } from 'node:net'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import { createServer, loadConfigFromFile } from 'vite'

const root = fileURLToPath(new URL('../../', import.meta.url))
const frontend = join(root, 'frontend')
const directory = mkdtempSync(join(tmpdir(), 'tentacles-dev-proxy-'))
const socket = createTcpServer()
socket.listen(0, '127.0.0.1')
await once(socket, 'listening')
const backendPort = socket.address().port
await new Promise(resolve => socket.close(resolve))
const python = process.env.TENTACLES_PYTHON || join(root, '.venv/bin/python')
const script = `
import sys, httpx, uvicorn
from pathlib import Path
from tentacles.config import AppConfig
from tentacles.server import create_app
root=Path(sys.argv[1])
config=AppConfig.model_validate({'app':{'data_dir':str(root/'data'),'reports_dir':str(root/'reports')},
    'server':{'scheduler':False},'sources':[{'id':'news','type':'rss','url':'https://fixture.test/rss','schedule':'1h'}]})
def respond(request):
    assert request.url.host=='fixture.test'
    return httpx.Response(200,text='<rss version="2.0"><channel><title>Test</title><item><title>Proxy bulletin</title><guid>one</guid></item></channel></rss>')
uvicorn.run(create_app(config,transport=httpx.MockTransport(respond)),host='127.0.0.1',port=int(sys.argv[2]),access_log=False,log_level='warning')
`
const backend = spawn(python, ['-c', script, directory, String(backendPort)], { cwd: root, stdio: ['ignore', 'ignore', 'pipe'] })
let log = ''
let backendDone = false
let spawnFailed = false
const backendExited = new Promise(resolve => {
  backend.once('exit', () => { backendDone = true; resolve() })
  backend.once('error', () => { backendDone = true; spawnFailed = true; resolve() })
})
backend.stderr.on('data', data => { log += data.toString() })
let vite
try {
  const target = `http://127.0.0.1:${backendPort}`
  let ready = false
  for (let i = 0; i < 100; i++) {
    if (spawnFailed || backendDone) throw new Error('Backend exited before readiness. Check TENTACLES_PYTHON.')
    try { ready = (await fetch(`${target}/api/health`, { signal: AbortSignal.timeout(1000) })).ok } catch { /* Wait for listener. */ }
    if (ready) break
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  assert(ready, 'backend readiness timeout')
  const loaded = await loadConfigFromFile({ command: 'serve', mode: 'development' }, join(frontend, 'vite.config.ts'), frontend)
  assert(loaded, 'missing Vite config')
  const config = loaded.config
  const proxy = config.server.proxy['/api']
  // Change only the test target, never normalize string/object proxy style:
  // a regression to string-form must retain Vite's implicit Host rewriting.
  config.server.proxy['/api'] = typeof proxy === 'string' ? target : { ...proxy, target }
  vite = await createServer({ ...config, configFile: false, root: frontend,
    server: { ...config.server, host: '127.0.0.1', port: 0 } })
  await vite.listen()
  const frontendPort = vite.httpServer.address().port
  const origin = `http://127.0.0.1:${frontendPort}`
  const listed = await fetch(`${origin}/api/sources`, { signal: AbortSignal.timeout(3000) })
  assert.equal(listed.status, 200)
  const fetched = await fetch(`${origin}/api/sources/news/fetch`, { method: 'POST', headers: { Origin: origin }, signal: AbortSignal.timeout(3000) })
  assert.equal(fetched.status, 200, 'same-origin bodyless POST through configured Vite proxy must succeed')
  assert.equal((await fetched.json()).status, 'ok')
  const denied = await fetch(`${origin}/api/sources/news/fetch`, { method: 'POST', headers: { Origin: 'http://untrusted.test' }, signal: AbortSignal.timeout(3000) })
  assert.equal(denied.status, 403, 'proxy must not bypass origin protection')
  console.log('Actual Vite + Uvicorn: development GET/manual fetch work; hostile Origin rejected. PASS')
} finally {
  try {
    if (vite) await vite.close()
  } finally {
    if (!backendDone) {
      backend.kill('SIGTERM')
      await Promise.race([backendExited, new Promise(resolve => setTimeout(resolve, 3000).unref())])
      if (!backendDone) { backend.kill('SIGKILL'); await backendExited }
    }
    rmSync(directory, { recursive: true, force: true })
  }
  // Keep even synthetic request/provider log contents out of normal output.
  if (log.includes('Traceback')) throw new Error('Backend emitted an unexpected traceback.')
}
