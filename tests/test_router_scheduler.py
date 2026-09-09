from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ImplicitVRLittleEndian, generate_uid
from pynetdicom import AE, evt
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import (
    CTImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from app.models import LocalAE, RemoteNode, RouteRule
from app.mwl_scp import WorklistSCP
from app.router_scheduler import RouterScheduler, compute_next_run
from app.store import ConfigStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _study_ds(study_uid: str) -> Dataset:
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = study_uid
    ds.PatientName = "DOE^JANE"
    ds.PatientID = "1001"
    ds.StudyDate = "20260101"
    ds.AccessionNumber = "ACC1"
    ds.ModalitiesInStudy = "CT"
    ds.StudyDescription = "CT CHEST"
    return ds


def _instance(study_uid: str) -> Dataset:
    now = datetime.now(timezone.utc)
    sop_uid = generate_uid()
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientName = "DOE^JANE"
    ds.PatientID = "1001"
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.Modality = "CT"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.Rows = 2
    ds.Columns = 2
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = b"\x00\x00\x00\x00"
    return ds


def test_compute_next_run_interval() -> None:
    rule = RouteRule(name="X", source_remote_id="pacs1", interval_minutes=30)
    after = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert compute_next_run(rule, after) == after + timedelta(minutes=30)


def test_compute_next_run_daily_same_day() -> None:
    rule = RouteRule(
        name="X",
        source_remote_id="pacs1",
        schedule_mode="daily",
        daily_times=["08:00", "20:00"],
    )
    after = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).astimezone().replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    next_run = compute_next_run(rule, after.astimezone(timezone.utc))
    local_next = next_run.astimezone()
    assert local_next.hour == 20
    assert local_next.date() == after.date()


def test_compute_next_run_daily_respects_days_of_week() -> None:
    # 2026-01-01 is a Thursday (weekday 3); only allow Monday (0).
    rule = RouteRule(
        name="X",
        source_remote_id="pacs1",
        schedule_mode="daily",
        daily_times=["08:00"],
        days_of_week=[0],
    )
    after = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc).astimezone()
    next_run = compute_next_run(rule, after.astimezone(timezone.utc)).astimezone()
    assert next_run.weekday() == 0
    assert next_run > after


def test_run_rule_reports_new_studies_without_destination(store: ConfigStore) -> None:
    port = _free_port()

    def handle_find(event):
        yield 0xFF00, _study_ds("1.2.3")
        yield 0x0000, None

    scp = AE(ae_title="QR_SCP")
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        remote = store.add_remote(
            RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port)
        ).remotes[-1]
        rule = store.add_route_rule(
            RouteRule(name="Nightly CT", source_remote_id=remote.id, modality="CT", date_scope="all")
        )
        storage_scp = WorklistSCP(store)
        scheduler = RouterScheduler(store, storage_scp)

        run = scheduler.run_rule(rule)
        assert run.ok, run.error
        assert run.matched_count == 1
        assert run.new_count == 1
        assert run.matches[0].status == "found"

        reloaded_rule = store.get_route_rule(rule.id)
        assert reloaded_rule.has_seen("1.2.3")
        assert reloaded_rule.last_run_ok is True
        assert reloaded_rule.next_run_at is not None

        # A second run sees the same study again but it is no longer new.
        second = scheduler.run_rule(store.get_route_rule(rule.id))
        assert second.matched_count == 1
        assert second.new_count == 0

        runs = store.list_route_runs(rule_id=rule.id)
        assert len(runs) == 2
    finally:
        server.shutdown()


def test_run_rule_retrieves_and_forwards_new_studies(store: ConfigStore) -> None:
    storage_port = _free_port()
    pacs_port = _free_port()
    dest_port = _free_port()
    instance = _instance("1.2.3")

    def handle_find(event):
        yield 0xFF00, _study_ds("1.2.3")
        yield 0x0000, None

    def handle_move(event):
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instance.SOPClassUID))]}
        yield 1
        yield 0xFF00, instance

    pacs_ae = AE(ae_title="QR_SCP")
    pacs_ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    pacs_ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    pacs_server = pacs_ae.start_server(
        ("127.0.0.1", pacs_port),
        block=False,
        evt_handlers=[(evt.EVT_C_FIND, handle_find), (evt.EVT_C_MOVE, handle_move)],
    )

    received: list[Dataset] = []

    def handle_store(event):
        received.append(event.dataset)
        return 0x0000

    dest_ae = AE(ae_title="DEST_SCP")
    dest_ae.add_supported_context(CTImageStorage)
    dest_server = dest_ae.start_server(
        ("127.0.0.1", dest_port), block=False, evt_handlers=[(evt.EVT_C_STORE, handle_store)]
    )

    try:
        time.sleep(0.05)
        store.save_local(
            LocalAE(
                ae_title="DICOMM",
                host="127.0.0.1",
                port=storage_port,
                timeout_seconds=5,
                storage_scp_enabled=True,
            )
        )
        pacs = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=pacs_port))
        pacs_node = pacs.remotes[-1]
        dest = store.add_remote(RemoteNode(name="dest", ae_title="DEST_SCP", host="127.0.0.1", port=dest_port))
        dest_node = dest.remotes[-1]

        rule = store.add_route_rule(
            RouteRule(
                name="Forward CT",
                source_remote_id=pacs_node.id,
                modality="CT",
                date_scope="all",
                destination_remote_ids=[dest_node.id],
            )
        )

        storage_scp = WorklistSCP(store)
        storage_scp.start()
        assert storage_scp.running, storage_scp.last_error
        scheduler = RouterScheduler(store, storage_scp)
        run = scheduler.run_rule(rule)

        assert run.ok, run.error
        assert run.new_count == 1
        assert run.matches[0].status == "forwarded", run.matches[0].error
        assert len(received) == 1
        assert str(received[0].SOPInstanceUID) == str(instance.SOPInstanceUID)

        reloaded_rule = store.get_route_rule(rule.id)
        assert reloaded_rule.has_seen("1.2.3")
    finally:
        storage_scp.stop()
        pacs_server.shutdown()
        dest_server.shutdown()


def test_run_rule_without_storage_scp_running_marks_failed(store: ConfigStore) -> None:
    port = _free_port()

    def handle_find(event):
        yield 0xFF00, _study_ds("1.2.3")
        yield 0x0000, None

    scp = AE(ae_title="QR_SCP")
    scp.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    server = scp.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_FIND, handle_find)])
    try:
        time.sleep(0.05)
        remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=port))
        pacs_node = remote.remotes[-1]
        dest = store.add_remote(RemoteNode(name="dest", ae_title="DEST_SCP", host="127.0.0.1", port=104))
        dest_node = dest.remotes[-1]
        rule = store.add_route_rule(
            RouteRule(
                name="Forward CT",
                source_remote_id=pacs_node.id,
                modality="CT",
                date_scope="all",
                destination_remote_ids=[dest_node.id],
            )
        )
        storage_scp = WorklistSCP(store)  # never started: storage_scp_enabled defaults False
        scheduler = RouterScheduler(store, storage_scp)
        run = scheduler.run_rule(rule)

        assert run.ok
        assert run.matches[0].status == "failed"
        assert "Storage SCP" in run.matches[0].error
        assert not store.get_route_rule(rule.id).has_seen("1.2.3")
    finally:
        server.shutdown()


def test_tick_skips_stopped_paused_and_not_due_rules(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    stopped = store.add_route_rule(
        RouteRule(name="Stopped", source_remote_id=remote.id, status="stopped")
    )
    paused = store.add_route_rule(
        RouteRule(name="Paused", source_remote_id=remote.id, status="paused")
    )
    not_due = store.add_route_rule(RouteRule(name="Not due", source_remote_id=remote.id))
    store.save_route_rule_run_state(
        store.get_route_rule(not_due.id).model_copy(
            update={"next_run_at": datetime.now(timezone.utc) + timedelta(hours=1)}
        )
    )

    storage_scp = WorklistSCP(store)
    scheduler = RouterScheduler(store, storage_scp)
    scheduler._tick()

    assert store.get_route_rule(stopped.id).last_run_at is None
    assert store.get_route_rule(paused.id).last_run_at is None
    assert store.get_route_rule(not_due.id).last_run_at is None
    assert store.list_route_runs() == []


def test_run_rule_stops_between_targets_when_interrupted_and_keeps_completed_work(
    store: ConfigStore,
) -> None:
    """Pausing/stopping mid-run must not redo or lose already-forwarded studies."""
    storage_port = _free_port()
    pacs_port = _free_port()
    dest_port = _free_port()
    instance_1 = _instance("1.2.1")
    instance_2 = _instance("1.2.2")

    def handle_find(event):
        yield 0xFF00, _study_ds("1.2.1")
        yield 0xFF00, _study_ds("1.2.2")
        yield 0x0000, None

    def handle_move(event):
        study_uid = str(event.identifier.StudyInstanceUID)
        instance = instance_1 if study_uid == "1.2.1" else instance_2
        yield "127.0.0.1", storage_port, {"contexts": [build_context(str(instance.SOPClassUID))]}
        yield 1
        yield 0xFF00, instance

    pacs_ae = AE(ae_title="QR_SCP")
    pacs_ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
    pacs_ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
    pacs_server = pacs_ae.start_server(
        ("127.0.0.1", pacs_port),
        block=False,
        evt_handlers=[(evt.EVT_C_FIND, handle_find), (evt.EVT_C_MOVE, handle_move)],
    )

    interrupt = threading.Event()
    received: list[Dataset] = []

    def handle_store(event):
        received.append(event.dataset)
        interrupt.set()  # simulate Pause/Stop being clicked right after the first forward
        return 0x0000

    dest_ae = AE(ae_title="DEST_SCP")
    dest_ae.add_supported_context(CTImageStorage)
    dest_server = dest_ae.start_server(
        ("127.0.0.1", dest_port), block=False, evt_handlers=[(evt.EVT_C_STORE, handle_store)]
    )

    try:
        time.sleep(0.05)
        store.save_local(
            LocalAE(
                ae_title="DICOMM",
                host="127.0.0.1",
                port=storage_port,
                timeout_seconds=5,
                storage_scp_enabled=True,
            )
        )
        pacs_node = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=pacs_port)).remotes[-1]
        dest_node = store.add_remote(RemoteNode(name="dest", ae_title="DEST_SCP", host="127.0.0.1", port=dest_port)).remotes[-1]
        rule = store.add_route_rule(
            RouteRule(
                name="Forward CT",
                source_remote_id=pacs_node.id,
                modality="CT",
                date_scope="all",
                destination_remote_ids=[dest_node.id],
            )
        )

        storage_scp = WorklistSCP(store)
        storage_scp.start()
        assert storage_scp.running, storage_scp.last_error
        scheduler = RouterScheduler(store, storage_scp)
        run = scheduler.run_rule(rule, interrupt=interrupt)

        assert run.status == "interrupted"
        assert run.new_count == 2
        assert len(run.matches) == 1
        assert run.matches[0].status == "forwarded"
        assert len(received) == 1

        reloaded = store.get_route_rule(rule.id)
        assert reloaded.has_seen("1.2.1")
        assert not reloaded.has_seen("1.2.2")
    finally:
        storage_scp.stop()
        pacs_server.shutdown()
        dest_server.shutdown()


def test_request_pause_keeps_next_run_at_for_catch_up(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    when = datetime.now(timezone.utc) + timedelta(minutes=5)
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id))
    store.save_route_rule_run_state(store.get_route_rule(rule.id).model_copy(update={"next_run_at": when}))

    scheduler = RouterScheduler(store, WorklistSCP(store))
    updated = scheduler.request_pause(rule.id)

    assert updated.status == "paused"
    assert updated.next_run_at == when
    assert store.get_route_rule(rule.id).status == "paused"


def test_request_stop_clears_next_run_at(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    when = datetime.now(timezone.utc) + timedelta(minutes=5)
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id))
    store.save_route_rule_run_state(store.get_route_rule(rule.id).model_copy(update={"next_run_at": when}))

    scheduler = RouterScheduler(store, WorklistSCP(store))
    updated = scheduler.request_stop(rule.id)

    assert updated.status == "stopped"
    assert updated.next_run_at is None


def test_request_pause_and_stop_interrupt_a_running_rule(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id))
    scheduler = RouterScheduler(store, WorklistSCP(store))

    event = scheduler._register(rule.id)
    assert event is not None
    assert scheduler.is_running(rule.id)

    scheduler.request_pause(rule.id)
    assert event.is_set()

    scheduler._unregister(rule.id)
    assert not scheduler.is_running(rule.id)


def test_start_rule_reactivates_a_paused_rule_and_runs_it(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    stale = datetime.now(timezone.utc) - timedelta(minutes=1)
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id, status="paused"))
    store.save_route_rule_run_state(store.get_route_rule(rule.id).model_copy(update={"next_run_at": stale}))

    before = datetime.now(timezone.utc)
    scheduler = RouterScheduler(store, WorklistSCP(store))
    outcome = scheduler.start_rule(rule.id)
    assert outcome == "started"

    deadline = time.time() + 5
    while scheduler.is_running(rule.id) and time.time() < deadline:
        time.sleep(0.02)

    reloaded = store.get_route_rule(rule.id)
    assert reloaded.status == "active"
    # The run this triggered computed a fresh schedule, replacing the stale one.
    assert reloaded.next_run_at is not None
    assert reloaded.next_run_at > before
    assert len(store.list_route_runs(rule_id=rule.id)) == 1


def test_start_rule_computes_fresh_schedule_when_stopped(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    rule = store.add_route_rule(
        RouteRule(name="X", source_remote_id=remote.id, status="stopped", interval_minutes=30)
    )
    assert rule.next_run_at is None

    before = datetime.now(timezone.utc)
    scheduler = RouterScheduler(store, WorklistSCP(store))
    scheduler.start_rule(rule.id)

    deadline = time.time() + 5
    while scheduler.is_running(rule.id) and time.time() < deadline:
        time.sleep(0.02)

    reloaded = store.get_route_rule(rule.id)
    assert reloaded.status == "active"
    assert reloaded.next_run_at is not None
    assert reloaded.next_run_at >= before + timedelta(minutes=29)


def test_run_now_reports_already_running(store: ConfigStore) -> None:
    remote = store.add_remote(RemoteNode(name="pacs", ae_title="QR_SCP", host="127.0.0.1", port=1)).remotes[-1]
    rule = store.add_route_rule(RouteRule(name="X", source_remote_id=remote.id))
    scheduler = RouterScheduler(store, WorklistSCP(store))

    event = scheduler._register(rule.id)
    assert event is not None
    try:
        assert scheduler.run_now(rule.id) == "already_running"
    finally:
        scheduler._unregister(rule.id)
