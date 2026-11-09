import assert from "node:assert/strict"
import { test } from "node:test"
import { buildOtlpPayload } from "../../opencode/dist/shared/src/otlp.js"
import { createPlugin } from "../../opencode/dist/shared/src/plugin.js"
import { createPluginV2 } from "../../opencode/dist/shared/src/v2.js"
import { release } from "../../opencode/dist/shared/src/dedupe.js"

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const attributesOf = (payload) => payload.resourceLogs[0].scopeLogs[0].logRecords[0].attributes
const variantOf = (payload) => attributesOf(payload).find((a) => a.key === "variant")?.value.stringValue

const base = {
  sessionId: "s", messageId: "m", model: "model", provider: "provider", promptLength: null,
  inputTokens: 1, outputTokens: 1, reasoningTokens: null, cachedTokens: null, cacheCreationTokens: null,
  totalTokens: 2, durationMs: null, ttftMs: null, statusCode: null, errorName: null, timestampMs: 1,
}

test("variant is sent trimmed and capped, and omitted when missing", () => {
  assert.equal(variantOf(buildOtlpPayload({ ...base, variant: " max " }, "opencode")), "max")
  assert.equal(variantOf(buildOtlpPayload({ ...base, variant: "x".repeat(500) }, "opencode")).length, 64)
  for (const variant of [undefined, null, "", "  ", 3, { name: "high" }]) {
    assert.equal(variantOf(buildOtlpPayload({ ...base, variant }, "opencode")), undefined)
  }
})

async function capture(run) {
  const sent = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_url, options) => {
    const body = JSON.parse(options.body)
    if (body.resourceLogs) sent.push(body)
    return { ok: true }
  }
  try {
    await run()
  } finally {
    globalThis.fetch = originalFetch
  }
  return sent
}

for (const variant of ["high", undefined]) {
  test(`legacy assistant message sends variant ${variant}`, async () => {
    const id = `legacy-variant-${variant}`
    const sent = await capture(async () => {
      const hooks = await createPlugin("opencode")({}, { endpoint: "http://collector" })
      await hooks.event({ event: { type: "message.updated", properties: { info: {
        id, role: "assistant", sessionID: "s", parentID: "p", providerID: "p", modelID: "m", variant,
        time: { created: 1000, completed: 2000 }, tokens: { input: 1, output: 1 },
      } } } })
    })
    release(`message:${id}`)
    assert.equal(sent.length, 1)
    assert.equal(variantOf(sent[0]), variant)
  })
}

test("v2 step sends the variant from session.step.started", async () => {
  const id = "v2-variant"
  const sent = await capture(async () => {
    createPluginV2("opencode").setup({ options: { endpoint: "http://collector" }, event: { subscribe: async function* () {
      yield { type: "session.step.started", created: 1000, data: { sessionID: "s", assistantMessageID: id, model: { providerID: "p", id: "m", variant: "default" } } }
      yield { type: "session.step.ended", created: 2000, data: { sessionID: "s", assistantMessageID: id, tokens: { input: 1, output: 1 } } }
    } } })
    await sleep(100)
  })
  release(`message:${id}`)
  assert.equal(sent.length, 1)
  assert.equal(variantOf(sent[0]), "default")
})
