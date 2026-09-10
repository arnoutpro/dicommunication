"""Phase 1 of the app merge: Dicomtag Analytics, Dicom Anonymizer, Dicom
Router, and Dicom Cleaner used to be separate windows at their own URL
prefix (/vue, /anonymize, /dicom-router, /cleaner), each hiding the rest of
the app. They're now reached directly at their plain path inside the one
Dicommunication window, and show up in its own navigation.
"""

from __future__ import annotations

from app.shell import (
    PRODUCT_ANALYTICS,
    PRODUCT_DICOMM,
    PROFILES,
    display_tool_name,
    profile_start_path,
    profile_window_title,
)
from app.tools import list_tools


def test_display_tool_name_maps_legacy_analytics_titles() -> None:
    assert display_tool_name("Vue PACS Database Analytics") == "Dicomtag Analytics"
    assert display_tool_name("C-FIND Advanced") == "Dicomtag Analytics"
    assert display_tool_name("C-ECHO") == "C-ECHO"


def test_profile_is_vestigial_and_always_opens_the_one_window() -> None:
    for profile in PROFILES:
        assert profile_start_path(profile) == "/"
        assert profile_window_title(profile) == PRODUCT_DICOMM


def test_no_tool_is_hidden_from_the_registry_anymore() -> None:
    ids = {tool.id for tool in list_tools()}
    assert {"c-find-advanced", "anonymize", "dicom-cleaner", "c-echo"} <= ids


def test_home_page_lists_every_product(client) -> None:
    home = client.get("/")
    assert home.status_code == 200
    body = home.text
    assert PRODUCT_DICOMM in body
    assert PRODUCT_ANALYTICS in body
    assert "Dicom Anonymizer" in body
    assert "Dicom Router" in body
    assert "Dicom Cleaner" in body
    assert 'href="/tools/c-find-advanced"' in body
    assert 'href="/tools/anonymize"' in body
    assert 'href="/tools/dicom-cleaner"' in body
    assert 'href="/router"' in body


def test_dicomtag_analytics_reachable_at_plain_path(client, remote) -> None:
    page = client.get("/tools/c-find-advanced")
    assert page.status_code == 200
    assert 'hx-post="/tools/c-find-advanced/run"' in page.text


def test_dicom_anonymizer_reachable_at_plain_path(client) -> None:
    page = client.get("/tools/anonymize")
    assert page.status_code == 200
    assert "Study Date" in page.text


def test_dicom_cleaner_reachable_at_plain_path(client) -> None:
    page = client.get("/tools/dicom-cleaner")
    assert page.status_code == 200
    assert "Dicom Cleaner" in page.text


def test_dicom_router_reachable_at_plain_path(client) -> None:
    page = client.get("/router")
    assert page.status_code == 200
    assert "Route rules" in page.text or "route rule" in page.text.lower()


def test_no_shell_prefixes_exist_anymore(client) -> None:
    for prefix in ("/vue/", "/anonymize/", "/dicom-router/", "/cleaner/"):
        response = client.get(prefix, follow_redirects=False)
        assert response.status_code == 404


def test_testbench_and_worklist_reachable_everywhere(client) -> None:
    assert client.get("/testbench").status_code == 200
    assert client.get("/worklist").status_code == 200
    assert client.get("/tools/c-echo").status_code == 200
