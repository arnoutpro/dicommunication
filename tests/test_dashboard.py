from __future__ import annotations

from app.models import RemoteNode, ToolResult
from app.store import ConfigStore


def _echo(remote: RemoteNode, ok: bool) -> ToolResult:
    return ToolResult(
        tool_id="c-echo", tool_name="C-ECHO", ok=ok, summary="", remote_id=remote.id, remote_name=remote.name
    )


def test_dashboard_without_nodes_points_at_adding_one(client) -> None:
    body = client.get("/").text
    assert "Next step" in body
    assert "Add a remote node" in body
    assert "C-ECHO all nodes" not in body


def test_dashboard_says_each_node_status_in_words(client, store: ConfigStore) -> None:
    ok_node = RemoteNode(name="PACS A", ae_title="PACS_A", host="127.0.0.1", port=104)
    bad_node = RemoteNode(name="PACS B", ae_title="PACS_B", host="127.0.0.1", port=105)
    new_node = RemoteNode(name="PACS C", ae_title="PACS_C", host="127.0.0.1", port=106)
    for node in (ok_node, bad_node, new_node):
        store.add_remote(node)
    store.add_results([_echo(ok_node, True), _echo(bad_node, False)])

    body = client.get("/").text
    assert "Echo OK" in body
    assert "Echo failed" in body
    assert "Not checked" in body
    assert "1 answering · 1 not answering · 1 not checked" in body
    # The next step goes after the failing node, with it preselected.
    assert f'/tools/ping?remote_id={bad_node.id}' in body
    assert "Network PING PACS B" in body


def test_node_links_open_tools_with_that_node_selected(client, store: ConfigStore) -> None:
    first = RemoteNode(name="First", ae_title="FIRST", host="127.0.0.1", port=104)
    second = RemoteNode(name="Second", ae_title="SECOND", host="127.0.0.1", port=105)
    store.add_remote(first)
    store.add_remote(second)

    for path in ("/tools/c-echo", "/tools/ping", "/testbench"):
        body = client.get(f"{path}?remote_id={second.id}").text
        assert f'<option value="{second.id}" selected>' in body, path
        assert f'<option value="{first.id}" selected>' not in body, path


def test_echo_all_from_dashboard_returns_to_dashboard(client, remote: RemoteNode) -> None:
    ran = client.post("/echo-board/run?return=home", follow_redirects=False)
    assert ran.status_code == 303
    assert ran.headers["location"] == "/"
    # The run was recorded, so the dashboard now has a status for the node.
    assert "Not checked" not in client.get("/").text
