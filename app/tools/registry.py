"""Auto-discovery registry: any module in app/tools/ that calls register() appears in the UI."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from app.tools.base import BaseTool

_REGISTRY: dict[str, BaseTool] = {}
_DISCOVERED = False


def register(tool: BaseTool) -> BaseTool:
    existing = _REGISTRY.get(tool.id)
    if existing is not None and type(existing) is not type(tool):
        raise ValueError(f"Duplicate tool id: {tool.id}")
    _REGISTRY[tool.id] = tool
    return tool


def get_tool(tool_id: str) -> BaseTool:
    try:
        return _REGISTRY[tool_id]
    except KeyError as exc:
        raise KeyError(f"Unknown tool: {tool_id}") from exc


def list_tools() -> list[BaseTool]:
    return list(_REGISTRY.values())


def discover() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    package_dir = Path(__file__).parent
    for path in sorted(package_dir.glob("*.py")):
        if path.name in {"__init__.py", "base.py", "registry.py"}:
            continue
        import_module(f"app.tools.{path.stem}")
    _DISCOVERED = True
