# eth-listener

`eth-listener` is a tiny helper library that wraps Ethereum JSON-RPC websocket
subscriptions with a synchronous, callback-based interface. Under the hood it
drives a lean asyncio websocket client (built on top of
[`websockets`](https://pypi.org/project/websockets/)) and keeps your callbacks
running on dedicated worker threads, letting you interact with Ethereum events
using a simple ``.on(event, callback)`` pattern that feels familiar to
JavaScript developers.

## Features

- Connect to any Ethereum node that exposes a websocket JSON-RPC endpoint
- Strongly typed callback payloads for common subscriptions such as
  `newHeads` and `newPendingTransactions`
- Automatic reconnection with stored subscription state
- Callbacks run in a dedicated worker pool so they never block the websocket
- Simple synchronous API: no need to manage event loops yourself
- Works with any JSON-RPC node that speaks standard websocket subscriptions

## Installation

```bash
pip install eth-listener
```

Python 3.9 or newer is required.

## Quickstart

```python
import argparse
import json
import logging
import time

from eth_listener import EthListener, NewHeadEvent

# Replace with your node's websocket endpoint
WS_URL = "wss://mainnet.infura.io/ws/v3/<your-project-id>"

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-raw", action="store_true")
    parser.add_argument("--dump-messages", action="store_true")
    parser.add_argument("--transport-debug", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logging.getLogger("websockets").setLevel(
        logging.DEBUG if args.transport_debug else logging.WARNING
    )
    if args.debug or args.dump_messages:
        logging.getLogger("eth_listener").setLevel(logging.DEBUG)

    listener = EthListener(WS_URL, start_timeout=0)

    def handle_new_head(event: NewHeadEvent) -> None:
        print(f"New block {event.number} with hash {event.hash}")
        if args.dump_raw:
            print(json.dumps(event.raw, indent=2))

    def print_message(raw: str) -> None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(raw)
        else:
            print(json.dumps(parsed, indent=2))

    if args.dump_messages:
        listener.add_raw_message_listener(print_message)

    with listener:
        listener.start(timeout=0)
        listener.on("newHeads", handle_new_head)

        # Keep your application alive while callbacks run on background threads
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if args.dump_messages:
                listener.remove_raw_message_listener(print_message)


if __name__ == "__main__":
    main()
```

### Unsubscribing

Every call to :meth:`EthListener.on` returns a :class:`~eth_listener.SubscriptionHandle`.
Use it to stop listening when you no longer need the events:

```python
handle = listener.on("newHeads", handle_new_head)
...
handle.unsubscribe()  # Removes the callback and issues eth_unsubscribe
```

Stopping the listener (via :meth:`stop`, the context manager, or object
finalisation) also unsubscribes from any remaining topics.

## Event payloads

The library ships with typed payloads for common subscriptions:

- :class:`~eth_listener.NewHeadEvent` for `newHeads`
- :class:`~eth_listener.NewPendingTransactionEvent` for `newPendingTransactions`

Each payload preserves the original JSON data in the ``raw`` attribute while
also exposing more convenient Pythonic fields (for example block numbers are
converted to integers).

Unknown subscriptions are delivered as the base
:class:`~eth_listener.BaseEthereumEvent`, letting you inspect the raw payload
and build your own helper classes when needed.

## Development

```bash
pip install -e .[dev]
pytest
```

Pull requests and issues are very welcome!
