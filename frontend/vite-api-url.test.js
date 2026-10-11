import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { resolveApiUrl } from './vite-api-url.js'

test('resolveApiUrl prefers TOKENAGE_API_URL over config', () => {
  const root = mkdtempSync(join(tmpdir(), 'tokenage-vite-api-url-'))
  const configPath = join(root, 'config.yaml')
  writeFileSync(
    configPath,
    [
      'server:',
      '  host: 127.0.0.1',
      '  api_port: 4999',
      '',
    ].join('\n'),
    'utf-8',
  )

  try {
    assert.equal(
      resolveApiUrl({
        env: {
          TOKENAGE_API_URL: 'http://localhost:4011',
          TOKENAGE_BACKEND_URL: 'http://localhost:4999',
        },
        trackerConfigPath: configPath,
      }),
      'http://localhost:4011',
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resolveApiUrl falls back to tracker config api_port', () => {
  const root = mkdtempSync(join(tmpdir(), 'tokenage-vite-api-url-'))
  const configPath = join(root, 'config.yaml')
  writeFileSync(
    configPath,
    [
      'server:',
      '  host: 127.0.0.1',
      '  api_port: 4011',
      '',
    ].join('\n'),
    'utf-8',
  )

  try {
    assert.equal(
      resolveApiUrl({
        env: {},
        trackerConfigPath: configPath,
      }),
      'http://127.0.0.1:4011',
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resolveApiUrl falls back to default localhost api port when config is missing', () => {
  assert.equal(
    resolveApiUrl({
      env: {},
      trackerConfigPath: join(tmpdir(), 'missing-tokenage-config.yaml'),
    }),
    'http://localhost:4001',
  )
})
