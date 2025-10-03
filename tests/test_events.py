from __future__ import annotations

from typing import Any, Dict

import pytest

from eth_listener.events import NewHeadEvent, NewPendingTransactionEvent


def test_new_head_event_converts_hex_values() -> None:
    payload: Dict[str, Any] = {
        "number": "0x10",
        "hash": "0xabc",
        "parentHash": "0xdef",
        "nonce": "0x00",
        "difficulty": "0x2a",
        "totalDifficulty": "0x2b",
        "size": "0x200",
        "gasLimit": "0x5208",
        "gasUsed": "0x0",
        "timestamp": "0x5f3759df",
        "transactions": ["0x1"],
        "uncles": None,
        "baseFeePerGas": "0x1",
    }

    event = NewHeadEvent.from_payload("0xsub", payload)

    assert event.subscription_id == "0xsub"
    assert event.number == 16
    assert event.difficulty == 42
    assert event.total_difficulty == 43
    assert event.size == 512
    assert event.gas_limit == 0x5208
    assert event.gas_used == 0
    assert event.timestamp == int("5f3759df", 16)
    assert event.transactions == ["0x1"]
    assert event.uncles == []
    assert event.base_fee_per_gas == 1
    assert event.raw is payload


@pytest.mark.parametrize("payload", ["0xdeadbeef", "deadbeef", "0x123"])
def test_new_pending_transaction_event(payload: str) -> None:
    event = NewPendingTransactionEvent.from_payload("0xabc", payload)
    assert event.transaction_hash == payload
    assert event.raw == payload
    assert event.subscription_id == "0xabc"
