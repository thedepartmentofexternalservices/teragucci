"""
AdapterRegistry -- manages all registered remote desktop adapters.

Maintains the active adapter and provides introspection for the MCP master.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseAdapter


class AdapterRegistry:
    """
    Holds all available adapter classes and the currently active instance.

    Usage:
        registry.use("vnc")               # switch active adapter
        registry.active.connect(...)      # use it
        registry.list()                   # -> [{"name": ..., "connected": ..., ...}]
    """

    def __init__(self, adapter_classes: list[type["BaseAdapter"]]) -> None:
        # Instantiate all adapters eagerly so they can report status at any time
        self._adapters: dict[str, "BaseAdapter"] = {}
        for cls in adapter_classes:
            instance = cls()
            self._adapters[instance.name] = instance

        # TeraGuchiAdapter is the default master
        self._active_name: str = next(
            (name for name in self._adapters if name == "teraguchi"),
            next(iter(self._adapters)),
        )

    @property
    def active(self) -> "BaseAdapter":
        return self._adapters[self._active_name]

    @property
    def active_name(self) -> str:
        return self._active_name

    def use(self, name: str) -> "BaseAdapter":
        """Switch to the named adapter. Returns the adapter instance."""
        if name not in self._adapters:
            available = ", ".join(sorted(self._adapters))
            raise ValueError(f"Unknown adapter {name!r}. Available: {available}")
        self._active_name = name
        return self._adapters[name]

    def get(self, name: str) -> "BaseAdapter | None":
        return self._adapters.get(name)

    def list(self) -> list[dict]:
        """Return a list of adapter info dicts, marking which is active."""
        result = []
        for name, adapter in self._adapters.items():
            info = adapter.adapter_info()
            info["active"] = name == self._active_name
            result.append(info)
        return result

    def __repr__(self) -> str:
        return (
            f"AdapterRegistry(active={self._active_name!r}, "
            f"adapters={list(self._adapters)})"
        )
