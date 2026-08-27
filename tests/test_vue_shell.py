from __future__ import annotations

from app.shell import (
    SHELL_DICOMM,
    SHELL_VUE,
    VUE_PREFIX,
    prefix_redirect_location,
    public_href,
    strip_vue_prefix,
    tools_for_shell,
    vue_path_allowed,
)


def test_strip_and_prefix_vue_paths() -> None:
    assert strip_vue_prefix("/vue") == "/"
    assert strip_vue_prefix("/vue/") == "/"
    assert strip_vue_prefix("/vue/config/remotes") == "/config/remotes"
    assert strip_vue_prefix("/config") == "/config"
    assert public_href("/", shell=SHELL_VUE) == "/vue/"
    assert public_href("/config/remotes", shell=SHELL_VUE) == "/vue/config/remotes"
    assert public_href("/static/css/app.css", shell=SHELL_VUE) == "/static/css/app.css"
    assert public_href("/logs", shell=SHELL_DICOMM) == "/logs"
    assert prefix_redirect_location("/config/local?saved=local") == "/vue/config/local?saved=local"
    assert prefix_redirect_location("/vue/") == "/vue/"
    assert prefix_redirect_location("https://example.test/config") == "https://example.test/vue/config"


def test_vue_allows_query_and_config_only() -> None:
    assert vue_path_allowed("/")
    assert vue_path_allowed("/tools/c-find-advanced/run")
    assert vue_path_allowed("/config/remotes")
    assert vue_path_allowed("/logs/live")
    assert vue_path_allowed("/help")
    assert not vue_path_allowed("/testbench")
    assert not vue_path_allowed("/tools/c-echo")
    assert not vue_path_allowed("/worklist")


def test_shell_tool_lists_split_c_find_advanced() -> None:
    dicomm = {tool.id for tool in tools_for_shell(SHELL_DICOMM)}
    vue = {tool.id for tool in tools_for_shell(SHELL_VUE)}
    assert "c-find-advanced" not in dicomm
    assert "c-echo" in dicomm
    assert vue == {"c-find-advanced"}
    assert VUE_PREFIX == "/vue"


def test_vue_home_is_query_page(client) -> None:
    page = client.get("/vue/")
    assert page.status_code == 200
    assert b"Vue PACS Database Analytics" in page.content
    assert b">Query<" in page.content
    assert b"No remote node configured" in page.content
    assert b"Test tools" not in page.content
    assert b"C-ECHO board" not in page.content
    assert b'href="/vue/config/remotes"' in page.content
    assert b"shell-vue" in page.content
    assert b"topbar" in page.content
    assert b"site-brand-mark-layer" in page.content


def test_vue_query_form_uses_prefixed_action(client, remote) -> None:
    page = client.get("/vue/")
    assert page.status_code == 200
    assert b"find-key-list" in page.content
    assert b'hx-post="/vue/tools/c-find-advanced/run"' in page.content


def test_vue_hides_workstation_tools(client) -> None:
    bounced = client.get("/vue/testbench", follow_redirects=False)
    assert bounced.status_code == 303
    assert bounced.headers["location"] == "/vue/"
    echo = client.get("/vue/tools/c-echo", follow_redirects=False)
    assert echo.status_code == 303


def test_vue_config_and_help_stay_in_shell(client) -> None:
    remotes = client.get("/vue/config/remotes")
    assert remotes.status_code == 200
    assert b"Remote DICOM nodes" in remotes.content
    assert b'action="/vue/config/remotes"' in remotes.content
    assert b"Test tools" not in remotes.content
    help_page = client.get("/vue/help")
    assert help_page.status_code == 200
    assert b"ELSCINT1" in help_page.content
    assert b"HL7 send" not in help_page.content
    about = client.get("/vue/about")
    assert about.status_code == 200
    assert b"Ships in the Dicommunication installer" in about.content


def test_dicommunication_sidebar_omits_analytics(client) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert b"Dicommunication" in home.content
    assert b'href="/tools/c-find-advanced"' not in home.content
    ping = client.get("/tools/ping")
    assert ping.status_code == 200
    assert b"Vue PACS Database Analytics" not in ping.content
