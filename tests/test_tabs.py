"""Phase 2 of the app overhaul: a top tab row (one tab per product, plus a
shared Configuration tab) replaces the old always-everything sidebar from
Phase 1. Each tab's content area still has its own left sidebar with
sub-menus; Configuration is no longer nested under every product's own page.
"""

from __future__ import annotations

from app.shell import TAB_ANALYTICS, TAB_ANONYMIZER, TAB_CLEANER, TAB_CONFIG, TAB_DICOMM, TAB_ROUTER, active_tab


def test_active_tab_maps_nav_and_tool_id() -> None:
    assert active_tab("home") == TAB_DICOMM
    assert active_tab("testbench") == TAB_DICOMM
    assert active_tab("echo-board") == TAB_DICOMM
    assert active_tab("worklist") == TAB_DICOMM
    assert active_tab("tools") == TAB_DICOMM
    assert active_tab("tools", "c-echo") == TAB_DICOMM
    assert active_tab("tools", "c-find-advanced") == TAB_ANALYTICS
    assert active_tab("tools", "anonymize") == TAB_ANONYMIZER
    assert active_tab("tools", "dicom-cleaner") == TAB_CLEANER
    assert active_tab("router") == TAB_ROUTER
    assert active_tab("config") == TAB_CONFIG
    assert active_tab("config-local") == TAB_CONFIG
    assert active_tab("config-identities") == TAB_CONFIG
    assert active_tab("config-remotes") == TAB_CONFIG
    assert active_tab("logs") == TAB_CONFIG
    # Pages that aren't part of one product (About, Help) fall back to the
    # Dicommunication tab rather than leaving nothing selected.
    assert active_tab("about") == TAB_DICOMM
    assert active_tab("help") == TAB_DICOMM


def test_every_page_shows_the_tab_row_with_one_active_tab(client) -> None:
    for path in ("/", "/testbench", "/tools/c-find-advanced", "/tools/anonymize", "/router", "/config", "/logs"):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.text
        assert 'class="tab-row"' in body, path
        assert body.count('class="tab-item active"') == 1, path


def test_dicommunication_tab_active_on_home(client) -> None:
    body = client.get("/").text
    assert '<a href="/" class="tab-item active">Dicommunication</a>' in body


def test_analytics_tab_active_and_isolated(client) -> None:
    body = client.get("/tools/c-find-advanced").text
    assert '<a href="/tools/c-find-advanced" class="tab-item active">Dicomtag Analytics</a>' in body
    assert "Configured nodes" not in body
    assert 'href="/logs"' not in body
    assert 'href="/testbench"' not in body


def test_anonymizer_tab_active_and_isolated(client) -> None:
    body = client.get("/tools/anonymize").text
    assert '<a href="/tools/anonymize" class="tab-item active">Dicom Anonymizer</a>' in body
    assert "Configured nodes" not in body
    assert 'href="/testbench"' not in body


def test_cleaner_tab_active_and_isolated(client) -> None:
    body = client.get("/tools/dicom-cleaner").text
    assert '<a href="/tools/dicom-cleaner" class="tab-item active">Dicom Cleaner</a>' in body
    assert "Configured nodes" not in body
    assert 'href="/testbench"' not in body


def test_router_tab_active_and_isolated(client) -> None:
    body = client.get("/router").text
    assert '<a href="/router" class="tab-item active">Dicom Router</a>' in body
    assert "Configured nodes" not in body
    assert 'href="/testbench"' not in body


def test_config_tab_active_and_holds_logs(client) -> None:
    for path in ("/config", "/config/local", "/config/identities", "/config/remotes", "/logs"):
        body = client.get(path).text
        assert '<a href="/config" class="tab-item active">Configuration</a>' in body, path
        assert 'href="/logs"' in body, path
        assert 'href="/config/remotes"' in body, path


def test_configuration_not_duplicated_on_every_product_tab(client) -> None:
    # Phase 1 showed "Configured nodes" and Logs on every single page.
    # Phase 2 moves them to the Configuration tab only.
    for path in ("/tools/c-find-advanced", "/tools/anonymize", "/tools/dicom-cleaner", "/router"):
        body = client.get(path).text
        assert "Configured nodes" not in body, path


def test_dicommunication_tab_sidebar_has_test_tools_but_not_promoted_products(client) -> None:
    body = client.get("/").text
    assert "Testbench" in body
    assert "C-ECHO board" in body
    assert "Worklist" in body
    # Dicomtag Analytics/Anonymizer/Cleaner are promoted to their own tabs —
    # each href appears exactly once, as that tab's own link in the tab
    # row, not a second time in the Dicommunication tab's tool list.
    assert body.count('href="/tools/c-find-advanced"') == 1
    assert body.count('href="/tools/anonymize"') == 1
    assert body.count('href="/tools/dicom-cleaner"') == 1
