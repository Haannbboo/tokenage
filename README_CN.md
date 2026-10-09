[English](README.md) | [中文](README_CN.md)

# tokenage

**面向命令行 LLM Agent 的本地优先可观测性工具。**

`tokenage` 用来查看你的 coding agents 到底在干什么：请求记录、token 使用量、费用估算、延迟、TTFT、模型、来源和 session ID。支持 **Claude Code**、**Codex**、**OpenCode**、**Kilo Code**，也支持 OpenAI/Anthropic 兼容流量。

它适合那些本地同时跑多个 LLM Agent、又想在一个地方回答这些问题的人：

- 哪个 Agent/模型花钱最多？
- 我刚刚那条命令到底有没有被追踪到？
- 这次 coding session 花了多少钱？
- 哪些请求慢、走了流式、命中了缓存、用了 reasoning？

默认是本地部署：配置在 `~/.tokenage/config.yaml`，使用量数据默认存在 SQLite `~/.tokenage/usage.db`，服务绑定在本机 loopback 端口。

## 它能做什么

- **追踪常见 coding agents**：通过本地 OTLP telemetry 追踪 Claude Code、Codex、OpenCode 和 Kilo Code。
- **追踪 OpenAI/Anthropic 兼容客户端**：通过运行在客户端服务里的可选本地 proxy 转发并记录请求。
- **提供 Dashboard**：查看使用量、费用、延迟、模型、来源、请求日志、setup health 和 first-event onboarding。
- **输出命令级摘要**：用 `tokenage` 跑 agent，结束后打印这次运行的使用量。
- **保持可检查**：纯 YAML 配置，默认 SQLite，日志在 `logs/`，不依赖 hosted backend。
- **支持 SQL 数据库**：默认本地 SQLite，也可以通过 SQLAlchemy `db.url` 指向 PostgreSQL/MySQL。

## 采集方式

`tokenage` 有两条互补的数据采集路径：

```text
Claude Code / Codex / OpenCode / Kilo Code
        │
        │ OTLP telemetry
        ▼
tokenage OTLP collector ──► database ──► dashboard / API / summaries
```

```text
OpenAI-compatible or Anthropic-compatible client
        │
        │ HTTP
        ▼
tokenage client proxy (127.0.0.1) ──► upstream provider
        │
        │ usage report（只含 metadata）
        ▼
tokenage server ──► database
```

Agent telemetry 更适合拿到 session、tool/reasoning metadata 等 agent-specific 字段。Proxy 更适合支持自定义 base URL 的客户端；它运行在你的机器上，provider key 不离开本机，服务端只收到 token 数和耗时。

## 快速开始

### 前置条件

- macOS 或 Linux shell 环境
- Python 3.13，或者可用的 `uv` 让安装脚本创建环境
- Node.js 18+，用于构建和提供 Dashboard
- 可选：本地已安装 `claude`、`codex`、`opencode` 或 `kilo`

### 1. 安装

按这台机器的需要选择组件，不带参数时两个都装：

```bash
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash                # server + client
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash -s -- --server # 只装 server
curl -fsSL https://YOUR_SERVER/install.sh | sh                                                         # 只装 client，连接该 server
```

Server 是 `~/.tokenage/src` 里的 git clone。Client 是 `~/.tokenage/versions` 下独立的
snapshot 和 virtualenv，即使与 server 在同一台机器上也一样，两者只通过 HTTP 通信。
两个都装就是先装 server，再装指向 `http://127.0.0.1:<api_port>` 的 client。
`~/.local/bin` 里的 `tokenage` launcher 是共享的。`tokenage update` 更新 client，
`tokenage server update` 更新 server。

在 checkout 里，`bash src/scripts/bootstrap.sh` 只是 server 安装：

1. 把 Python 依赖安装到 `.venv`
2. Node/npm 可用时构建 Dashboard
3. 没有 launcher 时在 `~/.local/bin/tokenage` 创建 symlink
4. 按需创建 `~/.tokenage/config.yaml`
5. 用 Supervisor 启动 API 和 OTLP 服务
6. 检查服务端口和 Dashboard
7. 重启 API，让刚构建出来的 Dashboard 生效

如果 `~/.local/bin` 不在 `PATH` 里，安装脚本会打印该加到 shell profile 的命令。

### 2. 打开 Dashboard

```bash
open http://localhost:4001
```

如果你在开发前端，想用 Vite dev server：

```bash
cd frontend
npm install
npm run dev
```

然后打开 [http://localhost:5173](http://localhost:5173)。

### 3. 把 Agent 指向 collector

Server 安装不会配置 Agent。改 Agent 是 client 的职责（同时安装两者时安装脚本会带你登录）：

```bash
tokenage setup
```

它会把这台机器上已安装的 Agent 指向本地 OTLP collector，不会动它不拥有的配置项。`tokenage setup --disable` 可以撤回这些设置。`tokenage status` 会显示装了什么、是否在运行、Agent 指向哪里。

### 4. 生成第一条 tracked event

Bootstrap 之后，运行 Dashboard 里展示的命令，或者直接用下面这些：

```bash
tokenage codex exec "hello"
tokenage claude
```

如果 symlink 还没进 `PATH`，可以用 repo-local fallback：

```bash
TOKENAGE_ROOT="$PWD" ./client/bin/tokenage codex exec "hello"
TOKENAGE_ROOT="$PWD" ./client/bin/tokenage claude
```

空 Dashboard 会自动检查第一条 event。没有假 demo 数据，也不用手动 seed。

## CLI 示例

Wrapper 会运行子命令，捕获运行期间的使用量，然后打印摘要。它上报给已经在运行的 collector：不会替你启动服务，不会为单次运行创建临时数据库或临时 collector，结束后也不需要合并任何东西。collector 不可达时子命令照样运行，只是不被追踪，退出码仍然是子命令自己的。

```bash
# 交互式 agents
tokenage codex
tokenage claude

# 一次性命令
tokenage codex exec "say hello in one sentence"

# 安装后的 CLI
tokenage codex exec "say hello in one sentence"
```

同一个命令也负责所有不是「跑一次被追踪的命令」的操作：

```bash
# 组件、服务、Agent
tokenage status
tokenage setup
tokenage update --check

# 本地 server 不需要 Google 账户：在 server 本机即可使用 dashboard 和 `tokenage login`；
# 其他机器的浏览器请在 server 上运行 `tokenage server login-link` 并打开输出的 URL。
# 用远端 server 代替本地 server
tokenage login --server https://app.example.com
tokenage logout
```

只有在传 `tokenage` 自己的 flags 时才需要 `--`：

```bash
tokenage --json -- codex
tokenage --usage-only -- codex exec "say hello in one sentence"
tokenage --wait-ms 5000 -- codex exec "say hello in one sentence"
tokenage --summary-dest file --summary-file /tmp/llm-summary.json -- claude
tokenage --no-summary -- codex exec "say hello"
```

完整 CLI 参考见 [docs/cli-reference.md](docs/cli-reference.md)，包括所有 flags、tracking model、退出码、服务命令、API endpoints 和环境变量。逐命令的行为见 [docs/cli-refactor.md](docs/cli-refactor.md)。

## Dashboard

Dashboard 提供：

- 无数据时的 first-event onboarding
- 使用量和费用概览
- 模型/来源拆分
- latency 和 TTFT 趋势
- 请求日志
- 已检测到的 Agent 和 setup health

默认情况下，后端 API 会在 `http://localhost:4001` 提供构建后的 Dashboard。前端 dev server 按下面顺序解析 API URL：

1. `TOKENAGE_API_URL`
2. `TOKENAGE_BACKEND_URL`
3. `~/.tokenage/config.yaml` 中的 `server.host` 和 `server.api_port`
4. `http://localhost:4001`

前端相关说明见 [frontend/README.md](frontend/README.md)。

## 默认本地服务

| 服务 | 默认 URL | 用途 |
| --- | --- | --- |
| API + Dashboard | `http://127.0.0.1:4001` | REST API 和构建后的 Dashboard |
| OTLP collector | `http://127.0.0.1:4002` | 本地 Agent telemetry ingestion |

服务命令：

```bash
tokenage server status
tokenage server restart
tokenage server stop
```

`tokenage server start` 负责把服务启动起来，`tokenage server bootstrap` 负责重新安装、重建 Dashboard 并重启 API 让新产物生效。`tokenage status` 是另一个命令：它报告装了哪些组件、它们是否在运行。

运行时文件在 `~/.tokenage/run/`。日志写入 `logs/`。

## 配置

主配置文件：

```text
~/.tokenage/config.yaml
```

最小服务端和数据库配置：

```yaml
models:
  gpt-5.4:
    cost:
      input: 2.5
      output: 15.0
      cacheRead: 0.25

server:
  host: 127.0.0.1
  api_port: 4001
  otlp_port: 4002

db:
  path: ~/.tokenage/usage.db
```

如果要用 PostgreSQL 或 MySQL 替代 SQLite，设置 `db.url`：

```yaml
db:
  url: postgresql+psycopg://user:password@db-host:5432/tokenage?sslmode=require
```

`tokenage server start` 会把 `config.example.yaml` 里缺失的默认值合并进用户配置，但不会覆盖已有值。

## 把客户端指向 Proxy

Proxy 运行在客户端服务（`tokenage client start`）里，不在服务端。它只绑定 `127.0.0.1`，配置写在 `~/.tokenage/client.yaml`（权限保持 `0600`，key 为明文）。没有 `proxy:` 段就没有 proxy。

```yaml
proxy:
  port: 4000              # 可选
  providers:
    my-provider:
      base_url: https://api.example.com/v1
      api_key: sk-...
      models: [gpt-5.4]
    anthropic:
      base_url: https://api.anthropic.com/v1
      api_key: sk-ant-...
      auth_scheme: x-api-key   # 默认 bearer
      enabled: true            # 默认值
      models: [claude-sonnet-4-6]
```

修改后运行 `tokenage client restart`。`tokenage status`、`tokenage client status` 和 `tokenage client start` 会打印 proxy URL，或 `proxy failed: <reason>`。显式设置 `port` 时要么绑定成功要么失败；未设置时复用上次的端口，否则从 4000 开始向上扫描。默认是 `http://127.0.0.1:4000`，实际 URL 以 `tokenage status` 为准。

OpenAI 兼容客户端：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:4000/v1
```

Anthropic 兼容客户端：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
```

客户端自己的 API key 会被忽略：proxy 把 provider 的 `api_key` 以 `Authorization: Bearer <key>` 注入上游；设置 `auth_scheme: x-api-key` 时（如 `api.anthropic.com`）则用 `x-api-key: <key>`。客户端仍需自己发送 `anthropic-version` header（Claude Code 等真实客户端都会发；proxy 原样透传，不设默认值）。

每次模型调用后，客户端会用本设备的 ingest token 向服务端 `POST /ingest/v1/usage` 发送只含 metadata 的使用量报告（模型、token 数、耗时；不含 prompt、response 或 key）。只尝试一次，失败即丢弃；费用由服务端计算。服务端在本机还是远端，行为都一样。

支持的 proxy paths：

- `/v1/chat/completions`
- `/v1/responses`
- `/v1/messages`

对于 streaming response，proxy 会把 TTFT 记录为第一个 upstream chunk 到达前的时间。

## Tracking 覆盖范围

| 指标 | Claude Code | Codex | OpenCode / Kilo Code | Direct proxy |
| --- | --- | --- | --- | --- |
| Input tokens | OTLP | OTLP | Plugin OTLP | Response usage |
| Output tokens | OTLP | OTLP | Plugin OTLP | Response usage |
| Cached tokens read | OTLP | OTLP | Plugin OTLP | Response usage |
| Cached tokens write | OTLP | 不可用 | Plugin OTLP | 不可用 |
| Reasoning tokens | 不可用 | OTLP | Plugin OTLP | Response usage |
| Tool tokens | 不可用 | OTLP | 不可用 | 不可用 |
| Prompt length | OTLP | OTLP | Plugin OTLP | 不可用 |
| Latency | OTLP | OTLP | Plugin OTLP | Proxy timing |
| TTFT | 不可用 | OTLP | Plugin OTLP | Streaming only |
| Session ID | OTLP | OTLP | Plugin OTLP | 不可用 |

TTFT 是运维参考指标，不是 billing-grade 指标。每个 Agent 暴露的 timing 数据不一样。

## API

常用本地 endpoints：

```bash
curl http://127.0.0.1:4001/usage?limit=20
curl http://127.0.0.1:4001/usage/summary
curl http://127.0.0.1:4001/usage/daily
curl http://127.0.0.1:4001/usage/high-watermark
curl http://127.0.0.1:4001/config
```

设备状态和 agent 配置检查属于 client：

```bash
tokenage status --json
```

`/usage` query params：`limit`、`offset`、`provider`、`model`、`since`、`until`。

`/usage/daily` query params：`since`、`until`、`provider`、`model`、`granularity`、`tz_offset`。

## 开发

安装/启动后端服务：

```bash
TOKENAGE_ROOT="$PWD" tokenage server start
```

运行后端测试：

```bash
./.venv/bin/python -m pytest -q
```

运行前端测试和构建：

```bash
cd frontend
npm test
npm run build
```

维护者专用 bootstrap smoke test：

```bash
bash src/scripts/dev/smoke-bootstrap-container.sh
```

这个检查会在全新的 Docker 或 Apple `container` 环境里运行 `src/scripts/bootstrap.sh`。它不是普通用户 setup 的一部分。

## 隐私和安全说明

- `tokenage` 设计为本地运行。
- 使用量默认存储在 `~/.tokenage/usage.db`。
- 如果配置了 `db.url`，使用量数据会写入该数据库。
- Proxy 只监听 loopback，并把客户端的凭证替换成 `client.yaml` 里的 provider key；客户端自己的凭证 header 不会转发到上游。provider key 留在本机，服务端只收到使用量 metadata。
- OTLP payload 由 Agent 自己发出；如果你需要严格控制 metadata，请检查 Agent telemetry settings。

## 贡献

欢迎 issues 和 PR。好的贡献通常包含：

- 清晰的 bug report 或产品问题
- 小而可测试的改动
- 改 Python 行为时配 backend tests（`pytest`）
- 改 Dashboard 行为时把 frontend tests 放到 `frontend/tests/`
- 改命令、setup 或行为时同步更新 docs

请保持示例一致：plain agent invocation 使用 `tokenage codex` 或 `tokenage claude`；只有传 `tokenage` 自己的 flags 时才保留 `--`。

## 开源协议

MIT。详见 [LICENSE](LICENSE)。
