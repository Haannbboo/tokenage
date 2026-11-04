"""The server component's CLI: operator commands that need the database.

Everything a per-user client does — the tracking wrapper, sign-in, agent
configuration, status — lives in ``client/`` and never imports this module.
``tokenage server <command>`` is routed here by the launcher (``bootstrap``
goes through ``src/scripts/bootstrap.sh`` first, which exec's back into this).
"""

from __future__ import annotations

import argparse
import secrets
import sys

from src import ops

PROG = "tokenage server"


def parse_token_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{PROG} token",
        description="Manage auth tokens (operator-only; needs database access).",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    create = subparsers.add_parser(
        "create",
        help="create a token for a user, creating the user if needed",
        description=(
            "Create a token for a user, creating the user if needed "
            "(operator-only; needs database access)."
        ),
    )
    create.add_argument("--email", required=True, help="user email")
    create.add_argument(
        "--kind", choices=("cli", "ingest", "web"), default="cli", help="token kind"
    )
    create.add_argument("--name", help="device name to label the token with")
    revoke = subparsers.add_parser(
        "revoke",
        help="revoke all of a user's tokens",
        description=(
            "Revoke all CLI, ingest and web tokens for a user. "
            "Switching to Google strands owner@localhost devices; "
            "sign them in again with a Google account."
        ),
    )
    revoke.add_argument("--email", required=True, help="user email")
    return parser.parse_args(argv)


def run_token_command(argv: list[str]) -> int:
    # Lazy: importing these loads the config, which `start` has to create first.
    from src.auth import mint_token
    from src.auth.tokens import revoke_user_tokens
    from src.database import init_db

    token_args = parse_token_args(argv)

    init_db()
    try:
        if token_args.action == "revoke":
            count = revoke_user_tokens(token_args.email)
            print(f"Revoked {count} tokens for {token_args.email.strip().lower()}.")
            return 0
        token, user = mint_token(
            token_args.email, kind=token_args.kind, device_name=token_args.name
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Minted {token_args.kind} token for {user.email} (shown once):")
    print(token)
    return 0


def run_login_link_command() -> int:
    from src.auth.google import store_login_code
    from src.auth.routes import auth_provider
    from src.config.app import CONFIG
    from src.config.server_config import resolve_server_urls

    if auth_provider() != "local":
        print("login-link only applies to auth.provider: local", file=sys.stderr)
        return 2
    code = secrets.token_urlsafe(24)
    store_login_code(code)
    api_url = resolve_server_urls(CONFIG)["api_url"]
    print(f"{api_url}/auth/local/login?code={code}")
    print("Single use; expires in 5 minutes.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "token":
        return run_token_command(args[1:])
    if args and args[0] == "login-link":
        return run_login_link_command()
    if args and args[0] in ops.COMMANDS:
        if args[0] in ("stop", "restart", "status"):
            if len(args) > 1:
                print(f"{PROG} {args[0]} takes no arguments", file=sys.stderr)
                return 2
            return ops.COMMANDS[args[0]]()
        return ops.COMMANDS[args[0]](args[1:])
    commands = " | ".join(["login-link", *ops.COMMANDS])
    print(
        f"usage: {PROG} token create|revoke --email <email> | {commands}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
