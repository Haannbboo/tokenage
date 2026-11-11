import asyncio
import selectors
import sys

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from client import prompts


@pytest.mark.parametrize("stream", ["stdin", "stdout"])
def test_non_terminal_returns_defaults(stream, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(getattr(sys, stream), "isatty", lambda: False)
    assert not prompts.interactive()
    assert prompts.select("Pick", [(1, "one"), (2, "two")], 2) == 2
    assert prompts.multiselect("Pick", [(1, "one")], [1]) == [1]
    assert prompts.text("Name", "default") == "default"
    assert prompts.confirm("Continue?", False) is False


@pytest.mark.parametrize(
    "optout",
    ["TOKENAGE_NO_PROMPT", "TOKENAGE_YES", "yes"],
)
def test_opt_out(optout, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    if optout == "--yes":
        monkeypatch.setattr(sys, "argv", ["tokenage", "--yes"])
    elif optout != "yes":
        monkeypatch.setenv(optout, "1")
    assert not prompts.interactive(yes=optout == "yes")


@pytest.mark.parametrize(
    "kind,keys,expected",
    [
        ("select", "\x1b[B\r", 2),
        ("multi", " \x1b[B \r", [2]),
        ("text", "\x01new\x0b\r", "new"),
        ("confirm", "\x1b[B\r", False),
    ],
)
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_keyboard_prompts(kind, keys, expected, platform, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", platform)
    selected = []
    if platform == "darwin":
        monkeypatch.delattr(asyncio, "Runner", raising=False)
        original = selectors.SelectSelector

        def record_selector():
            selector = original()
            selected.append(selector)
            return selector

        monkeypatch.setattr(selectors, "SelectSelector", record_selector)
    monkeypatch.setattr(prompts, "interactive", lambda **kw: True)
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=DummyOutput()),
    ):
        pipe.send_text(keys)
        if kind == "select":
            result = prompts.select("Pick", [(1, "one"), (2, "two")])
        elif kind == "multi":
            result = prompts.multiselect("Pick", [(1, "one"), (2, "two")], [1])
        elif kind == "text":
            result = prompts.text("Name", "old")
        else:
            result = prompts.confirm("Continue?")
    assert result == expected
    assert len(selected) == (1 if platform == "darwin" else 0)
    if platform == "darwin":
        assert selected[0].get_map() is None
    assert "◇" in capsys.readouterr().out


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_cancel(platform, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(prompts, "interactive", lambda **kw: True)
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=DummyOutput()),
    ):
        pipe.send_text("\x03")
        with pytest.raises(KeyboardInterrupt):
            prompts.confirm("Continue?")


def test_unrelated_yes_and_skip_login_do_not_disable_prompts(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["agent", "--yes"])
    monkeypatch.setenv("TOKENAGE_SKIP_LOGIN", "1")
    assert prompts.interactive()


def test_empty_choices_return_defaults():
    assert prompts.select("Pick", []) is None
    assert prompts.multiselect("Pick", []) == []


def test_installer_reasks_invalid_server_url(tmp_path, monkeypatch):
    answers = iter(
        [
            "app.example.com",
            "http://remote.example",
            "https://user:pass@app.example.com",
            "https://@app.example.com",
            "http://LOCALHOST:4123",
        ]
    )
    monkeypatch.setattr(prompts, "text", lambda *args: next(answers))
    monkeypatch.setattr(prompts, "interactive", lambda: True)
    out = tmp_path / "answers"
    prompts._installer(str(out), "client", "", "https://default.example")
    assert out.read_text() == "client\nhttp://LOCALHOST:4123\n"


def test_installer_exits_when_invalid_default_cannot_be_corrected(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TOKENAGE_NO_PROMPT", "tRUE")
    out = tmp_path / "answers"
    with pytest.raises(SystemExit) as error:
        prompts._installer(
            str(out), "client", "http://remote.example", "https://default.example"
        )
    assert error.value.code == 1
    assert capsys.readouterr().out.count("Enter an HTTPS URL") == 1
    assert not out.exists()
