// Run: npm install --prefix plugins/opencode && npm run build --prefix plugins/opencode && node --test plugins/shared/test/plugin.test.mjs
import assert from "node:assert/strict"
import { after, afterEach, beforeEach, test } from "node:test"
import { resolveOptions } from "../../opencode/dist/shared/src/plugin.js"

const name = "TOKENAGE_OTLP_HEADERS"
const original = process.env[name]
const originalWarn = console.warn
let warnings

beforeEach(() => {
  delete process.env[name]
  warnings = []
  console.warn = (...args) => warnings.push(args.join(" "))
})
afterEach(() => { console.warn = originalWarn })
after(() => {
  if (original === undefined) delete process.env[name]
  else process.env[name] = original
})

test("token only", () => {
  assert.deepEqual(resolveOptions({ token: "secret" }).headers, { "x-tokenage-token": "secret" })
})

test("environment headers are trimmed, decoded and work without a token", () => {
  process.env[name] = " X-Route = hello%20world%2C%3D, another=a=b "
  assert.deepEqual(resolveOptions().headers, { "x-route": "hello world,=", another: "a=b" })
  assert.deepEqual(warnings, [])
})

test("invalid headers are skipped with warnings that do not include values", () => {
  process.env[name] = "good=keep,cr=secret%0dvalue,lf=secret%0avalue,nul=secret%00value,bad name=secret,bare=secret%,other=keep"
  assert.deepEqual(resolveOptions().headers, { good: "keep", other: "keep" })
  assert.equal(warnings.length, 5)
  for (const key of ["cr", "lf", "nul", "bad name", "bare"]) {
    assert.ok(warnings.some((warning) => warning.includes(JSON.stringify(key))))
  }
  assert.ok(warnings.every((warning) => !warning.includes("secret")))
})

test("environment cannot set plugin-controlled headers", () => {
  process.env[name] = "Content-Type=text/plain,X-TOKENAGE-TOKEN=env,x-route=keep"
  assert.deepEqual(resolveOptions().headers, { "x-route": "keep" })
  assert.deepEqual(resolveOptions({ token: "secret" }).headers, { "x-route": "keep", "x-tokenage-token": "secret" })
})

test("no headers returns undefined", () => {
  assert.equal(resolveOptions().headers, undefined)
})
