import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../../', import.meta.url))
const directory = mkdtempSync(join(tmpdir(), 'tentacles-openapi-'))
const target = join(root, 'frontend/src/api-types.ts')
try {
  const python = process.env.TENTACLES_PYTHON || join(root, '.venv/bin/python')
  const schema = execFileSync(python, ['-m', 'tentacles.api_schema'], { cwd: root, maxBuffer: 8 * 1024 * 1024 })
  const source = join(directory, 'openapi.json')
  const output = join(directory, 'api-types.ts')
  writeFileSync(source, schema)
  execFileSync('pnpm', ['exec', 'openapi-typescript', source, '--output', output], { cwd: resolve(root, 'frontend'), stdio: 'inherit' })
  const generated = readFileSync(output, 'utf8')
  if (process.argv.includes('--check')) {
    if (readFileSync(target, 'utf8') !== generated) throw new Error('API types are stale. Run pnpm generate:api.')
    console.log('API types match the backend schema.')
  } else writeFileSync(target, generated)
} finally { rmSync(directory, { recursive: true, force: true }) }
