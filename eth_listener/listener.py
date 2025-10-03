"""High-level Ethereum websocket listener with a synchronous callback API."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from itertools import count
from types import TracebackType
from typing import Any, Callable, Coroutine, Dict, MutableMapping, Optional, Set, Type, TypeVar

try:  # pragma: no cover - import depends on environment
    import websockets
    from websockets.client import WebSocketClientProtocol
except ModuleNotFoundError as exc:  # pragma: no cover - missing dependency
    raise ImportError(
        "eth_listener requires the `websockets` package. Install it with ``pip install \"websockets>=11,<12\"``."
    ) from exc

from .events import BaseEthereumEvent, NewHeadEvent, NewPendingTransactionEvent

__all__ = ["EthListener", "SubscriptionHandle"]

Callback = Callable[[BaseEthereumEvent], None]
RawMessageCallback = Callable[[str], None]
T = TypeVar("T")

_EVENT_PARSERS: Dict[str, Callable[[str, Any], BaseEthereumEvent]] = {
    "newHeads": NewHeadEvent.from_payload,
    "newPendingTransactions": NewPendingTransactionEvent.from_payload,
}


def _parse_event(event: str, subscription_id: str, payload: Any) -> BaseEthereumEvent:
    parser = _EVENT_PARSERS.get(event)
    if parser is None:
        return BaseEthereumEvent(subscription_id=subscription_id, raw=payload)
    return parser(subscription_id, payload)


@dataclass(slots=True)
class SubscriptionHandle:
    """Represents an active subscription registered with :class:`EthListener`."""

    listener: Optional["EthListener"]
    event: str
    callback: Callback
    subscription_id: Optional[str] = None

    def unsubscribe(self) -> None:
        """Remove the callback from the listener and cancel upstream subscription."""

        if self.listener is None:
            return
        self.listener.off(self.event, self.callback)
        self.listener = None


class EthListener:
    """Listen to Ethereum websocket events via JSON-RPC subscriptions.

    Parameters
    ----------
    ws_url:
        Ethereum JSON-RPC websocket endpoint URL.
    reconnect_delay:
        Seconds to wait before attempting to reconnect after a connection
        failure.  The listener automatically attempts to re-establish the
        websocket connection and will re-subscribe to outstanding topics.
    max_workers:
        Number of worker threads dedicated to executing user callbacks.
        Callbacks are executed in a thread pool to prevent blocking the
        websocket receive loop.
    logger:
        Optional :class:`logging.Logger` used for diagnostics.  A reasonable
        default logger named ``eth_listener`` is created when omitted.
    auto_start:
        When ``True`` (default) the listener automatically starts the
        background event loop as soon as the first subscription is registered.
        When ``False`` the caller must invoke :meth:`start` manually.
    start_timeout:
        Default timeout (in seconds) applied when waiting for the background
        loop to finish bootstrapping. Use ``0`` to return immediately or
        ``None`` to wait indefinitely.
    """

    def __init__(
        self,
        ws_url: str,
        *,
        reconnect_delay: float = 5.0,
        max_workers: int = 4,
        logger: Optional[logging.Logger] = None,
        auto_start: bool = True,
        start_timeout: Optional[float] = 10.0,
    ) -> None:
        self._ws_url = ws_url
        self._reconnect_delay = reconnect_delay
        self._callbacks: MutableMapping[str, Set[Callback]] = defaultdict(set)
        self._subscription_ids: Dict[str, str] = {}
        self._event_for_subscription: Dict[str, str] = {}
        self._pending_subscriptions: Dict[int, str] = {}
        self._pending_unsubscriptions: Dict[int, str] = {}
        self._request_id_counter = count(1)
        self._callback_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._shutdown_complete = threading.Event()
        self._auto_start = auto_start
        self._ws: Optional[WebSocketClientProtocol] = None
        self._send_lock: Optional[asyncio.Lock] = None
        self._connected_event: Optional[asyncio.Event] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=max_workers
        )
        self._logger = logger or logging.getLogger("eth_listener")
        self._logger.addHandler(logging.NullHandler())
        self._logger.propagate = False
        self._start_timeout = start_timeout
        self._raw_message_listeners: Set[RawMessageCallback] = set()
        self._raw_listener_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self, *, timeout: Optional[float] = None) -> None:
        """Start the background asyncio loop.

        Parameters
        ----------
        timeout:
            Maximum number of seconds to wait for the background loop thread to
            signal readiness. Pass ``None`` to use the listener's configured
            startup timeout (see ``start_timeout`` init argument). Provide ``0``
            to return immediately after spawning the thread, or ``float('inf')``
            for no timeout.
        """

        if self._loop_thread and self._loop_thread.is_alive():
            return

        effective_timeout = self._start_timeout if timeout is None else timeout

        self._loop_ready.clear()
        self._shutdown_complete.clear()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)

        self._loop_thread = threading.Thread(
            target=self._run_loop, name="EthListenerLoop", daemon=True
        )
        self._loop_thread.start()

        if effective_timeout is not None and effective_timeout <= 0:
            return

        step = 0.05
        if effective_timeout is None:
            while not self._loop_ready.wait(step):
                if not self._loop_thread or not self._loop_thread.is_alive():
                    raise RuntimeError("EthListener loop thread terminated during startup")
            return

        deadline = time.monotonic() + effective_timeout
        step = 0.05
        while not self._loop_ready.wait(step):
            if not self._loop_thread or not self._loop_thread.is_alive():
                raise RuntimeError("EthListener loop thread terminated during startup")
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for EthListener to start")

    def stop(self) -> None:
        """Stop the listener and close the websocket connection."""

        with self._raw_listener_lock:
            self._raw_message_listeners.clear()

        loop = self._loop
        if loop is None:
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None
            return

        future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
        future.result()
        self._shutdown_complete.wait()
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5)
        self._loop_thread = None
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def close(self) -> None:  # pragma: no cover - ergonomic alias
        self.stop()

    def __enter__(self) -> "EthListener":
        if self._auto_start:
            self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        self.stop()
        return None

    def on(self, event: str, callback: Callback) -> SubscriptionHandle:
        """Register ``callback`` for a JSON-RPC websocket ``event``."""

        if self._auto_start:
            self.start()

        with self._callback_lock:
            callbacks = self._callbacks[event]
            is_first = not callbacks
            callbacks.add(callback)

        self._ensure_loop_ready()

        try:
            if is_first:
                if self._is_connected():
                    subscription_id = self._sync_await(self._ensure_subscription(event))
                else:
                    subscription_id = self._subscription_ids.get(event)
                    if subscription_id is None:
                        self._logger.debug("Deferring subscription for %%s until websocket is connected", event)
            else:
                subscription_id = self._subscription_ids.get(event)
        except Exception:
            with self._callback_lock:
                callbacks = self._callbacks.get(event)
                if callbacks and callback in callbacks:
                    callbacks.discard(callback)
                    if not callbacks:
                        self._callbacks.pop(event, None)
            raise

        return SubscriptionHandle(self, event, callback, subscription_id)

    def off(self, event: str, callback: Optional[Callback] = None) -> None:
        """Remove callbacks for ``event`` and unsubscribe when none remain."""

        with self._callback_lock:
            callbacks = self._callbacks.get(event)
            if not callbacks:
                return

            if callback is None:
                callbacks.clear()
            else:
                callbacks.discard(callback)

            should_remove = not callbacks
            if should_remove:
                self._callbacks.pop(event, None)

        if should_remove:
            self._sync_await(self._drop_subscription(event))

    def add_raw_message_listener(self, callback: RawMessageCallback) -> None:
        """Invoke ``callback`` with every inbound websocket message string."""

        with self._raw_listener_lock:
            self._raw_message_listeners.add(callback)

    def remove_raw_message_listener(self, callback: RawMessageCallback) -> None:
        """Stop invoking ``callback`` for inbound websocket messages."""

        with self._raw_listener_lock:
            self._raw_message_listeners.discard(callback)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._connected_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._loop_ready.set()

        try:
            loop.run_until_complete(self._connection_manager())
        finally:
            with suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._loop = None
            self._shutdown_complete.set()

    def _ensure_loop_ready(self) -> None:
        if self._loop is not None:
            return

        if not self._loop_thread or not self._loop_thread.is_alive():
            raise RuntimeError("EthListener has not been started; call start() before subscribing.")

        wait_timeout = self._start_timeout
        step = 0.05
        if wait_timeout is not None and wait_timeout <= 0:
            wait_timeout = None

        if wait_timeout is None:
            while not self._loop_ready.wait(step):
                if not self._loop_thread or not self._loop_thread.is_alive():
                    raise RuntimeError("EthListener loop thread terminated during startup")
        else:
            deadline = time.monotonic() + wait_timeout
            while not self._loop_ready.wait(step):
                if not self._loop_thread or not self._loop_thread.is_alive():
                    raise RuntimeError("EthListener loop thread terminated during startup")
                if time.monotonic() >= deadline:
                    raise RuntimeError("EthListener loop did not start within expected timeout")

        if self._loop is None:
            raise RuntimeError("EthListener has not been started; call start() before subscribing.")

    async def _connection_manager(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self._logger.debug("Connected to %s", self._ws_url)
                    self._ws = ws
                    assert self._connected_event is not None
                    self._connected_event.set()
                    await self._resubscribe_all()
                    await self._receive_loop(ws)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - network failures
                self._logger.debug("Websocket connection error: %s", exc, exc_info=True)
                if self._connected_event:
                    self._connected_event.clear()
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(self._reconnect_delay)
            finally:
                self._clear_pending_requests()
                if self._connected_event:
                    self._connected_event.clear()
                self._ws = None

    async def _receive_loop(self, ws: WebSocketClientProtocol) -> None:
        async for message in ws:
            if isinstance(message, (bytes, bytearray, memoryview)):
                message = bytes(message).decode("utf-8")
            await self._handle_message(message)

    async def _handle_message(self, message: str) -> None:
        self._notify_raw_message_listeners(message)
        if self._logger.isEnabledFor(logging.DEBUG):
            self._logger.debug("Received message: %s", message)

        payload = json.loads(message)

        if "id" in payload:
            request_id = payload["id"]

            event = self._pending_subscriptions.pop(request_id, None)
            if event is not None:
                if "error" in payload:
                    self._logger.debug("Subscription for %s failed: %s", event, payload["error"])
                else:
                    result = payload.get("result")
                    if isinstance(result, str):
                        self._subscription_ids[event] = result
                        self._event_for_subscription[result] = event
                    else:
                        self._logger.debug(
                            "Subscription for %s returned unexpected result: %s", event, result
                        )
                return

            event = self._pending_unsubscriptions.pop(request_id, None)
            if event is not None:
                if "error" in payload:
                    self._logger.debug("Unsubscribe for %s failed: %s", event, payload["error"])
                return

            return

        if payload.get("method") != "eth_subscription":
            self._logger.debug("Ignoring message: %s", payload)
            return

        params = payload.get("params", {})
        subscription_id = params.get("subscription")
        data = params.get("result")
        if not subscription_id:
            return

        event = self._event_for_subscription.get(subscription_id)
        if not event:
            self._logger.debug("No event registered for subscription %s", subscription_id)
            return

        structured_event = _parse_event(event, subscription_id, data)
        self._dispatch_event(event, structured_event)

    def _notify_raw_message_listeners(self, message: str) -> None:
        with self._raw_listener_lock:
            listeners = list(self._raw_message_listeners)

        if not listeners:
            return

        for listener in listeners:
            try:
                listener(message)
            except Exception:  # pragma: no cover - defensive logging only
                self._logger.debug("Raw message listener raised", exc_info=True)

    def _dispatch_event(self, event: str, payload: BaseEthereumEvent) -> None:
        callbacks = list(self._callbacks.get(event, ()))
        if not callbacks:
            return

        def _run(callback: Callback) -> None:
            try:
                callback(payload)
            except Exception as exc:  # pragma: no cover - user callback errors
                self._logger.debug("Callback for %s raised: %s", event, exc, exc_info=True)

        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)

        for callback in callbacks:
            self._executor.submit(_run, callback)

    async def _ensure_subscription(self, event: str) -> Optional[str]:
        if event in self._subscription_ids:
            return self._subscription_ids[event]
        return await self._subscribe(event)

    async def _subscribe(self, event: str) -> Optional[str]:
        request_id = next(self._request_id_counter)
        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "eth_subscribe",
            "params": [event],
        }
        self._pending_subscriptions[request_id] = event
        try:
            await self._send(message)
        except Exception:
            self._pending_subscriptions.pop(request_id, None)
            raise
        return self._subscription_ids.get(event)

    async def _drop_subscription(self, event: str) -> None:
        subscription_id = self._subscription_ids.pop(event, None)
        if not subscription_id:
            return
        self._event_for_subscription.pop(subscription_id, None)
        request_id = next(self._request_id_counter)
        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "eth_unsubscribe",
            "params": [subscription_id],
        }
        self._pending_unsubscriptions[request_id] = event
        try:
            await self._send(message)
        except Exception:
            self._pending_unsubscriptions.pop(request_id, None)
            raise

    async def _resubscribe_all(self) -> None:
        if not self._callbacks:
            return
        current_events = list(self._callbacks.keys())
        self._subscription_ids.clear()
        self._event_for_subscription.clear()
        for event in current_events:
            with suppress(Exception):
                await self._subscribe(event)

    def _is_connected(self) -> bool:
        event = self._connected_event
        return bool(event and event.is_set())

    async def _send(self, payload: Dict[str, Any]) -> None:
        if not self._send_lock or not self._connected_event:
            raise RuntimeError("Listener is not running")
        async with self._send_lock:
            stop_event = self._stop_event
            if stop_event and stop_event.is_set():
                raise RuntimeError("Listener is not running")

            waiters = [asyncio.create_task(self._connected_event.wait())]
            if stop_event is not None:
                waiters.append(asyncio.create_task(stop_event.wait()))

            done, pending = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                with suppress(Exception):
                    task.result()

            if pending:
                for task in pending:
                    task.cancel()
                with suppress(Exception):
                    await asyncio.gather(*pending, return_exceptions=True)

            if stop_event and stop_event.is_set() and not self._connected_event.is_set():
                raise RuntimeError("Listener is not running")

            if not self._ws:
                raise RuntimeError("Websocket not connected")
            await self._ws.send(json.dumps(payload))

    async def _shutdown_async(self) -> None:
        if not self._stop_event:
            return
        self._stop_event.set()
        for event in list(self._subscription_ids.keys()):
            with suppress(Exception):
                await self._drop_subscription(event)
        ws = self._ws
        if ws is not None:
            close = getattr(ws, "close", None)
            if close is not None:
                with suppress(Exception):
                    await close()
        self._clear_pending_requests()
        if self._connected_event:
            self._connected_event.clear()

    def _sync_await(self, awaitable: Coroutine[Any, Any, T]) -> T:
        loop = self._loop
        if loop is None:
            raise RuntimeError("Listener has not been started")
        return asyncio.run_coroutine_threadsafe(awaitable, loop).result()

    def _clear_pending_requests(self) -> None:
        self._pending_subscriptions.clear()
        self._pending_unsubscriptions.clear()

    # ------------------------------------------------------------------
    # Testing helpers
    # ------------------------------------------------------------------
    def inject_event(self, event: str, payload: Any, subscription_id: str = "test") -> None:
        structured_event = _parse_event(event, subscription_id, payload)
        self._dispatch_event(event, structured_event)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        with suppress(Exception):
            self.stop()
