"""The client half of ``tokenage``.

One command, two components. This module is the client: the tracking wrapper,
agent configuration, sign-in, and the component report. It never imports the
server, so it runs on a machine that has only a client — a dependency on
``src`` would break that, and ``tests/client/test_no_server_imports.py``
enforces it.

The server component is reached through the launcher, which routes
``tokenage server ...`` to the shell scripts in the server checkout.
"""

from __future__ import annotations

import argparse
import sys

from client import auth, setup, status, track, update
from client.paths import client_version

PROG = "tokenage"

SUBCOMMANDS = ("login", "logout", "setup", "status", "update", "check-server", "client")

EPILOG = """\
commands:
  login [--server URL]      sign in to a server and wire detected agents
  logout [--keep-agents]    remove this machine's credentials
  setup [--disable]         point detected agents at a collector
  status [--json]           report this client: sign-in, agents, wiring
  update [--check]          update the client
  client <command>          manage this device's background client service
                            (start, stop, restart, status, run)
  server <command>          start, stop, restart, bootstrap, status, update, token
                            (requires the server component)

anything else is run with usage tracking, e.g. `tokenage codex exec "hi"`
tracking options go before the command; use -- when they do:
  tokenage --json -- codex exec "hi"
"""

REMOVED = {
    "summary": "tokenage: 'summary' is no longer a command; see the usage dashboard.",
    "server": (
        "tokenage: 'server' commands are handled by the launcher script, "
        "which resolves the server component."
    ),
}


def _add_banner_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-banner", action="store_true", help="do not print the tokenage banner"
    )


def _build_wrapper_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Run a command with usage tracking.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"{PROG} {client_version()}"
    )
    parser.add_argument(
        "--json", action="store_true", help="write the usage summary as compact JSON"
    )
    parser.add_argument(
        "--usage-only",
        action="store_true",
        help="write only the usage summary to stdout and suppress child output",
    )
    parser.add_argument(
        "--summary-dest",
        choices=("stdout", "stderr", "file"),
        default="stderr",
        help="where the usage summary is written",
    )
    parser.add_argument(
        "--summary-file", help="path to write the summary when --summary-dest=file"
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=3000,
        help="max milliseconds to wait for tracked usage after the command exits",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=250,
        help="poll interval in milliseconds while waiting for the summary",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="run tracking but skip printing the usage summary",
    )
    _add_banner_flag(parser)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _build_subcommand(name: str) -> argparse.ArgumentParser:
    if name == "login":
        parser = argparse.ArgumentParser(
            prog=f"{PROG} login",
            description="Sign in to a server and wire detected agents.",
        )
        parser.add_argument("--server", help="server base URL")
        parser.add_argument("--device-name", help="device label for tokens")
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="print the login URL without opening a browser",
        )
        _add_banner_flag(parser)
        return parser
    if name == "logout":
        parser = argparse.ArgumentParser(
            prog=f"{PROG} logout",
            description="Remove this machine's credentials.",
        )
        parser.add_argument(
            "--keep-agents",
            action="store_true",
            help="leave agent configuration in place (it will be rejected)",
        )
        _add_banner_flag(parser)
        return parser
    if name == "setup":
        parser = argparse.ArgumentParser(
            prog=f"{PROG} setup",
            description="Point detected agents at a collector.",
        )
        parser.add_argument(
            "--disable",
            action="store_true",
            help="remove tokenage's telemetry settings instead of writing them",
        )
        _add_banner_flag(parser)
        return parser
    if name == "status":
        parser = argparse.ArgumentParser(
            prog=f"{PROG} status",
            description="Report this client: sign-in, agents and wiring.",
        )
        parser.add_argument(
            "--json", action="store_true", help="print one compact JSON line"
        )
        _add_banner_flag(parser)
        return parser
    if name == "update":
        parser = argparse.ArgumentParser(
            prog=f"{PROG} update",
            description="Update the client.",
        )
        parser.add_argument(
            "--check", action="store_true", help="report without changing anything"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="print the planned commands"
        )
        _add_banner_flag(parser)
        return parser
    if name == "client":
        parser = argparse.ArgumentParser(
            prog=f"{PROG} client",
            description="Manage this device's background client service.",
        )
        subparsers = parser.add_subparsers(dest="action", required=True)
        subparsers.add_parser("start", help="start the service in the background")
        subparsers.add_parser("stop", help="stop the background service")
        client_restart = subparsers.add_parser(
            "restart", help="stop then start the service"
        )
        client_restart.add_argument(
            "--if-running", action="store_true", help=argparse.SUPPRESS
        )
        client_status = subparsers.add_parser(
            "status", help="report whether the service runs"
        )
        client_status.add_argument(
            "--json", action="store_true", help="print one compact JSON line"
        )
        subparsers.add_parser("run", help="run the service in the foreground")
        _add_banner_flag(parser)
        return parser
    # check-server is internal: the installer uses it to refuse an incompatible box.
    parser = argparse.ArgumentParser(prog=f"{PROG} check-server")
    parser.add_argument("--server", required=True)
    _add_banner_flag(parser)
    return parser


def _run_tracking(argv: list[str]) -> int:
    parser = _build_wrapper_parser()
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.print_help()
        return 0

    options = track.options_from_args(args)
    if options.summary_dest == "file" and not options.summary_file:
        print("--summary-file is required when --summary-dest=file", file=sys.stderr)
        return 2
    if args.usage_only and args.no_summary:
        print("--usage-only cannot be combined with --no-summary", file=sys.stderr)
        return 2

    try:
        return track.run_with_tracking(command=command, options=options)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 127
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 126


def _run_subcommand(name: str, argv: list[str]) -> int:
    parser = _build_subcommand(name)
    args = parser.parse_args(argv[1:])
    if name == "login":
        return auth.login(
            args.server, device_name_arg=args.device_name, no_browser=args.no_browser
        )
    if name == "logout":
        return auth.logout(keep_agents=args.keep_agents)
    if name == "setup":
        return setup.run_setup(disable=args.disable)
    if name == "status":
        return status.run_status(as_json=args.json)
    if name == "update":
        return update.run_update(
            check=args.check,
            dry_run=args.dry_run,
        )
    if name == "client":
        from client import service

        if args.action == "start":
            return service.start()
        if args.action == "stop":
            return service.stop()
        if args.action == "restart":
            return (
                service.restart_if_running() if args.if_running else service.restart()
            )
        if args.action == "status":
            return service.run_status(as_json=args.json)
        return service.run_foreground()
    try:
        server = auth.normalize_server_url(args.server)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return auth.check_server(server)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    first = raw[0] if raw else ""

    if first in REMOVED:
        print(REMOVED[first], file=sys.stderr)
        return 2
    try:
        if first in SUBCOMMANDS:
            return _run_subcommand(first, raw)
        return _run_tracking(raw)
    except ValueError as exc:
        print(f"tokenage: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
