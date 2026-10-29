import assert from "node:assert/strict"
import { test } from "node:test"
import { createPluginV2 } from "../../opencode/dist/shared/src/v2.js"
import { release } from "../../opencode/dist/shared/src/dedupe.js"

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

for (const incompleteFirst of [true, false]) {
  test(`complete v2 tracker wins when ${incompleteFirst ? "incomplete" : "complete"} arrives first`, async () => {
    const messageId = `v2-order-${incompleteFirst}`
    const sent = []
    const originalFetch = globalThis.fetch
    globalThis.fetch = async (_url, options) => {
      sent.push(JSON.parse(options.body))
      return { ok: true }
    }
    const start = { type: "session.step.started", created: 1000, data: { sessionID: "s", assistantMessageID: messageId, model: { providerID: "provider", id: "model" } } }
    const end = { type: "session.step.ended", created: 2000, data: { sessionID: "s", assistantMessageID: messageId } }
    const subscribe = (complete, delay) => async function* () {
      if (complete) yield start
      await sleep(delay)
      yield end
    }
    try {
      const plugin = createPluginV2("opencode")
      plugin.setup({ options: { endpoint: "http://collector" }, event: { subscribe: subscribe(true, incompleteFirst ? 50 : 0) } })
      plugin.setup({ options: { endpoint: "http://collector" }, event: { subscribe: subscribe(false, incompleteFirst ? 0 : 50) } })
      await sleep(1200)
      assert.equal(sent.length, 1)
      const attributes = sent[0].resourceLogs[0].scopeLogs[0].logRecords[0].attributes
      assert.equal(attributes.find((a) => a.key === "model").value.stringValue, "model")
      assert.equal(attributes.find((a) => a.key === "duration_ms").value.intValue, 1000)
    } finally {
      globalThis.fetch = originalFetch
      release(`message:${messageId}`)
    }
  })
}

test("a lone incomplete v2 tracker still emits once", async () => {
  const messageId = "v2-incomplete-alone"
  const sent = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_url, options) => {
    sent.push(JSON.parse(options.body))
    return { ok: true }
  }
  const end = { type: "session.step.ended", created: 2000, data: { sessionID: "s", assistantMessageID: messageId } }
  try {
    createPluginV2("opencode").setup({ options: { endpoint: "http://collector" }, event: { subscribe: async function* () { yield end; yield end } } })
    await sleep(2200)
    assert.equal(sent.length, 1)
    const attributes = sent[0].resourceLogs[0].scopeLogs[0].logRecords[0].attributes
    assert.equal(attributes.find((a) => a.key === "model").value.stringValue, "unknown")
  } finally {
    globalThis.fetch = originalFetch
    release(`message:${messageId}`)
  }
})
