import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:http'

import {
  resolveProxyRequestUrl,
  shouldProxyApiRequest,
  createApiProxyMiddleware,
} from './vite-api-proxy.js'

test('shouldProxyApiRequest matches config, usage and session routes', () => {
  assert.equal(shouldProxyApiRequest('/config'), true)
  assert.equal(shouldProxyApiRequest('/config/evaluation'), true)
  assert.equal(shouldProxyApiRequest('/usage'), true)
  assert.equal(shouldProxyApiRequest('/usage/count?since=1'), true)
  assert.equal(shouldProxyApiRequest('/sessions'), true)
  assert.equal(shouldProxyApiRequest('/sessions/summary'), true)
  assert.equal(shouldProxyApiRequest('/local/sessions/123/evaluation'), true)
  assert.equal(shouldProxyApiRequest('/model-effectiveness?group_by=model'), true)
  assert.equal(shouldProxyApiRequest('/local/poll/job-123'), true)
  assert.equal(shouldProxyApiRequest('/local/evaluation-jobs/active'), true)
  assert.equal(shouldProxyApiRequest('/local/evaluation-jobs/job-123'), true)
  assert.equal(shouldProxyApiRequest('/auth/me'), true)
  assert.equal(shouldProxyApiRequest('/auth/google/login'), true)
  assert.equal(shouldProxyApiRequest('/auth/google/callback?code=x&state=y'), true)
  assert.equal(shouldProxyApiRequest('/auth/devices'), true)
  assert.equal(shouldProxyApiRequest('/auth/devices/abc-123/revoke'), true)
  assert.equal(shouldProxyApiRequest('/auth/logout'), true)
  assert.equal(shouldProxyApiRequest('/devices/status'), true)
  assert.equal(shouldProxyApiRequest('/version'), true)
  assert.equal(shouldProxyApiRequest('/assets/index.js'), false)
})

test('resolveProxyRequestUrl re-reads tracker config for each request', () => {
  const root = mkdtempSync(join(tmpdir(), 'tokenage-vite-api-proxy-'))
  const configPath = join(root, 'config.yaml')

  try {
    writeFileSync(
      configPath,
      ['server:', '  host: 127.0.0.1', '  api_port: 4004', ''].join('\n'),
      'utf-8',
    )
    assert.equal(
      resolveProxyRequestUrl('/usage/count', {
        env: {},
        trackerConfigPath: configPath,
      }),
      'http://127.0.0.1:4004/usage/count',
    )

    writeFileSync(
      configPath,
      ['server:', '  host: 127.0.0.1', '  api_port: 4011', ''].join('\n'),
      'utf-8',
    )
    assert.equal(
      resolveProxyRequestUrl('/usage/count', {
        env: {},
        trackerConfigPath: configPath,
      }),
      'http://127.0.0.1:4011/usage/count',
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resolveProxyRequestUrl ignores an incoming absolute frontend origin', () => {
  assert.equal(
    resolveProxyRequestUrl('http://localhost:5173/config?x=1', {
      env: {
        TOKENAGE_API_URL: 'http://127.0.0.1:4004',
      },
    }),
    'http://127.0.0.1:4004/config?x=1',
  )
})

test('apiProxyMiddleware relays a 302 with Location and Set-Cookie instead of following it', async () => {
  // Regression: fetch()'s default redirect: 'follow' resolves 302s
  // server-side and drops Location/Set-Cookie, which breaks the OAuth
  // callback flow (/auth/google/callback) under `npm run dev`.
  const backend = createServer((req, res) => {
    res.setHeader('set-cookie', [
      'session=abc123; Path=/; HttpOnly',
      'other=xyz; Path=/',
    ])
    res.writeHead(302, { Location: 'https://accounts.google.com/o/oauth2/auth' })
    res.end()
  })

  await new Promise(resolve => backend.listen(0, resolve))
  const backendPort = backend.address().port

  const middleware = createApiProxyMiddleware({
    env: { TOKENAGE_API_URL: `http://127.0.0.1:${backendPort}` },
  })
  const frontend = createServer((req, res) => {
    middleware(req, res, () => {
      res.statusCode = 404
      res.end()
    })
  })

  await new Promise(resolve => frontend.listen(0, resolve))
  const frontendPort = frontend.address().port

  try {
    const response = await fetch(
      `http://127.0.0.1:${frontendPort}/auth/google/callback?code=x`,
      { redirect: 'manual' },
    )

    assert.equal(response.status, 302)
    assert.equal(
      response.headers.get('location'),
      'https://accounts.google.com/o/oauth2/auth',
    )
    assert.deepEqual(response.headers.getSetCookie(), [
      'session=abc123; Path=/; HttpOnly',
      'other=xyz; Path=/',
    ])
  } finally {
    await new Promise(resolve => frontend.close(resolve))
    await new Promise(resolve => backend.close(resolve))
  }
})
