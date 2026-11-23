"""Looking up this computer's own names must never hang start-up (app/netutil.py)."""

from __future__ import annotations

import socket
import time

from app import netutil, tls


def test_a_quick_lookup_returns_its_answer() -> None:
    assert netutil.within(1, lambda: "answer", default="none") == "answer"


def test_a_lookup_that_never_answers_gives_the_default_after_the_limit() -> None:
    started = time.perf_counter()
    assert netutil.within(0.2, time.sleep, 5, default="gave up") == "gave up"
    assert time.perf_counter() - started < 2  # it did not wait for the lookup


def test_a_lookup_that_fails_gives_the_default() -> None:
    def broken() -> str:
        raise OSError("no such host")

    assert netutil.within(1, broken, default="none") == "none"


def test_own_names_never_waits_for_a_stuck_dns_lookup(monkeypatch) -> None:
    monkeypatch.setattr(netutil, "LOOKUP_SECONDS", 0.2)
    monkeypatch.setattr(socket, "gethostname", lambda: "Pacs-Mac")
    monkeypatch.setattr(socket, "getfqdn", lambda: time.sleep(5) or "pacs-mac.example.org")
    started = time.perf_counter()
    assert netutil.own_names() == ["pacs-mac"]  # the plain name is kept, the slow full name is left out
    assert time.perf_counter() - started < 2


def test_own_names_has_the_full_name_when_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "Pacs-Mac")
    monkeypatch.setattr(socket, "getfqdn", lambda: "pacs-mac.example.org")
    assert netutil.own_names() == ["pacs-mac", "pacs-mac.example.org"]


def test_the_certificate_identity_is_still_made_when_every_lookup_hangs(monkeypatch) -> None:
    monkeypatch.setattr(netutil, "LOOKUP_SECONDS", 0.2)
    monkeypatch.setattr(socket, "gethostname", lambda: "Pacs-Mac")
    monkeypatch.setattr(socket, "getfqdn", lambda: time.sleep(5) or "x")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: time.sleep(5) or [])
    started = time.perf_counter()
    names, addresses = tls.local_identity()
    assert time.perf_counter() - started < 3
    assert names == ["localhost", "pacs-mac"] and addresses == ["127.0.0.1", "::1"]
