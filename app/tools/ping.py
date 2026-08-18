"""Network reachability: DNS, ICMP ping, and TCP connect to the DICOM port."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from typing import Any

from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class PingTool(BaseTool):
    id = "ping"
    name = "Network PING"
    description = (
        "Resolve the host, send ICMP echo requests, and open a TCP connection "
        "to the remote DICOM port. ICMP is often blocked on hospital networks; "
        "TCP to the DICOM port is the more reliable check."
    )
    category = "connectivity"

    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        if remote is None:
            return ToolResult(
                tool_id=self.id,
                tool_name=self.name,
                ok=False,
                summary="Select a remote DICOM node first.",
            )

        started = time.perf_counter()
        timeout = local.timeout_seconds
        steps = [
            self._resolve(remote),
            self._icmp(remote, timeout=timeout),
            self._tcp(remote, timeout=timeout),
        ]
        dns_ok = steps[0].ok
        icmp_ok = steps[1].ok
        tcp_ok = steps[2].ok
        ok = dns_ok and tcp_ok

        if ok and icmp_ok:
            summary = f"{remote.connect_host}:{remote.port} is reachable over ICMP and TCP."
        elif ok:
            summary = (
                f"TCP {remote.connect_host}:{remote.port} is open. "
                "ICMP ping failed (often blocked on clinical networks)."
            )
        elif not dns_ok:
            summary = steps[0].message
        else:
            summary = steps[2].message

        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=ok,
            summary=summary,
            remote_id=remote.id,
            remote_name=remote.name,
            duration_ms=_elapsed_ms(started),
            steps=steps,
        )

    def _resolve(self, remote: RemoteNode) -> ToolStep:
        started = time.perf_counter()
        try:
            infos = socket.getaddrinfo(remote.connect_host, remote.port, type=socket.SOCK_STREAM)
            addresses = sorted({item[4][0] for item in infos})
            return ToolStep(
                name="DNS resolve",
                ok=True,
                message=f"{remote.connect_host} → {', '.join(addresses)}",
                duration_ms=_elapsed_ms(started),
                details={"addresses": addresses},
            )
        except socket.gaierror as exc:
            return ToolStep(
                name="DNS resolve",
                ok=False,
                message=f"Could not resolve {remote.connect_host}: {exc}",
                duration_ms=_elapsed_ms(started),
            )

    def _icmp(self, remote: RemoteNode, timeout: float) -> ToolStep:
        started = time.perf_counter()
        ping_bin = shutil.which("ping")
        if not ping_bin:
            return ToolStep(
                name="ICMP ping",
                ok=False,
                message="The ping command is not installed on this host.",
                duration_ms=_elapsed_ms(started),
            )

        count = 3
        wait_s = max(1, int(timeout))
        command = [ping_bin, "-c", str(count), "-W", str(wait_s), remote.connect_host]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=wait_s * count + 2,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolStep(
                name="ICMP ping",
                ok=False,
                message=f"ICMP ping to {remote.connect_host} timed out.",
                duration_ms=_elapsed_ms(started),
            )

        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        ok = completed.returncode == 0
        return ToolStep(
            name="ICMP ping",
            ok=ok,
            message=(
                f"{remote.connect_host} responded to ICMP"
                if ok
                else f"{remote.connect_host} did not respond to ICMP"
            ),
            duration_ms=_elapsed_ms(started),
            details={"returncode": completed.returncode, "output": output[-1500:]},
        )

    def _tcp(self, remote: RemoteNode, timeout: float) -> ToolStep:
        started = time.perf_counter()
        try:
            with socket.create_connection((remote.connect_host, remote.port), timeout=timeout):
                return ToolStep(
                    name="TCP port",
                    ok=True,
                    message=f"TCP {remote.connect_host}:{remote.port} is open",
                    duration_ms=_elapsed_ms(started),
                )
        except OSError as exc:
            return ToolStep(
                name="TCP port",
                ok=False,
                message=f"TCP {remote.connect_host}:{remote.port} is closed or filtered: {exc}",
                duration_ms=_elapsed_ms(started),
            )


register(PingTool())
