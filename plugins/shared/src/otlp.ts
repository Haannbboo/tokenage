export type OtlpPayloadParams = {
  sessionId: string
  messageId: string
  model: string
  provider: string
  promptLength: number | null
  inputTokens: number | null
  outputTokens: number | null
  reasoningTokens: number | null
  cachedTokens: number | null
  cacheCreationTokens: number | null
  totalTokens: number | null
  durationMs: number | null
  ttftMs: number | null
  statusCode: number | null
  errorName: string | null
  timestampMs: number
  // OpenCode/Kilo variant name (the server keeps it when it is an effort level).
  variant?: unknown
}

export function buildOtlpPayload(
  params: OtlpPayloadParams,
  serviceName: string,
): Record<string, any> {
  const eventName = `${serviceName}.message_completed`
  const scopeName = `${serviceName}-tokenage`
  const timeUnixNano = String(params.timestampMs * 1_000_000)
  const severityNumber = params.errorName != null || (params.statusCode != null && params.statusCode >= 400) ? 17 : 9

  const attrs: Record<string, any>[] = [
    { key: "event.name", value: { stringValue: eventName } },
    { key: "session.id", value: { stringValue: params.sessionId } },
    { key: "message.id", value: { stringValue: params.messageId } },
    { key: "model", value: { stringValue: params.model } },
    { key: "provider", value: { stringValue: params.provider } },
  ]

  if (params.inputTokens != null) {
    attrs.push({ key: "input_token_count", value: { intValue: params.inputTokens } })
  }
  if (params.outputTokens != null) {
    attrs.push({ key: "output_token_count", value: { intValue: params.outputTokens } })
  }
  if (params.totalTokens != null) {
    attrs.push({ key: "total_token_count", value: { intValue: params.totalTokens } })
  }
  if (params.durationMs != null) {
    attrs.push({ key: "duration_ms", value: { intValue: params.durationMs } })
  }
  if (params.reasoningTokens != null) {
    attrs.push({ key: "reasoning_token_count", value: { intValue: params.reasoningTokens } })
  }
  if (params.statusCode != null) {
    attrs.push({ key: "http.response.status_code", value: { intValue: params.statusCode } })
    attrs.push({ key: "status_code", value: { intValue: params.statusCode } })
  }
  if (params.errorName != null) {
    attrs.push({ key: "error.type", value: { stringValue: params.errorName } })
  }
  if (params.ttftMs != null) {
    attrs.push({ key: "ttft_ms", value: { intValue: params.ttftMs } })
  }
  if (params.promptLength != null) {
    attrs.push({ key: "prompt_length", value: { intValue: params.promptLength } })
  }
  if (params.cachedTokens != null) {
    attrs.push({ key: "cached_token_count", value: { intValue: params.cachedTokens } })
  }
  if (params.cacheCreationTokens != null) {
    attrs.push({ key: "cache_creation_token_count", value: { intValue: params.cacheCreationTokens } })
  }
  // Capped: the variant is a user-defined name; the server stores levels only.
  const variant = typeof params.variant === "string" ? params.variant.trim().slice(0, 64) : ""
  if (variant) {
    attrs.push({ key: "variant", value: { stringValue: variant } })
  }

  return {
    resourceLogs: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: serviceName } },
            { key: "session.id", value: { stringValue: params.sessionId } },
          ],
        },
        scopeLogs: [
          {
            scope: { name: scopeName },
            logRecords: [{ timeUnixNano, severityNumber, attributes: attrs }],
          },
        ],
      },
    ],
  }
}

export function buildToolEventPayload(
  params: {
    sessionId: string
    messageId: string
    callId: string
    toolName: string
    timestampMs: number
    durationMs?: number
  },
  serviceName: string,
): Record<string, any> {
  const eventName = `${serviceName}.tool_decision`
  const scopeName = `${serviceName}-tokenage`
  const timeUnixNano = String(params.timestampMs * 1_000_000)
  const attributes: Array<Record<string, any>> = [
    { key: "event.name", value: { stringValue: eventName } },
    { key: "session.id", value: { stringValue: params.sessionId } },
    { key: "message.id", value: { stringValue: params.messageId } },
    { key: "call_id", value: { stringValue: params.callId } },
    { key: "tool_name", value: { stringValue: params.toolName } },
  ]
  if (typeof params.durationMs === "number" && params.durationMs >= 0) {
    attributes.push({
      key: "duration_ms",
      value: { stringValue: String(params.durationMs) },
    })
  }

  return {
    resourceLogs: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: serviceName } },
            { key: "session.id", value: { stringValue: params.sessionId } },
          ],
        },
        scopeLogs: [
          {
            scope: { name: scopeName },
            logRecords: [
              {
                timeUnixNano,
                severityNumber: 9,
                attributes,
              },
            ],
          },
        ],
      },
    ],
  }
}

export async function emitOtlp(
  payload: Record<string, any>,
  endpoint: string,
  timeoutMs = 10_000,
  headers?: Record<string, string>,
): Promise<boolean> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    if (!response.ok) {
      console.error(`[tokenage] OTLP emission failed: collector returned ${response.status}`)
      return false
    }
    return true
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      console.error(`[tokenage] OTLP emission timed out after ${timeoutMs}ms`)
      return false
    }
    console.error("[tokenage] OTLP emission failed:", err)
    return false
  } finally {
    clearTimeout(timeoutId)
  }
}
