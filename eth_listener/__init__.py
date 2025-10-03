"""Top-level package for eth-listener.

The package exposes the :class:`EthListener` facade that allows Python
programs to subscribe to Ethereum JSON-RPC websocket events using a
synchronous callback API that is powered internally by an asynchronous
websocket client.
"""
from __future__ import annotations

from importlib import metadata

from .events import BaseEthereumEvent, NewHeadEvent, NewPendingTransactionEvent
from .listener import EthListener, SubscriptionHandle

try:
    __version__ = metadata.version("eth-listener")
except metadata.PackageNotFoundError:  # pragma: no cover - fallback for local dev
    __version__ = "0.0.0"

__all__ = [
    "EthListener",
    "SubscriptionHandle",
    "BaseEthereumEvent",
    "NewHeadEvent",
    "NewPendingTransactionEvent",
]
