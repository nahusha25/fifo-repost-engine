"""Runnable demonstration of the Milk -> Milk Powder task scenario.

Constructs the scenario, establishes baseline balances, modifies PR-01
incoming rate from 60 to 80, triggers reposting, and prints a before/after
summary of stock values and ledger changes.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Ensure src/ is importable when running standalone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostEngine


def main() -> None:
    """Run the task scenario and print a formatted before/after summary."""
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

    # Initial repost to establish balances
    engine.repost("Milk", "Stores", t_open)

    # Capture BEFORE state
    mfg_milk_before = next(
        e
        for e in ledger.all_entries()
        if e.voucher_no == "MFG-01" and e.item == "Milk"
    )
    mfg_powder_before = next(
        e
        for e in ledger.all_entries()
        if e.voucher_no == "MFG-01" and e.item == "Milk Powder"
    )
    dn_02_before = next(
        e for e in ledger.all_entries() if e.voucher_no == "DN-02"
    )
    milk_entries_before = [
        e for e in ledger.all_entries() if e.item == "Milk"
    ]

    print("=" * 60)
    print("BEFORE REPOST (PR-01 incoming_rate = 60)")
    print("=" * 60)
    print(f"MFG-01 Milk Outgoing Value:  {mfg_milk_before.outgoing_value}")
    print(f"MFG-01 Powder Incoming Rate: {mfg_powder_before.incoming_rate}")
    print(f"DN-02 Outgoing Value:        {dn_02_before.outgoing_value}")
    print(
        f"Final Milk Balance:          {milk_entries_before[-1].balance_qty} qty @ "
        f"{milk_entries_before[-1].balance_value} value"
    )

    # Update PR-01 incoming_rate from 60 to 80
    from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))

    # Repost
    res = engine.repost("Milk", "Stores", from_dt, early_stop=True)

    # Capture AFTER state
    pr_01_after = next(
        e for e in ledger.all_entries() if e.voucher_no == "PR-01"
    )
    dn_01_after = next(
        e for e in ledger.all_entries() if e.voucher_no == "DN-01"
    )
    pr_02_after = next(
        e for e in ledger.all_entries() if e.voucher_no == "PR-02"
    )
    mfg_milk_after = next(
        e
        for e in ledger.all_entries()
        if e.voucher_no == "MFG-01" and e.item == "Milk"
    )
    mfg_powder_after = next(
        e
        for e in ledger.all_entries()
        if e.voucher_no == "MFG-01" and e.item == "Milk Powder"
    )
    dn_02_after = next(
        e for e in ledger.all_entries() if e.voucher_no == "DN-02"
    )
    cogs_delta = dn_02_after.outgoing_value - dn_02_before.outgoing_value
    milk_entries_after = [
        e for e in ledger.all_entries() if e.item == "Milk"
    ]

    print("\n" + "=" * 60)
    print("AFTER REPOST (PR-01 incoming_rate = 80)")
    print("=" * 60)
    print(f"PR-01 Value Change:          {pr_01_after.value_change}")
    print(
        f"DN-01 Value Change:          {dn_01_after.value_change} "
        f"(balance_value after: {dn_01_after.balance_value})"
    )
    print(f"PR-02 Value Change:          {pr_02_after.value_change}")
    print(f"MFG-01 Milk Outgoing Value:  {mfg_milk_after.outgoing_value}")
    print(f"MFG-01 Powder Incoming Rate: {mfg_powder_after.incoming_rate}")
    print(f"DN-02 Outgoing Value:        {dn_02_after.outgoing_value}")
    print(f"COGS Delta on Finished Good: +{cogs_delta}")
    print(
        f"Final Milk Balance:          {milk_entries_after[-1].balance_qty} qty @ "
        f"{milk_entries_after[-1].balance_value} value"
    )

    print("\nRepostResult Partition:")
    print(
        "  value_changed:    "
        + str([(vc.voucher_no, vc.item) for vc in res.value_changed])
    )
    print(
        "  re_evaluated_only: "
        + str([e.voucher_no for e in res.re_evaluated_only])
    )
    print(f"  entries_replayed:  {res.entries_replayed}")


if __name__ == "__main__":
    main()
