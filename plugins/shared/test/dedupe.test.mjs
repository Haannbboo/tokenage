import assert from "node:assert/strict"
import { test } from "node:test"
import { MAX_TRACKED_MESSAGES } from "../../opencode/dist/shared/src/prompt.js"

const first = await import("../../opencode/dist/shared/src/dedupe.js?directory=a")
const second = await import("../../opencode/dist/shared/src/dedupe.js?directory=b")

test("separately evaluated modules share message and tool claims and releases", () => {
  assert.notEqual(first.claim, second.claim)
  for (const key of ["message:shared-test", "tool:shared-test"]) {
    assert.equal(first.claim(key), true)
    assert.equal(second.claim(key), false)
    second.release(key) // A failed emission permits another module to retry.
    assert.equal(first.claim(key), true)
    assert.equal(second.claim(key), false)
    first.release(key)
  }
})

test("shared claims retain a bounded recent history", () => {
  for (let i = 0; i <= MAX_TRACKED_MESSAGES; i++) {
    assert.equal(first.claim(`retention:${i}`), true)
  }
  assert.equal(second.claim(`retention:${MAX_TRACKED_MESSAGES}`), false)
  assert.equal(second.claim("retention:0"), true)
  for (let i = 0; i <= MAX_TRACKED_MESSAGES; i++) first.release(`retention:${i}`)
})
