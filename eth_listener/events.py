"""Typed event payloads dispatched by :class:`eth_listener.EthListener`.

The public callback API exposes strongly typed payload objects for common
Ethereum websocket subscriptions.  These dataclasses convert hexadecimal
fields into more convenient Python types while also preserving the raw
JSON payload for advanced consumers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "BaseEthereumEvent",
    "NewHeadEvent",
    "NewPendingTransactionEvent",
]


def _hex_to_int(value: Optional[str]) -> Optional[int]:
    """Best-effort conversion from a hex-encoded ``0x`` string to ``int``.

    ``None`` and empty strings are returned as ``None`` to make optional
    fields easier to work with.  The helper tolerates malformed inputs by
    raising :class:`ValueError`, which signals a programmer error in the
    upstream payload and is intentionally not swallowed.
    """

    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"", "0x"}:
            return 0
        return int(value, 16)
    raise TypeError(f"Expected hexadecimal string, received {type(value)!r}")


def _ensure_list(value: Optional[Iterable[Any]]) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


@dataclass(slots=True)
class BaseEthereumEvent:
    """Base payload that all typed Ethereum events extend."""

    subscription_id: str
    raw: Any


@dataclass(slots=True)
class NewHeadEvent(BaseEthereumEvent):
    """Represents the ``newHeads`` subscription payload.

    Numeric fields are converted from hexadecimal strings when possible.
    """

    number: Optional[int]
    hash: Optional[str]
    parent_hash: Optional[str]
    nonce: Optional[str]
    sha3_uncles: Optional[str]
    logs_bloom: Optional[str]
    transactions_root: Optional[str]
    state_root: Optional[str]
    receipts_root: Optional[str]
    miner: Optional[str]
    difficulty: Optional[int]
    total_difficulty: Optional[int]
    extra_data: Optional[str]
    size: Optional[int]
    gas_limit: Optional[int]
    gas_used: Optional[int]
    timestamp: Optional[int]
    transactions: List[str]
    uncles: List[str]
    base_fee_per_gas: Optional[int]

    @classmethod
    def from_payload(cls, subscription_id: str, payload: Dict[str, Any]) -> "NewHeadEvent":
        return cls(
            subscription_id=subscription_id,
            raw=payload,
            number=_hex_to_int(payload.get("number")),
            hash=payload.get("hash"),
            parent_hash=payload.get("parentHash"),
            nonce=payload.get("nonce"),
            sha3_uncles=payload.get("sha3Uncles"),
            logs_bloom=payload.get("logsBloom"),
            transactions_root=payload.get("transactionsRoot"),
            state_root=payload.get("stateRoot"),
            receipts_root=payload.get("receiptsRoot"),
            miner=payload.get("miner"),
            difficulty=_hex_to_int(payload.get("difficulty")),
            total_difficulty=_hex_to_int(payload.get("totalDifficulty")),
            extra_data=payload.get("extraData"),
            size=_hex_to_int(payload.get("size")),
            gas_limit=_hex_to_int(payload.get("gasLimit")),
            gas_used=_hex_to_int(payload.get("gasUsed")),
            timestamp=_hex_to_int(payload.get("timestamp")),
            transactions=_ensure_list(payload.get("transactions")),
            uncles=_ensure_list(payload.get("uncles")),
            base_fee_per_gas=_hex_to_int(payload.get("baseFeePerGas")),
        )


@dataclass(slots=True)
class NewPendingTransactionEvent(BaseEthereumEvent):
    """Represents the ``newPendingTransactions`` subscription payload."""

    transaction_hash: str

    @classmethod
    def from_payload(
        cls, subscription_id: str, payload: str
    ) -> "NewPendingTransactionEvent":
        return cls(subscription_id=subscription_id, raw=payload, transaction_hash=payload)
