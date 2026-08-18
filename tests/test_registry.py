from __future__ import annotations

from app.tools import get_tool, list_tools


def test_builtin_tools_are_registered() -> None:
    tools = {tool.id: tool for tool in list_tools()}
    assert set(tools) >= {"ping", "c-echo", "mwl-find"}
    assert tools["ping"].name == "Network PING"
    assert tools["c-echo"].name == "C-ECHO"
    assert tools["mwl-find"].name == "MWL C-FIND"
    assert get_tool("ping") is tools["ping"]


def test_unknown_tool_raises() -> None:
    try:
        get_tool("c-store")
    except KeyError as exc:
        assert "c-store" in str(exc)
    else:
        raise AssertionError("expected KeyError")
