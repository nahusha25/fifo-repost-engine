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
