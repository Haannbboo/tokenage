import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer, request } from 'node:http'

import {
  resolveProxyRequestUrl,
  shouldProxyApiRequest,
  createApiProxyMiddleware,
  isLocalHost,
} from './vite-api-proxy.js'

test('shouldProxyApiRequest matches config, usage and session routes', () => {
  assert.equal(shouldProxyApiRequest('/config'), false)
  assert.equal(shouldProxyApiRequest('/config/evaluation'), true)
  assert.equal(shouldProxyApiRequest('/pricing/overrides'), true)
  assert.equal(shouldProxyApiRequest('/usage'), true)
  assert.equal(shouldProxyApiRequest('/usage/count?since=1'), true)
  assert.equal(shouldProxyApiRequest('/sessions'), true)
  assert.equal(shouldProxyApiRequest('/sessions/distribution?granularity=hour&time_bucket=1776420000000000'), true)
  assert.equal(shouldProxyApiRequest('/usage?session_id=s&limit=30&before=123:id'), true)
  assert.equal(shouldProxyApiRequest('/usage/id/tool-calls'), true)
  assert.equal(shouldProxyApiRequest('/sessions/123/context'), true)
  assert.equal(shouldProxyApiRequest('/sessions/summary'), true)
  assert.equal(shouldProxyApiRequest('/sessions/123/evaluation'), true)
  assert.equal(shouldProxyApiRequest('/sessions/123/evaluation-jobs'), true)
  assert.equal(shouldProxyApiRequest('/model-effectiveness?group_by=model'), true)
  assert.equal(shouldProxyApiRequest('/evaluation-jobs/active'), true)
  assert.equal(shouldProxyApiRequest('/evaluation-jobs/job-123'), true)
  assert.equal(shouldProxyApiRequest('/evaluation-jobs/started'), true)
  assert.equal(shouldProxyApiRequest('/evaluation-jobs/completed'), true)
  assert.equal(shouldProxyApiRequest('/sessions/daily-effectiveness'), true)
  // Retired paths must reach the API's JSON 404 handlers.
  assert.equal(shouldProxyApiRequest('/local/sessions/123/evaluation'), true)
  assert.equal(shouldProxyApiRequest('/local/evaluation-jobs/active'), true)
  assert.equal(shouldProxyApiRequest('/local/poll/job-123'), true)
  assert.equal(shouldProxyApiRequest('/poll/job-123'), true)
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

test('a browser navigation to /pricing gets the page, its API calls do not', () => {
  const navigate = { method: 'GET', headers: { 'sec-fetch-mode': 'navigate', accept: 'text/html,*/*;q=0.8' } }
  assert.equal(shouldProxyApiRequest('/pricing', navigate), false)
  assert.equal(shouldProxyApiRequest('/pricing', { method: 'GET', headers: { accept: 'text/html' } }), false)
  assert.equal(shouldProxyApiRequest('/pricing', { method: 'GET', headers: { 'sec-fetch-mode': 'cors', accept: '*/*' } }), true)
  assert.equal(shouldProxyApiRequest('/pricing?source=user&limit=50', { method: 'GET', headers: { accept: 'application/json' } }), true)
  assert.equal(shouldProxyApiRequest('/pricing/overrides', navigate), true)
})

test('a browser navigation to /sessions gets the page, its API calls do not', () => {
  const navigate = { method: 'GET', headers: { 'sec-fetch-mode': 'navigate', accept: 'text/html,*/*;q=0.8' } }
  assert.equal(shouldProxyApiRequest('/sessions', navigate), false)
  assert.equal(shouldProxyApiRequest('/sessions?session=abc', navigate), false)
  assert.equal(shouldProxyApiRequest('/sessions', { method: 'GET', headers: { accept: 'text/html' } }), false)
  assert.equal(shouldProxyApiRequest('/sessions', { method: 'GET', headers: { 'sec-fetch-mode': 'cors', accept: '*/*' } }), true)
  assert.equal(shouldProxyApiRequest('/sessions', { method: 'GET', headers: { 'sec-fetch-mode': 'cors', accept: 'text/html' } }), true)
  assert.equal(shouldProxyApiRequest('/sessions', { method: 'GET', headers: { accept: 'application/json' } }), true)
  assert.equal(shouldProxyApiRequest('/sessions', { method: 'GET', headers: { accept: 'text/html;q=0.' } }), true)
  assert.equal(shouldProxyApiRequest('/sessions', { method: 'POST', headers: navigate.headers }), true)
  assert.equal(shouldProxyApiRequest('/sessions/summary', navigate), true)
  assert.equal(shouldProxyApiRequest('/usage', navigate), true)
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

test('apiProxyMiddleware translates only same-origin browser proofs for cookie writes', async () => {
  const backend = createServer((req, res) => {
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify({ origin: req.headers.origin, referer: req.headers.referer }))
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const middleware = createApiProxyMiddleware({
    env: { TOKENAGE_API_URL: `http://127.0.0.1:${backend.address().port}` },
  })
  const frontend = createServer((req, res) => middleware(req, res, () => res.end()))
  await new Promise(resolve => frontend.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${frontend.address().port}`
  try {
    const response = await fetch(`${origin}/auth/logout`, {
      method: 'POST', headers: { origin, cookie: 'tokenage_session=test' },
    })
    const backendOrigin = `http://127.0.0.1:${backend.address().port}`
    assert.deepEqual(await response.json(), { origin: backendOrigin })
    for (const headers of [
      { origin: 'http://foreign.example' },
      { origin: `${origin}0` },
      { origin: 'null', referer: `${origin}/settings` },
      {},
    ]) {
      const rejected = await fetch(`${origin}/auth/logout`, { method: 'POST', headers })
      assert.deepEqual(await rejected.json(), headers)
    }
    // Referer is never rewritten, and a safe method's Origin is left alone:
    // the API reads either one to send Google sign-in back to Vite dev.
    for (const method of ['POST', 'GET']) {
      const headers = { referer: `${origin}/settings` }
      const kept = await fetch(`${origin}/auth/logout`, { method, headers })
      assert.deepEqual(await kept.json(), headers)
    }
    const login = await fetch(`${origin}/auth/google/login`, { headers: { origin } })
    assert.deepEqual(await login.json(), { origin })
  } finally {
    await new Promise(resolve => frontend.close(resolve))
    await new Promise(resolve => backend.close(resolve))
  }
})

test('isLocalHost follows Vite default allowedHosts', () => {
  for (const host of ['localhost:5173', 'app.localhost', '127.0.0.1:5173', '[::1]:5173', '192.168.1.5']) {
    assert.equal(isLocalHost(host), true, host)
  }
  for (const host of [undefined, '', 'rebind.attacker.example:5173', 'localhost.evil', 'evil@127.0.0.1']) {
    assert.equal(isLocalHost(host), false, host)
  }
})

test('apiProxyMiddleware leaves a DNS-rebound Host to Vite instead of proxying it', async () => {
  let proxied = 0
  const backend = createServer((req, res) => {
    proxied += 1
    res.end()
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const middleware = createApiProxyMiddleware({
    env: { TOKENAGE_API_URL: `http://127.0.0.1:${backend.address().port}` },
  })
  const frontend = createServer((req, res) =>
    middleware(req, res, () => {
      res.statusCode = 403 // Vite's own host check answers next()
      res.end()
    }),
  )
  await new Promise(resolve => frontend.listen(0, '127.0.0.1', resolve))
  const rebound = `rebind.attacker.example:${frontend.address().port}`
  const send = (method, path) =>
    new Promise((resolve, reject) => {
      const req = request(
        {
          host: '127.0.0.1',
          port: frontend.address().port,
          method,
          path,
          headers: { host: rebound, origin: `http://${rebound}` },
        },
        res => {
          res.resume()
          res.on('end', () => resolve(res.statusCode))
        },
      )
      req.on('error', reject)
      req.end()
    })
  try {
    assert.equal(await send('POST', '/auth/cli/start'), 403)
    assert.equal(await send('GET', '/auth/me'), 403)
    assert.equal(proxied, 0)
  } finally {
    await new Promise(resolve => frontend.close(resolve))
    await new Promise(resolve => backend.close(resolve))
  }
})
