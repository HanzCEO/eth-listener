"""Minimal example that prints new Ethereum blocks from a local node."""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from eth_listener import EthListener, NewHeadEvent

WS_URL = "ws://localhost:8545"


def _build_handler() -> Callable[[NewHeadEvent], None]:
    def _handler(event: NewHeadEvent) -> None:
        number = event.number if event.number is not None else "?"
        print(f"Block #{number}: hash={event.hash} miner={event.miner}")
        if event.raw is not None:
            print(json.dumps(event.raw, indent=2))

    return _handler


def main() -> None:
    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

    transport_logger = logging.getLogger("websockets")
    transport_logger.setLevel(logging.DEBUG)

    logging.getLogger("eth_listener").setLevel(logging.DEBUG)

    listener = EthListener(WS_URL, start_timeout=0)
    handler = _build_handler()

    def _print_raw_message(raw: str) -> None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(raw)
        else:
            print(json.dumps(parsed, indent=2))

    # uncomment this if you want to see ws json messages
    listener.add_raw_message_listener(_print_raw_message)

    print(f"Connecting to {WS_URL} for newHeads events… Press Ctrl+C to exit.")
    with listener:
        listener.on("newHeads", handler)
        listener.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down listener…")
        finally:
            pass


if __name__ == "__main__":
    main()
