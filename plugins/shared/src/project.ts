import { execFile } from "node:child_process"
import { createHash, randomUUID } from "node:crypto"
import { chmod, lstat, mkdir, open, readFile, readdir, rename, stat, unlink } from "node:fs/promises"
import { homedir } from "node:os"
import { basename, dirname, isAbsolute, join } from "node:path"
import { MAX_TRACKED_MESSAGES } from "./prompt.js"

export function sanitizeProject(value: string): string | undefined {
  const name = value.trim().normalize("NFC")
  if (!name || name === "." || name === ".." || [...name].length > 100 || /[/\\\p{C}\p{Zl}\p{Zp}]/u.test(name)) return
  return name
}

export async function projectName(directory: string): Promise<string | undefined> {
  if (!isAbsolute(directory)) return
  let name = basename(directory)
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")))
  env.GIT_OPTIONAL_LOCKS = "0"
  // --path-format=absolute needs Git 2.31; older Git falls back to the folder name.
  try {
    const output = await new Promise<string>((resolve, reject) => {
      execFile("git", ["rev-parse", "--is-bare-repository", "--path-format=absolute", "--git-common-dir", "--show-toplevel"],
        { cwd: directory, env, timeout: 650, maxBuffer: 16384 }, (error, stdout) => error ? reject(error) : resolve(stdout))
    })
    const lines = output.replace(/\r?\n$/, "").split(/\r?\n/)
    if (lines.length === 3 && lines[0] === "false" && isAbsolute(lines[1]) && isAbsolute(lines[2])) {
      name = basename(lines[1]) === ".git" ? basename(dirname(lines[1])) : basename(lines[2])
    }
  } catch { /* Local Git is optional; never log directories or hook input. */ }
  return sanitizeProject([...name].slice(0, 100).join(""))
}

type ProjectAttempt = { attempts: number; inFlight: boolean; done: boolean }
const projectKey = Symbol.for("tokenage.plugin.projects")
const shared = globalThis as typeof globalThis & { [projectKey]?: Map<string, ProjectAttempt> }
const projects = shared[projectKey] ??= new Map<string, ProjectAttempt>()

export async function reportProject(agent: string, sessionID: unknown, directory: unknown): Promise<boolean> {
  if (typeof sessionID !== "string" || sessionID.length > 256 || !/^[A-Za-z0-9_.:-]+$/.test(sessionID)) return true
  const key = `project:${agent}:${sessionID}`
  const state = projects.get(key) ?? { attempts: 0, inFlight: false, done: false }
  if (state.done) return true
  if (state.inFlight || state.attempts >= 3) return false
  state.attempts++
  state.inFlight = true
  projects.set(key, state)
  if (projects.size > MAX_TRACKED_MESSAGES) projects.delete(projects.keys().next().value!)
  let temporary: string | undefined
  try {
    if (typeof directory !== "string" || !isAbsolute(directory)) return state.done = true
    const home = process.env.TOKENAGE_HOME || join(homedir(), ".tokenage")
    const credentials = JSON.parse(await readFile(join(home, "credentials.json"), "utf8"))
    if (typeof credentials.server_url !== "string" || typeof credentials.ingest_token !== "string" || !credentials.server_url || !credentials.ingest_token) return false
    if (!(await stat(directory)).isDirectory()) return state.done = true
    const project = await projectName(directory)
    if (!project) return state.done = true
    const binding = createHash("sha256").update(`${credentials.server_url}\n${credentials.ingest_token}`).digest("hex")
    const spool = join(home, "spool")
    await mkdir(spool, { recursive: true, mode: 0o700 })
    if ((await lstat(spool)).isSymbolicLink()) return false
    await chmod(spool, 0o700)
    if ((await readdir(spool)).filter(name => name.endsWith(".json")).length >= 1000) return false
    const name = `${randomUUID()}.${binding}`
    temporary = join(spool, `.${name}.tmp`)
    const file = await open(temporary, "wx", 0o600)
    try {
      await file.writeFile(JSON.stringify({ schema_version: 1, agent, session_id: sessionID, project, ts: Date.now() * 1000 }))
    } finally { await file.close() }
    await rename(temporary, join(spool, `${name}.json`))
    return state.done = true
  } catch { return false /* Fail open; metadata is never printed. */ }
  finally {
    state.inFlight = false
    if (temporary) await unlink(temporary).catch(() => {})
  }
}
