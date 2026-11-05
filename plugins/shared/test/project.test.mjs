import assert from "node:assert/strict"
import { after, test } from "node:test"
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile, stat, rename, chmod } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createPlugin } from "../../opencode/dist/shared/src/plugin.js"
import { createPluginV2 } from "../../opencode/dist/shared/src/v2.js"
import { sanitizeProject, projectName, reportProject } from "../../opencode/dist/shared/src/project.js"

const home = await mkdtemp(join(tmpdir(), "tokenage-project-test-"))
const original = process.env.TOKENAGE_HOME
process.env.TOKENAGE_HOME = home
await writeFile(join(home, "credentials.json"), JSON.stringify({ server_url: "https://collector.example", ingest_token: "fake" }))
after(async () => {
  if (original === undefined) delete process.env.TOKENAGE_HOME
  else process.env.TOKENAGE_HOME = original
  await rm(home, { recursive: true, force: true })
})
const directory = join(home, "orchard")
await mkdir(directory)
async function waitReports(count) {
  for (let i = 0; i < 100; i++) {
    const rows = await reports().catch(() => [])
    if (rows.length >= count) return rows
    await new Promise(resolve => setTimeout(resolve, 10))
  }
  assert.fail("project reports did not finish")
}
async function reports() {
  const names = (await readdir(join(home, "spool"))).filter(n => n.endsWith(".json"))
  return Promise.all(names.map(async name => {
    assert.equal((await stat(join(home, "spool", name))).mode & 0o777, 0o600)
    const raw = await readFile(join(home, "spool", name), "utf8")
    assert.equal(raw.includes(home), false)
    return JSON.parse(raw)
  }))
}

test("project sanitization agrees with protocol rules and folder fallback", async () => {
  assert.equal(sanitizeProject(" Cafe\u0301 "), "Café")
  for (const name of ["", ".", "..", "a/b", "a\\b", "a\nb", "a\u202eb", "a\u2028b", "x".repeat(101)]) assert.equal(sanitizeProject(name), undefined)
  assert.equal(await projectName(directory), "orchard")
})

for (const agent of ["opencode", "kilo"]) {
  test(`${agent} v1 spools session-created and resumed IDs using session directory`, async () => {
    const plugin = await createPlugin(agent)({ directory: "/wrong/global" }, { endpoint: "https://collector.example/v1/logs" })
    const id = `${agent}-created`
    const event = { type: "session.created", properties: { info: { id, directory } } }
    await plugin.event({ event })
    await plugin.event({ event })
    const resumed = await createPlugin(agent)({ directory }, { endpoint: "https://collector.example/v1/logs" })
    await resumed.event({ event: { type: "message.updated", properties: { info: { role: "user", sessionID: `${agent}-resumed` } } } })
    const rows = await waitReports(agent === "opencode" ? 2 : 4)
    assert.equal(rows.filter(r => r.session_id === id).length, 1)
    assert.deepEqual(rows.find(r => r.session_id === id).project, "orchard")
    assert.equal(rows.find(r => r.session_id === `${agent}-resumed`).agent, agent)
  })
}

test("v2 uses each session's location and never the plugin's global directory", async () => {
  const grove = join(home, "grove")
  await mkdir(grove)
  const events = [
    { type: "session.created", location: { directory }, data: { sessionID: "v2-created", info: { directory: "/wrong/info" } } },
    { type: "session.inbox.enqueued", data: { sessionID: "v2-resumed" } },
  ]
  let finish
  const done = new Promise(resolve => { finish = resolve })
  const calls = []
  const close = createPluginV2("opencode").setup({
    options: { endpoint: "https://collector.example/v1/logs" },
    location: { directory: "/wrong/global" },
    session: { get: async ({ sessionID }) => { calls.push(sessionID); return { location: { directory: grove } } } },
    event: { subscribe: async function* () { yield* events; finish() } },
  })
  await done
  close()
  const rows = await waitReports(6)
  assert.equal(rows.find(r => r.session_id === "v2-created").project, "orchard")
  assert.equal(rows.find(r => r.session_id === "v2-resumed").project, "grove")
  assert.deepEqual(calls, ["v2-resumed"])
})


test("missing-directory reports deterministically skip the session", async () => {
  const before = (await reports()).length
  assert.equal(await reportProject("opencode", "failed-once", undefined), true)
  assert.equal(await reportProject("opencode", "failed-once", directory), true)
  assert.equal((await reports()).length, before)
})

test("v2 pending location lookup runs once and does not block event handling", async () => {
  let calls = 0
  let finished
  const done = new Promise(resolve => { finished = resolve })
  const close = createPluginV2("opencode").setup({
    options: { endpoint: "https://collector.example/v1/logs" },
    session: { get: () => { calls++; return new Promise(() => {}) } },
    event: { subscribe: async function* () {
      for (let i = 0; i < 10; i++) yield { type: "session.inbox.enqueued", data: { sessionID: "missing-location" } }
      finished()
    } },
  })
  await Promise.race([done, new Promise((_, reject) => setTimeout(() => reject(new Error("events blocked on project")), 1000))])
  close()
  assert.equal(calls, 1)
})


const pause = () => new Promise(resolve => setTimeout(resolve, 35))

test("v2 info directory avoids lookup when event location is absent", async () => {
  let calls = 0
  const before = (await reports()).length
  const close = createPluginV2("opencode").setup({
    options: { endpoint: "https://collector.example/v1/logs" },
    session: { get: async () => { calls++; throw new Error("unexpected lookup") } },
    event: { subscribe: async function* () { yield { type: "session.created", data: { info: { id: "v2-info", directory } } } } },
  })
  const rows = await waitReports(before + 1)
  close()
  assert.equal(rows.find(r => r.session_id === "v2-info").project, "orchard")
  assert.equal(calls, 0)
})

test("v2 retries failed lookups up to three times without blocking events", async () => {
  let calls = 0
  let finished
  const done = new Promise(resolve => { finished = resolve })
  const close = createPluginV2("opencode").setup({
    options: { endpoint: "https://collector.example/v1/logs" },
    session: { get: async () => { calls++; throw new Error("offline") } },
    event: { subscribe: async function* () {
      for (let i = 0; i < 6; i++) {
        yield { type: "session.inbox.enqueued", data: { sessionID: "v2-retry-limit" } }
        await pause()
      }
      finished()
    } },
  })
  await done
  close()
  assert.equal(calls, 3)
})

test("v2 recovers from lookup failure on a later event", async () => {
  let calls = 0
  const before = (await reports()).length
  const close = createPluginV2("opencode").setup({
    options: { endpoint: "https://collector.example/v1/logs" },
    session: { get: async () => { if (++calls === 1) throw new Error("offline"); return { directory } } },
    event: { subscribe: async function* () {
      for (let i = 0; i < 4; i++) {
        yield { type: "session.inbox.enqueued", data: { sessionID: "v2-recovers" } }
        await pause()
      }
    } },
  })
  const rows = await waitReports(before + 1)
  close()
  assert.equal(rows.find(r => r.session_id === "v2-recovers").project, "orchard")
  assert.equal(calls, 2)
})

for (const version of [1, 2]) {
  test(`v${version} retries signed-out reports but stops after three attempts`, async () => {
    const before = (await reports()).length
    const credentials = join(home, "credentials.json")
    await rename(credentials, credentials + ".saved")
    const emit = async (id, count) => {
      if (version === 1) {
        const plugin = await createPlugin("opencode")({ directory }, { endpoint: "https://collector.example/v1/logs" })
        for (let i = 0; i < count; i++) {
          await plugin.event({ event: { type: "session.updated", properties: { info: { id, directory } } } })
          await pause()
        }
      } else {
        let finished
        const done = new Promise(resolve => { finished = resolve })
        const close = createPluginV2("opencode").setup({
          options: { endpoint: "https://collector.example/v1/logs" },
          event: { subscribe: async function* () {
            for (let i = 0; i < count; i++) {
              yield { type: "session.updated", location: { directory }, data: { sessionID: id } }
              await pause()
            }
            finished()
          } },
        })
        await done
        close()
      }
    }
    try {
      await emit(`v${version}-exhausted`, 5)
      await emit(`v${version}-signedout`, 1)
    } finally { await rename(credentials + ".saved", credentials) }
    await emit(`v${version}-exhausted`, 2)
    await emit(`v${version}-signedout`, 2)
    const rows = await waitReports(before + 1)
    assert.equal(rows.filter(r => r.session_id === `v${version}-exhausted`).length, 0)
    assert.equal(rows.filter(r => r.session_id === `v${version}-signedout`).length, 1)
  })
}

test("report retries a spool write failure and counts only JSON files", async () => {
  const spool = join(home, "spool")
  await rename(spool, spool + ".saved")
  await writeFile(spool, "unavailable")
  try { assert.equal(await reportProject("opencode", "spool-retry", directory), false) }
  finally { await rm(spool); await rename(spool + ".saved", spool) }
  await Promise.all(Array.from({ length: 1000 }, (_, i) => writeFile(join(spool, `.${i}.tmp`), "")))
  const before = (await reports()).length
  assert.equal(await reportProject("opencode", "spool-retry", directory), true)
  assert.equal((await reports()).length, before + 1)
})

test("git probe removes inherited GIT variables and truncates by code point", async () => {
  const bin = join(home, "bin")
  await mkdir(bin)
  const probe = join(bin, "git")
  await writeFile(probe, `#!/usr/bin/env node
const unexpected = Object.keys(process.env).filter(key => key.startsWith("GIT_") && key !== "GIT_OPTIONAL_LOCKS")
if (unexpected.length || process.env.GIT_OPTIONAL_LOCKS !== "0") process.exit(1)
console.log("false\\n/tmp/" + "😀".repeat(101) + "/.git\\n/tmp/top")
`)
  await chmod(probe, 0o700)
  const oldPath = process.env.PATH
  const oldGit = process.env.GIT_CEILING_DIRECTORIES
  process.env.PATH = bin + ":" + oldPath
  process.env.GIT_CEILING_DIRECTORIES = home
  try { assert.equal(await projectName(directory), "😀".repeat(100)) }
  finally {
    process.env.PATH = oldPath
    if (oldGit === undefined) delete process.env.GIT_CEILING_DIRECTORIES
    else process.env.GIT_CEILING_DIRECTORIES = oldGit
  }
})
