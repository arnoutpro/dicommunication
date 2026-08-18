from __future__ import annotations

from app.tools import get_tool, list_tools, list_tools_by_category


def test_builtin_tools_are_registered() -> None:
    tools = {tool.id: tool for tool in list_tools()}
    assert set(tools) >= {"ping", "c-echo", "mwl-find", "c-store", "c-find"}
    assert tools["ping"].name == "Network PING"
    assert tools["c-echo"].name == "C-ECHO"
    assert tools["mwl-find"].name == "MWL C-FIND"
    assert tools["c-store"].name == "C-STORE"
    assert tools["c-find"].name == "C-FIND"
    assert get_tool("ping") is tools["ping"]


def test_unknown_tool_raises() -> None:
    try:
        get_tool("c-move")
    except KeyError as exc:
        assert "c-move" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_tools_grouped_by_category() -> None:
    groups = dict(list_tools_by_category())
    assert "Connectivity" in groups
    assert "DIMSE" in groups
    assert [tool.id for tool in groups["Connectivity"]] == ["ping"]
    assert [tool.id for tool in groups["DIMSE"]] == ["c-echo", "c-store", "c-find", "mwl-find"]
