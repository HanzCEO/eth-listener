from __future__ import annotations

import asyncio
import json
import threading
import types
from unittest.mock import Mock

import pytest

from eth_listener import EthListener, NewHeadEvent, SubscriptionHandle
from eth_listener.events import BaseEthereumEvent


def test_inject_event_invokes_callbacks() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    try:
        event_received = threading.Event()
        numbers: list[int | None] = []

        def callback(event: BaseEthereumEvent) -> None:
            assert isinstance(event, NewHeadEvent)
            numbers.append(event.number)
            event_received.set()

        listener._callbacks["newHeads"].add(callback)  # pyright: ignore[reportPrivateUsage]
        listener.inject_event("newHeads", {"number": "0x42"})

        assert event_received.wait(timeout=1), "callback not invoked"
        assert numbers == [0x42]
    finally:
        listener.stop()


def test_subscription_handle_unsubscribe_calls_off() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    try:
        listener.off = Mock()  # type: ignore[assignment]
        handle = SubscriptionHandle(listener=listener, event="newHeads", callback=lambda _: None)
        handle.unsubscribe()
        listener.off.assert_called_once()
        assert handle.listener is None
    finally:
        listener.stop()


def test_on_requires_start_when_auto_start_disabled() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    try:
        with pytest.raises(RuntimeError, match="has not been started"):
            listener.on("newHeads", lambda _: None)
    finally:
        listener.stop()


def test_raw_message_listener_receives_payload() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    try:
        captured: list[str] = []
        listener.add_raw_message_listener(captured.append)
        asyncio.run(listener._handle_message('{"jsonrpc": "2.0"}'))

        assert captured == ['{"jsonrpc": "2.0"}']
    finally:
        listener.stop()


def test_on_defers_subscription_until_connected() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    loop = asyncio.new_event_loop()
    try:
        listener._loop = loop  # pyright: ignore[reportPrivateUsage]
        listener._connected_event = asyncio.Event()  # pyright: ignore[reportPrivateUsage]
        listener._connected_event.clear()  # pyright: ignore[reportPrivateUsage]

        called = False

        def _fail_sync_await(self: EthListener, awaitable: asyncio.Future) -> None:
            nonlocal called
            called = True
            raise AssertionError("_sync_await should not run when disconnected")

        listener._sync_await = types.MethodType(_fail_sync_await, listener)  # pyright: ignore[reportPrivateUsage]

        handle = listener.on("newHeads", lambda _: None)

        assert handle.subscription_id is None
        assert not called
        assert "newHeads" in listener._callbacks  # pyright: ignore[reportPrivateUsage]
    finally:
        listener._loop = None  # pyright: ignore[reportPrivateUsage]
        loop.close()


def test_on_subscribes_immediately_when_connected() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    loop = asyncio.new_event_loop()
    try:
        listener._loop = loop  # pyright: ignore[reportPrivateUsage]
        listener._connected_event = asyncio.Event()  # pyright: ignore[reportPrivateUsage]
        listener._connected_event.set()  # pyright: ignore[reportPrivateUsage]

        def _fake_sync_await(self: EthListener, awaitable):
            awaitable.close()
            self._subscription_ids["newHeads"] = "sub-1"  # pyright: ignore[reportPrivateUsage]
            return "sub-1"

        listener._sync_await = types.MethodType(_fake_sync_await, listener)  # pyright: ignore[reportPrivateUsage]

        handle = listener.on("newHeads", lambda _: None)

        assert handle.subscription_id == "sub-1"
        assert listener._subscription_ids["newHeads"] == "sub-1"  # pyright: ignore[reportPrivateUsage]
    finally:
        listener._loop = None  # pyright: ignore[reportPrivateUsage]
        loop.close()


def test_subscription_response_updates_mappings() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    try:
        listener._pending_subscriptions[1] = "newHeads"  # pyright: ignore[reportPrivateUsage]
        asyncio.run(
            listener._handle_message(json.dumps({"jsonrpc": "2.0", "id": 1, "result": "sub-1"}))
        )

        assert listener._subscription_ids["newHeads"] == "sub-1"  # pyright: ignore[reportPrivateUsage]
        assert listener._event_for_subscription["sub-1"] == "newHeads"  # pyright: ignore[reportPrivateUsage]
        assert 1 not in listener._pending_subscriptions  # pyright: ignore[reportPrivateUsage]
    finally:
        listener.stop()


def test_unsubscribe_response_clears_pending() -> None:
    listener = EthListener("ws://localhost", auto_start=False)
    try:
        listener._pending_unsubscriptions[2] = "newHeads"  # pyright: ignore[reportPrivateUsage]
        asyncio.run(
            listener._handle_message(json.dumps({"jsonrpc": "2.0", "id": 2, "result": True}))
        )

        assert 2 not in listener._pending_unsubscriptions  # pyright: ignore[reportPrivateUsage]
    finally:
        listener.stop()
