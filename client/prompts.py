"""Shared clack-style client prompts. Non-interactive calls return defaults.

Options are (value, label, hint) tuples. TOKENAGE_NO_PROMPT=1 (or
TOKENAGE_YES=1) accepts defaults, as does --yes or a non-terminal stream.
"""

from __future__ import annotations

import asyncio
import os
import sys


def is_terminal(stream) -> bool:
    """Missing and closed streams cannot accept interactive input."""
    try:
        return stream is not None and not stream.closed and stream.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def interactive(*, yes: bool = False) -> bool:
    return (
        not yes
        and not any(
            os.environ.get(name, "").lower() in {"1", "true", "yes"}
            for name in ("TOKENAGE_NO_PROMPT", "TOKENAGE_YES")
        )
        and is_terminal(sys.stdin)
        and is_terminal(sys.stdout)
    )


def _run(title, body, bindings, answer, *, buffer=None):
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    @bindings.add("c-c")
    @bindings.add("c-d")
    def cancel(event):
        event.app.exit(exception=KeyboardInterrupt())

    @bindings.add("enter")
    def submit(event):
        event.app.exit(result=True)

    header = Window(
        FormattedTextControl(
            [("class:rail", "┌\n│\n"), ("class:active", f"◆  {title}")]
        ),
        height=3,
    )
    footer = Window(FormattedTextControl([("class:rail", "└")]), height=1)
    app = Application(
        layout=Layout(HSplit([header, body, footer])),
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "rail": "ansibrightblack",
                "active": "ansicyan bold",
                "selected": "ansigreen",
                "muted": "ansibrightblack",
            }
        ),
        erase_when_done=True,
    )
    if buffer is not None:
        app.layout.focus(body)
    if sys.platform == "darwin":
        # The installer reads /dev/tty when stdin contains `curl | sh`'s
        # script. macOS kqueue cannot watch that device, although select can.
        # Keep this loop local to the prompt; don't change asyncio's policy.
        import selectors

        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            loop.run_until_complete(app.run_async())
        finally:
            loop.close()
    else:
        app.run()
    print(f"│\n◇  {title}\n│  {answer()}\n└", flush=True)


def _choose(title, options, default, multiple, yes):
    options = list(options)
    if not options:
        return list(default or []) if multiple else default
    values = [option[0] for option in options]
    if multiple:
        selected = set(default or [])
        if not selected.issubset(values):
            raise ValueError("default must be among the options")
    else:
        default = default if default is not None else values[0]
        if default not in values:
            raise ValueError("select requires options and a valid default")
        selected = {default}
    if not interactive(yes=yes):
        return list(default or []) if multiple else default
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    pos = [values.index(default) if not multiple else 0]
    kb = KeyBindings()

    @kb.add("up")
    def up(event):
        pos[0] = (pos[0] - 1) % len(options)

    @kb.add("down")
    def down(event):
        pos[0] = (pos[0] + 1) % len(options)

    if multiple:

        @kb.add(" ")
        def toggle(event):
            selected.symmetric_difference_update({values[pos[0]]})

    def render():
        lines = []
        for i, option in enumerate(options):
            value, label, *hint = option
            active = value in selected if multiple else i == pos[0]
            symbol = ("◼" if active else "◻") if multiple else ("●" if active else "○")
            suffix = f" ({hint[0]})" if hint and hint[0] and i == pos[0] else ""
            lines.extend(
                [
                    ("class:active", "│  "),
                    ("class:selected" if active else "class:muted", f"{symbol} "),
                    ("class:active" if i == pos[0] else "", f"{label}{suffix}\n"),
                ]
            )
        return lines

    def result():
        return [v for v in values if v in selected] if multiple else values[pos[0]]

    _run(
        title,
        Window(
            FormattedTextControl(
                render,
                show_cursor=False,
                get_cursor_position=lambda: Point(x=0, y=pos[0]),
            )
        ),
        kb,
        lambda: (
            ", ".join(o[1] for o in options if o[0] in result())
            if multiple
            else options[pos[0]][1]
        ),
    )
    return result()


def select(title, options, default=None, *, yes=False):
    return _choose(title, options, default, False, yes)


def multiselect(title, options, default=None, *, yes=False):
    return _choose(title, options, default, True, yes)


def text(title: str, default: str = "", *, yes: bool = False) -> str:
    if not interactive(yes=yes):
        return default
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import BufferControl

    buffer = Buffer()
    buffer.text = default
    buffer.cursor_position = len(default)
    _run(
        title,
        Window(BufferControl(buffer=buffer), get_line_prefix=lambda *_: "│  "),
        KeyBindings(),
        lambda: buffer.text or default,
        buffer=buffer,
    )
    return buffer.text or default


def confirm(title: str, default: bool = True, *, yes: bool = False) -> bool:
    return select(title, [(True, "Yes"), (False, "No")], default, yes=yes)


def _installer(out: str, component: str, server_url: str, hosted: str) -> None:
    """install.sh's first questions; it runs this file alone, before the client
    exists. Writes the component and its URL (or "-" when unused), one per line."""
    if not component:
        component = select(
            "What do you want to install?",
            [
                ("client", "Client", "track the agents on this machine"),
                ("server", "Server", "host your own dashboard and collector"),
                ("both", "Both", "your own server + a client signed in to it"),
            ],
        )
    if component == "client":
        title = (
            "Server URL" if server_url else "Server URL (the tokenage hosted server)"
        )
        from urllib.parse import urlsplit

        while True:
            answer = text(title, server_url or hosted).strip()
            try:
                parsed = urlsplit(answer)
                _ = parsed.port
                valid = (
                    bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and not any(c.isspace() for c in answer)
                    and (
                        parsed.scheme == "https"
                        or (
                            parsed.scheme == "http"
                            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                        )
                    )
                )
            except ValueError:
                valid = False
            if valid:
                server_url = answer
                break
            print("Enter an HTTPS URL (HTTP is allowed for localhost).")
            if not interactive():
                raise SystemExit(1)
    else:
        server_url = "-"
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(f"{component}\n{server_url}\n")


if __name__ == "__main__":
    if sys.argv[1:2] != ["install"]:
        raise SystemExit("usage: prompts.py install OUT COMPONENT SERVER_URL HOSTED")
    try:
        _installer(*sys.argv[2:6])
    except KeyboardInterrupt:
        raise SystemExit(130) from None
