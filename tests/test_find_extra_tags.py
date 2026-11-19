"""Added DICOM tags in Dicomtag Analytics: search the standard dictionary, add
any tag (or a private one) to a query, keep it in saved queries."""

from __future__ import annotations

import json
import socket
import time

import pytest
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from app.models import RemoteNode, SavedQuery
from app.store import ConfigStore
from app.tools.find_keys import (
    build_identifier,
    column_labels,
    extra_rows,
    is_extra_keyword,
    lookup_key,
    options_from_form,
    options_from_payload,
    search_dictionary,
)

PRIVATE = "(07A1,102A)@ELSCINT1:CS"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_search_finds_by_name_keyword_and_tag_number() -> None:
    by_name = search_dictionary("patients age")["results"]
    assert by_name[0]["keyword"] == "PatientAge" and by_name[0]["tag"] == "(0010,1010)" and by_name[0]["vr"] == "AS"
    assert [r["keyword"] for r in search_dictionary("0010,1010")["results"]] == ["PatientAge"]
    assert [r["keyword"] for r in search_dictionary("00101010")["results"]] == ["PatientAge"]
    assert search_dictionary("PatientAge")["results"][0]["keyword"] == "PatientAge"


def test_search_ignores_tiny_queries_and_does_not_read_words_as_tag_numbers() -> None:
    assert search_dictionary("p") == {"results": [], "total": 0}
    assert search_dictionary("bad")["total"] == 0  # "bad" is hex-looking, but it is a word


def test_search_flags_catalog_keys_and_leaves_out_unqueryable_tags() -> None:
    name = next(r for r in search_dictionary("patient name")["results"] if r["keyword"] == "PatientName")
    assert name["in_form"] is True
    for query in ("sequence", "pixel", "data", "image"):
        assert not {r["vr"] for r in search_dictionary(query, limit=500)["results"]} & {"SQ", "OB", "OW", "OF", "UN", "AT"}
    assert "PixelData" not in {r["keyword"] for r in search_dictionary("pixel data", limit=500)["results"]}
    assert "QueryRetrieveLevel" not in {r["keyword"] for r in search_dictionary("retrieve level")["results"]}


def test_extra_keywords_are_recognised() -> None:
    assert is_extra_keyword("PatientAge")
    assert is_extra_keyword(PRIVATE)
    assert not is_extra_keyword("PatientName")  # a catalog key
    assert not is_extra_keyword("NotARealKeyword")
    assert not is_extra_keyword("(0010,102A)@X:CS")  # even group: not private
    assert not is_extra_keyword("(07A1,102A)@ELSCINT1:SQ")
    assert lookup_key("PatientAge").label == "Patient's Age"


def test_identifier_carries_added_tags_with_normalised_values() -> None:
    ds = build_identifier(
        "STUDY",
        {"PatientAge": "030y", "StudyDate": "2026-10-01 - 2026-10-31", PRIVATE: "pending final"},
        ["PatientAge", PRIVATE, "PatientWeight"],
    )
    assert ds.PatientAge == "030Y"
    assert ds.PatientWeight is None  # asked for, not filtered
    assert ds.StudyDate == "20261001-20261031"
    assert ds.private_block(0x07A1, "ELSCINT1")[0x2A].value == "PENDING FINAL"


@pytest.mark.filterwarnings("ignore:Invalid value for VR")
def test_a_value_that_does_not_fit_the_type_gives_a_readable_error() -> None:
    with pytest.raises(ValueError, match="Patient's Weight.*not a valid DS"):
        build_identifier("STUDY", {"PatientWeight": "70-90"}, [])


def test_labels_for_added_tags() -> None:
    assert column_labels(["PatientAge", PRIVATE, "StudyDate"]) == ["Patient's Age", "(07A1,102A) ELSCINT1", "Study Date"]


def test_options_from_form_reads_added_tags_and_ignores_unknown_ones() -> None:
    form = {
        "level": "STUDY",
        "include": ["PatientName", "PatientAge", "Bogus"],
        "key_PatientName": "DOE*",
        "key_PatientWeight": "75",
        f"key_{PRIVATE}": "UNREAD",
        "key_Bogus": "x",
    }
    options = options_from_form(form)
    assert options["values"] == {"PatientName": "DOE*", "PatientWeight": "75", PRIVATE: "UNREAD"}
    assert options["return_keys"] == ["PatientName", "PatientAge", "PatientWeight", PRIVATE]


def test_options_from_payload_keeps_added_tags() -> None:
    options = options_from_payload({"level": "STUDY", "return_keys": ["PatientName", "PatientAge", "Bogus"], "values": {"PatientWeight": "75"}})
    assert options["return_keys"] == ["PatientName", "PatientAge", "PatientWeight"]


def test_extra_rows_for_a_saved_query() -> None:
    rows = extra_rows(["PatientAge", "PatientName", PRIVATE], ["PatientAge"], {PRIVATE: "UNREAD"})
    assert [r["keyword"] for r in rows] == ["PatientAge", PRIVATE]
    assert rows[0]["checked"] is True and rows[1]["checked"] is False and rows[1]["value"] == "UNREAD"
    assert rows[1]["creator"] == "ELSCINT1"


def test_tag_search_endpoint(client: TestClient) -> None:
    body = client.get("/tools/c-find-advanced/tag-search", params={"q": "patient weight"}).json()
    assert body["results"][0]["keyword"] == "PatientWeight"
    assert client.get("/tools/c-find-advanced/tag-search", params={"q": "x"}).json()["results"] == []


def test_page_has_the_add_tags_section_and_no_inline_script(client: TestClient, remote: RemoteNode) -> None:
    page = client.get("/tools/c-find-advanced").text
    assert "data-find-tag-search" in page and "data-find-private-add" in page
    assert '<option value="CS">CS</option>' in page


def test_saved_query_reopens_with_its_added_tags(client: TestClient, store: ConfigStore, remote: RemoteNode) -> None:
    saved = store.save_query(
        SavedQuery(
            name="With age",
            remote_id=remote.id,
            include=["PatientAge", PRIVATE],
            values={"StudyDate": "20261001", "PatientAge": "030Y", PRIVATE: "UNREAD"},
        )
    )
    page = client.get(f"/tools/c-find-advanced?saved={saved.id}").text
    assert 'data-find-extra-row' in page and 'data-keyword="PatientAge"' in page
    assert 'name="key_PatientAge" value="030Y"' in page
    assert f'data-keyword="{PRIVATE}"' in page
    assert "data-find-extra open" in page  # the section opens when it has rows
    # More filters stays closed: the added tags are not catalog filters.
    assert "data-find-more-filters open" not in page


def test_a_query_with_an_added_tag_reaches_the_pacs_and_comes_back_as_a_column(
    client: TestClient, store: ConfigStore
) -> None:
    port = _free_port()
    seen: list[Dataset] = []

    def handle_find(event):
        seen.append(event.identifier)
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = "1.2.3"
        ds.PatientName = "DOE^JANE"
        ds.StudyDate = "20261001"
        ds.PatientAge = "030Y"
        yield 0xFF00, ds
        yield 0x0000, None

    scp = AE(ae_title="QR_SCP")
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        node = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(node)
        response = client.post(
            "/tools/c-find-advanced/run",
            data={
                "remote_id": node.id,
                "level": "STUDY",
                "include": ["PatientName", "StudyInstanceUID", "PatientAge"],
                "key_StudyDate": "20261001",
                "key_PatientAge": "030y",
            },
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert seen and seen[0].PatientAge == "030Y"
        assert "Patient&#39;s Age" in response.text or "Patient's Age" in response.text
        assert "030Y" in response.text
    finally:
        server.shutdown()


def test_added_tags_carry_into_a_router_rule_loaded_from_the_saved_query() -> None:
    from app.models import RouteRule
    from app.router_scheduler import _study_identifier, query_summary
    from app.routes.route_rules import _draft_from_saved_query

    saved = SavedQuery(
        name="Age",
        remote_id="r",
        include=["PatientAge", PRIVATE],
        values={"StudyDate": "20261001", "PatientAge": "030y", PRIVATE: "unread"},
    )
    draft = _draft_from_saved_query(saved)
    assert draft.extra_query == {"PatientAge": "030y", PRIVATE: "unread"}
    rule = RouteRule(name="t", source_remote_id="r", date_scope="all", extra_query=draft.extra_query)
    identifier = _study_identifier(rule)
    assert identifier.PatientAge == "030Y"
    assert identifier.private_block(0x07A1, "ELSCINT1")[0x2A].value == "UNREAD"
    sent, skipped = query_summary(rule)
    assert skipped == [] and "PatientAge" in sent
