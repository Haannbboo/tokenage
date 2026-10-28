import { finiteNumber, statusCodeFromError, errorName } from "./helpers.js"
import { makePromptState, MAX_TRACKED_MESSAGES } from "./prompt.js"
import { claim, release } from "./dedupe.js"
import { buildOtlpPayload, buildToolEventPayload, emitOtlp } from "./otlp.js"

declare const process: { env: Record<string, string | undefined> }

// No default collector: an entry without an `endpoint` is not configured and
// the plugin stays inert rather than posting to a guessed address.
function getEndpoint(options?: Record<string, any>): string | undefined {
  return options?.endpoint && typeof options.endpoint === "string" ? options.endpoint : undefined
}

export function resolveOptions(options?: Record<string, any>): {
  endpoint: string | undefined
  headers: Record<string, string> | undefined
} {
  const token = typeof options?.token === "string" ? options.token : undefined
  const headers = new Map<string, string>()
  // Use tokenage-specific headers to avoid forwarding credentials for other OTLP backends.
  for (const entry of (process.env.TOKENAGE_OTLP_HEADERS ?? "").split(",")) {
    const separator = entry.indexOf("=")
    if (separator < 0) continue
    const key = entry.slice(0, separator).trim().toLowerCase()
    if (key === "content-type" || key === "x-tokenage-token") continue
    try {
      const value = decodeURIComponent(entry.slice(separator + 1).trim())
      if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key) || /[^\t\x20-\x7e\x80-\xff]/.test(value)) throw new Error()
      headers.set(key, value)
    } catch {
      console.warn(`Skipping invalid OTLP header: ${JSON.stringify(key)}`)
    }
  }
  if (token) headers.set("x-tokenage-token", token)
  return {
    endpoint: getEndpoint(options),
    headers: headers.size ? Object.fromEntries(headers) : undefined,
  }
}

function normalizedProvider(value: unknown): string {
  if (typeof value !== "string") return "unknown"
  const normalized = value.trim().toLowerCase()
  return normalized || "unknown"
}

function nonNegativeNumber(value: unknown): number | null {
  const parsed = finiteNumber(value)
  return parsed != null && parsed >= 0 ? parsed : null
}

function getCompletedInfo(info: any): { completedAt: number | null; statusCode: number | null } {
  const knownStatus = statusCodeFromError(info.error)
  const statusCode = knownStatus ?? (info.error ? 500 : null)
  const completedAt = finiteNumber(info.time?.completed)
  if (completedAt == null && statusCode == null) return { completedAt: null, statusCode: null }
  if (!info.tokens && statusCode == null) return { completedAt: null, statusCode: null }
  return { completedAt, statusCode }
}

// Length of the union of tool intervals clipped to [start, end]: parallel tools count once.
export function toolTimeWithin(intervals: [number, number][], start: number, end: number): number {
  let total = 0
  let cursor = start
  for (const [s, e] of [...intervals].sort((a, b) => a[0] - b[0])) {
    const lo = Math.max(s, cursor)
    const hi = Math.min(e, end)
    if (hi > lo) {
      total += hi - lo
      cursor = hi
    }
  }
  return total
}

async function handleMessageUpdated(
  input: any,
  endpoint: string,
  info: any,
  clientSource: string,
  processed: Set<string>,
  pending: Set<string>,
  promptState: ReturnType<typeof makePromptState>,
  toolIntervals: Map<string, [number, number][]>,
  headers?: Record<string, string>,
): Promise<void> {
  if (info.role === "user") {
    promptState.rememberUserMessage(info.id)
    return
  }

  if (info.role !== "assistant") return
  if (processed.has(info.id) || pending.has(info.id)) return

  const { completedAt, statusCode } = getCompletedInfo(info)
  if (completedAt == null && statusCode == null) return

  const claimKey = `message:${info.id}`
  if (!claim(claimKey)) return
  pending.add(info.id)
  let emitted = false

  try {
    const provider = normalizedProvider(info.providerID)
    const model = info.modelID || "unknown"
    const createdAt: number = finiteNumber(info.time?.created) ?? Date.now()
    const timestampMs = completedAt ?? Date.now()
    // Latency excludes tool execution, like Claude Code and Codex.
    const durationMs =
      completedAt != null
        ? Math.max(0, completedAt - createdAt - toolTimeWithin(toolIntervals.get(info.id) ?? [], createdAt, completedAt))
        : null
    const firstPartStart = promptState.firstAssistantPartStart.get(info.id)
    const ttftMs =
      firstPartStart != null ? Math.max(0, Math.round(firstPartStart - createdAt)) : null

    const inputTokens = nonNegativeNumber(info.tokens?.input)
    const outputTokens = nonNegativeNumber(info.tokens?.output)
    const reasoningTokens = nonNegativeNumber(info.tokens?.reasoning)
    const cachedTokens = nonNegativeNumber(info.tokens?.cache?.read)
    const cacheCreationTokens = nonNegativeNumber(info.tokens?.cache?.write)
    const tokenValues = [inputTokens, outputTokens, reasoningTokens]
    const totalTokens: number | null = tokenValues.some((value) => value != null)
      ? tokenValues.reduce<number>((sum, value) => sum + (value ?? 0), 0)
      : null
    const promptLength =
      (promptState.userMessages.has(info.parentID) ? promptState.promptLengthForMessage(info.parentID) : null) ??
      (await promptState.fetchPromptLength(input.client, info.sessionID, info.parentID))

    const payload = buildOtlpPayload(
      {
        sessionId: info.sessionID,
        messageId: info.id,
        model,
        provider,
        promptLength,
        inputTokens,
        outputTokens,
        reasoningTokens,
        cachedTokens,
        cacheCreationTokens,
        totalTokens,
        durationMs,
        ttftMs,
        statusCode,
        errorName: errorName(info.error),
        timestampMs,
      },
      clientSource,
    )

    emitted = await emitOtlp(payload, endpoint, 10_000, headers)
    if (!emitted) return

    processed.add(info.id)
    if (processed.size > 10_000) {
      const entries = Array.from(processed)
      for (let i = 0; i < entries.length / 2; i++) {
        processed.delete(entries[i])
      }
    }
    promptState.firstAssistantPartStart.delete(info.id)
    toolIntervals.delete(info.id)
  } finally {
    if (!emitted) release(claimKey)
    pending.delete(info.id)
  }
}

export function createPlugin(clientSource: string) {
  const processed = new Set<string>()
  const pending = new Set<string>()
  const promptState = makePromptState()
  const emittedTools = new Set<string>()
  const toolIntervals = new Map<string, [number, number][]>()

  return async (input: any, options?: Record<string, any>) => {
    const { endpoint, headers } = resolveOptions(options)
    if (!endpoint) return {}

    return {
      event: async ({ event }: { event: { type: string; properties?: Record<string, any> } }) => {
        if (event.type === "message.part.updated") {
          const part = event.properties?.part
          if (!part) return
          promptState.rememberPromptPart(part)
          promptState.rememberFirstAssistantPart(part, Date.now())

          const t = part.state?.time
          if (
            part.type === "tool" &&
            (part.state?.status === "completed" || part.state?.status === "error") &&
            part.messageID &&
            typeof t?.start === "number" &&
            typeof t?.end === "number"
          ) {
            const intervals = toolIntervals.get(part.messageID) ?? []
            intervals.push([t.start, t.end])
            toolIntervals.set(part.messageID, intervals)
            while (toolIntervals.size > MAX_TRACKED_MESSAGES) {
              toolIntervals.delete(toolIntervals.keys().next().value!)
            }
          }

          // Emit tool_decision event when a ToolPart completes.
          if (
            part.type === "tool" &&
            part.state?.status === "completed" &&
            part.tool &&
            part.callID &&
            !emittedTools.has(part.callID) &&
            claim(`tool:${part.callID}`)
          ) {
            emittedTools.add(part.callID)
            if (emittedTools.size > 10_000) {
              const entries = Array.from(emittedTools)
              for (let i = 0; i < entries.length / 2; i++) emittedTools.delete(entries[i])
            }
            const time = part.state?.time
            const durationMs =
              time &&
              typeof time.start === "number" &&
              typeof time.end === "number"
                ? Math.max(0, time.end - time.start)
                : undefined
            const toolPayload = buildToolEventPayload(
              {
                sessionId: part.sessionID,
                messageId: part.messageID,
                callId: part.callID,
                toolName: part.tool,
                // Stamped at the tool's start, like the other sources' tool events.
                timestampMs:
                  typeof time?.start === "number" ? time.start : typeof time?.end === "number" ? time.end : Date.now(),
                durationMs,
              },
              clientSource,
            )
            if (!(await emitOtlp(toolPayload, endpoint, 10_000, headers))) release(`tool:${part.callID}`)
          }
          return
        }

        if (event.type !== "message.updated") return

        const info = event.properties?.info
        if (!info) return

        await handleMessageUpdated(input, endpoint, info, clientSource, processed, pending, promptState, toolIntervals, headers)
      },
    }
  }
}
