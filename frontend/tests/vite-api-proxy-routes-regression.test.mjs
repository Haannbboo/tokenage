import assert from 'node:assert/strict'
import { shouldProxyApiRequest } from '../vite-api-proxy.js'

const proxiedRoutes = [
  '/config/evaluation',
  '/usage',
  '/usage/count',
  '/usage/logs?limit=1',
  '/usage/tools',
  '/devices/status',
  '/model-effectiveness?group_by=model',
  '/evaluation-jobs/active',
  '/evaluation-jobs/job-123',
  '/evaluation-jobs/started',
  '/sessions/session-123/evaluation',
  '/sessions/session-123/evaluation-jobs',
  '/pricing',
  '/pricing/z-ai/glm-5.1-20260406',
  '/pricing/overrides',
  '/pricing/overrides/z-ai%2Fglm-5.1',
  '/usage/reprice?model=z-ai%2Fglm-5.1',
  '/auth/devices',
  '/auth/devices/device-123/revoke',
]

for (const route of proxiedRoutes) {
  assert.equal(
    shouldProxyApiRequest(route),
    true,
    `${route} should be proxied to the tokenage API`,
  )
}

// The /local/* evaluation routes were hard removed; requests must not be
// proxied to the API anymore.
const frontendRoutes = [
  '/',
  '/assets/index.js',
  '/favicon.ico',
  '/local/evaluation-jobs/active',
  '/local/sessions/session-123/evaluation-jobs',
  '/local/poll/job-123',
]

for (const route of frontendRoutes) {
  assert.equal(
    shouldProxyApiRequest(route),
    false,
    `${route} should remain handled by Vite/frontend`,
  )
}

console.log('vite api proxy route coverage regression passed')
