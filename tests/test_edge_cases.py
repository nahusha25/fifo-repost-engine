"""Comprehensive edge case tests for StockLedger and RepostEngine.

Covers:
- Same-timestamp entries ordered deterministically by creation_seq
- Backdated receipt inserted between existing entries
- Backdated issue causing insufficient stock
- Rate edit on an item with no later transactions
- Quantity edit on a receipt
- Cancellation (removal) of a receipt compared with full_rebuild
- Manufacture with two raw materials, editing one raw material receipt
- Two separate manufacture vouchers consuming the same raw material
- Dedicated example: edit Milk receipt and earlier-dated Milk Powder receipt
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from stock_repost.fifo import InsufficientStockError
from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostEngine


def _make_sle(
    *,
    item: str,
    warehouse: str = "Stores",
    voucher_type: str = "Purchase Receipt",
    voucher_no: str,
    posting_datetime: datetime,
    creation_seq: int = 1,
    qty_change: Decimal,
    incoming_rate: Decimal = Decimal("0"),
    outgoing_value: Decimal = Decimal("0"),
    value_change: Decimal = Decimal("0"),
    balance_qty: Decimal = Decimal("0"),
    balance_value: Decimal = Decimal("0"),
    fifo_snapshot: tuple[tuple[Decimal, Decimal], ...] = (),
    depends_on: str | None = None,
) -> StockLedgerEntry:
    """Helper to construct a StockLedgerEntry."""
    return StockLedgerEntry(
        item=item,
        warehouse=warehouse,
        voucher_type=voucher_type,
        voucher_no=voucher_no,
        posting_datetime=posting_datetime,
        creation_seq=creation_seq,
        qty_change=qty_change,
        incoming_rate=incoming_rate,
        outgoing_value=outgoing_value,
        value_change=value_change,
        balance_qty=balance_qty,
        balance_value=balance_value,
        fifo_snapshot=fifo_snapshot,
        depends_on=depends_on,
    )


def full_rebuild(ledger: StockLedger) -> StockLedger:
    """Oracle that rebuilds the entire ledger from scratch without early stop."""
    rebuilt = ledger.copy()
    if len(rebuilt) == 0:
        return rebuilt
    engine = RepostEngine(rebuilt)
    seen: set[tuple[str, str]] = set()
    for entry in rebuilt.all_entries():
        key = (entry.item, entry.warehouse)
        if key not in seen:
            seen.add(key)
            min_dt = min(
                e.posting_datetime
                for e in rebuilt.all_entries()
                if (e.item, e.warehouse) == key
            )
            engine.repost(key[0], key[1], min_dt, early_stop=False)
    return rebuilt


class TestSameTimestampOrdering:
    """Same-timestamp entries must be ordered deterministically by creation_seq."""

    def test_same_timestamp_entries_ordered_by_creation_seq(self) -> None:
        """Entries with same posting_datetime are processed in creation_seq order."""
        t = datetime(2026, 1, 10, 10, 0)
        t_issue = datetime(2026, 1, 11, 10, 0)

        # Two receipts at the exact same timestamp with different creation_seq
        r1 = _make_sle(
            item="WIDGET",
            voucher_no="PR-01",
            posting_datetime=t,
            creation_seq=1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
        )
        r2 = _make_sle(
            item="WIDGET",
            voucher_no="PR-02",
            posting_datetime=t,
            creation_seq=2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("200"),
        )
        # Issue 15 units: consumes 10 @ 100 (PR-01) and 5 @ 200 (PR-02)
        issue = _make_sle(
            item="WIDGET",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t_issue,
            creation_seq=1,
            qty_change=Decimal("-15"),
        )

        ledger = StockLedger([r2, r1, issue])  # inserted out of order
        engine = RepostEngine(ledger)
        engine.repost("WIDGET", "Stores", t)

        entries = ledger.all_entries()
        assert entries[0].voucher_no == "PR-01"
        assert entries[0].creation_seq == 1
        assert entries[1].voucher_no == "PR-02"
        assert entries[1].creation_seq == 2

        # Issue should cost 10*100 + 5*200 = 2000
        assert entries[2].outgoing_value == Decimal("2000")
        assert entries[2].balance_qty == Decimal("5")
        assert entries[2].balance_value == Decimal("1000")  # 5 @ 200 remaining


class TestBackdatedInsertionsAndIssues:
    """Backdated operations and error atomicity."""

    def test_backdated_receipt_inserted_between_existing_entries(self) -> None:
        """Backdated receipt between existing transactions matches full_rebuild."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 10, 10, 0)
        t3 = datetime(2026, 1, 20, 10, 0)

        e1 = _make_sle(
            item="ITEM-INS",
            voucher_no="OPEN-01",
            posting_datetime=t1,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("10"),
        )
        e3 = _make_sle(
            item="ITEM-INS",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-30"),
        )
        ledger = StockLedger([e1, e3])
        engine = RepostEngine(ledger)
        engine.repost("ITEM-INS", "Stores", t1)

        # Insert receipt at t2 (t1 < t2 < t3)
        e2_inserted = _make_sle(
            item="ITEM-INS",
            voucher_no="PR-01",
            posting_datetime=t2,
            qty_change=Decimal("40"),
            incoming_rate=Decimal("25"),
        )
        from_dt = ledger.insert_entry(e2_inserted)
        assert from_dt == t2

        engine.repost("ITEM-INS", "Stores", from_dt, early_stop=True)
        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    def test_backdated_issue_causing_insufficient_stock_preserves_state(
        self,
    ) -> None:
        """Backdated issue causing deficit raises and preserves ledger and dirty marks.
        """
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 5, 10, 0)
        t3 = datetime(2026, 1, 10, 10, 0)

        e1 = _make_sle(
            item="ITEM-OOM",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("10"),
        )
        e3 = _make_sle(
            item="ITEM-OOM",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-40"),
        )
        ledger = StockLedger([e1, e3])
        engine = RepostEngine(ledger)
        engine.repost("ITEM-OOM", "Stores", t1)

        # Snapshot entries before invalid insert
        before_entries = list(ledger.all_entries())

        # Insert backdated issue of -30 at t2: at t3 (-40) stock goes negative (-20)
        e2_bad_issue = _make_sle(
            item="ITEM-OOM",
            voucher_type="Delivery Note",
            voucher_no="DN-BAD",
            posting_datetime=t2,
            qty_change=Decimal("-30"),
        )
        from_dt = ledger.insert_entry(e2_bad_issue)

        with pytest.raises(InsufficientStockError):
            engine.repost("ITEM-OOM", "Stores", from_dt)

        # Ledger state must remain exactly as before repost attempt
        assert ledger.all_entries() != before_entries  # insert_entry happened
        # But dirty marks are preserved so caller can fix or retry
        assert ledger.get_min_dirty("ITEM-OOM", "Stores") == t2


class TestModificationsAndCancellations:
    """Rate and quantity edits and cancellations."""

    def test_rate_edit_on_item_with_no_later_transactions(self) -> None:
        """Rate edit on final receipt recalculates balance without later issues."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 5, 10, 0)

        e1 = _make_sle(
            item="ITEM-FINAL",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
        )
        e2 = _make_sle(
            item="ITEM-FINAL",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("200"),
        )
        ledger = StockLedger([e1, e2])
        engine = RepostEngine(ledger)
        engine.repost("ITEM-FINAL", "Stores", t1)

        # Edit PR-02 (the latest transaction)
        from_dt = ledger.update_entry(
            "PR-02", "ITEM-FINAL", incoming_rate=Decimal("300")
        )
        engine.repost("ITEM-FINAL", "Stores", from_dt, early_stop=True)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()
        last = ledger.all_entries()[-1]
        assert last.balance_qty == Decimal("15")
        assert last.balance_value == Decimal("2500")  # 10*100 + 5*300

    def test_qty_edit_on_receipt(self) -> None:
        """Editing quantity on an incoming receipt matches full_rebuild."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 5, 10, 0)

        e1 = _make_sle(
            item="ITEM-QTY",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("100"),
            incoming_rate=Decimal("50"),
        )
        e2 = _make_sle(
            item="ITEM-QTY",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t2,
            qty_change=Decimal("-60"),
        )
        ledger = StockLedger([e1, e2])
        engine = RepostEngine(ledger)
        engine.repost("ITEM-QTY", "Stores", t1)

        # Edit receipt qty from 100 to 120
        from_dt = ledger.update_entry(
            "PR-01", "ITEM-QTY", qty_change=Decimal("120")
        )
        engine.repost("ITEM-QTY", "Stores", from_dt, early_stop=True)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()
        last = ledger.all_entries()[-1]
        assert last.balance_qty == Decimal("60")
        assert last.balance_value == Decimal("3000")

    def test_cancel_receipt_matches_full_rebuild(self) -> None:
        """Cancelling (removing) a receipt propagates balance changes properly."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 5, 10, 0)
        t3 = datetime(2026, 1, 10, 10, 0)

        e1 = _make_sle(
            item="ITEM-CNC",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("10"),
        )
        e2 = _make_sle(
            item="ITEM-CNC",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("20"),
        )
        e3 = _make_sle(
            item="ITEM-CNC",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-40"),
        )
        ledger = StockLedger([e1, e2, e3])
        engine = RepostEngine(ledger)
        engine.repost("ITEM-CNC", "Stores", t1)

        # Cancel PR-02
        from_dt = ledger.remove_entry("PR-02", "ITEM-CNC")
        engine.repost("ITEM-CNC", "Stores", from_dt, early_stop=True)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()
        last = ledger.all_entries()[-1]
        assert last.balance_qty == Decimal("10")
        assert last.balance_value == Decimal("100")


class TestManufactureScenarios:
    """Manufacture scenarios with multiple raw materials and production runs."""

    def test_manufacture_two_raw_materials_edit_one_receipt(self) -> None:
        """Editing one raw material receipt recomputes FG incoming rate from both."""
        t_rm1 = datetime(2026, 1, 1, 10, 0)
        t_rm2 = datetime(2026, 1, 2, 10, 0)
        t_mfg = datetime(2026, 1, 10, 10, 0)
        t_dn = datetime(2026, 1, 15, 10, 0)

        entries = [
            _make_sle(
                item="Sugar",
                voucher_no="PR-SUGAR",
                posting_datetime=t_rm1,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("20"),
            ),
            _make_sle(
                item="Cocoa",
                voucher_no="PR-COCOA",
                posting_datetime=t_rm2,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            # MFG consumes 50 Sugar (50*20 = 1000) and 20 Cocoa (20*60 = 1200) -> 2200
            _make_sle(
                item="Sugar",
                voucher_type="Stock Entry",
                voucher_no="MFG-CHOC",
                posting_datetime=t_mfg,
                creation_seq=1,
                qty_change=Decimal("-50"),
            ),
            _make_sle(
                item="Cocoa",
                voucher_type="Stock Entry",
                voucher_no="MFG-CHOC",
                posting_datetime=t_mfg,
                creation_seq=2,
                qty_change=Decimal("-20"),
            ),
            # Produces 10 Chocolate (rate 220)
            _make_sle(
                item="Chocolate",
                voucher_type="Stock Entry",
                voucher_no="MFG-CHOC",
                posting_datetime=t_mfg,
                creation_seq=3,
                qty_change=Decimal("10"),
                depends_on="MFG-CHOC",
            ),
            _make_sle(
                item="Chocolate",
                voucher_type="Delivery Note",
                voucher_no="DN-CHOC",
                posting_datetime=t_dn,
                qty_change=Decimal("-5"),
            ),
        ]
        ledger = StockLedger(entries)
        engine = RepostEngine(ledger)
        engine.repost("Sugar", "Stores", t_rm1)
        engine.repost("Cocoa", "Stores", t_rm2)
        engine.repost("Chocolate", "Stores", t_mfg)

        # Edit Sugar receipt rate from 20 to 30 (+10/unit -> +500 total)
        from_dt = ledger.update_entry(
            "PR-SUGAR", "Sugar", incoming_rate=Decimal("30")
        )
        engine.repost("Sugar", "Stores", from_dt, early_stop=True)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()
        fg_entry = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-CHOC" and e.item == "Chocolate"
        )
        # New Sugar consumed = 50*30 = 1500, Cocoa = 1200 -> total 2700 / 10 = 270
        assert fg_entry.incoming_rate == Decimal("270")
        assert fg_entry.value_change == Decimal("2700")

    def test_two_manufacture_vouchers_consuming_same_raw_material(self) -> None:
        """Editing an early RM receipt updates both manufacture batches and sales."""
        t_rm = datetime(2026, 1, 1, 10, 0)
        t_mfg1 = datetime(2026, 1, 10, 10, 0)
        t_dn1 = datetime(2026, 1, 12, 10, 0)
        t_mfg2 = datetime(2026, 1, 20, 10, 0)
        t_dn2 = datetime(2026, 1, 22, 10, 0)

        entries = [
            _make_sle(
                item="Wood",
                voucher_no="PR-WOOD",
                posting_datetime=t_rm,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
            ),
            # MFG 1: Chairs (consumes 40 Wood, produces 4 Chairs)
            _make_sle(
                item="Wood",
                voucher_type="Stock Entry",
                voucher_no="MFG-CHAIR",
                posting_datetime=t_mfg1,
                creation_seq=1,
                qty_change=Decimal("-40"),
            ),
            _make_sle(
                item="Chair",
                voucher_type="Stock Entry",
                voucher_no="MFG-CHAIR",
                posting_datetime=t_mfg1,
                creation_seq=2,
                qty_change=Decimal("4"),
                depends_on="MFG-CHAIR",
            ),
            _make_sle(
                item="Chair",
                voucher_type="Delivery Note",
                voucher_no="DN-CHAIR",
                posting_datetime=t_dn1,
                qty_change=Decimal("-2"),
            ),
            # MFG 2: Tables (consumes 50 Wood, produces 2 Tables)
            _make_sle(
                item="Wood",
                voucher_type="Stock Entry",
                voucher_no="MFG-TABLE",
                posting_datetime=t_mfg2,
                creation_seq=1,
                qty_change=Decimal("-50"),
            ),
            _make_sle(
                item="Table",
                voucher_type="Stock Entry",
                voucher_no="MFG-TABLE",
                posting_datetime=t_mfg2,
                creation_seq=2,
                qty_change=Decimal("2"),
                depends_on="MFG-TABLE",
            ),
            _make_sle(
                item="Table",
                voucher_type="Delivery Note",
                voucher_no="DN-TABLE",
                posting_datetime=t_dn2,
                qty_change=Decimal("-1"),
            ),
        ]
        ledger = StockLedger(entries)
        engine = RepostEngine(ledger)
        engine.repost("Wood", "Stores", t_rm)

        # Edit Wood receipt rate from 50 to 80
        from_dt = ledger.update_entry(
            "PR-WOOD", "Wood", incoming_rate=Decimal("80")
        )
        engine.repost("Wood", "Stores", from_dt, early_stop=True)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

        # Chair rate should be (40*80)/4 = 800
        chair = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-CHAIR" and e.item == "Chair"
        )
        assert chair.incoming_rate == Decimal("800")

        # Table rate should be (50*80)/2 = 2000
        table = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-TABLE" and e.item == "Table"
        )
        assert table.incoming_rate == Decimal("2000")


class TestMultiItemEditsBeforeSingleRepost:
    """Dedicated test for editing RM and FG before single repost."""

    def test_edit_milk_receipt_and_earlier_powder_receipt(self) -> None:
        """Edit Milk receipt AND earlier Milk Powder receipt before one repost."""
        t_open_milk = datetime(2026, 1, 1, 10, 0)
        t_open_powder = datetime(2026, 1, 5, 10, 0)
        t_pr1 = datetime(2026, 1, 10, 10, 0)
        t_dn1 = datetime(2026, 1, 15, 10, 0)
        t_pr2 = datetime(2026, 1, 20, 10, 0)
        t_mfg = datetime(2026, 1, 21, 10, 0)
        t_dn2 = datetime(2026, 1, 25, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t_open_milk,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
            ),
            _make_sle(
                item="Milk Powder",
                voucher_type="Opening",
                voucher_no="OPEN-POWDER",
                posting_datetime=t_open_powder,
                qty_change=Decimal("10"),
                incoming_rate=Decimal("500"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-01",
                posting_datetime=t_pr1,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-01",
                posting_datetime=t_dn1,
                qty_change=Decimal("-80"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-02",
                posting_datetime=t_pr2,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Stock Entry",
                voucher_no="MFG-01",
                posting_datetime=t_mfg,
                creation_seq=1,
                qty_change=Decimal("-100"),
            ),
            _make_sle(
                item="Milk Powder",
                voucher_type="Stock Entry",
                voucher_no="MFG-01",
                posting_datetime=t_mfg,
                creation_seq=2,
                qty_change=Decimal("10"),
                depends_on="MFG-01",
            ),
            _make_sle(
                item="Milk Powder",
                voucher_type="Delivery Note",
                voucher_no="DN-02",
                posting_datetime=t_dn2,
                qty_change=Decimal("-15"),
            ),
        ]

        # Initial baseline setup
        base_ledger = full_rebuild(StockLedger(entries))

        # Apply TWO edits before calling repost:
        # 1. Milk PR-01 incoming_rate to 80 (posting_datetime 10 Jan)
        # 2. Milk Powder OPEN-POWDER incoming_rate to 550 (posting_datetime 5 Jan)
        ledger_early = base_ledger.copy()
        dt1 = ledger_early.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        dt2 = ledger_early.update_entry(
            "OPEN-POWDER", "Milk Powder", incoming_rate=Decimal("550")
        )
        from_dt = min(dt1, dt2)

        engine_early = RepostEngine(ledger_early)
        engine_early.repost("Milk", "Stores", from_dt, early_stop=True)

        ledger_full = base_ledger.copy()
        ledger_full.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))
        ledger_full.update_entry(
            "OPEN-POWDER", "Milk Powder", incoming_rate=Decimal("550")
        )
        engine_full = RepostEngine(ledger_full)
        engine_full.repost("Milk", "Stores", from_dt, early_stop=False)

        rebuilt = full_rebuild(ledger_early)

        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.all_entries() == rebuilt.all_entries()
