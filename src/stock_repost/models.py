"""Domain models for the stock reposting engine.

These are immutable records representing stock-ledger state.  Each entry
captures a single stock movement together with the resulting balances.
Entries are frozen because reposting creates *new* entries rather than
mutating existing ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class StockLedgerEntry:
    """A single stock ledger entry recording a stock movement.

    Every field uses ``Decimal`` for monetary or quantity values to
    avoid floating-point drift.  The ``fifo_snapshot`` captures the
    FIFO layer state *after* this entry, enabling efficient reposting
    from any point in the timeline.

    Attributes:
        item: Item code / SKU.
        warehouse: Warehouse where the movement occurred.
        voucher_type: Source document type (e.g. ``"Purchase Receipt"``).
        voucher_no: Source document identifier.
        posting_datetime: Effective timestamp of the movement.
        creation_seq: Tie-breaker when multiple entries share the same
            ``posting_datetime``.
        qty_change: Signed quantity delta (positive = receipt,
            negative = issue).
        incoming_rate: Per-unit rate for receipts; ``Decimal("0")``
            for issues.
        outgoing_value: Total value consumed for issues;
            ``Decimal("0")`` for receipts.
        value_change: Net value change applied to the running balance.
        balance_qty: Running quantity balance *after* this entry.
        balance_value: Running value balance *after* this entry.
        fifo_snapshot: FIFO layer state after this entry, as a tuple
            of ``(qty, rate)`` pairs.
        depends_on: Optional voucher-detail key linking this entry to
            an upstream consumption SLE.
    """

    item: str
    warehouse: str
    voucher_type: str
    voucher_no: str
    posting_datetime: datetime
    creation_seq: int
    qty_change: Decimal
    incoming_rate: Decimal
    outgoing_value: Decimal
    value_change: Decimal
    balance_qty: Decimal
    balance_value: Decimal
    fifo_snapshot: tuple[tuple[Decimal, Decimal], ...]
    depends_on: str | None = None
