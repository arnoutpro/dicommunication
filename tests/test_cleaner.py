from __future__ import annotations

import socket

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage
from pynetdicom import AE, evt
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.main import create_app
from app.models import LocalAE, RemoteNode
from app.shell import (
    CLEANER_PREFIX,
    PROFILE_CLEANER,
    PRODUCT_NAMES,
    SHELL_CLEANER,
    cleaner_path_allowed,
    is_cleaner_profile,
    is_cleaner_public_path,
    profile_start_path,
    profile_window_title,
    public_href,
    tools_exclude,
    tools_for_shell,
)
from app.store import ConfigStore
from app.tools.dicom_preview import MAX_PREVIEW_DIM, PreviewError, render_preview_png
from app.tools.redact_engine import RedactionError, parse_region, redact_pixels


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_instance(
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    *,
    rows: int = 4,
    cols: int = 4,
    frames: int = 1,
    samples_per_pixel: int = 1,
) -> Dataset:
    ds = Dataset()
    ds.PatientName = "DOE^JANE"
    ds.PatientID = "12345"
    ds.PatientBirthDate = "19800101"
    ds.AccessionNumber = "ACC1"
    ds.StudyDescription = "US ABDOMEN"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.Modality = "US"
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = samples_per_pixel
    if samples_per_pixel > 1:
        ds.PhotometricInterpretation = "RGB"
        ds.PlanarConfiguration = 0
    else:
        ds.PhotometricInterpretation = "MONOCHROME2"
    if frames > 1:
        ds.NumberOfFrames = frames
    shape = [frames] if frames > 1 else []
    shape += [rows, cols]
    if samples_per_pixel > 1:
        shape += [samples_per_pixel]
    pixels = (np.arange(int(np.prod(shape)), dtype=np.uint8) % 251 + 1).reshape(shape)
    ds.PixelData = pixels.tobytes()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    return ds


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


def test_cleaner_shell_constants() -> None:
    assert PRODUCT_NAMES[SHELL_CLEANER] == "Dicom Cleaner"
    assert is_cleaner_profile(PROFILE_CLEANER)
    assert not is_cleaner_profile("dicommunication")
    assert profile_start_path(PROFILE_CLEANER) == "/cleaner/"
    assert profile_window_title(PROFILE_CLEANER) == "Dicom Cleaner"


def test_cleaner_path_helpers() -> None:
    assert is_cleaner_public_path("/cleaner")
    assert is_cleaner_public_path("/cleaner/config/remotes")
    assert not is_cleaner_public_path("/vue")
    assert public_href("/", shell=SHELL_CLEANER) == "/cleaner/"
    assert public_href("/config/remotes", shell=SHELL_CLEANER) == "/cleaner/config/remotes"
    assert public_href("/static/css/app.css", shell=SHELL_CLEANER) == "/static/css/app.css"
    assert cleaner_path_allowed("/")
    assert cleaner_path_allowed("/tools/dicom-cleaner/run")
    assert cleaner_path_allowed("/help")
    assert not cleaner_path_allowed("/testbench")
    assert not cleaner_path_allowed("/tools/c-echo")


def test_cleaner_tool_hidden_from_other_shells() -> None:
    from app.shell import SHELL_DICOMM, SHELL_VUE

    assert "dicom-cleaner" not in tools_exclude(SHELL_CLEANER)
    assert "dicom-cleaner" in tools_exclude(SHELL_DICOMM)
    assert "dicom-cleaner" in tools_exclude(SHELL_VUE)
    ids = {tool.id for tool in tools_for_shell(SHELL_CLEANER)}
    assert ids == {"dicom-cleaner"}


def test_cleaner_shell_sidebar_has_no_test_tools(client) -> None:
    response = client.get("/cleaner/")
    assert response.status_code == 200
    body = response.text
    assert "Testbench" not in body
    assert "C-ECHO board" not in body
    assert "Worklist" not in body
    assert "Configured nodes" in body
    assert "Dicom Cleaner" in body


# ---------------------------------------------------------------------------
# Redaction engine
# ---------------------------------------------------------------------------


def test_parse_region_defaults_and_clamps() -> None:
    region = parse_region("", "", "", "")
    assert (region.x, region.y, region.width, region.height) == (0, 0, 0, 100)
    region = parse_region("-5", "bogus", "10", "0")
    assert (region.x, region.y, region.width, region.height) == (0, 0, 10, 1)


def test_redact_pixels_requires_pixel_data() -> None:
    ds = Dataset()
    with pytest.raises(RedactionError):
        redact_pixels(ds, parse_region(0, 0, 0, 2))


def test_redact_pixels_gray_2d() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.1", rows=6, cols=4, samples_per_pixel=1)
    region = parse_region(0, 0, 0, 2)
    redact_pixels(ds, region)
    array = ds.pixel_array
    assert array.ndim == 2
    assert (array[0:2, :] == 0).all()
    assert not (array[2:, :] == 0).all()
    assert ds.BurnedInAnnotation == "NO"


def test_redact_pixels_color_2d() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.2", rows=6, cols=4, samples_per_pixel=3)
    region = parse_region(0, 0, 0, 2)
    redact_pixels(ds, region)
    array = ds.pixel_array
    assert array.ndim == 3
    assert (array[0:2, :, :] == 0).all()
    assert not (array[2:, :, :] == 0).all()
    assert ds.PlanarConfiguration == 0


def test_redact_pixels_gray_multiframe() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.3", rows=6, cols=4, frames=3, samples_per_pixel=1)
    region = parse_region(0, 0, 0, 2)
    redact_pixels(ds, region)
    array = ds.pixel_array
    assert array.ndim == 3
    assert (array[:, 0:2, :] == 0).all()
    assert not (array[:, 2:, :] == 0).all()


def test_redact_pixels_color_multiframe() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.4", rows=6, cols=4, frames=2, samples_per_pixel=3)
    region = parse_region(0, 0, 0, 2)
    redact_pixels(ds, region)
    array = ds.pixel_array
    assert array.ndim == 4
    assert (array[:, 0:2, :, :] == 0).all()
    assert not (array[:, 2:, :, :] == 0).all()


def test_redact_pixels_region_confined_to_columns() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.5", rows=6, cols=6, samples_per_pixel=1)
    region = parse_region(2, 1, 2, 2)
    redact_pixels(ds, region)
    array = ds.pixel_array
    assert (array[1:3, 2:4] == 0).all()
    assert not (array[0, :] == 0).all()
    assert not (array[:, 0] == 0).all()


# ---------------------------------------------------------------------------
# End-to-end: query + run against real (fake) SCPs, through the HTTP layer
# ---------------------------------------------------------------------------


def _start_find_move_store_scp(find_port: int, storage_port: int, study_uid: str, instance: Dataset):
    received: list[Dataset] = []

    def handle_find(event):
        identifier = event.identifier
        if str(getattr(identifier, "QueryRetrieveLevel", "STUDY")) == "STUDY":
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "12345"
            ds.StudyDate = "20260101"
            ds.AccessionNumber = "ACC1"
            ds.StudyDescription = "US ABDOMEN"
            ds.ModalitiesInStudy = "US"
            ds.StudyInstanceUID = study_uid
            yield 0xFF00, ds
        yield 0x0000, None

    def handle_move(event):
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instance.SOPClassUID))]}
        yield 1
        yield 0xFF00, instance

    def handle_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        received.append(ds)
        return 0x0000

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    ae.add_supported_context(str(instance.SOPClassUID))
    server = ae.start_server(
        ("127.0.0.1", find_port),
        block=False,
        evt_handlers=[
            (evt.EVT_C_FIND, handle_find),
            (evt.EVT_C_MOVE, handle_move),
            (evt.EVT_C_STORE, handle_store),
        ],
    )
    return server, received


def test_cleaner_query_then_run_redacts_and_sends_back(tmp_path) -> None:
    find_port = _free_port()
    local_port = _free_port()
    study_uid = "1.2.826.0.1.3680043.8.498.31223344"
    series_uid = "1.2.826.0.1.3680043.8.498.31223345"
    sop_uid = "1.2.826.0.1.3680043.8.498.31223346"
    instance = _make_instance(study_uid, series_uid, sop_uid, rows=8, cols=6, samples_per_pixel=1)
    server, received = _start_find_move_store_scp(find_port, local_port, study_uid, instance)

    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    app = create_app(store)

    try:
        with TestClient(app) as client:
            query = client.post(
                f"{CLEANER_PREFIX}/tools/dicom-cleaner/run",
                data={"action": "query", "remote_id": remote.id, "study_date": "2026-01-01"},
            )
            assert query.status_code == 200
            assert "DOE^JANE" in query.text

            run = client.post(
                f"{CLEANER_PREFIX}/tools/dicom-cleaner/run",
                data={
                    "action": "run",
                    "remote_id": remote.id,
                    "level": "STUDY",
                    "study_uid": [study_uid],
                    "region_x": "0",
                    "region_y": "0",
                    "region_width": "0",
                    "region_height": "3",
                    "uid_mode": "new",
                    "destination_remote_id": remote.id,
                },
            )
            assert run.status_code == 200
            assert "Redacted and sent" in run.text, run.text

            assert len(received) == 1
            cleaned = received[0]
            assert str(cleaned.SOPInstanceUID) != sop_uid
            assert cleaned.BurnedInAnnotation == "NO"
            array = cleaned.pixel_array
            assert (array[0:3, :] == 0).all()
            assert not (array[3:, :] == 0).all()
    finally:
        server.shutdown()


def test_cleaner_run_same_uid_mode_keeps_original_sop_instance_uid(tmp_path) -> None:
    find_port = _free_port()
    local_port = _free_port()
    study_uid = "1.2.826.0.1.3680043.8.498.41223344"
    series_uid = "1.2.826.0.1.3680043.8.498.41223345"
    sop_uid = "1.2.826.0.1.3680043.8.498.41223346"
    instance = _make_instance(study_uid, series_uid, sop_uid, rows=8, cols=6, samples_per_pixel=1)
    server, received = _start_find_move_store_scp(find_port, local_port, study_uid, instance)

    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    app = create_app(store)

    try:
        with TestClient(app) as client:
            run = client.post(
                f"{CLEANER_PREFIX}/tools/dicom-cleaner/run",
                data={
                    "action": "run",
                    "remote_id": remote.id,
                    "level": "STUDY",
                    "study_uid": [study_uid],
                    "region_x": "0",
                    "region_y": "0",
                    "region_width": "0",
                    "region_height": "3",
                    "uid_mode": "same",
                    "destination_remote_id": remote.id,
                },
            )
            assert run.status_code == 200
            assert "Redacted and sent" in run.text, run.text
            assert len(received) == 1
            assert str(received[0].SOPInstanceUID) == sop_uid
    finally:
        server.shutdown()


def test_cleaner_run_requires_study_selection() -> None:
    from app.tools.cleaner import CleanerTool

    tool = CleanerTool()
    local = LocalAE(ae_title="DICOMM")
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=104)

    result = tool.run(local, remote, {"action": "run", "study_uids": []})
    assert not result.ok
    assert "select" in result.summary.lower()


# ---------------------------------------------------------------------------
# Preview PNG rendering
# ---------------------------------------------------------------------------


def test_render_preview_png_requires_pixel_data() -> None:
    with pytest.raises(PreviewError):
        render_preview_png(Dataset())


def test_render_preview_png_gray_2d_matches_original_dims() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.20", rows=12, cols=8, samples_per_pixel=1)
    png_bytes, orig_rows, orig_cols, png_rows, png_cols = render_preview_png(ds)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert (orig_rows, orig_cols) == (12, 8)
    assert (png_rows, png_cols) == (12, 8)


def test_render_preview_png_color_2d() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.21", rows=10, cols=10, samples_per_pixel=3)
    png_bytes, orig_rows, orig_cols, png_rows, png_cols = render_preview_png(ds)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert (orig_rows, orig_cols) == (10, 10)
    assert (png_rows, png_cols) == (10, 10)


def test_render_preview_png_multiframe_uses_first_frame() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.22", rows=6, cols=6, frames=3, samples_per_pixel=1)
    png_bytes, orig_rows, orig_cols, png_rows, png_cols = render_preview_png(ds)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert (orig_rows, orig_cols) == (6, 6)
    assert (png_rows, png_cols) == (6, 6)


def test_render_preview_png_downscales_large_images() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.23", rows=2000, cols=1500, samples_per_pixel=1)
    _png_bytes, orig_rows, orig_cols, png_rows, png_cols = render_preview_png(ds)
    assert (orig_rows, orig_cols) == (2000, 1500)
    assert max(png_rows, png_cols) <= MAX_PREVIEW_DIM


# ---------------------------------------------------------------------------
# Preview routes: browse images, then load one as a PNG
# ---------------------------------------------------------------------------


def _start_browsable_scp(find_port: int, storage_port: int, study_uid: str, instance: Dataset):
    series_uid = str(instance.SeriesInstanceUID)
    sop_uid = str(instance.SOPInstanceUID)
    received: list[Dataset] = []

    def handle_find(event):
        identifier = event.identifier
        level = str(getattr(identifier, "QueryRetrieveLevel", "STUDY"))
        if level == "STUDY":
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.PatientName = "DOE^JANE"
            ds.PatientID = "12345"
            ds.StudyDate = "20260101"
            ds.AccessionNumber = "ACC1"
            ds.StudyDescription = "US ABDOMEN"
            ds.ModalitiesInStudy = "US"
            ds.StudyInstanceUID = study_uid
            yield 0xFF00, ds
        elif level == "SERIES":
            ds = Dataset()
            ds.QueryRetrieveLevel = "SERIES"
            ds.StudyInstanceUID = study_uid
            ds.SeriesInstanceUID = series_uid
            ds.Modality = "US"
            ds.SeriesNumber = "1"
            ds.SeriesDescription = "Test series"
            ds.NumberOfSeriesRelatedInstances = "1"
            yield 0xFF00, ds
        elif level == "IMAGE":
            ds = Dataset()
            ds.QueryRetrieveLevel = "IMAGE"
            ds.StudyInstanceUID = study_uid
            ds.SeriesInstanceUID = series_uid
            ds.SOPInstanceUID = sop_uid
            ds.InstanceNumber = "1"
            yield 0xFF00, ds
        yield 0x0000, None

    def handle_move(event):
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instance.SOPClassUID))]}
        yield 1
        yield 0xFF00, instance

    def handle_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        received.append(ds)
        return 0x0000

    ae = AE(ae_title="QR_SCP")
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    ae.add_supported_context(str(instance.SOPClassUID))
    server = ae.start_server(
        ("127.0.0.1", find_port),
        block=False,
        evt_handlers=[
            (evt.EVT_C_FIND, handle_find),
            (evt.EVT_C_MOVE, handle_move),
            (evt.EVT_C_STORE, handle_store),
        ],
    )
    return server, received


def test_cleaner_preview_images_requires_a_checked_study(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=_free_port(), storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=_free_port())
    store.add_remote(remote)
    app = create_app(store)
    with TestClient(app) as client:
        response = client.post(
            f"{CLEANER_PREFIX}/tools/dicom-cleaner/preview-images",
            data={"remote_id": remote.id},
        )
        assert response.status_code == 200
        assert "Check a study" in response.text


def test_cleaner_preview_browse_then_load_image(tmp_path) -> None:
    find_port = _free_port()
    local_port = _free_port()
    study_uid = "1.2.826.0.1.3680043.8.498.51223344"
    series_uid = "1.2.826.0.1.3680043.8.498.51223345"
    sop_uid = "1.2.826.0.1.3680043.8.498.51223346"
    instance = _make_instance(study_uid, series_uid, sop_uid, rows=8, cols=6, samples_per_pixel=1)
    server, _received = _start_browsable_scp(find_port, local_port, study_uid, instance)

    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    app = create_app(store)

    try:
        with TestClient(app) as client:
            images = client.post(
                f"{CLEANER_PREFIX}/tools/dicom-cleaner/preview-images",
                data={"remote_id": remote.id, "study_uid": [study_uid]},
            )
            assert images.status_code == 200
            assert f"{series_uid}|{sop_uid}" in images.text
            assert "Load image" in images.text

            preview = client.post(
                f"{CLEANER_PREFIX}/tools/dicom-cleaner/preview-image",
                data={
                    "remote_id": remote.id,
                    "study_uid": study_uid,
                    "image": f"{series_uid}|{sop_uid}",
                },
            )
            assert preview.status_code == 200
            assert "data-cleaner-preview-canvas" in preview.text
            assert 'data-orig-rows="8"' in preview.text
            assert 'data-orig-cols="6"' in preview.text
            assert "data:image/png;base64," in preview.text
    finally:
        server.shutdown()


def test_cleaner_preview_image_requires_a_picked_image(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=_free_port(), storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=_free_port())
    store.add_remote(remote)
    app = create_app(store)
    with TestClient(app) as client:
        response = client.post(
            f"{CLEANER_PREFIX}/tools/dicom-cleaner/preview-image",
            data={"remote_id": remote.id, "study_uid": "1.2.3"},
        )
        assert response.status_code == 200
        assert "Pick an image" in response.text


def test_retrieve_many_survives_association_dropped_before_first_move(monkeypatch) -> None:
    """Regression: a peer that accepts the association then drops it before
    the first C-MOVE goes out must fail gracefully, not crash the request —
    pynetdicom's send_c_move raises RuntimeError in exactly that case.
    """
    from app.tools import cleaner as cleaner_module

    class _FakeAssoc:
        is_established = True
        accepted_contexts = [object()]

        def send_c_move(self, *args, **kwargs):
            raise RuntimeError(
                "The association with a peer SCP must be established before sending a C-MOVE request"
            )

        def release(self):
            pass

        def abort(self):
            pass

    def fake_associate(local, remote, abstract_syntaxes, transfer_syntaxes=None):
        return object(), _FakeAssoc()

    monkeypatch.setattr(cleaner_module, "associate", fake_associate)

    local = LocalAE(ae_title="DICOMM")
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=104)
    datasets, error, _contexts = cleaner_module._retrieve_many(local, remote, [{"study_uid": "1.2.3"}], "DICOMM")
    assert datasets == []
    assert error is not None and "RuntimeError" in error
