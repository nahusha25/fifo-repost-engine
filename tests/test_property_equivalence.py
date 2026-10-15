"""Property-based tests for StockLedger and RepostEngine equivalence and idempotency.

Uses Hypothesis to generate random valid ledgers across 1-3 items, apply
random edits before a single repost, and assert that:
1. early_stop=True == early_stop=False == full_rebuild (all SLE fields match).
2. Idempotency holds (second repost produces zero value_changed).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from stock_repost.fifo import InsufficientStockError
from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostEngine

# Register hypothesis profiles
settings.register_profile("ci", max_examples=50, deadline=None)
settings.register_profile("dev", max_examples=300, deadline=None)
settings.register_profile("stress", max_examples=1000, deadline=None)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev").strip())


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
    """Helper to build a StockLedgerEntry."""
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


# Strategy for generating valid ledger scenarios
@st.composite
def valid_ledger_scenario(draw: st.DrawFn) -> tuple[StockLedger, list[str]]:
    """Draw a valid multi-item ledger with optional manufacture vouchers."""
    items = ["RawMaterial1", "RawMaterial2", "FinishedGood"]
    warehouse = "Stores"
    base_time = datetime(2026, 1, 1, 10, 0)
    current_time = base_time

    entries: list[StockLedgerEntry] = []
    running_balances = {item: Decimal("0") for item in items}
    seq_counter = 1

    # Phase 1: Initial Receipts for raw materials
    for item in ["RawMaterial1", "RawMaterial2"]:
        qty = Decimal(str(draw(st.integers(min_value=50, max_value=200))))
        rate = Decimal(str(draw(st.integers(min_value=10, max_value=100))))
        entries.append(
            _make_sle(
                item=item,
                voucher_no=f"OPEN-{item}",
                posting_datetime=current_time,
                creation_seq=seq_counter,
                qty_change=qty,
                incoming_rate=rate,
            )
        )
        running_balances[item] += qty
        current_time += timedelta(days=1)
        seq_counter += 1

    # Optional initial FG receipt
    has_fg_open = draw(st.booleans())
    if has_fg_open:
        fg_qty = Decimal(str(draw(st.integers(min_value=10, max_value=50))))
        fg_rate = Decimal(str(draw(st.integers(min_value=100, max_value=300))))
        entries.append(
            _make_sle(
                item="FinishedGood",
                voucher_no="OPEN-FG",
                posting_datetime=current_time,
                creation_seq=seq_counter,
                qty_change=fg_qty,
                incoming_rate=fg_rate,
            )
        )
        running_balances["FinishedGood"] += fg_qty
        current_time += timedelta(days=1)
        seq_counter += 1

    # Phase 2: Interleaved Receipts and Issues
    num_steps = draw(st.integers(min_value=3, max_value=8))
    for step in range(num_steps):
        target_item = draw(st.sampled_from(items))
        can_issue = running_balances[target_item] > Decimal("10")
        is_receipt = draw(st.booleans()) if can_issue else True

        if is_receipt:
            qty = Decimal(str(draw(st.integers(min_value=20, max_value=100))))
            rate = Decimal(str(draw(st.integers(min_value=10, max_value=150))))
            entries.append(
                _make_sle(
                    item=target_item,
                    voucher_no=f"PR-{step}",
                    posting_datetime=current_time,
                    creation_seq=seq_counter,
                    qty_change=qty,
                    incoming_rate=rate,
                )
            )
            running_balances[target_item] += qty
        else:
            avail = int(running_balances[target_item])
            if avail <= 5:
                continue
            consume_qty = Decimal(
                str(draw(st.integers(min_value=1, max_value=avail - 2)))
            )
            entries.append(
                _make_sle(
                    item=target_item,
                    voucher_type="Delivery Note",
                    voucher_no=f"DN-{step}",
                    posting_datetime=current_time,
                    creation_seq=seq_counter,
                    qty_change=-consume_qty,
                )
            )
            running_balances[target_item] -= consume_qty

        current_time += timedelta(days=1)
        seq_counter += 1

    # Phase 3: Manufacture vouchers (0 to 2)
    num_mfg = draw(st.integers(min_value=0, max_value=2))
    for mfg_idx in range(num_mfg):
        rm1_avail = int(running_balances["RawMaterial1"])
        if rm1_avail <= 10:
            break
        rm1_consume = Decimal(
            str(draw(st.integers(min_value=5, max_value=rm1_avail - 5)))
        )
        fg_produce = Decimal(str(draw(st.integers(min_value=2, max_value=10))))
        mfg_v_no = f"MFG-{mfg_idx}"

        entries.append(
            _make_sle(
                item="RawMaterial1",
                voucher_type="Stock Entry",
                voucher_no=mfg_v_no,
                posting_datetime=current_time,
                creation_seq=1,
                qty_change=-rm1_consume,
            )
        )
        running_balances["RawMaterial1"] -= rm1_consume

        entries.append(
            _make_sle(
                item="FinishedGood",
                voucher_type="Stock Entry",
                voucher_no=mfg_v_no,
                posting_datetime=current_time,
                creation_seq=2,
                qty_change=fg_produce,
                depends_on=mfg_v_no,
            )
        )
        running_balances["FinishedGood"] += fg_produce
        current_time += timedelta(days=1)

    ledger = StockLedger(entries)
    # Establish baseline
    engine = RepostEngine(ledger)
    for it in items:
        engine.repost(it, warehouse, base_time, early_stop=False)

    return ledger, items


class TestPropertyEquivalence:
    """Hypothesis property test for early-stop equivalence and idempotency."""

    @given(scenario=valid_ledger_scenario(), data=st.data())
    def test_repost_equivalence_and_idempotency(
        self,
        scenario: tuple[StockLedger, list[str]],
        data: st.DataObject,
    ) -> None:
        """Assert early_stop True == False == full_rebuild across random edits."""
        base_ledger, _items = scenario
        warehouse = "Stores"
        ledger_early = base_ledger.copy()
        ledger_full = base_ledger.copy()

        # Find receipts that can be safely edited
        receipts = [
            e
            for e in ledger_early.all_entries()
            if e.qty_change > Decimal("0") and e.voucher_type == "Purchase Receipt"
        ]
        assume(len(receipts) >= 2)

        # Draw 1 to 4 edits
        num_edits = data.draw(st.integers(min_value=1, max_value=4))
        chosen_receipts = data.draw(
            st.lists(
                st.sampled_from(receipts),
                min_size=1,
                max_size=num_edits,
                unique_by=lambda r: (r.voucher_no, r.item),
            )
        )

        edited_dts: list[datetime] = []
        edited_items: list[str] = []

        for r in chosen_receipts:
            edit_type = data.draw(st.sampled_from(["rate", "qty"]))
            if edit_type == "rate":
                new_rate = Decimal(
                    str(data.draw(st.integers(min_value=20, max_value=200)))
                )
                dt_e = ledger_early.update_entry(
                    r.voucher_no, r.item, incoming_rate=new_rate
                )
                ledger_full.update_entry(
                    r.voucher_no, r.item, incoming_rate=new_rate
                )
            else:
                add_qty = Decimal(
                    str(data.draw(st.integers(min_value=10, max_value=50)))
                )
                new_qty = r.qty_change + add_qty
                dt_e = ledger_early.update_entry(
                    r.voucher_no, r.item, qty_change=new_qty
                )
                ledger_full.update_entry(
                    r.voucher_no, r.item, qty_change=new_qty
                )

            edited_dts.append(dt_e)
            edited_items.append(r.item)

        assume(len(edited_dts) > 0)
        from_dt = min(edited_dts)
        entry_item = edited_items[0]

        try:
            engine_early = RepostEngine(ledger_early)
            engine_early.repost(entry_item, warehouse, from_dt, early_stop=True)

            engine_full = RepostEngine(ledger_full)
            engine_full.repost(entry_item, warehouse, from_dt, early_stop=False)

            rebuilt = full_rebuild(ledger_early)
        except InsufficientStockError:
            assume(False)

        # 1. Equivalence check
        assert ledger_early.all_entries() == ledger_full.all_entries()
        assert ledger_early.all_entries() == rebuilt.all_entries()

        # 2. Idempotency check: second repost on the settled ledger
        res_idem = engine_early.repost(
            entry_item, warehouse, from_dt, early_stop=True
        )
        assert len(res_idem.value_changed) == 0
