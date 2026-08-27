from __future__ import annotations

import socket
import time

from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.models import LocalAE, RemoteNode, VirtualAE
from app.mwl_scp import WorklistSCP
from app.tools.find_advanced import CFindAdvancedTool, retrieve_storage_gate_message
from test_sr import make_radiology_sr
from app.tools.find_keys import (
    KEYS,
    LEVEL_PARENTS,
    apply_key,
    build_identifier,
    key_unlocked,
    keys_for_level,
    missing_parents,
    normalize_da,
    options_from_form,
    options_from_payload,
    records_to_csv,
    validate_query,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_scp(ae_title: str, port: int, handlers):
    scp = AE(ae_title=ae_title)
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    return scp.start_server(("127.0.0.1", port), block=False, evt_handlers=handlers)


def test_catalog_covers_hierarchical_study_root_keys() -> None:
    study = {key.keyword for key in keys_for_level("STUDY")}
    series = {key.keyword for key in keys_for_level("SERIES")}
    image = {key.keyword for key in keys_for_level("IMAGE")}
    assert {"PatientName", "PatientID", "StudyDate", "StudyInstanceUID", "ModalitiesInStudy"} <= study
    assert {"StudyInstanceUID", "SeriesInstanceUID", "Modality", "BodyPartExamined"} <= series
    assert {"StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "InstanceNumber"} <= image
    assert "BodyPartExamined" not in study
    assert "SOPInstanceUID" not in series
    assert LEVEL_PARENTS["SERIES"] == ("StudyInstanceUID",)
    assert LEVEL_PARENTS["IMAGE"] == ("StudyInstanceUID", "SeriesInstanceUID")
    assert len({key.keyword for key in KEYS}) == len(KEYS)
    vue_status = next(key for key in KEYS if key.keyword == "TamarStudyStatus")
    assert vue_status.private_creator == "ELSCINT1"
    assert vue_status.default_return is False
    assert vue_status.group == "vue-study"
    assert "TamarStudyStatus" not in {key.keyword for key in keys_for_level("STUDY") if key.default_return}
    assign = next(key for key in KEYS if key.keyword == "TamarAssignToDoctor")
    assert assign.vr == "SH"
    assert "Confirmed matching key" in assign.hint
    assert assign.placeholder == "user@site"
    study_date = next(key for key in KEYS if key.keyword == "StudyDate")
    assert study_date.match_required is True
    assert study_date.levels == ("STUDY",)
    modality = next(key for key in KEYS if key.keyword == "Modality")
    assert "Structured Report" in modality.hint


def test_series_and_image_keys_stay_locked_without_parent_uids() -> None:
    series_modality = next(key for key in KEYS if key.keyword == "Modality")
    assert key_unlocked(series_modality, {})[0] is False
    assert key_unlocked(series_modality, {"StudyInstanceUID": "1.2.3"})[0] is True
    repetition = next(key for key in KEYS if key.keyword == "RepetitionTime")
    assert key_unlocked(repetition, {"StudyInstanceUID": "1.2.3", "Modality": "CT"})[0] is False
    assert key_unlocked(repetition, {"StudyInstanceUID": "1.2.3", "Modality": "MR"})[0] is True
    instance = next(key for key in KEYS if key.keyword == "InstanceNumber")
    assert key_unlocked(instance, {"StudyInstanceUID": "1.2.3"})[0] is False
    assert key_unlocked(instance, {"StudyInstanceUID": "1.2.3", "SeriesInstanceUID": "1.2.3.4"})[0] is True


def test_validate_query_requires_parent_unique_keys() -> None:
    assert validate_query("STUDY", {}) == "Study Date is required."
    assert validate_query("STUDY", {"StudyDate": "20260826"}) is None
    assert missing_parents("SERIES", {}) == ["StudyInstanceUID"]
    assert "Study Instance UID" in (validate_query("SERIES", {}) or "")
    assert validate_query("SERIES", {"StudyInstanceUID": "1.2"}) is None
    assert missing_parents("IMAGE", {"StudyInstanceUID": "1.2"}) == ["SeriesInstanceUID"]
    assert validate_query("IMAGE", {"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.3"}) is None


def test_html_dates_become_dicom_da() -> None:
    assert normalize_da("2026-08-26") == "20260826"
    assert normalize_da("2026-01-01 - 2026-12-31") == "20260101-20261231"
    assert normalize_da("20260101-20261231") == "20260101-20261231"


def test_options_and_identifier_include_return_and_match_keys() -> None:
    form = {
        "level": "SERIES",
        "include": ["StudyInstanceUID", "Modality", "SeriesDescription"],
        "key_StudyInstanceUID": "1.2.3",
        "key_Modality": "ct",
        "key_SeriesDescription": "",
    }
    options = options_from_form(form)
    assert options["level"] == "SERIES"
    assert options["values"]["Modality"] == "ct"
    identifier = build_identifier(options["level"], options["values"], options["return_keys"])
    assert identifier.QueryRetrieveLevel == "SERIES"
    assert str(identifier.StudyInstanceUID) == "1.2.3"
    assert str(identifier.Modality) == "CT"
    assert identifier.SeriesDescription is None or str(identifier.SeriesDescription) == ""
    payload = options_from_payload(
        {
            "level": "IMAGE",
            "study_instance_uid": "1.2",
            "series_instance_uid": "1.2.3",
            "return_keys": ["SOPInstanceUID", "InstanceNumber"],
        }
    )
    assert payload["values"]["StudyInstanceUID"] == "1.2"
    assert payload["values"]["SeriesInstanceUID"] == "1.2.3"


def test_records_to_csv_quotes_commas() -> None:
    csv_text = records_to_csv(
        [{"PatientName": "DOE, JANE", "PatientID": "1001"}],
        ["PatientName", "PatientID"],
    )
    assert csv_text.splitlines()[0] == "Patient Name,Patient ID"
    assert '"DOE, JANE"' in csv_text


def test_vue_private_keys_are_off_by_default_and_round_trip() -> None:
    from app.tools.find_keys import default_return_keys, record_from_dataset

    assert "TamarStudyStatus" not in default_return_keys("STUDY")
    assert not any("Grid Token" in key.label for key in KEYS)
    identifier = build_identifier(
        "STUDY",
        {"TamarStudyStatus": "UNREAD", "PatientID": "1001"},
        ["PatientID", "TamarStudyStatus"],
    )
    assert str(identifier[0x07A1, 0x0010].value).strip() == "ELSCINT1"
    block = identifier.private_block(0x07A1, "ELSCINT1")
    assert str(block[0x2A].value).strip() == "UNREAD"
    ds = Dataset()
    apply_key(ds, "TamarStudyStatus", "UNREAD")
    apply_key(ds, "TamarNumberOfImagesInSeries", "238")
    apply_key(ds, "TamarStudyRvu", "2")
    row = record_from_dataset(
        ds, ["TamarStudyStatus", "TamarNumberOfImagesInSeries", "TamarStudyRvu"]
    )
    assert row["TamarStudyStatus"] == "UNREAD"
    assert row["TamarNumberOfImagesInSeries"] == "238"
    assert row["TamarStudyRvu"].startswith("2")


def test_c_find_advanced_refuses_series_without_study_uid() -> None:
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=9)
    result = CFindAdvancedTool().run(LocalAE(timeout_seconds=2), remote, {"level": "SERIES"})
    assert result.ok is False
    assert "Study Instance UID" in result.summary
    assert result.steps == []


def test_c_find_advanced_study_series_and_image_against_scp() -> None:
    port = _free_port()
    seen: list[str] = []

    def handle_find(event):
        identifier = event.identifier
        level = str(getattr(identifier, "QueryRetrieveLevel", ""))
        seen.append(level)
        ds = Dataset()
        ds.QueryRetrieveLevel = level
        ds.StudyInstanceUID = "1.2.3"
        if level == "STUDY":
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "1001"
            ds.StudyDescription = "CT CHEST"
            ds.ModalitiesInStudy = "CT"
        elif level == "SERIES":
            ds.SeriesInstanceUID = "1.2.3.4"
            ds.Modality = "CT"
            ds.BodyPartExamined = "CHEST"
            ds.SeriesDescription = "AXIAL"
        else:
            ds.SeriesInstanceUID = "1.2.3.4"
            ds.SOPInstanceUID = "1.2.3.4.5"
            ds.InstanceNumber = "12"
            ds.Rows = 512
            ds.Columns = 512
        yield 0xFF00, ds
        yield 0x0000, None

    server = _start_scp("QR_SCP", port, [(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        local = LocalAE(timeout_seconds=5)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        tool = CFindAdvancedTool()
        study = tool.run(
            local,
            remote,
            {"level": "STUDY", "values": {"PatientID": "1001", "StudyDate": "20260826"}},
        )
        assert study.ok, study.summary
        assert study.records[0]["PatientID"] == "1001"
        assert study.records[0]["StudyInstanceUID"] == "1.2.3"
        series = tool.run(
            local,
            remote,
            {
                "level": "SERIES",
                "values": {"StudyInstanceUID": "1.2.3", "BodyPartExamined": "CHEST"},
            },
        )
        assert series.ok, series.summary
        assert series.records[0]["SeriesInstanceUID"] == "1.2.3.4"
        assert series.records[0]["BodyPartExamined"] == "CHEST"
        image = tool.run(
            local,
            remote,
            {
                "level": "IMAGE",
                "values": {
                    "StudyInstanceUID": "1.2.3",
                    "SeriesInstanceUID": "1.2.3.4",
                },
            },
        )
        assert image.ok, image.summary
        assert image.records[0]["SOPInstanceUID"] == "1.2.3.4.5"
        assert image.records[0]["InstanceNumber"] == "12"
        assert seen == ["STUDY", "SERIES", "IMAGE"]
    finally:
        server.shutdown()


def test_c_find_advanced_page_and_api(client, store) -> None:
    port = _free_port()

    def handle_find(event):
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientName = "DOE^JANE"
        ds.PatientID = "1001"
        ds.StudyInstanceUID = "1.2.3"
        ds.StudyDescription = "CT CHEST"
        yield 0xFF00, ds
        yield 0x0000, None

    server = _start_scp("QR_SCP", port, [(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)
        page = client.get("/vue/")
        assert page.status_code == 200
        assert b"Vue PACS Database Analytics" in page.content
        assert b'data-find-advanced' in page.content
        assert b'value="STUDY"' in page.content
        assert b'value="SERIES"' in page.content
        assert b'value="IMAGE"' in page.content
        assert b"Body Part Examined" in page.content
        assert b"SOP Instance UID" in page.content
        assert b"find-key-list" in page.content
        assert b"find-tree" in page.content
        assert b"find-workspace" in page.content
        assert b"find-key-grid" not in page.content
        assert b'data-match-required="1"' in page.content
        assert b'data-find-stop' in page.content
        assert b'data-find-select="sr"' in page.content
        assert b"ELSCINT1" in page.content
        assert b"Tamar Study Status" in page.content
        assert b'data-find-copy' not in page.content
        missing_date = client.post(
            "/tools/c-find-advanced/run",
            data={
                "remote_id": remote.id,
                "level": "STUDY",
                "include": ["PatientName", "PatientID"],
                "key_PatientID": "1001",
            },
            headers={"HX-Request": "true"},
        )
        assert missing_date.status_code == 200
        assert b"Study Date is required" in missing_date.content
        form = client.post(
            "/tools/c-find-advanced/run",
            data={
                "remote_id": remote.id,
                "level": "STUDY",
                "include": ["PatientName", "PatientID", "StudyInstanceUID", "StudyDescription"],
                "key_PatientID": "1001",
                "key_StudyDate": "20260826",
            },
            headers={"HX-Request": "true"},
        )
        assert form.status_code == 200
        assert b"DOE^JANE" in form.content
        assert b"data-find-copy" in form.content
        assert b"List SR reports" in form.content
        assert b"Download CSV" in form.content
        assert b"Download JSON" in form.content
        assert b"<textarea hidden data-find-export>" in form.content
        assert b'<script type="application/json" data-find-export>' not in form.content
        assert b"<summary>Timing</summary>" in form.content
        assert b"<summary>Association</summary>" in form.content
        api = client.post(
            "/api/tools/c-find-advanced/run",
            json={
                "remote_id": remote.id,
                "options": {"level": "STUDY", "values": {"PatientID": "1001", "StudyDate": "20260826"}},
            },
        )
        assert api.status_code == 200
        body = api.json()
        assert body["ok"] is True
        assert body["records"][0]["PatientID"] == "1001"
        missing_api = client.post(
            "/api/tools/c-find-advanced/run",
            json={"remote_id": remote.id, "options": {"level": "STUDY", "values": {"PatientID": "1001"}}},
        )
        assert missing_api.status_code == 200
        assert missing_api.json()["ok"] is False
        assert "Study Date is required" in missing_api.json()["summary"]
        blocked = client.post(
            "/api/tools/c-find-advanced/run",
            json={"remote_id": remote.id, "options": {"level": "SERIES"}},
        )
        assert blocked.status_code == 200
        assert blocked.json()["ok"] is False
        assert "Study Instance UID" in blocked.json()["summary"]
    finally:
        server.shutdown()


def test_empty_numeric_return_key_is_zero_length() -> None:
    ds = Dataset()
    apply_key(ds, "Rows", "")
    assert "Rows" in ds
    assert ds.Rows is None or str(ds.Rows) == ""


def test_list_sr_reports_from_study_results() -> None:
    port = _free_port()

    def handle_find(event):
        identifier = event.identifier
        level = str(getattr(identifier, "QueryRetrieveLevel", ""))
        modality = str(getattr(identifier, "Modality", "") or "").upper()
        ds = Dataset()
        ds.QueryRetrieveLevel = level
        ds.StudyInstanceUID = str(getattr(identifier, "StudyInstanceUID", "") or "1.2.3")
        if level == "SERIES" and modality == "SR":
            ds.SeriesInstanceUID = "1.2.3.99"
            ds.Modality = "SR"
            ds.SeriesNumber = "999"
            ds.SeriesDescription = "Report"
            ds.NumberOfSeriesRelatedInstances = "1"
        elif level == "SERIES":
            ds.SeriesInstanceUID = "1.2.3.4"
            ds.Modality = "CT"
        else:
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "1001"
            ds.StudyDate = "20260826"
            ds.AccessionNumber = "ACC1"
            ds.StudyDescription = "CT CHEST"
            ds.ModalitiesInStudy = "CT"
        yield 0xFF00, ds
        yield 0x0000, None

    server = _start_scp("QR_SCP", port, [(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        local = LocalAE(timeout_seconds=5)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        result = CFindAdvancedTool().run(
            local,
            remote,
            {
                "follow": "sr_series",
                "studies": [
                    {
                        "StudyInstanceUID": "1.2.3",
                        "PatientName": "DOE^JANE",
                        "PatientID": "1001",
                        "StudyDate": "20260826",
                        "AccessionNumber": "ACC1",
                        "StudyDescription": "CT CHEST",
                        "ModalitiesInStudy": "CT",
                    }
                ],
            },
        )
        assert result.ok, result.summary
        assert result.records[0]["Modality"] == "SR"
        assert result.records[0]["SeriesInstanceUID"] == "1.2.3.99"
        assert result.records[0]["PatientName"] == "DOE^JANE"
        assert result.records[0]["StudyDate"] == "20260826"
        assert result.steps[1].details.get("kind") == "sr_series"
    finally:
        server.shutdown()


def test_list_sr_reports_via_form(client, store) -> None:
    port = _free_port()

    def handle_find(event):
        identifier = event.identifier
        level = str(getattr(identifier, "QueryRetrieveLevel", ""))
        modality = str(getattr(identifier, "Modality", "") or "").upper()
        ds = Dataset()
        ds.QueryRetrieveLevel = level
        ds.StudyInstanceUID = "1.2.3"
        if level == "SERIES" and modality == "SR":
            ds.SeriesInstanceUID = "1.2.3.99"
            ds.Modality = "SR"
            ds.SeriesDescription = "Report"
        else:
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "1001"
            ds.StudyDate = "20260826"
        yield 0xFF00, ds
        yield 0x0000, None

    server = _start_scp("QR_SCP", port, [(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        store.add_remote(remote)
        listed = client.post(
            "/tools/c-find-advanced/run",
            data={
                "remote_id": remote.id,
                "level": "STUDY",
                "follow": "sr_series",
                "studies_json": '[{"StudyInstanceUID":"1.2.3","PatientName":"DOE^JANE","StudyDate":"20260826"}]',
            },
            headers={"HX-Request": "true"},
        )
        assert listed.status_code == 200
        assert b"Structured Report series" in listed.content
        assert b"1.2.3.99" in listed.content
        assert b"Retrieve report text" in listed.content
        assert b"DOE^JANE" in listed.content
    finally:
        server.shutdown()


def test_retrieve_sr_requires_storage_scp() -> None:
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=9)
    result = CFindAdvancedTool().run(
        LocalAE(ae_title="MICROdicom", timeout_seconds=2),
        remote,
        {
            "follow": "retrieve_sr",
            "_listen_ae": "DICOMM",
            "studies": [{"StudyInstanceUID": "1.2.3", "SeriesInstanceUID": "1.2.3.99", "Modality": "SR"}],
        },
    )
    assert result.ok is False
    assert "Accept C-STORE is off" in result.summary
    assert "DICOMM" in result.summary
    assert "11112" in result.summary
    assert "MICROdicom" in result.summary
    assert "calling AE" in result.summary
    gate = retrieve_storage_gate_message(
        dest_ae="DICOMM",
        port=11112,
        calling_ae="MICROdicom",
        enabled=False,
        running=False,
    )
    assert gate is not None
    assert "not to a viewer" in gate


def test_retrieve_sr_form_links_local_ae(client, remote, store) -> None:
    identity = VirtualAE(name="MicroDicom", ae_title="MicroDicom")
    store.add_identity(identity)
    html = client.post(
        "/vue/tools/c-find-advanced/run",
        data={
            "remote_id": remote.id,
            "identity_id": identity.id,
            "follow": "retrieve_sr",
            "studies_json": '[{"StudyInstanceUID":"1.2.3","SeriesInstanceUID":"1.2.3.99","Modality":"SR"}]',
        },
        headers={"HX-Request": "true"},
    )
    assert html.status_code == 200
    assert b"Accept C-STORE is off" in html.content
    assert b"DICOMM" in html.content
    assert b"MicroDicom" in html.content
    assert b"Open Local DICOM AE" in html.content
    assert b'href="/vue/config/local"' in html.content


def test_retrieve_sr_parses_content_sequence(store) -> None:
    storage_port = _free_port()
    move_port = _free_port()
    report = make_radiology_sr()

    def handle_move(event):
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(report.SOPClassUID))]}
        yield 1
        yield 0xFF00, report

    move_ae = AE(ae_title="QR_SCP")
    move_ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    move_server = move_ae.start_server(
        ("127.0.0.1", move_port),
        block=False,
        evt_handlers=[(evt.EVT_C_MOVE, handle_move)],
    )
    store.save_local(
        LocalAE(
            ae_title="DICOMM",
            host="127.0.0.1",
            port=storage_port,
            timeout_seconds=5,
            storage_scp_enabled=True,
        )
    )
    scp = WorklistSCP(store)
    scp.start()
    assert scp.running, scp.last_error
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=move_port)
        result = CFindAdvancedTool().run(
            store.load().local,
            remote,
            {
                "follow": "retrieve_sr",
                "studies": [
                    {
                        "StudyInstanceUID": "1.2.3",
                        "SeriesInstanceUID": "1.2.3.99",
                        "Modality": "SR",
                        "PatientName": "DOE^JANE",
                    }
                ],
                "_listen_ae": "DICOMM",
                "_storage_enabled": True,
                "_storage_running": True,
            },
        )
        assert result.ok, result.summary
        assert result.records[0]["DocumentTitle"] == "Radiology Report"
        assert result.records[0]["Findings"] == "No acute osseous abnormality."
        assert result.records[0]["Impression"] == "Normal CT chest."
        assert "No acute osseous abnormality." in result.records[0]["sr_text"]
        names = [item["name"] for item in result.records[0]["sr_items"]]
        assert "Finding" in names
        assert result.steps[1].details.get("kind") == "sr_content"
    finally:
        scp.stop()
        move_server.shutdown()


def test_retrieve_sr_continues_after_a_failed_series(store) -> None:
    storage_port = _free_port()
    move_port = _free_port()
    first = make_radiology_sr(series_uid="1.2.3.99")
    second = make_radiology_sr(series_uid="1.2.3.100")
    calls = {"n": 0}

    def handle_move(event):
        calls["n"] += 1
        identifier = event.identifier
        series = str(getattr(identifier, "SeriesInstanceUID", "") or "")
        if series.endswith(".100"):
            yield 0xA702
            return
        report = first if series.endswith(".99") else second
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(report.SOPClassUID))]}
        yield 1
        yield 0xFF00, report

    move_ae = AE(ae_title="QR_SCP")
    move_ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    move_server = move_ae.start_server(
        ("127.0.0.1", move_port),
        block=False,
        evt_handlers=[(evt.EVT_C_MOVE, handle_move)],
    )
    store.save_local(
        LocalAE(
            ae_title="DICOMM",
            host="127.0.0.1",
            port=storage_port,
            timeout_seconds=5,
            storage_scp_enabled=True,
        )
    )
    scp = WorklistSCP(store)
    scp.start()
    assert scp.running, scp.last_error
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=move_port)
        result = CFindAdvancedTool().run(
            store.load().local,
            remote,
            {
                "follow": "retrieve_sr",
                "studies": [
                    {
                        "StudyInstanceUID": "1.2.3",
                        "SeriesInstanceUID": "1.2.3.99",
                        "Modality": "SR",
                    },
                    {
                        "StudyInstanceUID": "1.2.3",
                        "SeriesInstanceUID": "1.2.3.100",
                        "Modality": "SR",
                    },
                    {
                        "StudyInstanceUID": "1.2.3",
                        "SeriesInstanceUID": "1.2.3.101",
                        "Modality": "SR",
                    },
                ],
                "_listen_ae": "DICOMM",
                "_storage_enabled": True,
                "_storage_running": True,
            },
        )
        assert result.ok, result.summary
        assert len(result.records) >= 1
        assert result.records[0]["SeriesInstanceUID"] == "1.2.3.99"
        assert result.steps[1].details.get("failed_moves") == 1
        assert "series failed" in result.summary
    finally:
        scp.stop()
        move_server.shutdown()


def test_retrieve_sr_html_shows_findings_cards(client, store, app) -> None:
    storage_port = _free_port()
    move_port = _free_port()
    report = make_radiology_sr()

    def handle_move(event):
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(report.SOPClassUID))]}
        yield 1
        yield 0xFF00, report

    move_ae = AE(ae_title="QR_SCP")
    move_ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    move_server = move_ae.start_server(
        ("127.0.0.1", move_port),
        block=False,
        evt_handlers=[(evt.EVT_C_MOVE, handle_move)],
    )
    store.save_local(
        LocalAE(
            ae_title="DICOMM",
            host="127.0.0.1",
            port=storage_port,
            timeout_seconds=5,
            storage_scp_enabled=True,
        )
    )
    app.state.mwl_scp.restart()
    assert app.state.mwl_scp.running, app.state.mwl_scp.last_error
    try:
        time.sleep(0.05)
        remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=move_port)
        store.add_remote(remote)
        html = client.post(
            "/vue/tools/c-find-advanced/run",
            data={
                "remote_id": remote.id,
                "follow": "retrieve_sr",
                "studies_json": (
                    '[{"StudyInstanceUID":"1.2.3","SeriesInstanceUID":"1.2.3.99",'
                    '"Modality":"SR","PatientName":"DOE^JANE"}]'
                ),
            },
            headers={"HX-Request": "true"},
        )
        assert html.status_code == 200
        assert b"Structured Report contents" in html.content
        assert b"find-sr-card" in html.content
        assert b"<h4>Findings</h4>" in html.content
        assert b"<h4>Impression</h4>" in html.content
        assert b"No acute osseous abnormality." in html.content
        assert b"Content Sequence" in html.content
        assert b"<textarea hidden data-find-export>" in html.content
        assert b'data-find-export-hint' in html.content
    finally:
        move_server.shutdown()
