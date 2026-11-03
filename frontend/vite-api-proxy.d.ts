type RequestInfo = {
  method?: string
  headers?: Record<string, string | string[] | undefined>
}

export function isPageNavigation(method?: string, headers?: RequestInfo['headers']): boolean

export function shouldProxyApiRequest(requestUrl: string, request?: RequestInfo): boolean

export function resolveProxyRequestUrl(
  requestUrl: string,
  options?: {
    env?: Record<string, string | undefined>
    trackerConfigPath?: string
  },
): string

export function createApiProxyMiddleware(options?: {
  env?: Record<string, string | undefined>
  trackerConfigPath?: string
}): (
  req: import('node:http').IncomingMessage,
  res: import('node:http').ServerResponse,
  next: () => void,
) => Promise<void>

export function createApiProxyPlugin(options?: {
  env?: Record<string, string | undefined>
  trackerConfigPath?: string
}): {
  name: string
  configureServer(server: {
    middlewares: { use(middleware: unknown): void }
  }): void
}
