from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "opencode"


def _copy_clean_plugin_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    plugins_dir = workspace / "plugins"
    plugins_dir.mkdir(parents=True)

    def ignore_ignored_artifacts(directory: str, names: list[str]) -> set[str]:
        ignored = {"dist", "node_modules", "CLAUDE.local.md"} & set(names)
        if Path(directory).name == "src":
            ignored |= {"shared"} & set(names)
        return ignored

    shutil.copytree(
        REPO_ROOT / "plugins" / "opencode",
        plugins_dir / "opencode",
        ignore=ignore_ignored_artifacts,
    )
    shutil.copytree(
        REPO_ROOT / "plugins" / "shared",
        plugins_dir / "shared",
        ignore=lambda _dir, names: {"CLAUDE.local.md"} & set(names),
    )
    return plugins_dir / "opencode"


def _npm_install(plugin_dir: Path) -> None:
    """Install locked dependencies for the copied plugin workspace.

    A timeout is treated as a skip, not a failure: cold-cache or congested npm
    on slow CI runners can exceed the budget, and that is an environment
    problem, not a plugin regression. The test still runs when npm is healthy.
    """
    try:
        install = subprocess.run(
            ["npm", "ci"],
            cwd=plugin_dir,
            text=True,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("npm ci timed out; npm registry too slow on this runner")
    assert install.returncode == 0, install.stderr


@pytest.mark.slow
def test_opencode_plugin_emits_status_for_failed_assistant_messages(tmp_path):
    if not shutil.which("npm") or not shutil.which("node"):
        pytest.skip("node and npm are required for the OpenCode plugin runtime test")

    plugin_dir = _copy_clean_plugin_workspace(tmp_path)
    _npm_install(plugin_dir)
    shared_src = plugin_dir.parent / "shared" / "src"
    shared_src.joinpath("PRIVATE.local.md").write_text("private\n", encoding="utf-8")
    shared_src.joinpath("generated.js").write_text("export {}\n", encoding="utf-8")
    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=plugin_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    assert not plugin_dir.joinpath("src", "shared", "src", "PRIVATE.local.md").exists()
    assert not plugin_dir.joinpath("src", "shared", "src", "generated.js").exists()

    harness = textwrap.dedent(
        """
        const { default: plugin } = await import("./dist/index.js")

        const payloads = []
        globalThis.fetch = async (_url, options) => {
          payloads.push(JSON.parse(options.body))
          return { ok: true, status: 200 }
        }

        const hooks = await plugin.server({
          client: {
            session: {
              message: async () => undefined,
            },
          },
        }, { endpoint: "http://collector.example/v1/logs" })

        await hooks.event({
          event: {
            type: "message.updated",
            properties: {
              info: {
                id: "msg-api-error",
                sessionID: "ses-1",
                role: "assistant",
                time: { created: 1000, completed: 1250 },
                error: {
                  name: "APIError",
                  data: { statusCode: 429, isRetryable: true },
                },
                parentID: "user-1",
                modelID: "claude-sonnet",
                providerID: "anthropic",
              },
            },
          },
        })

        await hooks.event({
          event: {
            type: "message.updated",
            properties: {
              info: {
                id: "msg-unknown-error",
                sessionID: "ses-1",
                role: "assistant",
                time: { created: 2000, completed: 2200 },
                error: {
                  name: "UnknownError",
                  data: { message: "sanitized fixture" },
                },
                parentID: "user-2",
                modelID: "claude-sonnet",
                providerID: "anthropic",
              },
            },
          },
        })

        await hooks.event({
          event: {
            type: "message.updated",
            properties: {
              info: {
                id: "msg-token-edge",
                sessionID: "ses-1",
                role: "assistant",
                time: { created: 3000, completed: 3200 },
                tokens: {
                  input: -1,
                  output: 2,
                  reasoning: -3,
                  cache: { read: -4, write: 5 },
                },
                parentID: "user-3",
                modelID: "claude-sonnet",
                providerID: " Anthropic ",
              },
            },
          },
        })

        await hooks.event({
          event: {
            type: "message.updated",
            properties: {
              info: {
                id: "msg-unnamed-error",
                sessionID: "ses-1",
                role: "assistant",
                time: { created: 4000, completed: 4100 },
                error: {
                  data: { message: "sanitized fixture" },
                },
                parentID: "user-4",
                modelID: "claude-sonnet",
                providerID: "anthropic",
              },
            },
          },
        })

        await hooks.event({
          event: {
            type: "message.part.updated",
            properties: {
              part: {
                type: "tool",
                sessionID: "ses-1",
                messageID: "msg-tool",
                callID: "call-tool-1",
                tool: "bash",
                state: {
                  status: "completed",
                  time: { start: 5000, end: 5234 },
                },
              },
            },
          },
        })

        // msg-tool: call-tool-1 [5000,5234] overlaps call-tool-2 [5100,5300];
        // call-tool-3 [5500,5600] is disjoint. Union = 400 ms of tool time.
        for (const [callID, start, end] of [["call-tool-2", 5100, 5300], ["call-tool-3", 5500, 5600]]) {
          await hooks.event({
            event: {
              type: "message.part.updated",
              properties: {
                part: {
                  type: "tool",
                  sessionID: "ses-1",
                  messageID: "msg-tool",
                  callID,
                  tool: "read",
                  state: { status: "completed", time: { start, end } },
                },
              },
            },
          })
        }

        await hooks.event({
          event: {
            type: "message.updated",
            properties: {
              info: {
                id: "msg-tool",
                sessionID: "ses-1",
                role: "assistant",
                time: { created: 4900, completed: 6000 },
                tokens: { input: 1, output: 2 },
                parentID: "user-5",
                modelID: "claude-sonnet",
                providerID: "anthropic",
              },
            },
          },
        })

        const recordFor = (payload) => payload.resourceLogs[0].scopeLogs[0].logRecords[0]
        const attrsFor = (payload) =>
          Object.fromEntries(
            recordFor(payload).attributes.map(
              (attr) => [attr.key, Object.values(attr.value)[0]],
            ),
          )

        console.log(JSON.stringify({
          records: payloads.map(recordFor),
          attrs: payloads.map(attrsFor),
        }))
        """
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        cwd=plugin_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    output = json.loads(result.stdout)
    records = output["records"]
    payloads = output["attrs"]
    assert records[0]["severityNumber"] == 17
    assert payloads[0]["event.name"] == "opencode.message_completed"
    assert payloads[0]["http.response.status_code"] == 429
    assert payloads[0]["status_code"] == 429
    assert payloads[0]["error.type"] == "APIError"
    assert "input_token_count" not in payloads[0]
    assert "output_token_count" not in payloads[0]

    assert records[1]["severityNumber"] == 17
    assert payloads[1]["event.name"] == "opencode.message_completed"
    assert payloads[1]["http.response.status_code"] == 500
    assert payloads[1]["status_code"] == 500
    assert payloads[1]["error.type"] == "UnknownError"

    assert records[2]["severityNumber"] == 9
    assert payloads[2]["provider"] == "anthropic"
    assert payloads[2]["output_token_count"] == 2
    assert payloads[2]["cache_creation_token_count"] == 5
    assert payloads[2]["total_token_count"] == 2
    assert "input_token_count" not in payloads[2]
    assert "reasoning_token_count" not in payloads[2]
    assert "cached_token_count" not in payloads[2]

    assert records[3]["severityNumber"] == 17
    assert payloads[3]["http.response.status_code"] == 500
    assert "error.type" not in payloads[3]

    assert records[4]["severityNumber"] == 9
    assert records[4]["timeUnixNano"] == "5000000000"
    assert payloads[4]["event.name"] == "opencode.tool_decision"
    assert payloads[4]["tool_name"] == "bash"
    assert payloads[4]["call_id"] == "call-tool-1"
    assert payloads[4]["duration_ms"] == "234"

    # Latency excludes the union of the message's tool intervals.
    assert payloads[7]["event.name"] == "opencode.message_completed"
    assert payloads[7]["duration_ms"] == (6000 - 4900) - ((5300 - 5000) + (5600 - 5500))


@pytest.mark.slow
def test_opencode_plugin_otlp_emit_uses_abort_signal(tmp_path):
    if not shutil.which("npm") or not shutil.which("node"):
        pytest.skip("node and npm are required for the OpenCode plugin runtime test")

    plugin_dir = _copy_clean_plugin_workspace(tmp_path)
    _npm_install(plugin_dir)
    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=plugin_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    harness = textwrap.dedent(
        """
        const { emitOtlp } = await import("./dist/shared/src/otlp.js")

        globalThis.fetch = async (_url, options) => {
          return new Promise((resolve, reject) => {
            options.signal?.addEventListener("abort", () => {
              reject(new DOMException("timed out", "AbortError"))
            })
            setTimeout(() => resolve({ ok: true, status: 200 }), 20)
          })
        }

        const emitted = await emitOtlp({}, "http://collector.example/v1/logs", 1)
        console.log(JSON.stringify({ emitted }))
        """
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        cwd=plugin_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"emitted": False}


@pytest.mark.slow
def test_opencode_plugin_v2_tracks_usage_tools_and_failures(tmp_path):
    if not shutil.which("npm") or not shutil.which("node"):
        pytest.skip("node and npm are required for the OpenCode plugin runtime test")

    plugin_dir = _copy_clean_plugin_workspace(tmp_path)
    _npm_install(plugin_dir)
    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=plugin_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    # Event shapes are those OpenCode 2.0 publishes on ctx.event.subscribe().
    harness = textwrap.dedent(
        """
        const { default: plugin, TokenagePlugin } = await import("./dist/index.js")

        const payloads = []
        globalThis.fetch = async (_url, options) => {
          payloads.push({ headers: options.headers, body: JSON.parse(options.body) })
          return { ok: true, status: 200 }
        }

        const sid = "ses_1"
        const ev = (type, created, data) => ({ id: "evt_" + created, type, created, data: { sessionID: sid, ...data } })
        const model = { id: "mock-1", providerID: " Mock ", variant: "default" }
        const events = [
          ev("session.inbox.enqueued", 900, { inboxID: "i1", item: { type: "user", payload: { text: "hello there", files: [] } } }),
          ev("session.step.started", 1000, { assistantMessageID: "msg_a", agent: "build", model, started: 1000 }),
          ev("session.text.started", 1400, { assistantMessageID: "msg_a", ordinal: 0 }),
          ev("session.step.ended", 1900, { assistantMessageID: "msg_a", finish: "stop", cost: 0,
            tokens: { input: 600, output: 5, reasoning: 2, cache: { read: 600, write: 3 } } }),
          ev("session.step.ended", 1900, { assistantMessageID: "msg_a", finish: "stop", cost: 0, tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } }),
          ev("session.step.started", 2000, { assistantMessageID: "msg_b", agent: "build", model, started: 2000 }),
          ev("session.tool.input.started", 2100, { assistantMessageID: "msg_b", id: "call_1", name: "read" }),
          ev("session.tool.called", 2200, { assistantMessageID: "msg_b", id: "call_1", input: {}, executed: false }),
          ev("session.tool.success", 2250, { assistantMessageID: "msg_b", id: "call_1", content: [], executed: false }),
          ev("session.tool.input.started", 2260, { assistantMessageID: "msg_b", id: "call_2", name: "bash" }),
          ev("session.tool.called", 2270, { assistantMessageID: "msg_b", id: "call_2", input: {}, executed: false }),
          ev("session.tool.failed", 2280, { assistantMessageID: "msg_b", id: "call_2", error: { type: "tool.execution", message: "x" } }),
          ev("session.step.ended", 2300, { assistantMessageID: "msg_b", finish: "tool-calls", cost: 0,
            tokens: { input: 10, output: 20, reasoning: 0, cache: { read: 0, write: 0 } } }),
          ev("session.step.started", 3000, { assistantMessageID: "msg_c", agent: "build", model, started: 3000 }),
          ev("session.retry.scheduled", 3010, { assistantMessageID: "msg_c", attempt: 2, at: 3100, error: { type: "provider.rate-limit", message: "x", status: 429 } }),
          ev("session.step.failed", 3500, { assistantMessageID: "msg_c", error: { type: "provider.rate-limit", message: "x", status: 429 } }),
          ev("session.step.started", 4000, { assistantMessageID: "msg_d", agent: "build", model, started: 4000 }),
          ev("session.step.failed", 4100, { assistantMessageID: "msg_d", error: { type: "provider.unknown", message: "x" } }),
        ]

        const stream = (async function* () { for (const e of events) yield e })()
        let cleanup
        // A context without an event stream (OpenCode 1.19 also calls setup)
        // must not track: `server` already does.
        const noStream = plugin.setup({ options: {} })
        cleanup = plugin.setup({
          options: { endpoint: "http://collector.example/v1/logs", token: "tk" },
          event: { subscribe: () => stream },
        })
        await new Promise((resolve) => setTimeout(resolve, 200))
        cleanup()

        const attrsFor = (payload) =>
          Object.fromEntries(
            payload.body.resourceLogs[0].scopeLogs[0].logRecords[0].attributes.map(
              (attr) => [attr.key, Object.values(attr.value)[0]],
            ),
          )
        console.log(JSON.stringify({
          id: plugin.id,
          noStream: noStream ?? null,
          serverIsFunction: typeof plugin.server,
          legacyExport: typeof TokenagePlugin,
          headers: payloads.map((p) => p.headers["x-tokenage-token"]),
          times: payloads.map((p) => p.body.resourceLogs[0].scopeLogs[0].logRecords[0].timeUnixNano),
          attrs: payloads.map(attrsFor),
        }))
        """
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        cwd=plugin_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    output = json.loads(result.stdout)
    assert output["id"] == "opencode-tokenage"
    assert output["noStream"] is None
    assert output["serverIsFunction"] == "function"
    assert output["legacyExport"] == "function"
    attrs = output["attrs"]
    # msg_a (usage; duplicate step.ended ignored), read tool, msg_b usage,
    # two failures. The failed bash tool is not recorded, like OpenCode 1.
    assert [a.get("message.id") for a in attrs] == [
        "msg_a",
        "msg_b",
        "msg_b",
        "msg_c",
        "msg_d",
    ]
    assert set(output["headers"]) == {"tk"}

    usage = attrs[0]
    assert usage["event.name"] == "opencode.message_completed"
    assert usage["session.id"] == "ses_1"
    assert usage["provider"] == "mock"
    assert usage["model"] == "mock-1"
    assert usage["input_token_count"] == 600
    assert usage["output_token_count"] == 5
    assert usage["reasoning_token_count"] == 2
    assert usage["total_token_count"] == 607
    assert usage["cached_token_count"] == 600
    assert usage["cache_creation_token_count"] == 3
    assert usage["prompt_length"] == len("hello there")
    assert usage["duration_ms"] == 900
    assert usage["ttft_ms"] == 400
    assert "status_code" not in usage
    assert output["times"][0] == "1900000000"

    tool = attrs[1]
    assert tool["event.name"] == "opencode.tool_decision"
    assert tool["tool_name"] == "read"
    assert tool["call_id"] == "call_1"
    assert tool["duration_ms"] == "50"
    assert output["times"][1] == "2200000000"

    assert attrs[2]["event.name"] == "opencode.message_completed"
    # Step 2000-2300 minus the read [2200, 2250] and failed bash [2270, 2280] runs.
    assert attrs[2]["duration_ms"] == 240
    assert "ttft_ms" not in attrs[2]

    assert attrs[3]["status_code"] == 429
    assert attrs[3]["error.type"] == "provider.rate-limit"
    assert attrs[3]["duration_ms"] == 500
    assert attrs[4]["status_code"] == 500
    assert attrs[4]["error.type"] == "provider.unknown"
