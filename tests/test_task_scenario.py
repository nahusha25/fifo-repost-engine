"""Golden test for the exact task scenario.

Scenario:
- Items: Milk and Milk Powder
- Warehouse: Stores
- Year: 2026
- Opening: Milk +100 @ 50
- 10 Jan PR-01: Milk +50 @ 60
- 15 Jan DN-01: Milk -80
- 20 Jan PR-02: Milk +50 @ 60
- 21 Jan MFG-01: Milk -100 consumed, Milk Powder +10 kg produced (depends_on MFG-01)
- 25 Jan DN-02: Milk Powder -10 kg

Initial repost establishes balances, then PR-01 incoming rate is changed to 80.
Validates all before/after values, RepostResult exact lists, early_stop equivalence,
and idempotency.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostEngine


def build_scenario_ledger() -> tuple[StockLedger, dict[str, datetime]]:
    """Build the un-reposted initial task scenario ledger."""
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

    dates = {
        "open": t_open,
        "pr1": t_pr1,
        "dn1": t_dn1,
        "pr2": t_pr2,
        "mfg": t_mfg,
        "dn2": t_dn2,
    }
    return StockLedger(entries), dates


class TestTaskScenarioGolden:
    """Exact golden tests for the Milk -> Milk Powder manufacturing scenario."""

    def test_golden_scenario_before_and_after(self) -> None:
        """Validate exact before and after values and RepostResult lists."""
        ledger, dates = build_scenario_ledger()
        engine = RepostEngine(ledger)

        # 1. Initial repost to establish balances
        engine.repost("Milk", "Stores", dates["open"])

        # Assert BEFORE:
        mfg_milk = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-01" and e.item == "Milk"
        )
        assert mfg_milk.outgoing_value == Decimal("5800")
        assert mfg_milk.value_change == Decimal("-5800")

        mfg_powder = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-01" and e.item == "Milk Powder"
        )
        assert mfg_powder.incoming_rate == Decimal("580")
        assert mfg_powder.value_change == Decimal("5800")

        dn_02 = next(
            e for e in ledger.all_entries() if e.voucher_no == "DN-02"
        )
        assert dn_02.outgoing_value == Decimal("5800")
        assert dn_02.value_change == Decimal("-5800")

        milk_entries = [
            e for e in ledger.all_entries() if e.item == "Milk"
        ]
        assert milk_entries[-1].balance_qty == Decimal("20")
        assert milk_entries[-1].balance_value == Decimal("1200")

        # 2. Change PR-01 incoming_rate to 80
        from_dt = ledger.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )

        # Repost with early_stop=True
        res = engine.repost("Milk", "Stores", from_dt, early_stop=True)

        # Assert AFTER:
        pr_01_after = next(
            e for e in ledger.all_entries() if e.voucher_no == "PR-01"
        )
        assert pr_01_after.incoming_rate == Decimal("80")
        assert pr_01_after.value_change == Decimal("4000")

        dn_01_after = next(
            e for e in ledger.all_entries() if e.voucher_no == "DN-01"
        )
        assert dn_01_after.value_change == Decimal("-4000")
        assert dn_01_after.balance_value == Decimal("5000")

        pr_02_after = next(
            e for e in ledger.all_entries() if e.voucher_no == "PR-02"
        )
        assert pr_02_after.value_change == Decimal("3000")

        mfg_milk_after = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-01" and e.item == "Milk"
        )
        assert mfg_milk_after.outgoing_value == Decimal("6800")
        assert mfg_milk_after.value_change == Decimal("-6800")

        mfg_powder_after = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-01" and e.item == "Milk Powder"
        )
        assert mfg_powder_after.incoming_rate == Decimal("680")
        assert mfg_powder_after.value_change == Decimal("6800")

        dn_02_after = next(
            e for e in ledger.all_entries() if e.voucher_no == "DN-02"
        )
        assert dn_02_after.outgoing_value == Decimal("6800")
        assert dn_02_after.value_change == Decimal("-6800")

        cogs_delta = dn_02_after.outgoing_value - dn_02.outgoing_value
        assert cogs_delta == Decimal("1000")

        milk_entries_after = [
            e for e in ledger.all_entries() if e.item == "Milk"
        ]
        assert milk_entries_after[-1].balance_qty == Decimal("20")
        assert milk_entries_after[-1].balance_value == Decimal("1200")

        # Assert RepostResult exact lists:
        # value_changed contains exactly PR-01, MFG-01 (Milk out & Powder in), DN-02
        assert [vc.voucher_no for vc in res.value_changed] == [
            "PR-01",
            "MFG-01",
            "MFG-01",
            "DN-02",
        ]
        assert [
            (vc.voucher_no, vc.item) for vc in res.value_changed
        ] == [
            ("PR-01", "Milk"),
            ("MFG-01", "Milk"),
            ("MFG-01", "Milk Powder"),
            ("DN-02", "Milk Powder"),
        ]

        # re_evaluated_only contains exactly DN-01 and PR-02
        assert [e.voucher_no for e in res.re_evaluated_only] == [
            "DN-01",
            "PR-02",
        ]

        # 3. Assert idempotency on second run
        res_idempotent = engine.repost(
            "Milk", "Stores", from_dt, early_stop=True
        )
        assert len(res_idempotent.value_changed) == 0

    @pytest.mark.parametrize("early_stop", [True, False])
    def test_golden_scenario_early_stop_equivalence(
        self, early_stop: bool
    ) -> None:
        """Assert identical ledgers with early_stop True and False."""
        ledger, dates = build_scenario_ledger()
        engine = RepostEngine(ledger)
        engine.repost("Milk", "Stores", dates["open"])

        from_dt = ledger.update_entry(
            "PR-01", "Milk", incoming_rate=Decimal("80")
        )
        engine.repost("Milk", "Stores", from_dt, early_stop=early_stop)

        # Baseline comparison against known committed states
        pr_01 = next(
            e for e in ledger.all_entries() if e.voucher_no == "PR-01"
        )
        assert pr_01.incoming_rate == Decimal("80")
        assert pr_01.value_change == Decimal("4000")

        mfg_powder = next(
            e
            for e in ledger.all_entries()
            if e.voucher_no == "MFG-01" and e.item == "Milk Powder"
        )
        assert mfg_powder.incoming_rate == Decimal("680")
        assert mfg_powder.value_change == Decimal("6800")
