"""Task scenario definition and factory functions.

Constructs the canonical Milk -> Milk Powder manufacturing scenario
spanning January 2026.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostEngine


def build_task_scenario(
    *, initial_repost: bool = True
) -> tuple[StockLedger, RepostEngine]:
    """Build and initialise the canonical Milk -> Milk Powder task scenario.

    Timeline:
    - 01 Jan 2026 10:00: Opening Milk +100 @ 50 (Warehouse: Stores)
    - 10 Jan 2026 10:00: PR-01 Purchase Receipt Milk +50 @ 60
    - 15 Jan 2026 10:00: DN-01 Delivery Note Milk -80
    - 20 Jan 2026 10:00: PR-02 Purchase Receipt Milk +50 @ 60
    - 21 Jan 2026 10:00: MFG-01 Stock Entry Milk -100 consumed, Milk Powder +10 produced
    - 25 Jan 2026 10:00: DN-02 Delivery Note Milk Powder -10

    Args:
        initial_repost: If True (default), runs an initial repost from
            the opening entry datetime to establish baseline valuation layers
            and running balance snapshots.

    Returns:
        A tuple of ``(StockLedger, RepostEngine)`` ready for adjustments.
    """
    t_open = datetime(2026, 1, 1, 10, 0)
    t_pr1 = datetime(2026, 1, 10, 10, 0)
    t_dn1 = datetime(2026, 1, 15, 10, 0)
    t_pr2 = datetime(2026, 1, 20, 10, 0)
    t_mfg = datetime(2026, 1, 21, 10, 0)
    t_dn2 = datetime(2026, 1, 25, 10, 0)

    entries = [
        StockLedgerEntry(
            item="Milk",
            warehouse="Stores",
            voucher_type="Opening",
            voucher_no="OPEN-01",
            posting_datetime=t_open,
            creation_seq=1,
            qty_change=Decimal("100"),
            incoming_rate=Decimal("50"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("5000"),
            balance_qty=Decimal("100"),
            balance_value=Decimal("5000"),
            fifo_snapshot=((Decimal("100"), Decimal("50")),),
        ),
        StockLedgerEntry(
            item="Milk",
            warehouse="Stores",
            voucher_type="Purchase Receipt",
            voucher_no="PR-01",
            posting_datetime=t_pr1,
            creation_seq=1,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("60"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("3000"),
            balance_qty=Decimal("150"),
            balance_value=Decimal("8000"),
            fifo_snapshot=(
                (Decimal("100"), Decimal("50")),
                (Decimal("50"), Decimal("60")),
            ),
        ),
        StockLedgerEntry(
            item="Milk",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t_dn1,
            creation_seq=1,
            qty_change=Decimal("-80"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("4000"),
            value_change=Decimal("-4000"),
            balance_qty=Decimal("70"),
            balance_value=Decimal("4000"),
            fifo_snapshot=(
                (Decimal("20"), Decimal("50")),
                (Decimal("50"), Decimal("60")),
            ),
        ),
        StockLedgerEntry(
            item="Milk",
            warehouse="Stores",
            voucher_type="Purchase Receipt",
            voucher_no="PR-02",
            posting_datetime=t_pr2,
            creation_seq=1,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("60"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("3000"),
            balance_qty=Decimal("120"),
            balance_value=Decimal("7000"),
            fifo_snapshot=(
                (Decimal("20"), Decimal("50")),
                (Decimal("50"), Decimal("60")),
                (Decimal("50"), Decimal("60")),
            ),
        ),
        StockLedgerEntry(
            item="Milk",
            warehouse="Stores",
            voucher_type="Stock Entry",
            voucher_no="MFG-01",
            posting_datetime=t_mfg,
            creation_seq=1,
            qty_change=Decimal("-100"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("5800"),
            value_change=Decimal("-5800"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("1200"),
            fifo_snapshot=((Decimal("20"), Decimal("60")),),
        ),
        StockLedgerEntry(
            item="Milk Powder",
            warehouse="Stores",
            voucher_type="Stock Entry",
            voucher_no="MFG-01",
            posting_datetime=t_mfg,
            creation_seq=2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("580"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("5800"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("5800"),
            fifo_snapshot=((Decimal("10"), Decimal("580")),),
            depends_on="MFG-01",
        ),
        StockLedgerEntry(
            item="Milk Powder",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-02",
            posting_datetime=t_dn2,
            creation_seq=1,
            qty_change=Decimal("-10"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("5800"),
            value_change=Decimal("-5800"),
            balance_qty=Decimal("0"),
            balance_value=Decimal("0"),
            fifo_snapshot=(),
        ),
    ]

    ledger = StockLedger(entries)
    engine = RepostEngine(ledger)
    if initial_repost:
        engine.repost("Milk", "Stores", t_open)

    return ledger, engine
