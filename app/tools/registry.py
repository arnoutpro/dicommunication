"""Auto-discovery registry: any module in app/tools/ that calls register() appears in the UI."""

from __future__ import annotations

import pkgutil
from importlib import import_module
from pathlib import Path

from app.tools.base import BaseTool

# Fallback when pkgutil cannot see frozen modules. Keep in sync with app/tools/*.py.
BUILTIN_TOOL_MODULES = ("echo", "find", "hl7", "mwl", "ping", "store")

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


CATEGORY_LABELS = {
    "connectivity": "Connectivity",
    "dimse": "DIMSE",
    "hl7": "HL7",
    "general": "General",
}
CATEGORY_ORDER = ("connectivity", "dimse", "hl7", "general")
TOOL_ORDER = ("ping", "c-echo", "c-store", "c-find", "mwl-find", "hl7-send")


def list_tools_by_category() -> list[tuple[str, list[BaseTool]]]:
    buckets: dict[str, list[BaseTool]] = {}
    for tool in list_tools():
        buckets.setdefault(tool.category, []).append(tool)

    def _tool_sort(tool: BaseTool) -> tuple[int, str]:
        try:
            return TOOL_ORDER.index(tool.id), tool.name
        except ValueError:
            return len(TOOL_ORDER), tool.name

    groups: list[tuple[str, list[BaseTool]]] = []
    seen: set[str] = set()
    for category in CATEGORY_ORDER:
        if category in buckets:
            groups.append(
                (CATEGORY_LABELS.get(category, category), sorted(buckets[category], key=_tool_sort))
            )
            seen.add(category)
    for category in sorted(buckets):
        if category in seen:
            continue
        label = CATEGORY_LABELS.get(category, category.replace("_", " ").title())
        groups.append((label, sorted(buckets[category], key=_tool_sort)))
    return groups


def discover() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    names = set(BUILTIN_TOOL_MODULES)
    package_dir = Path(__file__).parent
    try:
        for path in sorted(package_dir.glob("*.py")):
            if path.name in {"__init__.py", "base.py", "registry.py"}:
                continue
            names.add(path.stem)
    except OSError:
        pass
    try:
        import app.tools as tools_pkg

        for module_info in pkgutil.iter_modules(tools_pkg.__path__):
            if module_info.name in {"base", "registry"}:
                continue
            names.add(module_info.name)
    except (ImportError, AttributeError, OSError):
        pass
    for name in sorted(names):
        import_module(f"app.tools.{name}")
    _DISCOVERED = True
