"""In-memory sessions for the DICOM import page.

A session is one folder being imported: the scan index, the edits, and the
progress of the scan and of the send. It lives in memory only (nothing about the
patients is written to disk) and is dropped after it has been idle for a while,
or when the user starts over. A scan or a send runs in a background thread so the
page can show progress instead of waiting on one long request.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from app.applog import log
from app.dicom_import import (
    ImportEdits,
    ImportProblem,
    ScanProgress,
    ScanResult,
    SendOptions,
    SendProgress,
    scan_folder,
    send_import,
)
from app.models import LocalAE, RemoteNode

IDLE_SECONDS = 2 * 60 * 60
MAX_SESSIONS = 4

SCANNING, READY, SENDING, DONE, FAILED = "scanning", "ready", "sending", "done", "error"


@dataclass
class ImportSession:
    id: str
    folder: str
    include_headerless: bool = False
    state: str = SCANNING
    scan_progress: ScanProgress = field(default_factory=ScanProgress)
    scan: ScanResult | None = None
    error: str = ""
    edits: ImportEdits = field(default_factory=ImportEdits)
    send_progress: SendProgress | None = None
    send_target: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    touched: float = field(default_factory=time.monotonic)

    @property
    def busy(self) -> bool:
        return self.state in (SCANNING, SENDING)

    def touch(self) -> None:
        self.touched = time.monotonic()


class ImportSessions:
    def __init__(self, idle_seconds: float = IDLE_SECONDS, max_sessions: int = MAX_SESSIONS) -> None:
        self._sessions: dict[str, ImportSession] = {}
        self._lock = threading.Lock()
        self.idle_seconds = idle_seconds
        self.max_sessions = max_sessions

    def _expire(self) -> None:
        now = time.monotonic()
        for sid in [s.id for s in self._sessions.values() if not s.busy and now - s.touched > self.idle_seconds]:
            del self._sessions[sid]

    def get(self, sid: str) -> ImportSession | None:
        with self._lock:
            self._expire()
            session = self._sessions.get(sid)
            if session is not None:
                session.touch()
            return session

    def discard(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session is not None:
            session.cancel.set()  # a scan or send still running stops at the next file

    def start_scan(self, folder: str, include_headerless: bool = False) -> ImportSession:
        """Make a session and scan in the background. ImportProblem if there is no room."""
        with self._lock:
            self._expire()
            if len(self._sessions) >= self.max_sessions:
                idle = sorted((s for s in self._sessions.values() if not s.busy), key=lambda s: s.touched)
                if not idle:
                    raise ImportProblem("Too many imports are running at once. Wait for one to finish.")
                del self._sessions[idle[0].id]
            session = ImportSession(id=uuid.uuid4().hex[:16], folder=folder.strip(), include_headerless=include_headerless)
            self._sessions[session.id] = session
        threading.Thread(target=self._scan, args=(session,), daemon=True, name=f"import-scan-{session.id}").start()
        return session

    def _scan(self, session: ImportSession) -> None:
        try:
            session.scan = scan_folder(
                session.folder,
                progress=lambda p: setattr(session, "scan_progress", p),
                cancel=session.cancel,
                include_headerless=session.include_headerless,
            )
            session.state = READY
        except ImportProblem as exc:
            session.error, session.state = str(exc), FAILED
        except Exception as exc:  # noqa: BLE001
            log.exception("DICOM import scan failed")
            session.error, session.state = f"{type(exc).__name__}: {exc}", FAILED
        finally:
            session.cancel.clear()
            session.touch()

    def start_send(
        self,
        session: ImportSession,
        local: LocalAE,
        remote: RemoteNode,
        edits: ImportEdits,
        options: SendOptions,
    ) -> None:
        if session.scan is None or session.state not in (READY, DONE):
            raise ImportProblem("There is nothing ready to send.")
        session.edits = edits
        session.send_progress = SendProgress()
        session.send_target = f"{remote.name} ({remote.ae_title})"
        session.state = SENDING
        session.cancel.clear()
        log.info("DICOM import: sending to %s", session.send_target)  # no patient data in the log
        threading.Thread(
            target=self._send, args=(session, local, remote, edits, options), daemon=True, name=f"import-send-{session.id}"
        ).start()

    def _send(self, session: ImportSession, local: LocalAE, remote: RemoteNode, edits: ImportEdits, options: SendOptions) -> None:
        assert session.scan is not None and session.send_progress is not None
        try:
            send_import(session.scan, edits, local, remote, options, session.send_progress, session.cancel)
        except Exception as exc:  # noqa: BLE001
            log.exception("DICOM import send failed")
            session.send_progress.message = f"{type(exc).__name__}: {exc}"
            session.send_progress.done = True
        finally:
            session.state = DONE
            session.cancel.clear()
            session.touch()
            progress = session.send_progress
            log.info("DICOM import finished: %s stored, %s failed, %s not sent", progress.stored, progress.failed, progress.not_attempted)

    def cancel(self, session: ImportSession) -> None:
        if session.busy:
            session.cancel.set()

    def reset_send(self, session: ImportSession) -> None:
        if session.state == DONE:
            session.send_progress = None
            session.state = READY
