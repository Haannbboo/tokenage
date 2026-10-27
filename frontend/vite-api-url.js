import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

import yaml from 'js-yaml'

const DEFAULT_API_URL = 'http://localhost:4001'

function buildApiUrlFromConfig(config) {
  const server = config?.server ?? {}
  const apiPort = server.api_port ?? 4001
  if (server.base_url) {
    return server.base_url.replace(/\/+$/, '')
  }
  const host = server.host || 'localhost'
  return `http://${host}:${apiPort}`
}

function readApiUrlFromTrackerConfig(trackerConfigPath) {
  try {
    const parsed = yaml.load(readFileSync(trackerConfigPath, 'utf-8')) ?? {}
    return buildApiUrlFromConfig(parsed)
  } catch {
    return null
  }
}

export function resolveApiUrl({
  env,
  trackerConfigPath = join(homedir(), '.tokenage', 'config.yaml'),
} = {}) {
  if (env?.TOKENAGE_API_URL) {
    return env.TOKENAGE_API_URL
  }

  if (env?.TOKENAGE_BACKEND_URL) {
    return env.TOKENAGE_BACKEND_URL
  }

  return readApiUrlFromTrackerConfig(trackerConfigPath) ?? DEFAULT_API_URL
}
