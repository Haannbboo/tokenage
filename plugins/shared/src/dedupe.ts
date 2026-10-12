import { MAX_TRACKED_MESSAGES } from "./prompt.js"

// OpenCode 1.19+ can load one entry through both `server` and `setup`. Claim
// each message and tool call per process so a record is emitted only once.
const claimed = new Set<string>()

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
