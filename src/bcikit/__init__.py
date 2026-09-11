"""Public API with lazy imports so the protocol codec stays standard-library only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .protocol import PROTOCOL_VERSION

if TYPE_CHECKING:
    from .models import IntegrityStats, Marker, SampleBatch, SessionConfig, SessionState
    from .session import DeviceSession
    from .transport import ReplayTransport, Transport, VirtualTransport, WiFiTransport

__all__ = [
    "DeviceSession",
    "IntegrityStats",
    "Marker",
    "PROTOCOL_VERSION",
    "ReplayTransport",
    "SampleBatch",
    "SessionConfig",
    "SessionState",
    "Transport",
    "VirtualTransport",
    "WiFiTransport",
]


def __getattr__(name: str) -> Any:
    if name in {"IntegrityStats", "Marker", "SampleBatch", "SessionConfig", "SessionState"}:
        from . import models

        return getattr(models, name)
    if name == "DeviceSession":
        from .session import DeviceSession

        return DeviceSession
    if name in {"ReplayTransport", "Transport", "VirtualTransport", "WiFiTransport"}:
        from . import transport

        return getattr(transport, name)
    raise AttributeError(f"module 'bcikit' has no attribute {name!r}")
