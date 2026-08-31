"""Contract every test tool implements so new PACS checks can be dropped in."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.models import LocalAE, RemoteNode, ToolResult


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class BaseTool(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str] = "general"
    requires_remote: ClassVar[bool] = True
    template: ClassVar[str] = "tool.html"

    @abstractmethod
    def run(
        self,
        local: LocalAE,
        remote: RemoteNode | None,
        options: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute the tool against the configured local AE and optional remote node."""
