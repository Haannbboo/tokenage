"""Open-loop ingest load: OTLP Claude turns + proxy usage reports at fixed request rates.

uv run --no-project --with httpx python loadgen.py --url http://SERVER:PORT --tokens tokens.txt --rates 20 --step 60
tokens.txt: one ingest token per line (one per simulated user). Prints one JSON line per step; exits 1 after a failed step.
"""

import argparse
import asyncio
import json
import random
import time
import uuid

import httpx

MODELS = ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]
TOOLS = ["Bash", "Read", "Edit", "Grep"]


def attr(key, value):
    kind = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {kind: str(value)}}


def claude_turn(session):
    """One OTLP request: tool_decision, api_request, tool_result of one prompt."""
    now = time.time_ns()
    prompt, tool_id = uuid.uuid4().hex, "toolu_" + uuid.uuid4().hex[:20]
    tool = random.choice(TOOLS)
    events = [
        {"event.name": "tool_decision", "tool_name": tool, "tool_use_id": tool_id},
        {
            "event.name": "api_request",
            "model": random.choice(MODELS),
            "input_tokens": random.randint(50, 5000),
            "output_tokens": random.randint(10, 2000),
            "cache_read_tokens": random.randint(0, 50000),
            "cache_creation_tokens": random.randint(0, 2000),
            "duration_ms": random.randint(500, 20000),
        },
        {"event.name": "tool_result", "tool_use_id": tool_id, "duration_ms": 120},
    ]
    records = [
        {
            "timeUnixNano": str(now + i),
            "attributes": [attr("prompt.id", prompt), attr("session.id", session)]
            + [attr(k, v) for k, v in e.items()],
        }
        for i, e in enumerate(events)
    ]
    resource = [attr("service.name", "claude-code"), attr("session.id", session)]
    body = {
        "resourceLogs": [
            {
                "resource": {"attributes": resource},
                "scopeLogs": [{"logRecords": records}],
            }
        ]
    }
    return "/otlp/v1/logs", body


def proxy_report(_session):
    prompt, completion = random.randint(50, 5000), random.randint(10, 2000)
    return "/ingest/v1/usage", {
        "ts": time.time_ns() // 1000,
        "provider": "openai",
        "model": random.choice(["gpt-5", "gpt-5-mini"]),
        "endpoint": "/v1/chat/completions",
        "client_source": "proxy",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "latency_ms": random.randint(200, 10000),
        "status": 200,
        "tool_calls": [
            {
                "tool_use_id": "call_" + uuid.uuid4().hex[:20],
                "tool_name": random.choice(TOOLS),
            }
        ],
    }


async def step(client, args, tokens, rate):
    latencies, statuses = [], {}
    ok, aborted = 0, False
    sessions = {t: uuid.uuid4().hex for t in tokens}

    async def one():
        nonlocal ok, aborted
        token = random.choice(tokens)
        if random.random() < 0.02:  # a user starts a new session now and then
            sessions[token] = uuid.uuid4().hex
        make = claude_turn if random.random() < args.otlp_share else proxy_report
        path, body = make(sessions[token])
        start = time.monotonic()
        try:
            r = await client.post(
                args.url + path, json=body, headers={"x-tokenage-token": token}
            )
            key = str(r.status_code)
        except httpx.HTTPError as exc:
            key = type(exc).__name__
        latencies.append(time.monotonic() - start)
        statuses[key] = statuses.get(key, 0) + 1
        ok += key.isdigit() and 200 <= int(key) < 300
        if (len(latencies) - ok) / len(latencies) > 0.2:
            aborted = True

    # Open loop: requests start on schedule whether or not earlier ones finished,
    # so a slow server shows up as latency and errors, not as a lower send rate.
    tasks, t0 = [], time.monotonic()
    for i in range(int(rate * args.step)):
        await asyncio.sleep(max(0, t0 + i / rate - time.monotonic()))
        if aborted:
            break
        tasks.append(asyncio.create_task(one()))
    await asyncio.gather(*tasks)
    latencies.sort()
    pct = lambda p: round(latencies[int(p * (len(latencies) - 1))], 3)  # noqa: E731
    print(
        json.dumps(
            {
                "rate": rate,
                "sent": len(tasks),
                "ok": ok,
                "aborted": aborted,
                "statuses": statuses,
                "p50": pct(0.5),
                "p95": pct(0.95),
                "p99": pct(0.99),
                "wall_s": round(time.monotonic() - t0, 1),
            }
        ),
        flush=True,
    )
    return not aborted and ok == len(tasks) and pct(0.95) < 1


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--tokens", required=True)
    p.add_argument("--rates", default="20,40,60,80")
    p.add_argument("--step", type=float, default=60, help="seconds per rate")
    p.add_argument("--timeout", type=float, default=10, help="request timeout seconds")
    p.add_argument("--otlp-share", type=float, default=0.7)
    args = p.parse_args()
    rates = [float(r) for r in args.rates.split(",")]
    if args.timeout <= 0 or args.step <= 0 or any(r * args.step < 1 for r in rates):
        p.error("timeout and step must be positive; each rate must schedule a request")
    with open(args.tokens) as f:
        tokens = [t.strip() for t in f if t.strip()]
    if not tokens:
        p.error("tokens file is empty")
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        for rate in rates:
            if not await step(client, args, tokens, rate):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
