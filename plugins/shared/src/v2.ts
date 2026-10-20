import { finiteNumber } from "./helpers.js"
import { MAX_TRACKED_MESSAGES } from "./prompt.js"
import { buildOtlpPayload, buildToolEventPayload, emitOtlp } from "./otlp.js"
import { claim, release } from "./dedupe.js"
import { resolveOptions, toolTimeWithin } from "./plugin.js"

// OpenCode 2 publishes its public event stream through `ctx.event.subscribe()`.
// Unlike v1 there is no message/part snapshot: one assistant message is one
// model step, framed by `session.step.started` and `session.step.ended` (or
// `session.step.failed`), with tool calls and text/reasoning events in between.

type V2Event = {
  id?: string
  created?: number
  type?: string
  data?: Record<string, any>
}

type StepState = {
  sessionId: string
  provider: string
  model: string
  startedAt: number
  firstPartAt: number | null
}

type ToolState = {
  sessionId: string
  messageId: string
  name: string
  calledAt: number | null
}

function nonNegative(value: unknown): number | null {
  const parsed = finiteNumber(value)
  return parsed != null && parsed >= 0 ? parsed : null
}

function trim<K, V>(map: Map<K, V>): void {
  while (map.size > MAX_TRACKED_MESSAGES) {
    const oldest = map.keys().next().value
    if (oldest === undefined) break
    map.delete(oldest)
  }
}

function createV2Tracker(
  clientSource: string,
  endpoint: string,
  headers: Record<string, string> | undefined,
) {
  const steps = new Map<string, StepState>()
  const tools = new Map<string, ToolState>()
  // Tool run intervals per assistant message, subtracted from the step latency.
  const toolIntervals = new Map<string, [number, number][]>()

  function recordToolInterval(callId: string, end: number): void {
    const tool = tools.get(callId)
    if (!tool?.messageId || tool.calledAt == null) return
    const intervals = toolIntervals.get(tool.messageId) ?? []
    intervals.push([tool.calledAt, end])
    toolIntervals.set(tool.messageId, intervals)
    trim(toolIntervals)
  }
  const promptLengths = new Map<string, number>()

  async function finishStep(event: V2Event, failed: boolean): Promise<void> {
    const data = event.data ?? {}
    const messageId: string | undefined = data.assistantMessageID
    if (!messageId) return
    const step = steps.get(messageId)
    steps.delete(messageId)
    const intervals = toolIntervals.get(messageId) ?? []
    toolIntervals.delete(messageId)
    const sessionId: string | undefined = data.sessionID ?? step?.sessionId
    if (!sessionId || !claim(`message:${messageId}`)) return

    const now = finiteNumber(event.created) ?? Date.now()
    const error = failed ? data.error : null
    const statusCode = failed ? (finiteNumber(error?.status) ?? 500) : null
    const tokens = data.tokens
    const inputTokens = nonNegative(tokens?.input)
    const outputTokens = nonNegative(tokens?.output)
    const reasoningTokens = nonNegative(tokens?.reasoning)
    const parts = [inputTokens, outputTokens, reasoningTokens]
    const totalTokens = parts.some((value) => value != null)
      ? parts.reduce<number>((sum, value) => sum + (value ?? 0), 0)
      : null

    const payload = buildOtlpPayload(
      {
        sessionId,
        messageId,
        model: step?.model ?? "unknown",
        provider: step?.provider ?? "unknown",
        promptLength: promptLengths.get(sessionId) ?? null,
        inputTokens,
        outputTokens,
        reasoningTokens,
        cachedTokens: nonNegative(tokens?.cache?.read),
        cacheCreationTokens: nonNegative(tokens?.cache?.write),
        totalTokens,
        // Latency excludes tool execution, like Claude Code and Codex.
        durationMs: step
          ? Math.max(0, now - step.startedAt - toolTimeWithin(intervals, step.startedAt, now))
          : null,
        ttftMs:
          step?.firstPartAt != null ? Math.max(0, Math.round(step.firstPartAt - step.startedAt)) : null,
        statusCode,
        errorName: failed ? (typeof error?.type === "string" ? error.type : "error") : null,
        timestampMs: now,
      },
      clientSource,
    )
    if (!(await emitOtlp(payload, endpoint, 10_000, headers))) release(`message:${messageId}`)
  }

  async function emitTool(event: V2Event, callId: string): Promise<void> {
    const tool = tools.get(callId)
    tools.delete(callId)
    if (!tool || !claim(`tool:${callId}`)) return
    const end = finiteNumber(event.created) ?? Date.now()
    const payload = buildToolEventPayload(
      {
        sessionId: tool.sessionId,
        messageId: tool.messageId,
        callId,
        toolName: tool.name,
        // Stamped at the tool's start, like the other sources' tool events.
        timestampMs: tool.calledAt ?? end,
        durationMs: tool.calledAt != null ? Math.max(0, end - tool.calledAt) : undefined,
      },
      clientSource,
    )
    if (!(await emitOtlp(payload, endpoint, 10_000, headers))) release(`tool:${callId}`)
  }

  return async (event: V2Event): Promise<void> => {
    const data = event.data ?? {}
    const created = finiteNumber(event.created) ?? Date.now()
    switch (event.type) {
      case "session.inbox.enqueued": {
        const item = data.item
        if (data.sessionID && item?.type === "user" && typeof item.payload?.text === "string") {
          promptLengths.set(data.sessionID, item.payload.text.length)
          trim(promptLengths)
        }
        return
      }
      case "session.step.started": {
        if (!data.assistantMessageID) return
        // A retried step reuses the message id; time the attempt that finishes.
        const provider =
          typeof data.model?.providerID === "string" ? data.model.providerID.trim().toLowerCase() : ""
        steps.set(data.assistantMessageID, {
          sessionId: data.sessionID,
          provider: provider || "unknown",
          model: data.model?.id || "unknown",
          startedAt: finiteNumber(data.started) ?? created,
          firstPartAt: null,
        })
        trim(steps)
        return
      }
      case "session.text.started":
      case "session.reasoning.started": {
        const step = steps.get(data.assistantMessageID)
        if (step && step.firstPartAt == null) step.firstPartAt = created
        return
      }
      case "session.tool.input.started": {
        if (!data.id || !data.name) return
        tools.set(data.id, {
          sessionId: data.sessionID,
          messageId: data.assistantMessageID,
          name: data.name,
          calledAt: null,
        })
        trim(tools)
        return
      }
      case "session.tool.called": {
        const tool = tools.get(data.id)
        if (tool) tool.calledAt = created
        return
      }
      case "session.tool.success":
        recordToolInterval(data.id, created)
        await emitTool(event, data.id)
        return
      case "session.tool.failed":
        // Like v1, only completed tools are recorded; their run time still
        // leaves the step latency.
        recordToolInterval(data.id, created)
        tools.delete(data.id)
        return
      case "session.step.ended":
        await finishStep(event, false)
        return
      case "session.step.failed":
        await finishStep(event, true)
        return
    }
  }
}

type V2Context = {
  options?: Record<string, any>
  event: { subscribe(options?: { signal?: AbortSignal }): AsyncIterable<V2Event> }
}

// Structurally what `Plugin.define` returns, so the package needs no
// dependency on `@opencode/plugin`.
export function createPluginV2(clientSource: string) {
  return {
    id: `${clientSource}-tokenage`,
    setup(ctx: V2Context) {
      // OpenCode 1.19+ also calls `setup` with a context that has no event
      // stream; `server` already tracks there.
      if (typeof ctx?.event?.subscribe !== "function") return
      const { endpoint, headers } = resolveOptions(ctx.options)
      if (!endpoint) return
      const handle = createV2Tracker(clientSource, endpoint, headers)
      const controller = new AbortController()
      void (async () => {
        try {
          for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
            try {
              await handle(event)
            } catch (err) {
              console.error("[tokenage] event handling failed:", err)
            }
          }
        } catch (err) {
          if (!controller.signal.aborted) console.error("[tokenage] event stream failed:", err)
        }
      })()
      return () => controller.abort()
    },
  }
}
