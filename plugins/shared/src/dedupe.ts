import { MAX_TRACKED_MESSAGES } from "./prompt.js"

// OpenCode can evaluate a separate plugin module for every open directory in
// one service process. Share claims across those modules and server/setup.
const claimKey = Symbol.for("tokenage.plugin.claimed")
const shared = globalThis as typeof globalThis & { [claimKey]?: Set<string> }
const claimed = shared[claimKey] ??= new Set<string>()

export function claim(key: string): boolean {
  if (claimed.has(key)) return false
  claimed.add(key)
  if (claimed.size > MAX_TRACKED_MESSAGES) {
    const entries = Array.from(claimed)
    for (let i = 0; i < entries.length / 2; i++) claimed.delete(entries[i])
  }
  return true
}

export function release(key: string): void {
  claimed.delete(key)
}
