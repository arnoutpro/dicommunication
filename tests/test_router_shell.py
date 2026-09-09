from __future__ import annotations

from app.shell import (
    PRODUCT_NAMES,
    PRODUCT_ROUTER,
    PROFILE_ROUTER,
    ROUTER_PREFIX,
    SHELL_DICOMM,
    SHELL_ROUTER,
    is_router_profile,
    prefix_redirect_location,
    public_href,
    router_path_allowed,
    strip_router_prefix,
    tools_for_shell,
)


def test_router_product_name_and_profile() -> None:
    assert PRODUCT_ROUTER == "Dicom Router"
    assert PRODUCT_NAMES[SHELL_ROUTER] == "Dicom Router"
    assert PROFILE_ROUTER == "dicom-router"
    assert is_router_profile(PROFILE_ROUTER)
    assert not is_router_profile("dicommunication")


def test_strip_and_prefix_router_paths() -> None:
    assert strip_router_prefix("/dicom-router") == "/"
    assert strip_router_prefix("/dicom-router/") == "/"
    assert strip_router_prefix("/dicom-router/config/remotes") == "/config/remotes"
    assert strip_router_prefix("/config") == "/config"
    assert public_href("/", shell=SHELL_ROUTER) == "/dicom-router/"
    assert public_href("/router?edit=1", shell=SHELL_ROUTER) == "/dicom-router/router?edit=1"
    assert public_href("/static/css/app.css", shell=SHELL_ROUTER) == "/static/css/app.css"
    assert (
        prefix_redirect_location("/config/local?saved=local", prefix=ROUTER_PREFIX)
        == "/dicom-router/config/local?saved=local"
    )
    assert prefix_redirect_location("/dicom-router/", prefix=ROUTER_PREFIX) == "/dicom-router/"


def test_router_allows_router_and_config_only() -> None:
    assert router_path_allowed("/")
    assert router_path_allowed("/router")
    assert router_path_allowed("/router?edit=1")
    assert router_path_allowed("/router/abc/runs")
    assert router_path_allowed("/router/abc/run-now")
    assert router_path_allowed("/config/remotes")
    assert router_path_allowed("/logs/live")
    assert router_path_allowed("/help")
    assert not router_path_allowed("/testbench")
    assert not router_path_allowed("/tools/c-echo")
    assert not router_path_allowed("/worklist")


def test_router_shell_excludes_every_registry_tool() -> None:
    dicomm = {tool.id for tool in tools_for_shell(SHELL_DICOMM)}
    router_shell = tools_for_shell(SHELL_ROUTER)
    assert "c-echo" in dicomm
    assert router_shell == []


def test_router_home_is_route_rules_page(client) -> None:
    page = client.get("/dicom-router/")
    assert page.status_code == 200
    assert b"Dicom Router" in page.content
    assert b'data-product-name="Dicom Router"' in page.content
    assert b">Route rules<" in page.content
    assert b"Scheduled routing rules" in page.content
    assert b"Test tools" not in page.content
    assert b"C-ECHO board" not in page.content
    assert b'href="/dicom-router/config/remotes"' in page.content
    assert b"topbar" in page.content


def test_router_home_sidebar_lists_rules_with_prefixed_links(client, store, remote) -> None:
    from app.models import RouteRule

    rule = store.add_route_rule(RouteRule(name="Nightly CT", source_remote_id=remote.id))
    page = client.get("/dicom-router/")
    assert page.status_code == 200
    assert "Nightly CT" in page.text
    assert f'href="/dicom-router/router/{rule.id}/runs"' in page.text


def test_router_form_uses_prefixed_action(client, remote) -> None:
    page = client.get("/dicom-router/")
    assert page.status_code == 200
    assert b'action="/dicom-router/router"' in page.content


def test_router_hides_workstation_tools(client) -> None:
    bounced = client.get("/dicom-router/testbench", follow_redirects=False)
    assert bounced.status_code == 303
    assert bounced.headers["location"] == "/dicom-router/"
    echo = client.get("/dicom-router/tools/c-echo", follow_redirects=False)
    assert echo.status_code == 303


def test_router_config_and_help_stay_in_shell(client) -> None:
    remotes = client.get("/dicom-router/config/remotes")
    assert remotes.status_code == 200
    assert b"Remote DICOM nodes" in remotes.content
    assert b'action="/dicom-router/config/remotes"' in remotes.content
    assert b"Test tools" not in remotes.content
    help_page = client.get("/dicom-router/help")
    assert help_page.status_code == 200


def test_dicommunication_sidebar_has_router_link(client) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert b"Dicommunication" in home.content
    assert b'href="/router"' in home.content
