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
from app.store import ConfigStore
from app.tools.dicom_preview import MAX_PREVIEW_DIM, PreviewError, render_preview_png
from app.tools.redact_engine import RedactionError, parse_region, parse_text_stamp, redact_pixels


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
# Now reached at its plain path, alongside every other tool in the same
# window's navigation — see tests/test_single_app.py for the merged-app
# behavior shared with Dicomtag Analytics/Dicom Anonymizer/Dicom Router.
# ---------------------------------------------------------------------------


def test_cleaner_has_its_own_tab(client) -> None:
    response = client.get("/tools/dicom-cleaner")
    assert response.status_code == 200
    body = response.text
    assert "Dicom Cleaner" in body
    assert 'class="tab-item active"' in body
    # Testbench etc. live under the Dicommunication tab, not this one.
    assert "Testbench" not in body


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
# Text label
# ---------------------------------------------------------------------------


def _stamp(text="HELLO", **kw):
    args = dict(x=2, y=2, size=20, family="sans", bold="", color="white", background="none")
    args.update(kw)
    return parse_text_stamp(text, args["x"], args["y"], args["size"], args["family"], args["bold"],
                            args["color"], args["background"])


def test_parse_text_stamp_clamps_and_falls_back() -> None:
    stamp = parse_text_stamp("a\r\nb", "-4", "x", "9999", "comic", "on", "pink", "grey")
    assert stamp.text == "a\nb"
    assert (stamp.x, stamp.y) == (0, 0)
    assert stamp.size == 400
    assert (stamp.family, stamp.bold, stamp.color, stamp.background) == ("sans", True, "white", "none")
    assert parse_text_stamp("", 0, 0, 1, "", "", "", "").size == 6
    assert not parse_text_stamp("  \n ", 0, 0, 24, "sans", "", "white", "none").enabled


def test_text_only_draws_white_text_and_keeps_the_rest() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.20", rows=60, cols=200, samples_per_pixel=1)
    before = ds.pixel_array.copy()
    redact_pixels(ds, None, _stamp())
    array = ds.pixel_array
    assert array.max() == 255  # white ink
    assert (array[40:, :] == before[40:, :]).all()  # nothing below the text changed
    assert (array != before).any()
    assert ds.BurnedInAnnotation == "YES"


def test_text_after_redaction_sits_on_the_black_bar() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.21", rows=60, cols=200, samples_per_pixel=1)
    redact_pixels(ds, parse_region(0, 0, 0, 40), _stamp(color="yellow"))
    array = ds.pixel_array
    assert array[0:40].max() == 211  # yellow on gray is its brightness
    assert array[0:40].min() == 0  # the bar is still black around the letters
    assert ds.BurnedInAnnotation == "YES"


def test_text_background_box_and_bold_and_mono() -> None:
    plain = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.22", rows=60, cols=240, samples_per_pixel=1)
    boxed = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.23", rows=60, cols=240, samples_per_pixel=1)
    untouched = plain.pixel_array.copy()
    redact_pixels(plain, None, _stamp(color="black"))
    redact_pixels(boxed, None, _stamp(color="black", background="white"))
    assert (boxed.pixel_array == 255).sum() > (untouched == 255).sum() + 200  # the white box
    assert (plain.pixel_array == 255).sum() <= (untouched == 255).sum()  # black ink alone adds no white
    thin = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.24", rows=60, cols=240, samples_per_pixel=1)
    heavy = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.25", rows=60, cols=240, samples_per_pixel=1)
    redact_pixels(thin, None, _stamp(family="mono"))
    redact_pixels(heavy, None, _stamp(family="mono", bold="on"))
    assert int((heavy.pixel_array == 255).sum()) > int((thin.pixel_array == 255).sum())


def test_text_color_and_multiframe_and_multiline() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.26", rows=80, cols=200, frames=2, samples_per_pixel=3)
    redact_pixels(ds, None, _stamp("one\ntwo", color="red", background="black"))
    array = ds.pixel_array
    assert array.shape == (2, 80, 200, 3)
    for frame in array:
        assert ((frame[..., 0] == 255) & (frame[..., 1] < 100)).any()  # red ink
        assert (frame[..., 0] == 255).sum() == (array[0][..., 0] == 255).sum()  # same on every frame
    # two lines make a taller box than one
    one = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.27", rows=80, cols=200, samples_per_pixel=1)
    two = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.28", rows=80, cols=200, samples_per_pixel=1)
    redact_pixels(one, None, _stamp("one", background="black", color="white"))
    redact_pixels(two, None, _stamp("one\ntwo", background="black", color="white"))
    assert int((two.pixel_array == 0).sum()) > int((one.pixel_array == 0).sum())


def test_text_respects_bit_depth_signedness_and_monochrome1() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.29", rows=60, cols=200, samples_per_pixel=1)
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 12, 11
    ds.PixelData = np.full((60, 200), 100, dtype=np.uint16).tobytes()
    redact_pixels(ds, None, _stamp())
    assert ds.pixel_array.max() == 4095  # white at 12 bits, not 16

    signed = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.30", rows=60, cols=200, samples_per_pixel=1)
    signed.BitsAllocated, signed.BitsStored, signed.HighBit, signed.PixelRepresentation = 16, 16, 15, 1
    signed.PixelData = np.full((60, 200), -50, dtype=np.int16).tobytes()
    redact_pixels(signed, None, _stamp())
    assert signed.pixel_array.max() == 32767

    mono1 = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.31", rows=60, cols=200, samples_per_pixel=1)
    mono1.PhotometricInterpretation = "MONOCHROME1"
    redact_pixels(mono1, None, _stamp())
    assert mono1.pixel_array.min() == 0  # white is 0 in MONOCHROME1


def test_text_partly_or_wholly_outside_the_image_is_clipped() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.32", rows=30, cols=100, samples_per_pixel=1)
    before = ds.pixel_array.copy()
    redact_pixels(ds, None, _stamp("far away", x=500, y=500))
    assert (ds.pixel_array == before).all()
    redact_pixels(ds, None, _stamp("edge", x=90, y=25, size=40))  # must not raise
    assert ds.pixel_array.shape == (30, 100)


def test_nothing_to_do_changes_nothing() -> None:
    ds = _make_instance("1.2.3", "1.2.3.4", "1.2.3.4.33", rows=20, cols=20, samples_per_pixel=1)
    before = ds.pixel_array.copy()
    redact_pixels(ds, None, _stamp("   "))
    assert (ds.pixel_array == before).all()
    assert ds.BurnedInAnnotation == "NO"



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
                "/tools/dicom-cleaner/run",
                data={"action": "query", "remote_id": remote.id, "study_date": "2026-01-01"},
            )
            assert query.status_code == 200
            assert "DOE^JANE" in query.text
            assert 'name="study_date" value="2026-01-01"' in query.text

            run = client.post(
                "/tools/dicom-cleaner/run",
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
                "/tools/dicom-cleaner/run",
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


def test_cleaner_run_can_add_text_without_a_black_bar(tmp_path) -> None:
    find_port = _free_port()
    local_port = _free_port()
    study_uid = "1.2.826.0.1.3680043.8.498.51223344"
    series_uid = "1.2.826.0.1.3680043.8.498.51223345"
    sop_uid = "1.2.826.0.1.3680043.8.498.51223346"
    instance = _make_instance(study_uid, series_uid, sop_uid, rows=60, cols=200, samples_per_pixel=1)
    original = instance.pixel_array.copy()
    server, received = _start_find_move_store_scp(find_port, local_port, study_uid, instance)

    store = ConfigStore(tmp_path / "config")
    store.save_local(LocalAE(ae_title="DICOMM", host="127.0.0.1", port=local_port, storage_scp_enabled=True))
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=find_port)
    store.add_remote(remote)
    app = create_app(store)

    try:
        with TestClient(app) as client:
            run = client.post(
                "/tools/dicom-cleaner/run",
                data={
                    "action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": [study_uid],
                    "region_enabled": ["0"],  # the page sends 0, then 1 when the box is ticked
                    "region_height": "20",
                    "text": "REVIEWED", "text_x": "5", "text_y": "30", "text_size": "18",
                    "text_family": "mono", "text_bold": "1", "text_color": "yellow", "text_background": "black",
                    "uid_mode": "new", "destination_remote_id": remote.id,
                },
            )
            assert run.status_code == 200
            assert "Stamped and sent" in run.text, run.text
            assert len(received) == 1
            array = received[0].pixel_array
            assert received[0].BurnedInAnnotation == "YES"
            assert (array[0:25, :] == original[0:25, :]).all()  # no black bar, nothing above the text changed
            assert (array[25:, :] != original[25:, :]).any()  # the label is down here
    finally:
        server.shutdown()


def test_cleaner_run_with_no_bar_and_no_text_does_nothing(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config")
    remote = RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=_free_port())
    store.add_remote(remote)
    app = create_app(store)
    with TestClient(app) as client:
        run = client.post(
            "/tools/dicom-cleaner/run",
            data={"action": "run", "remote_id": remote.id, "level": "STUDY", "study_uid": ["1.2.3"],
                  "region_enabled": ["0", "0"], "text": ""},
        )
        assert "Nothing to do" in run.text



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
            "/tools/dicom-cleaner/preview-images",
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
                "/tools/dicom-cleaner/preview-images",
                data={"remote_id": remote.id, "study_uid": [study_uid]},
            )
            assert images.status_code == 200
            assert f"{series_uid}|{sop_uid}" in images.text
            assert "Load image" in images.text

            preview = client.post(
                "/tools/dicom-cleaner/preview-image",
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
            "/tools/dicom-cleaner/preview-image",
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
