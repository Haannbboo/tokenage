import type { Plugin } from "@opencode-ai/plugin"
import { createPlugin } from "./shared/src/plugin.js"
import { createPluginV2 } from "./shared/src/v2.js"

// One default export serves both majors: OpenCode 1 (>=1.18.29) calls `server`
// and OpenCode 2 calls `setup` with the `id`.
const server: Plugin = createPlugin("opencode") as unknown as Plugin
export default { ...createPluginV2("opencode"), server }

// OpenCode releases before 1.18.29 load every function export instead of the
// default object's `server`.
export const TokenagePlugin: Plugin = server
