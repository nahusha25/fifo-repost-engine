"""Property-based tests for structural ledger edits and repost equivalence.

Tests that early_stop=True == early_stop=False == full_rebuild across random
combinations of structural mutations (rate updates, quantity updates,
backdated receipt insertions, receipt cancellations, and backdated issue insertions).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from stock_repost.fifo import InsufficientStockError
from stock_repost.ledger import StockLedger
from stock_repost.repost import RepostEngine
from test_property_equivalence import (
    _make_sle,
    full_rebuild,
    valid_ledger_scenario,
)


class TestPropertyStructuralEdits:
    """Hypothesis tests for equivalence and idempotency across structural edits."""

    @given(scenario=valid_ledger_scenario(), data=st.data())
    def test_structural_edits_equivalence_and_idempotency(
        self,
        scenario: tuple[StockLedger, list[str]],
        data: st.DataObject,
    ) -> None:
        """Assert early_stop True == False == full_rebuild across structural edits.

        Randomly applies 1 to 4 structural edits (rate change, quantity change,
        backdated receipt insertion, receipt cancellation, backdated issue insertion)
        to three identical copies of a valid ledger, triggers a single repost from
        the dirty ranges, and validates complete equivalence and idempotency.
        """
        base_ledger, _items = scenario
        ledger_early = base_ledger.copy()
        ledger_full = base_ledger.copy()
        ledger_for_rebuild = base_ledger.copy()

        num_edits = data.draw(st.integers(min_value=1, max_value=4))

        for edit_idx in range(num_edits):
            entries = list(ledger_early.all_entries())
            if not entries:
                break

            items = sorted({e.item for e in entries})
            receipts = [
                e
                for e in entries
                if e.qty_change > Decimal("0") and e.voucher_type == "Purchase Receipt"
            ]

            depended_vouchers = {
                e.depends_on for e in entries if e.depends_on is not None
            }
            cancellable_receipts = [
                e for e in receipts if e.voucher_no not in depended_vouchers
            ]

            available_ops = ["rate", "qty", "insert_receipt", "insert_issue"]
            if len(cancellable_receipts) > 1:
                available_ops.append("cancel_receipt")

            op = data.draw(st.sampled_from(available_ops))

            if op == "rate" and receipts:
                chosen_r = data.draw(st.sampled_from(receipts))
                new_rate = Decimal(
                    str(data.draw(st.integers(min_value=10, max_value=200)))
                )
                ledger_early.update_entry(
                    chosen_r.voucher_no, chosen_r.item, incoming_rate=new_rate
                )
                ledger_full.update_entry(
                    chosen_r.voucher_no, chosen_r.item, incoming_rate=new_rate
                )
                ledger_for_rebuild.update_entry(
                    chosen_r.voucher_no, chosen_r.item, incoming_rate=new_rate
                )

            elif op == "qty" and receipts:
                chosen_r = data.draw(st.sampled_from(receipts))
                add_qty = Decimal(
                    str(data.draw(st.integers(min_value=-5, max_value=40)))
                )
                new_qty = chosen_r.qty_change + add_qty
                if new_qty <= Decimal("0"):
                    new_qty = Decimal("5")
                ledger_early.update_entry(
                    chosen_r.voucher_no, chosen_r.item, qty_change=new_qty
                )
                ledger_full.update_entry(
                    chosen_r.voucher_no, chosen_r.item, qty_change=new_qty
                )
                ledger_for_rebuild.update_entry(
                    chosen_r.voucher_no, chosen_r.item, qty_change=new_qty
                )

            elif op == "insert_receipt":
                target_item = data.draw(st.sampled_from(items))
                item_entries = [e for e in entries if e.item == target_item]
                min_dt = min(e.posting_datetime for e in item_entries)
                max_dt = max(e.posting_datetime for e in item_entries)
                span_hours = max(1, int((max_dt - min_dt).total_seconds() // 3600))
                hours_offset = data.draw(
                    st.integers(min_value=1, max_value=span_hours)
                )
                insert_dt = min_dt + timedelta(hours=hours_offset)
                ins_qty = Decimal(
                    str(data.draw(st.integers(min_value=5, max_value=40)))
                )
                ins_rate = Decimal(
                    str(data.draw(st.integers(min_value=10, max_value=150)))
                )
                v_no = f"INS-PR-{edit_idx}"
                new_sle = _make_sle(
                    item=target_item,
                    warehouse="Stores",
                    voucher_type="Purchase Receipt",
                    voucher_no=v_no,
                    posting_datetime=insert_dt,
                    creation_seq=50 + edit_idx,
                    qty_change=ins_qty,
                    incoming_rate=ins_rate,
                )
                ledger_early.insert_entry(new_sle)
                ledger_full.insert_entry(new_sle)
                ledger_for_rebuild.insert_entry(new_sle)

            elif op == "cancel_receipt" and cancellable_receipts:
                chosen_r = data.draw(st.sampled_from(cancellable_receipts))
                ledger_early.remove_entry(chosen_r.voucher_no, chosen_r.item)
                ledger_full.remove_entry(chosen_r.voucher_no, chosen_r.item)
                ledger_for_rebuild.remove_entry(
                    chosen_r.voucher_no, chosen_r.item
                )

            elif op == "insert_issue":
                target_item = data.draw(st.sampled_from(items))
                item_entries = [e for e in entries if e.item == target_item]
                min_dt = min(e.posting_datetime for e in item_entries)
                max_dt = max(e.posting_datetime for e in item_entries)
                span_hours = max(1, int((max_dt - min_dt).total_seconds() // 3600))
                hours_offset = data.draw(
                    st.integers(min_value=1, max_value=span_hours)
                )
                insert_dt = min_dt + timedelta(hours=hours_offset)
                issue_qty = Decimal(
                    str(data.draw(st.integers(min_value=1, max_value=5)))
                )
                v_no = f"INS-DN-{edit_idx}"
                new_sle = _make_sle(
                    item=target_item,
                    warehouse="Stores",
                    voucher_type="Delivery Note",
                    voucher_no=v_no,
                    posting_datetime=insert_dt,
                    creation_seq=70 + edit_idx,
                    qty_change=-issue_qty,
                )
                ledger_early.insert_entry(new_sle)
                ledger_full.insert_entry(new_sle)
                ledger_for_rebuild.insert_entry(new_sle)

        dirty = ledger_early.dirty_ranges()
        assume(len(dirty) > 0)

        # Multi-key seeding: pick one key from dirty ranges and global minimum datetime
        first_key = next(iter(dirty))
        rep_item, rep_wh = first_key
        from_dt = min(d_min for d_min, _ in dirty.values())

        pre_repost_entries = ledger_early.all_entries()
        pre_repost_dirty = ledger_early.dirty_ranges()

        try:
            engine_early = RepostEngine(ledger_early)
            engine_early.repost(rep_item, rep_wh, from_dt, early_stop=True)
        except InsufficientStockError:
            # When InsufficientStockError is raised, original ledger and dirty
            # marks must remain intact.
            assert ledger_early.all_entries() == pre_repost_entries
            assert ledger_early.dirty_ranges() == pre_repost_dirty
            assume(False)

        try:
            engine_full = RepostEngine(ledger_full)
            engine_full.repost(rep_item, rep_wh, from_dt, early_stop=False)
            rebuilt = full_rebuild(ledger_for_rebuild)
        except InsufficientStockError:
            assume(False)

        # 1. Equivalence check across all fields
        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.all_entries() == rebuilt.all_entries()

        # 2. Idempotency check: second repost on the settled ledger
        res_idem = engine_early.repost(
            rep_item, rep_wh, from_dt, early_stop=True
        )
        assert len(res_idem.value_changed) == 0
        assert ledger_early.all_entries() == rebuilt.all_entries()

    def test_insufficient_stock_preserves_ledger_and_dirty_marks(self) -> None:
        """Assert that InsufficientStockError leaves ledger state untouched."""
        base_time = datetime(2026, 1, 1, 10, 0)
        sle1 = _make_sle(
            item="RawMaterial1",
            voucher_type="Purchase Receipt",
            voucher_no="PR-01",
            posting_datetime=base_time,
            creation_seq=1,
            qty_change=Decimal("10"),
            incoming_rate=Decimal("20"),
        )
        ledger = StockLedger([sle1])
        engine = RepostEngine(ledger)
        engine.repost("RawMaterial1", "Stores", base_time, early_stop=False)

        # Insert a backdated issue that exceeds stock (15 > 10)
        issue_sle = _make_sle(
            item="RawMaterial1",
            voucher_type="Delivery Note",
            voucher_no="DN-EXCEED",
            posting_datetime=base_time + timedelta(hours=1),
            creation_seq=2,
            qty_change=Decimal("-15"),
        )
        ledger.insert_entry(issue_sle)

        pre_entries = ledger.all_entries()
        pre_dirty = ledger.dirty_ranges()
        assert len(pre_dirty) > 0

        try:
            engine.repost(
                "RawMaterial1", "Stores", base_time, early_stop=True
            )
            raise AssertionError("Should have raised InsufficientStockError")
        except InsufficientStockError:
            pass

        assert ledger.all_entries() == pre_entries
        assert ledger.dirty_ranges() == pre_dirty
