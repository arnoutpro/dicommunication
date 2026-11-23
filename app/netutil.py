"""Looking up this computer's own names without ever hanging.

``socket.getfqdn()`` and ``socket.getaddrinfo(<own host name>)`` ask DNS (and on a Mac,
Bonjour). On a machine whose name does not resolve they can block for many seconds, and
Python gives them no timeout. They are used when the service starts (the certificate and
the allowed host names), so a slow lookup must not delay or block start-up: the answer is
simply left out after a few seconds, and the app still knows ``localhost`` and the plain
host name.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable, TypeVar

T = TypeVar("T")

LOOKUP_SECONDS = 3.0


def within(seconds: float, lookup: Callable[..., T], *args: object, default: T) -> T:
    """Run ``lookup(*args)`` and give up after ``seconds``, returning ``default`` (also on OSError)."""
    answer: list[T] = []

    def run() -> None:
        try:
            answer.append(lookup(*args))
        except OSError:
            pass

    worker = threading.Thread(target=run, daemon=True, name="own-name-lookup")  # a stuck lookup must not keep the process alive
    worker.start()
    worker.join(seconds)
    return answer[0] if answer else default


def own_names() -> list[str]:
    """This computer's host name and, if DNS answers in time, its fully qualified name (lowercase)."""
    found: list[str] = []
    for lookup in (socket.gethostname, socket.getfqdn):
        name = within(LOOKUP_SECONDS, lookup, default="").strip().lower()
        if name and name not in found:
            found.append(name)
    return found


def own_addresses() -> list[str]:
    """The IPv4 addresses this computer's name resolves to, if that answers in time."""
    infos = within(LOOKUP_SECONDS, socket.getaddrinfo, socket.gethostname(), None, socket.AF_INET, default=[])
    found: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in found:
            found.append(address)
    return found
