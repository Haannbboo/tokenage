import { resolveApiUrl } from './vite-api-url.js'

// SPA pages that share their path with an API route (mirrors
// _SPA_API_SHARED_PAGES in src/api.py): a browser navigation gets the page.
const SHARED_PAGES = new Set(['/sessions'])

export function isPageNavigation(method = 'GET', headers = {}) {
  if (method !== 'GET') return false
  const mode = headers['sec-fetch-mode']
  if (mode !== undefined) return mode === 'navigate'
  const accept = String(headers.accept ?? '')
  const [media, ...params] = accept.split(',')[0].toLowerCase().split(';')
  return media.trim() === 'text/html' && !params.some(p => {
    const match = /^q=([0-9]+(?:\.[0-9]*)?)$/.exec(p.trim())
    return match !== null && Number(match[1]) === 0
  })
}

export function shouldProxyApiRequest(requestUrl, { method, headers } = {}) {
  const pathname = new URL(requestUrl, 'http://localhost').pathname
  if (SHARED_PAGES.has(pathname) && headers && isPageNavigation(method, headers)) return false
  return (
    pathname === '/pricing' ||
    pathname.startsWith('/pricing/') ||
    pathname === '/auth' ||
    pathname.startsWith('/auth/') ||
    pathname === '/usage' ||
    pathname.startsWith('/usage/') ||
    // Retired endpoints must reach the API's JSON 404 handlers.
    pathname === '/evaluation-jobs' ||
    pathname.startsWith('/evaluation-jobs/') ||
    pathname === '/config/evaluation' ||
    pathname.startsWith('/poll/') ||
    pathname.startsWith('/local/') ||
    pathname === '/model-effectiveness' ||
    pathname === '/sessions' ||
    pathname.startsWith('/sessions/') ||
    pathname === '/devices/status' ||
    pathname === '/version'
  )
}

export function resolveProxyRequestUrl(
  requestUrl,
  { env, trackerConfigPath } = {},
) {
  const normalizedRequestUrl = new URL(requestUrl, 'http://localhost')
  return new URL(
    `${normalizedRequestUrl.pathname}${normalizedRequestUrl.search}`,
    resolveApiUrl({ env, trackerConfigPath }),
  ).toString()
}

async function readRequestBody(req) {
  const chunks = []
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  }
  return chunks.length > 0 ? Buffer.concat(chunks) : undefined
}

const LOOPBACK_ADDRESSES = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1'])

function buildForwardHeaders(headers, remoteAddress) {
  const forwarded = new Headers()
  for (const [key, value] of Object.entries(headers)) {
    if (value === undefined) continue
    if (key.toLowerCase() === 'host') continue

    if (Array.isArray(value)) {
      for (const item of value) {
        forwarded.append(key, item)
      }
      continue
    }

    forwarded.set(key, value)
  }
  // The API treats a direct loopback request as the local owner. This proxy
  // is itself loopback, so mark requests from other machines (vite --host) as
  // forwarded or they would be signed in as the owner.
  if (remoteAddress && !LOOPBACK_ADDRESSES.has(remoteAddress)) {
    forwarded.set('x-forwarded-for', remoteAddress)
  }
  return forwarded
}

export function createApiProxyMiddleware({ env, trackerConfigPath } = {}) {
  return async function apiProxyMiddleware(req, res, next) {
    if (!req.url || !shouldProxyApiRequest(req.url, { method: req.method, headers: req.headers })) {
      next()
      return
    }

    try {
      const response = await fetch(
        resolveProxyRequestUrl(req.url, { env, trackerConfigPath }),
        {
          method: req.method,
          headers: buildForwardHeaders(req.headers, req.socket?.remoteAddress),
          body:
            req.method === 'GET' || req.method === 'HEAD'
              ? undefined
              : await readRequestBody(req),
          // OAuth routes (/auth/google/login, /auth/google/callback) rely on
          // 302 Location and Set-Cookie reaching the browser as-is. Default
          // 'follow' redirect makes fetch() swallow those server-side.
          redirect: 'manual',
        },
      )

      res.statusCode = response.status
      response.headers.forEach((value, key) => {
        if (key.toLowerCase() === 'set-cookie') return
        res.setHeader(key, value)
      })
      const setCookies = response.headers.getSetCookie?.() ?? []
      if (setCookies.length > 0) {
        res.setHeader('set-cookie', setCookies)
      }

      const body = Buffer.from(await response.arrayBuffer())
      res.end(body)
    } catch (error) {
      res.statusCode = 502
      res.setHeader('content-type', 'application/json')
      res.end(
        JSON.stringify({
          detail:
            error instanceof Error
              ? error.message
              : 'Failed to proxy request to tokenage API',
        }),
      )
    }
  }
}

export function createApiProxyPlugin(options = {}) {
  return {
    name: 'tokenage-api-proxy',
    configureServer(server) {
      server.middlewares.use(createApiProxyMiddleware(options))
    },
  }
}
