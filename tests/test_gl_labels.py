"""Tests for item-labeled Stock In Hand lines in GL vouchers and CLI rendering.

Verifies that GLEntry preserves account='Stock In Hand' while populating
item with the respective item SKU, and that CLI rendering formats
individual voucher lines as 'Stock In Hand (<item>)' while keeping
net-by-account totals grouped under 'Stock In Hand'.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from stock_repost.cli import main
from stock_repost.gl import generate_gl_entries
from stock_repost.scenario import build_task_scenario


class TestGLLabels:
    """Tests for item labeling on Stock In Hand lines."""

    def test_gl_entries_retain_stock_in_hand_account_and_populate_item(
        self,
    ) -> None:
        """GLEntry.account remains 'Stock In Hand' while entry.item is set."""
        ledger, engine = build_task_scenario(initial_repost=True)
        from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))
        result = engine.repost("Milk", "Stores", from_dt, early_stop=True)
        summary = generate_gl_entries(result)

        # Check PR-01 entries
        pr_res = next(v for v in summary.voucher_results if v.voucher_no == "PR-01")
        sih_pr = next(e for e in pr_res.entries if e.account == "Stock In Hand")
        assert sih_pr.account == "Stock In Hand"
        assert sih_pr.item == "Milk"

        # Check MFG-01 entries
        mfg_res = next(v for v in summary.voucher_results if v.voucher_no == "MFG-01")
        mfg_milk = next(
            e for e in mfg_res.entries if e.account == "Stock In Hand" and e.credit > 0
        )
        assert mfg_milk.account == "Stock In Hand"
        assert mfg_milk.item == "Milk"

        mfg_powder = next(
            e for e in mfg_res.entries if e.account == "Stock In Hand" and e.debit > 0
        )
        assert mfg_powder.account == "Stock In Hand"
        assert mfg_powder.item == "Milk Powder"

        # Check DN-02 entries
        dn_res = next(v for v in summary.voucher_results if v.voucher_no == "DN-02")
        sih_dn = next(e for e in dn_res.entries if e.account == "Stock In Hand")
        assert sih_dn.account == "Stock In Hand"
        assert sih_dn.item == "Milk Powder"

    def test_cli_renders_item_specific_stock_in_hand_labels(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CLI renders item Stock In Hand labels and groups net totals under SIH."""
        exit_code = main()
        assert exit_code == 0

        out = capsys.readouterr().out

        # Individual voucher entries should be item-labeled
        assert "Stock In Hand (Milk)" in out
        assert "Stock In Hand (Milk Powder)" in out

        # Net balance section should group under "Stock In Hand"
        assert "NET GL BALANCES BY ACCOUNT" in out
        net_section = out.split("NET GL BALANCES BY ACCOUNT")[1]
        sih_net_lines = [
            line
            for line in net_section.splitlines()
            if line.startswith("Stock In Hand")
        ]
        assert len(sih_net_lines) == 1
        assert "2000" in sih_net_lines[0]
        assert "0" in sih_net_lines[0]
