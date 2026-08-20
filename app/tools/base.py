"""Contract every test tool implements so new PACS checks can be dropped in."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.models import LocalAE, RemoteNode, ToolResult


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
