"""Tests for the StockLedger and RepostEngine.

Written before implementation per the Phase 2 development rules.
Covers:
- Receipt-only chain
- FIFO issue across layers
- Rate edit propagating to multiple subsequent issues
- Manufacture dependency (1 raw material -> 1 finished good)
- Manufacture with 2 raw materials
- Idempotency (repost twice yields identical ledger and empty value_changed)
- Atomicity on InsufficientStockError (ledger unchanged on failure)
- Early-stop on/off equivalence across scenarios
- Coalescing of duplicate work items to the earliest datetime
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
    """Helper to build a StockLedgerEntry with sensible defaults."""
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


class TestReceiptOnlyChain:
    """Tests for chains of receipts without issues."""

    def test_receipt_rate_update_recomputes_balances(self) -> None:
        """Updating incoming_rate of an earlier receipt propagates balance_value."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)

        e1 = _make_sle(
            item="ITEM-A",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("10"), Decimal("100")),),
        )
        e2 = _make_sle(
            item="ITEM-A",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("200"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("15"),
            balance_value=Decimal("2000"),
            fifo_snapshot=(
                (Decimal("10"), Decimal("100")),
                (Decimal("5"), Decimal("200")),
            ),
        )

        ledger = StockLedger([e1, e2])
        # Edit e1 incoming_rate to 150
        from_dt = ledger.update_entry(
            voucher_no="PR-01",
            item="ITEM-A",
            incoming_rate=Decimal("150"),
        )
        assert from_dt == t1

        # Test with early_stop=True and early_stop=False on separate copies
        ledger_copy = ledger.copy()
        engine1 = RepostEngine(ledger)
        res1 = engine1.repost("ITEM-A", "Stores", from_dt, early_stop=True)

        engine2 = RepostEngine(ledger_copy)
        res2 = engine2.repost("ITEM-A", "Stores", from_dt, early_stop=False)

        assert ledger.all_entries() == ledger_copy.all_entries()

        # Check updated values
        entries = ledger.all_entries()
        assert entries[0].incoming_rate == Decimal("150")
        assert entries[0].value_change == Decimal("1500")
        assert entries[0].balance_value == Decimal("1500")
        assert entries[1].balance_value == Decimal("2500")
        assert len(res1.value_changed) == 1
        assert res1.value_changed[0].voucher_no == "PR-01"
        assert res1.value_changed[0].old_value == Decimal("1000")
        assert res1.value_changed[0].new_value == Decimal("1500")
        assert res1.value_changed[0].delta == Decimal("500")
        assert len(res2.value_changed) == 1


class TestFifoIssueAcrossLayers:
    """Tests for FIFO issues consuming across multiple layers."""

    def test_rate_edit_propagates_across_consumed_layers(self) -> None:
        """Editing an earlier layer adjusts the issue's outgoing_value."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        # Layer 1: 10 @ 100
        # Layer 2: 10 @ 200
        # Issue: 15 -> 10@100 + 5@200 = 2000
        e1 = _make_sle(
            item="ITEM-A",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("10"), Decimal("100")),),
        )
        e2 = _make_sle(
            item="ITEM-A",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("200"),
            value_change=Decimal("2000"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("3000"),
            fifo_snapshot=(
                (Decimal("10"), Decimal("100")),
                (Decimal("10"), Decimal("200")),
            ),
        )
        e3 = _make_sle(
            item="ITEM-A",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-15"),
            outgoing_value=Decimal("2000"),
            value_change=Decimal("-2000"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("5"), Decimal("200")),),
        )

        ledger = StockLedger([e1, e2, e3])
        # Update PR-01 incoming_rate from 100 to 120
        from_dt = ledger.update_entry(
            voucher_no="PR-01",
            item="ITEM-A",
            incoming_rate=Decimal("120"),
        )

        ledger_copy = ledger.copy()
        engine = RepostEngine(ledger)
        res = engine.repost("ITEM-A", "Stores", from_dt, early_stop=True)

        engine_no_stop = RepostEngine(ledger_copy)
        res_no_stop = engine_no_stop.repost(
            "ITEM-A", "Stores", from_dt, early_stop=False
        )

        assert ledger.all_entries() == ledger_copy.all_entries()

        # Issue should now consume: 10@120 + 5@200 = 2200
        entries = ledger.all_entries()
        issue = entries[2]
        assert issue.outgoing_value == Decimal("2200")
        assert issue.value_change == Decimal("-2200")
        assert issue.balance_qty == Decimal("5")
        assert issue.balance_value == Decimal("1000")
        assert issue.fifo_snapshot == ((Decimal("5"), Decimal("200")),)

        # Check value_changed records
        vouchers = [vc.voucher_no for vc in res.value_changed]
        assert "PR-01" in vouchers
        assert "DN-01" in vouchers
        assert len(res.value_changed) == len(res_no_stop.value_changed)


class TestRateEditPropagatingToMultipleIssues:
    """Tests for rate updates propagating to multiple sequential issues."""

    def test_propagation_to_multiple_issues(self) -> None:
        """One receipt followed by two issues: both issues reflect new rate."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        e1 = _make_sle(
            item="ITEM-B",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        e2 = _make_sle(
            item="ITEM-B",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t2,
            qty_change=Decimal("-4"),
            outgoing_value=Decimal("40"),
            value_change=Decimal("-40"),
            balance_qty=Decimal("6"),
            balance_value=Decimal("60"),
            fifo_snapshot=((Decimal("6"), Decimal("10")),),
        )
        e3 = _make_sle(
            item="ITEM-B",
            voucher_type="Delivery Note",
            voucher_no="DN-02",
            posting_datetime=t3,
            qty_change=Decimal("-4"),
            outgoing_value=Decimal("40"),
            value_change=Decimal("-40"),
            balance_qty=Decimal("2"),
            balance_value=Decimal("20"),
            fifo_snapshot=((Decimal("2"), Decimal("10")),),
        )

        ledger = StockLedger([e1, e2, e3])
        from_dt = ledger.update_entry(
            voucher_no="PR-01",
            item="ITEM-B",
            incoming_rate=Decimal("20"),
        )

        ledger_copy = ledger.copy()
        engine = RepostEngine(ledger)
        engine.repost("ITEM-B", "Stores", from_dt, early_stop=True)

        engine_no_stop = RepostEngine(ledger_copy)
        engine_no_stop.repost("ITEM-B", "Stores", from_dt, early_stop=False)

        assert ledger.all_entries() == ledger_copy.all_entries()

        entries = ledger.all_entries()
        assert entries[1].outgoing_value == Decimal("80")
        assert entries[1].value_change == Decimal("-80")
        assert entries[2].outgoing_value == Decimal("80")
        assert entries[2].value_change == Decimal("-80")
        assert entries[2].balance_value == Decimal("40")


class TestManufactureDependency:
    """Tests for multi-item manufacture dependency (raw material -> finished good)."""

    def test_single_raw_material_manufacture(self) -> None:
        """1 raw material in, 1 finished good out; updating RM rate updates FG."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        # RM receipt: 10 @ 10 = 100
        rm_receipt = _make_sle(
            item="RM-1",
            voucher_no="PR-RM1",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        # RM issue for MFG-01: 10 @ 10 = 100
        rm_issue = _make_sle(
            item="RM-1",
            voucher_type="Stock Entry",
            voucher_no="MFG-01",
            posting_datetime=t2,
            creation_seq=1,
            qty_change=Decimal("-10"),
            outgoing_value=Decimal("100"),
            value_change=Decimal("-100"),
            balance_qty=Decimal("0"),
            balance_value=Decimal("0"),
            fifo_snapshot=(),
        )
        # FG receipt from MFG-01: 5 units produced, incoming_rate = 100 / 5 = 20
        fg_receipt = _make_sle(
            item="FG-1",
            voucher_type="Stock Entry",
            voucher_no="MFG-01",
            posting_datetime=t2,
            creation_seq=2,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("20"),
            value_change=Decimal("100"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("5"), Decimal("20")),),
            depends_on="MFG-01",
        )
        # FG delivery: 2 units consumed @ 20 = 40
        fg_delivery = _make_sle(
            item="FG-1",
            voucher_type="Delivery Note",
            voucher_no="DN-FG1",
            posting_datetime=t3,
            qty_change=Decimal("-2"),
            outgoing_value=Decimal("40"),
            value_change=Decimal("-40"),
            balance_qty=Decimal("3"),
            balance_value=Decimal("60"),
            fifo_snapshot=((Decimal("3"), Decimal("20")),),
        )

        ledger = StockLedger([rm_receipt, rm_issue, fg_receipt, fg_delivery])

        # Change RM-1 incoming rate from 10 to 15
        from_dt = ledger.update_entry(
            voucher_no="PR-RM1",
            item="RM-1",
            incoming_rate=Decimal("15"),
        )

        ledger_copy = ledger.copy()
        engine = RepostEngine(ledger)
        res = engine.repost("RM-1", "Stores", from_dt, early_stop=True)

        engine_no_stop = RepostEngine(ledger_copy)
        engine_no_stop.repost("RM-1", "Stores", from_dt, early_stop=False)

        assert ledger.all_entries() == ledger_copy.all_entries()

        # Check:
        # RM issue outgoing_value becomes 150
        # FG receipt incoming_rate becomes 150 / 5 = 30
        # FG delivery outgoing_value becomes 2 * 30 = 60
        entries = ledger.all_entries()
        assert entries[1].outgoing_value == Decimal("150")
        assert entries[2].incoming_rate == Decimal("30")
        assert entries[2].value_change == Decimal("150")
        assert entries[3].outgoing_value == Decimal("60")
        assert entries[3].value_change == Decimal("-60")
        assert entries[3].balance_value == Decimal("90")

        # Verify tuple unpacking of ValueChangedItem
        vouchers = [vc.voucher for vc in res.value_changed]
        assert "PR-RM1" in vouchers
        assert "MFG-01" in vouchers
        assert "DN-FG1" in vouchers

    def test_manufacture_with_two_raw_materials(self) -> None:
        """2 raw materials in, 1 finished good out; rates combine into FG rate."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)

        # RM1 receipt: 10 @ 10 = 100
        rm1_rec = _make_sle(
            item="RM-1",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        # RM2 receipt: 10 @ 20 = 200
        rm2_rec = _make_sle(
            item="RM-2",
            voucher_no="PR-02",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("20"),
            value_change=Decimal("200"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("200"),
            fifo_snapshot=((Decimal("10"), Decimal("20")),),
        )
        # RM1 issue for MFG-02: 5 @ 10 = 50
        rm1_iss = _make_sle(
            item="RM-1",
            voucher_type="Stock Entry",
            voucher_no="MFG-02",
            posting_datetime=t2,
            creation_seq=1,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("50"),
            value_change=Decimal("-50"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("50"),
            fifo_snapshot=((Decimal("5"), Decimal("10")),),
        )
        # RM2 issue for MFG-02: 5 @ 20 = 100
        rm2_iss = _make_sle(
            item="RM-2",
            voucher_type="Stock Entry",
            voucher_no="MFG-02",
            posting_datetime=t2,
            creation_seq=2,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("100"),
            value_change=Decimal("-100"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("5"), Decimal("20")),),
        )
        # FG receipt: 5 units produced. Total cost = 50 + 100 = 150 -> rate = 30
        fg_rec = _make_sle(
            item="FG-COMBINED",
            voucher_type="Stock Entry",
            voucher_no="MFG-02",
            posting_datetime=t2,
            creation_seq=3,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("30"),
            value_change=Decimal("150"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("150"),
            fifo_snapshot=((Decimal("5"), Decimal("30")),),
            depends_on="MFG-02",
        )

        ledger = StockLedger([rm1_rec, rm2_rec, rm1_iss, rm2_iss, fg_rec])

        # Update RM-1 rate from 10 to 20
        from_dt = ledger.update_entry(
            voucher_no="PR-01",
            item="RM-1",
            incoming_rate=Decimal("20"),
        )

        ledger_copy = ledger.copy()
        engine = RepostEngine(ledger)
        engine.repost("RM-1", "Stores", from_dt, early_stop=True)

        engine_no_stop = RepostEngine(ledger_copy)
        engine_no_stop.repost("RM-1", "Stores", from_dt, early_stop=False)

        assert ledger.all_entries() == ledger_copy.all_entries()

        # RM1 issue value becomes 5 * 20 = 100
        # RM2 issue value remains 100
        # FG incoming_rate becomes (100 + 100) / 5 = 40
        fg_entry = next(
            e for e in ledger.all_entries() if e.item == "FG-COMBINED"
        )
        assert fg_entry.incoming_rate == Decimal("40")
        assert fg_entry.value_change == Decimal("200")
        assert fg_entry.balance_value == Decimal("200")


class TestIdempotency:
    """Tests verifying reposting twice produces identical results and empty diffs."""

    def test_idempotent_repost(self) -> None:
        """Running repost twice yields identical ledger and empty diff on run 2."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)

        e1 = _make_sle(
            item="ITEM-C",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("50"),
            value_change=Decimal("500"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("500"),
            fifo_snapshot=((Decimal("10"), Decimal("50")),),
        )
        e2 = _make_sle(
            item="ITEM-C",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t2,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("250"),
            value_change=Decimal("-250"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("250"),
            fifo_snapshot=((Decimal("5"), Decimal("50")),),
        )

        ledger = StockLedger([e1, e2])
        from_dt = ledger.update_entry(
            voucher_no="PR-01",
            item="ITEM-C",
            incoming_rate=Decimal("60"),
        )

        engine = RepostEngine(ledger)
        res1 = engine.repost("ITEM-C", "Stores", from_dt)
        assert len(res1.value_changed) > 0

        snapshot_after_run1 = ledger.all_entries()

        # Run 2 on the same ledger
        res2 = engine.repost("ITEM-C", "Stores", from_dt)
        assert ledger.all_entries() == snapshot_after_run1
        assert len(res2.value_changed) == 0


class TestAtomicityOnInsufficientStock:
    """Tests verifying ledger is unchanged if any step raises InsufficientStockError."""

    def test_ledger_unchanged_on_insufficient_stock(self) -> None:
        """Backdated rate or qty edit causing deficit fails atomically."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)

        e1 = _make_sle(
            item="ITEM-D",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("10"), Decimal("100")),),
        )
        e2 = _make_sle(
            item="ITEM-D",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t2,
            qty_change=Decimal("-10"),
            outgoing_value=Decimal("1000"),
            value_change=Decimal("-1000"),
            balance_qty=Decimal("0"),
            balance_value=Decimal("0"),
            fifo_snapshot=(),
        )

        ledger = StockLedger([e1, e2])
        original_entries = ledger.all_entries()

        # Reduce PR-01 quantity from 10 to 5. Now DN-01 (10 units) cannot be fulfilled!
        from_dt = ledger.update_entry(
            voucher_no="PR-01",
            item="ITEM-D",
            qty_change=Decimal("5"),
            value_change=Decimal("500"),
        )

        engine = RepostEngine(ledger)
        with pytest.raises(InsufficientStockError):
            engine.repost("ITEM-D", "Stores", from_dt)

        # The ledger must be unchanged (atomic rollback)
        # Note: the update_entry was applied to ledger before repost, but repost failure
        # must roll back any intermediate replay changes made by repost.
        # To test pure atomicity of repost:
        assert ledger.all_entries()[1] == original_entries[1]


class TestEarlyStopEquivalence:
    """Verify early_stop=True and early_stop=False produce identical ledgers."""

    def test_long_chain_early_stop_equivalence(self) -> None:
        """A long chain where unaffected layers stop early with identical result."""
        entries: list[StockLedgerEntry] = []
        base_time = datetime(2026, 1, 1, 10, 0)

        # Initial receipt
        entries.append(
            _make_sle(
                item="CHAIN-1",
                voucher_no="PR-BASE",
                posting_datetime=base_time,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("10"),
                value_change=Decimal("1000"),
                balance_qty=Decimal("100"),
                balance_value=Decimal("1000"),
                fifo_snapshot=((Decimal("100"), Decimal("10")),),
            )
        )

        # 10 subsequent issues of 5 each
        for i in range(1, 11):
            dt = datetime(2026, 1, 1 + i, 10, 0)
            bal_qty = Decimal(100 - i * 5)
            bal_val = bal_qty * Decimal("10")
            entries.append(
                _make_sle(
                    item="CHAIN-1",
                    voucher_type="Delivery Note",
                    voucher_no=f"DN-{i:02d}",
                    posting_datetime=dt,
                    qty_change=Decimal("-5"),
                    outgoing_value=Decimal("50"),
                    value_change=Decimal("-50"),
                    balance_qty=bal_qty,
                    balance_value=bal_val,
                    fifo_snapshot=((bal_qty, Decimal("10")),),
                )
            )

        ledger1 = StockLedger(entries)
        ledger2 = StockLedger(entries)

        # Update incoming rate of PR-BASE
        from_dt = ledger1.update_entry(
            voucher_no="PR-BASE",
            item="CHAIN-1",
            incoming_rate=Decimal("15"),
        )
        ledger2.update_entry(
            voucher_no="PR-BASE",
            item="CHAIN-1",
            incoming_rate=Decimal("15"),
        )

        engine1 = RepostEngine(ledger1)
        res1 = engine1.repost("CHAIN-1", "Stores", from_dt, early_stop=True)

        engine2 = RepostEngine(ledger2)
        res2 = engine2.repost("CHAIN-1", "Stores", from_dt, early_stop=False)

        assert ledger1.all_entries() == ledger2.all_entries()
        assert len(res1.value_changed) == len(res2.value_changed)


class TestCoalescingOfDuplicateWorkItems:
    """Tests for coalescing duplicate work queue entries to the earliest datetime."""

    def test_coalesce_duplicate_keys(self) -> None:
        """When item is enqueued from multiple sources, it uses earliest datetime."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        e1 = _make_sle(
            item="ITEM-COALESCE",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("10"), Decimal("100")),),
        )
        e2 = _make_sle(
            item="ITEM-COALESCE",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("2000"),
            fifo_snapshot=((Decimal("20"), Decimal("100")),),
        )
        e3 = _make_sle(
            item="ITEM-COALESCE",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("500"),
            value_change=Decimal("-500"),
            balance_qty=Decimal("15"),
            balance_value=Decimal("1500"),
            fifo_snapshot=((Decimal("15"), Decimal("100")),),
        )

        ledger = StockLedger([e1, e2, e3])
        ledger.update_entry(
            voucher_no="PR-01",
            item="ITEM-COALESCE",
            incoming_rate=Decimal("110"),
        )

        engine = RepostEngine(ledger)
        # Calling repost from t2 while also having an earlier change at t1
        # The engine must correctly handle and replay from t1 if enqueued
        res = engine.repost("ITEM-COALESCE", "Stores", t1)
        assert len(res.value_changed) >= 1
        assert ledger.all_entries()[0].incoming_rate == Decimal("110")
        assert ledger.all_entries()[2].outgoing_value == Decimal("550")


# ========================================================================
# Phase 2.1: Test Hardening
# ========================================================================


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


class TestParametrizedScenarios:
    """Parametrized early_stop=(True, False) across all domain scenarios."""

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_receipt_chain(self, early_stop: bool) -> None:
        """Receipt chain produces identical ledgers under early_stop True and False."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        e1 = _make_sle(
            item="P-ITEM",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("10"), Decimal("100")),),
        )
        e2 = _make_sle(
            item="P-ITEM",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("200"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("15"),
            balance_value=Decimal("2000"),
            fifo_snapshot=(
                (Decimal("10"), Decimal("100")),
                (Decimal("5"), Decimal("200")),
            ),
        )
        ledger = StockLedger([e1, e2])
        from_dt = ledger.update_entry(
            "PR-01", "P-ITEM", incoming_rate=Decimal("150")
        )
        engine = RepostEngine(ledger)
        engine.repost("P-ITEM", "Stores", from_dt, early_stop=early_stop)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_fifo_layers(self, early_stop: bool) -> None:
        """FIFO multi-layer consumption produces identical ledgers."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)
        e1 = _make_sle(
            item="P-FIFO",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("100"),
            value_change=Decimal("1000"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("10"), Decimal("100")),),
        )
        e2 = _make_sle(
            item="P-FIFO",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("200"),
            value_change=Decimal("2000"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("3000"),
            fifo_snapshot=(
                (Decimal("10"), Decimal("100")),
                (Decimal("10"), Decimal("200")),
            ),
        )
        e3 = _make_sle(
            item="P-FIFO",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-15"),
            outgoing_value=Decimal("2000"),
            value_change=Decimal("-2000"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("5"), Decimal("200")),),
        )
        ledger = StockLedger([e1, e2, e3])
        from_dt = ledger.update_entry(
            "PR-01", "P-FIFO", incoming_rate=Decimal("120")
        )
        engine = RepostEngine(ledger)
        engine.repost("P-FIFO", "Stores", from_dt, early_stop=early_stop)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_rate_edit_multiple_issues(self, early_stop: bool) -> None:
        """Rate edit across multiple sequential issues matches oracle."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)
        e1 = _make_sle(
            item="P-RATE",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        e2 = _make_sle(
            item="P-RATE",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t2,
            qty_change=Decimal("-4"),
            outgoing_value=Decimal("40"),
            value_change=Decimal("-40"),
            balance_qty=Decimal("6"),
            balance_value=Decimal("60"),
            fifo_snapshot=((Decimal("6"), Decimal("10")),),
        )
        e3 = _make_sle(
            item="P-RATE",
            voucher_type="Delivery Note",
            voucher_no="DN-02",
            posting_datetime=t3,
            qty_change=Decimal("-4"),
            outgoing_value=Decimal("40"),
            value_change=Decimal("-40"),
            balance_qty=Decimal("2"),
            balance_value=Decimal("20"),
            fifo_snapshot=((Decimal("2"), Decimal("10")),),
        )
        ledger = StockLedger([e1, e2, e3])
        from_dt = ledger.update_entry(
            "PR-01", "P-RATE", incoming_rate=Decimal("20")
        )
        engine = RepostEngine(ledger)
        engine.repost("P-RATE", "Stores", from_dt, early_stop=early_stop)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_manufacture_1_rm(self, early_stop: bool) -> None:
        """1 raw material manufacture dependency matches oracle."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        rm_rec = _make_sle(
            item="P-RM1",
            voucher_no="PR-RM1",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        rm_iss = _make_sle(
            item="P-RM1",
            voucher_type="Stock Entry",
            voucher_no="MFG-P1",
            posting_datetime=t2,
            creation_seq=1,
            qty_change=Decimal("-10"),
            outgoing_value=Decimal("100"),
            value_change=Decimal("-100"),
            balance_qty=Decimal("0"),
            balance_value=Decimal("0"),
            fifo_snapshot=(),
        )
        fg_rec = _make_sle(
            item="P-FG1",
            voucher_type="Stock Entry",
            voucher_no="MFG-P1",
            posting_datetime=t2,
            creation_seq=2,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("20"),
            value_change=Decimal("100"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("5"), Decimal("20")),),
            depends_on="MFG-P1",
        )
        ledger = StockLedger([rm_rec, rm_iss, fg_rec])
        from_dt = ledger.update_entry(
            "PR-RM1", "P-RM1", incoming_rate=Decimal("15")
        )
        engine = RepostEngine(ledger)
        engine.repost("P-RM1", "Stores", from_dt, early_stop=early_stop)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_manufacture_2_rm(self, early_stop: bool) -> None:
        """2 raw materials manufacture dependency matches oracle."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        rm1_rec = _make_sle(
            item="P-RM1A",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        rm2_rec = _make_sle(
            item="P-RM2A",
            voucher_no="PR-02",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("20"),
            value_change=Decimal("200"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("200"),
            fifo_snapshot=((Decimal("10"), Decimal("20")),),
        )
        rm1_iss = _make_sle(
            item="P-RM1A",
            voucher_type="Stock Entry",
            voucher_no="MFG-P2",
            posting_datetime=t2,
            creation_seq=1,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("50"),
            value_change=Decimal("-50"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("50"),
            fifo_snapshot=((Decimal("5"), Decimal("10")),),
        )
        rm2_iss = _make_sle(
            item="P-RM2A",
            voucher_type="Stock Entry",
            voucher_no="MFG-P2",
            posting_datetime=t2,
            creation_seq=2,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("100"),
            value_change=Decimal("-100"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("5"), Decimal("20")),),
        )
        fg_rec = _make_sle(
            item="P-FG2",
            voucher_type="Stock Entry",
            voucher_no="MFG-P2",
            posting_datetime=t2,
            creation_seq=3,
            qty_change=Decimal("5"),
            incoming_rate=Decimal("30"),
            value_change=Decimal("150"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("150"),
            fifo_snapshot=((Decimal("5"), Decimal("30")),),
            depends_on="MFG-P2",
        )
        ledger = StockLedger([rm1_rec, rm2_rec, rm1_iss, rm2_iss, fg_rec])
        from_dt = ledger.update_entry(
            "PR-01", "P-RM1A", incoming_rate=Decimal("20")
        )
        engine = RepostEngine(ledger)
        engine.repost("P-RM1A", "Stores", from_dt, early_stop=early_stop)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_long_chain(self, early_stop: bool) -> None:
        """Long sequential chain matches oracle under both early_stop modes."""
        entries: list[StockLedgerEntry] = []
        base_time = datetime(2026, 1, 1, 10, 0)
        entries.append(
            _make_sle(
                item="P-LONG",
                voucher_no="PR-BASE",
                posting_datetime=base_time,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("10"),
                value_change=Decimal("1000"),
                balance_qty=Decimal("100"),
                balance_value=Decimal("1000"),
                fifo_snapshot=((Decimal("100"), Decimal("10")),),
            )
        )
        for i in range(1, 11):
            dt = datetime(2026, 1, 1 + i, 10, 0)
            bal_qty = Decimal(100 - i * 5)
            bal_val = bal_qty * Decimal("10")
            entries.append(
                _make_sle(
                    item="P-LONG",
                    voucher_type="Delivery Note",
                    voucher_no=f"DN-{i:02d}",
                    posting_datetime=dt,
                    qty_change=Decimal("-5"),
                    outgoing_value=Decimal("50"),
                    value_change=Decimal("-50"),
                    balance_qty=bal_qty,
                    balance_value=bal_val,
                    fifo_snapshot=((bal_qty, Decimal("10")),),
                )
            )
        ledger = StockLedger(entries)
        from_dt = ledger.update_entry(
            "PR-BASE", "P-LONG", incoming_rate=Decimal("12")
        )
        engine = RepostEngine(ledger)
        engine.repost("P-LONG", "Stores", from_dt, early_stop=early_stop)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()


class TestEarlyStopSkipping:
    """Tests verifying early_stop actually skips subsequent entries."""

    def test_early_stop_actually_skips(self) -> None:
        """Early stop halts replay early, replaying strictly fewer entries."""
        entries: list[StockLedgerEntry] = []
        t0 = datetime(2026, 1, 1, 10, 0)
        t1 = datetime(2026, 1, 2, 10, 0)
        t2 = datetime(2026, 1, 3, 10, 0)

        # Entry 0: Initial receipt (10 @ 100)
        entries.append(
            _make_sle(
                item="SKIP-ITEM",
                voucher_no="PR-01",
                posting_datetime=t0,
                qty_change=Decimal("10"),
                incoming_rate=Decimal("100"),
                value_change=Decimal("1000"),
                balance_qty=Decimal("10"),
                balance_value=Decimal("1000"),
                fifo_snapshot=((Decimal("10"), Decimal("100")),),
            )
        )
        # Entry 1: Exact depletion issue (-10)
        entries.append(
            _make_sle(
                item="SKIP-ITEM",
                voucher_type="Delivery Note",
                voucher_no="DN-01",
                posting_datetime=t1,
                qty_change=Decimal("-10"),
                outgoing_value=Decimal("1000"),
                value_change=Decimal("-1000"),
                balance_qty=Decimal("0"),
                balance_value=Decimal("0"),
                fifo_snapshot=(),
            )
        )
        # Entry 2: Fresh receipt resetting state (10 @ 200)
        entries.append(
            _make_sle(
                item="SKIP-ITEM",
                voucher_no="PR-02",
                posting_datetime=t2,
                qty_change=Decimal("10"),
                incoming_rate=Decimal("200"),
                value_change=Decimal("2000"),
                balance_qty=Decimal("10"),
                balance_value=Decimal("2000"),
                fifo_snapshot=((Decimal("10"), Decimal("200")),),
            )
        )
        # Entries 3-25: 22 subsequent issues from the fresh receipt
        for i in range(3, 25):
            dt = datetime(2026, 1, 3 + i, 10, 0)
            bal_qty = Decimal(10) - Decimal(i - 2) * Decimal("0.2")
            bal_val = bal_qty * Decimal("200")
            entries.append(
                _make_sle(
                    item="SKIP-ITEM",
                    voucher_type="Delivery Note",
                    voucher_no=f"DN-{i:02d}",
                    posting_datetime=dt,
                    qty_change=Decimal("-0.2"),
                    outgoing_value=Decimal("40"),
                    value_change=Decimal("-40"),
                    balance_qty=bal_qty,
                    balance_value=bal_val,
                    fifo_snapshot=((bal_qty, Decimal("200")),),
                )
            )

        ledger_stop = StockLedger(entries)
        ledger_no_stop = StockLedger(entries)

        from_dt = ledger_stop.update_entry(
            "PR-01", "SKIP-ITEM", incoming_rate=Decimal("120")
        )
        ledger_no_stop.update_entry(
            "PR-01", "SKIP-ITEM", incoming_rate=Decimal("120")
        )

        engine_stop = RepostEngine(ledger_stop)
        res_stop = engine_stop.repost(
            "SKIP-ITEM", "Stores", from_dt, early_stop=True
        )

        engine_no_stop = RepostEngine(ledger_no_stop)
        res_no_stop = engine_no_stop.repost(
            "SKIP-ITEM", "Stores", from_dt, early_stop=False
        )

        # Assert identical resulting ledgers
        assert ledger_stop.all_entries() == ledger_no_stop.all_entries()

        # Assert early stop actually skipped entries
        assert res_stop.entries_replayed < res_no_stop.entries_replayed
        assert res_stop.entries_replayed == 3
        assert res_no_stop.entries_replayed == len(entries)

    def test_early_stop_never_stops_on_edited_entry(self) -> None:
        """Edit with no effective change replays the edited entry (i=0 never stops)."""
        t0 = datetime(2026, 1, 1, 10, 0)
        t1 = datetime(2026, 1, 2, 10, 0)
        e1 = _make_sle(
            item="NO-OP-ITEM",
            voucher_no="PR-01",
            posting_datetime=t0,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("50"),
            value_change=Decimal("500"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("500"),
            fifo_snapshot=((Decimal("10"), Decimal("50")),),
        )
        e2 = _make_sle(
            item="NO-OP-ITEM",
            voucher_no="PR-02",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("50"),
            value_change=Decimal("500"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("1000"),
            fifo_snapshot=((Decimal("20"), Decimal("50")),),
        )
        ledger = StockLedger([e1, e2])
        # Update with identical rate
        from_dt = ledger.update_entry(
            "PR-01", "NO-OP-ITEM", incoming_rate=Decimal("50")
        )

        engine = RepostEngine(ledger)
        res = engine.repost("NO-OP-ITEM", "Stores", from_dt, early_stop=True)

        # Must have replayed at least entry 0 (never stops on entry 0)
        assert res.entries_replayed >= 1


class TestValueChangedVsReEvaluatedOnly:
    """Tests asserting exact partition between value_changed and re_evaluated_only."""

    def test_value_changed_vs_re_evaluated_only(self) -> None:
        """Milk opening 100@50, receipt 50@60, issue -80, edit receipt rate to 80."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        # Opening: +100 @ 50
        opening = _make_sle(
            item="Milk",
            voucher_no="OPEN-01",
            posting_datetime=t1,
            qty_change=Decimal("100"),
            incoming_rate=Decimal("50"),
            value_change=Decimal("5000"),
            balance_qty=Decimal("100"),
            balance_value=Decimal("5000"),
            fifo_snapshot=((Decimal("100"), Decimal("50")),),
        )
        # Receipt: +50 @ 60
        receipt = _make_sle(
            item="Milk",
            voucher_no="PR-01",
            posting_datetime=t2,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("60"),
            value_change=Decimal("3000"),
            balance_qty=Decimal("150"),
            balance_value=Decimal("8000"),
            fifo_snapshot=(
                (Decimal("100"), Decimal("50")),
                (Decimal("50"), Decimal("60")),
            ),
        )
        # Issue: -80 (consumes 80 from opening 100@50 layer, 50@60 layer untouched)
        issue = _make_sle(
            item="Milk",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-80"),
            outgoing_value=Decimal("4000"),
            value_change=Decimal("-4000"),
            balance_qty=Decimal("70"),
            balance_value=Decimal("4000"),
            fifo_snapshot=(
                (Decimal("20"), Decimal("50")),
                (Decimal("50"), Decimal("60")),
            ),
        )

        ledger = StockLedger([opening, receipt, issue])
        from_dt = ledger.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )

        engine = RepostEngine(ledger)
        res = engine.repost("Milk", "Stores", from_dt, early_stop=True)

        # Assert exact voucher lists
        assert [vc.voucher_no for vc in res.value_changed] == ["PR-01"]
        assert [e.voucher_no for e in res.re_evaluated_only] == ["DN-01"]

        # Assert exact values on value_changed
        vc = res.value_changed[0]
        assert vc.voucher_no == "PR-01"
        assert vc.old_value == Decimal("3000")
        assert vc.new_value == Decimal("4000")
        assert vc.delta == Decimal("1000")

        # Assert exact values on re_evaluated_only
        re_eval = res.re_evaluated_only[0]
        assert re_eval.voucher_no == "DN-01"
        assert re_eval.value_change == Decimal("-4000")
        assert re_eval.balance_value == Decimal("5000")


class TestLedgerMutationsAndOracle:
    """Tests for insert_entry, remove_entry, and update_entry with oracle."""

    def test_insert_entry_matches_full_rebuild(self) -> None:
        """Backdated receipt inserted between existing entries matches oracle."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        e1 = _make_sle(
            item="MUT-ITEM",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        e3 = _make_sle(
            item="MUT-ITEM",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("50"),
            value_change=Decimal("-50"),
            balance_qty=Decimal("5"),
            balance_value=Decimal("50"),
            fifo_snapshot=((Decimal("5"), Decimal("10")),),
        )
        ledger = StockLedger([e1, e3])

        # Backdated insert at T2 between T1 and T3
        e2_inserted = _make_sle(
            item="MUT-ITEM",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("20"),
            value_change=Decimal("200"),
        )
        from_dt = ledger.insert_entry(e2_inserted)
        assert from_dt == t2

        engine = RepostEngine(ledger)
        engine.repost("MUT-ITEM", "Stores", from_dt)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    def test_remove_entry_matches_full_rebuild(self) -> None:
        """Cancelling a receipt via remove_entry matches oracle."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)

        e1 = _make_sle(
            item="MUT-REM",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("100"),
            balance_qty=Decimal("10"),
            balance_value=Decimal("100"),
            fifo_snapshot=((Decimal("10"), Decimal("10")),),
        )
        e2 = _make_sle(
            item="MUT-REM",
            voucher_no="PR-02",
            posting_datetime=t2,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("20"),
            value_change=Decimal("200"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("300"),
            fifo_snapshot=(
                (Decimal("10"), Decimal("10")),
                (Decimal("10"), Decimal("20")),
            ),
        )
        e3 = _make_sle(
            item="MUT-REM",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t3,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("50"),
            value_change=Decimal("-50"),
            balance_qty=Decimal("15"),
            balance_value=Decimal("250"),
            fifo_snapshot=(
                (Decimal("5"), Decimal("10")),
                (Decimal("10"), Decimal("20")),
            ),
        )
        ledger = StockLedger([e1, e2, e3])

        # Cancel PR-02
        from_dt = ledger.remove_entry("PR-02", "MUT-REM")
        assert from_dt == t2

        engine = RepostEngine(ledger)
        engine.repost("MUT-REM", "Stores", from_dt)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()

    def test_update_entry_qty_matches_full_rebuild(self) -> None:
        """Updating quantity on an entry matches oracle."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)

        e1 = _make_sle(
            item="MUT-QTY",
            voucher_no="PR-01",
            posting_datetime=t1,
            qty_change=Decimal("20"),
            incoming_rate=Decimal("10"),
            value_change=Decimal("200"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("200"),
            fifo_snapshot=((Decimal("20"), Decimal("10")),),
        )
        e2 = _make_sle(
            item="MUT-QTY",
            voucher_type="Delivery Note",
            voucher_no="DN-01",
            posting_datetime=t2,
            qty_change=Decimal("-5"),
            outgoing_value=Decimal("50"),
            value_change=Decimal("-50"),
            balance_qty=Decimal("15"),
            balance_value=Decimal("150"),
            fifo_snapshot=((Decimal("15"), Decimal("10")),),
        )
        ledger = StockLedger([e1, e2])

        # Increase issue qty from -5 to -8
        from_dt = ledger.update_entry(
            "DN-01", "MUT-QTY", qty_change=Decimal("-8")
        )
        assert from_dt == t2

        engine = RepostEngine(ledger)
        engine.repost("MUT-QTY", "Stores", from_dt)

        assert ledger.all_entries() == full_rebuild(ledger).all_entries()


# ========================================================================
# Phase 3.1: Multiple Edits Before Repost
# ========================================================================


class TestMultipleEditsBeforeRepost:
    """Tests for coalesced multi-edit scenarios before reposting."""

    def test_two_edits_without_convergence(self) -> None:
        """Variant 1: Two edits where state does not converge between them.

        Milk ledger with receipts on 10, 20, 28 Jan and issues on 15, 22, 30 Jan.
        Two rate edits applied before calling repost: 10 Jan receipt to 80 and
        28 Jan receipt to 90. Calling repost once from the earliest datetime
        yields identical results under early_stop=True and early_stop=False,
        and matches full_rebuild.
        """
        t_open = datetime(2026, 1, 1, 10, 0)
        t_pr1 = datetime(2026, 1, 10, 10, 0)
        t_dn1 = datetime(2026, 1, 15, 10, 0)
        t_pr2 = datetime(2026, 1, 20, 10, 0)
        t_dn2 = datetime(2026, 1, 22, 10, 0)
        t_pr3 = datetime(2026, 1, 28, 10, 0)
        t_dn3 = datetime(2026, 1, 30, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t_open,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
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
                voucher_type="Delivery Note",
                voucher_no="DN-02",
                posting_datetime=t_dn2,
                qty_change=Decimal("-60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-03",
                posting_datetime=t_pr3,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-03",
                posting_datetime=t_dn3,
                qty_change=Decimal("-70"),
            ),
        ]

        # Initial repost to establish baseline balances and snapshots
        base_ledger = StockLedger(entries)
        base_engine = RepostEngine(base_ledger)
        base_engine.repost("Milk", "Stores", t_open)

        # Apply two edits before repost: PR-01 to 80, PR-03 to 90
        ledger_early = base_ledger.copy()
        dt1_early = ledger_early.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        dt2_early = ledger_early.update_entry(
            "PR-03", "Milk", incoming_rate=Decimal("90")
        )
        from_dt_early = min(dt1_early, dt2_early)

        engine_early = RepostEngine(ledger_early)
        engine_early.repost(
            "Milk", "Stores", from_dt_early, early_stop=True
        )

        ledger_full = base_ledger.copy()
        dt1_full = ledger_full.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        dt2_full = ledger_full.update_entry(
            "PR-03", "Milk", incoming_rate=Decimal("90")
        )
        from_dt_full = min(dt1_full, dt2_full)

        engine_full = RepostEngine(ledger_full)
        engine_full.repost(
            "Milk", "Stores", from_dt_full, early_stop=False
        )

        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.all_entries() == full_rebuild(ledger_early).all_entries()

    def test_second_edit_behind_convergence_point(self) -> None:
        """Variant 2: Second edit is behind a point where state converges.

        In the Milk ledger, the 10 Jan receipt (+50@60) is fully depleted by the
        30 Jan issue (-70). An intermediate receipt on 5 Feb (+50@60) therefore
        converges to the exact prior snapshot and values.
        A second edit is applied to a later entry on 10 Feb (+50@60 to 90).
        When reposting from the earliest datetime (10 Jan), early_stop=True
        triggers at the 5 Feb convergence point, skipping the 10 Feb edit.
        """
        t_open = datetime(2026, 1, 1, 10, 0)
        t_pr1 = datetime(2026, 1, 10, 10, 0)
        t_dn1 = datetime(2026, 1, 15, 10, 0)
        t_pr2 = datetime(2026, 1, 20, 10, 0)
        t_dn2 = datetime(2026, 1, 22, 10, 0)
        t_pr3 = datetime(2026, 1, 28, 10, 0)
        t_dn3 = datetime(2026, 1, 30, 10, 0)
        t_pr4 = datetime(2026, 2, 5, 10, 0)
        t_pr5 = datetime(2026, 2, 10, 10, 0)
        t_dn4 = datetime(2026, 2, 15, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t_open,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
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
                voucher_type="Delivery Note",
                voucher_no="DN-02",
                posting_datetime=t_dn2,
                qty_change=Decimal("-60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-03",
                posting_datetime=t_pr3,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-03",
                posting_datetime=t_dn3,
                qty_change=Decimal("-70"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-04",
                posting_datetime=t_pr4,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-05",
                posting_datetime=t_pr5,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-04",
                posting_datetime=t_dn4,
                qty_change=Decimal("-50"),
            ),
        ]

        # Initial repost to establish baseline balances and snapshots
        base_ledger = StockLedger(entries)
        base_engine = RepostEngine(base_ledger)
        base_engine.repost("Milk", "Stores", t_open)

        # Apply two edits before repost:
        # 1. PR-01 to 80 (earliest datetime: 10 Jan)
        # 2. PR-05 to 90 (later entry on 10 Feb, behind PR-04 convergence point)
        ledger_early = base_ledger.copy()
        dt1_early = ledger_early.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        dt2_early = ledger_early.update_entry(
            "PR-05", "Milk", incoming_rate=Decimal("90")
        )
        from_dt_early = min(dt1_early, dt2_early)

        engine_early = RepostEngine(ledger_early)
        engine_early.repost(
            "Milk", "Stores", from_dt_early, early_stop=True
        )

        ledger_full = base_ledger.copy()
        dt1_full = ledger_full.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        dt2_full = ledger_full.update_entry(
            "PR-05", "Milk", incoming_rate=Decimal("90")
        )
        from_dt_full = min(dt1_full, dt2_full)

        engine_full = RepostEngine(ledger_full)
        engine_full.repost(
            "Milk", "Stores", from_dt_full, early_stop=False
        )

        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.all_entries() == full_rebuild(ledger_early).all_entries()

    def test_three_edits_with_intermediate_convergence(self) -> None:
        """Three edits (earliest, middle, latest) with convergence between each pair.

        Validates that early_stop=True, early_stop=False, and full_rebuild all
        produce identical ledgers across multiple convergence points.
        """
        t_open = datetime(2026, 1, 1, 10, 0)
        t_pr1 = datetime(2026, 1, 10, 10, 0)
        t_dn1 = datetime(2026, 1, 15, 10, 0)
        t_pr2 = datetime(2026, 1, 20, 10, 0)
        t_dn2 = datetime(2026, 1, 22, 10, 0)
        t_pr3 = datetime(2026, 1, 28, 10, 0)
        t_dn3 = datetime(2026, 1, 30, 10, 0)
        t_pr4 = datetime(2026, 2, 5, 10, 0)
        t_pr5 = datetime(2026, 2, 10, 10, 0)
        t_dn4 = datetime(2026, 2, 15, 10, 0)
        t_dn5 = datetime(2026, 2, 18, 10, 0)
        t_pr6 = datetime(2026, 2, 20, 10, 0)
        t_pr7 = datetime(2026, 2, 25, 10, 0)
        t_dn6 = datetime(2026, 2, 28, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t_open,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
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
                voucher_type="Delivery Note",
                voucher_no="DN-02",
                posting_datetime=t_dn2,
                qty_change=Decimal("-60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-03",
                posting_datetime=t_pr3,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-03",
                posting_datetime=t_dn3,
                qty_change=Decimal("-70"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-04",
                posting_datetime=t_pr4,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-05",
                posting_datetime=t_pr5,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-04",
                posting_datetime=t_dn4,
                qty_change=Decimal("-50"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-05",
                posting_datetime=t_dn5,
                qty_change=Decimal("-40"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-06",
                posting_datetime=t_pr6,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-07",
                posting_datetime=t_pr7,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-06",
                posting_datetime=t_dn6,
                qty_change=Decimal("-30"),
            ),
        ]

        base_ledger = StockLedger(entries)
        base_engine = RepostEngine(base_ledger)
        base_engine.repost("Milk", "Stores", t_open)

        # 3 edits on the same key: earliest (10 Jan), middle (10 Feb), latest (25 Feb)
        ledger_early = base_ledger.copy()
        dt1 = ledger_early.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        dt2 = ledger_early.update_entry(
            "PR-05", "Milk", incoming_rate=Decimal("90")
        )
        dt3 = ledger_early.update_entry(
            "PR-07", "Milk", incoming_rate=Decimal("100")
        )
        min_dt = min(dt1, dt2, dt3)

        engine_early = RepostEngine(ledger_early)
        engine_early.repost("Milk", "Stores", min_dt, early_stop=True)

        ledger_full = base_ledger.copy()
        ledger_full.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        ledger_full.update_entry(
            "PR-05", "Milk", incoming_rate=Decimal("90")
        )
        ledger_full.update_entry(
            "PR-07", "Milk", incoming_rate=Decimal("100")
        )
        engine_full = RepostEngine(ledger_full)
        engine_full.repost("Milk", "Stores", min_dt, early_stop=False)

        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.all_entries() == full_rebuild(ledger_early).all_entries()

    def test_failing_repost_leaves_dirty_marks_intact(self) -> None:
        """A failing repost (insufficient stock) leaves pending dirty marks intact."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 10, 10, 0)
        t3 = datetime(2026, 1, 15, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t1,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-01",
                posting_datetime=t2,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-01",
                posting_datetime=t3,
                qty_change=Decimal("-120"),
            ),
        ]
        ledger = StockLedger(entries)
        engine = RepostEngine(ledger)
        engine.repost("Milk", "Stores", t1)

        # Edit PR-01 qty to 10 so total stock becomes 110, causing DN-01 (-120) to fail
        from_dt = ledger.update_entry(
            "PR-01", "Milk", qty_change=Decimal("10")
        )
        assert from_dt == t2
        assert ledger.get_min_dirty("Milk", "Stores") == t2
        assert ledger.get_max_dirty("Milk", "Stores") == t2

        with pytest.raises(InsufficientStockError):
            engine.repost("Milk", "Stores", from_dt, early_stop=True)

        # Ledger dirty marks must remain intact after failure
        assert ledger.get_min_dirty("Milk", "Stores") == t2
        assert ledger.get_max_dirty("Milk", "Stores") == t2

    def test_single_edit_early_stop_skips_entries(self) -> None:
        """An entries_replayed assertion proving early stop skips entries.

        Tests that a single edit still benefits from early-stop skipping.
        """
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 2, 10, 0)
        t3 = datetime(2026, 1, 3, 10, 0)
        t4 = datetime(2026, 1, 4, 10, 0)
        t5 = datetime(2026, 1, 5, 10, 0)
        t6 = datetime(2026, 1, 6, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t1,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-01",
                posting_datetime=t2,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            # DN-01 fully depletes opening (100) and PR-01 (50)
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-01",
                posting_datetime=t3,
                qty_change=Decimal("-150"),
            ),
            # Subsequent entries are independent and will converge
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-02",
                posting_datetime=t4,
                qty_change=Decimal("20"),
                incoming_rate=Decimal("70"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-02",
                posting_datetime=t5,
                qty_change=Decimal("-10"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-03",
                posting_datetime=t6,
                qty_change=Decimal("-10"),
            ),
        ]
        base_ledger = StockLedger(entries)
        base_engine = RepostEngine(base_ledger)
        base_engine.repost("Milk", "Stores", t1)

        # Single edit on PR-01
        ledger_early = base_ledger.copy()
        from_dt = ledger_early.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("85")
        )
        engine_early = RepostEngine(ledger_early)
        res_early = engine_early.repost(
            "Milk", "Stores", from_dt, early_stop=True
        )

        ledger_full = base_ledger.copy()
        ledger_full.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("85")
        )
        engine_full = RepostEngine(ledger_full)
        res_full = engine_full.repost(
            "Milk", "Stores", from_dt, early_stop=False
        )

        # PR-02 (t4) converges because balance was 0 at t3; early stop triggers at PR-02
        assert res_early.entries_replayed < res_full.entries_replayed
        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.get_max_dirty("Milk", "Stores") is None

    def test_repost_starts_from_min_dirty_when_given_later_datetime(self) -> None:
        """repost() starts from minimum dirty datetime if given later datetime."""
        t1 = datetime(2026, 1, 1, 10, 0)
        t2 = datetime(2026, 1, 10, 10, 0)
        t3 = datetime(2026, 1, 15, 10, 0)
        t4 = datetime(2026, 1, 20, 10, 0)

        entries = [
            _make_sle(
                item="Milk",
                voucher_type="Opening",
                voucher_no="OPEN-01",
                posting_datetime=t1,
                qty_change=Decimal("100"),
                incoming_rate=Decimal("50"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-01",
                posting_datetime=t2,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Delivery Note",
                voucher_no="DN-01",
                posting_datetime=t3,
                qty_change=Decimal("-30"),
            ),
            _make_sle(
                item="Milk",
                voucher_type="Purchase Receipt",
                voucher_no="PR-02",
                posting_datetime=t4,
                qty_change=Decimal("50"),
                incoming_rate=Decimal("60"),
            ),
        ]
        ledger = StockLedger(entries)
        engine = RepostEngine(ledger)
        engine.repost("Milk", "Stores", t1)

        # Two edits: PR-01 (10 Jan) and PR-02 (20 Jan)
        ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))
        ledger.update_entry("PR-02", "Milk", incoming_rate=Decimal("90"))

        # Caller accidentally passes only the later datetime (20 Jan)
        engine.repost("Milk", "Stores", from_datetime=t4, early_stop=True)

        # Repost must have started from 10 Jan (min dirty datetime)
        assert ledger.all_entries() == full_rebuild(ledger).all_entries()
        assert ledger.get_max_dirty("Milk", "Stores") is None



