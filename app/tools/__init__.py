"""Tool plugins. Add a new file in this package, subclass BaseTool, and call register()."""

from app.tools.registry import discover, get_tool, list_tools, register

discover()

__all__ = ["discover", "get_tool", "list_tools", "register"]
