"""Tests for the GL entry generation module (stock_repost.gl).

Written before implementation per the Phase 4 development rules.
Tests exact accounting entries, debit/credit balancing per voucher and net,
sign convention handling, and verification that unchanged entries produce
no GL entries.
"""

from __future__ import annotations

from decimal import Decimal

from stock_repost.gl import (
    GLSummary,
    generate_gl_entries,
)
from stock_repost.repost import RepostResult, ValueChangedItem
from stock_repost.scenario import build_task_scenario


class TestGLEntryGeneration:
    """Unit tests for generate_gl_entries and GLSummary."""

    def test_task_scenario_exact_gl_numbers(self) -> None:
        """Validate exact GL postings, per-voucher balance, and net summary.

        Expected for the task scenario:
        - PR-01 +1000: Dr Stock In Hand 1000, Cr Stock Received But Not Billed 1000
        - MFG-01 Milk out +1000: Cr Stock In Hand 1000
        - MFG-01 Powder in +1000: Dr Stock In Hand 1000
        - DN-02 +1000: Dr Cost of Goods Sold 1000, Cr Stock In Hand 1000
        - Net: COGS +1000, Stock Received But Not Billed credit 1000,
          Stock In Hand net 0
        - Every voucher and the total must balance debit = credit.
        """
        ledger, engine = build_task_scenario()
        from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))
        res = engine.repost("Milk", "Stores", from_dt, early_stop=True)

        gl_summary = generate_gl_entries(res)
        assert isinstance(gl_summary, GLSummary)
        assert gl_summary.is_balanced

        # Per-voucher validations
        vouchers = {v.voucher_no: v for v in gl_summary.voucher_results}
        assert set(vouchers.keys()) == {"PR-01", "MFG-01", "DN-02"}

        # 1. PR-01
        pr1_res = vouchers["PR-01"]
        assert pr1_res.is_balanced
        assert pr1_res.total_debit == Decimal("1000")
        assert pr1_res.total_credit == Decimal("1000")
        pr1_accounts = {e.account: (e.debit, e.credit) for e in pr1_res.entries}
        assert pr1_accounts["Stock In Hand"] == (Decimal("1000"), Decimal("0"))
        assert pr1_accounts["Stock Received But Not Billed"] == (
            Decimal("0"),
            Decimal("1000"),
        )

        # 2. MFG-01
        mfg_res = vouchers["MFG-01"]
        assert mfg_res.is_balanced
        assert mfg_res.total_debit == Decimal("1000")
        assert mfg_res.total_credit == Decimal("1000")
        mfg_milk = next(e for e in mfg_res.entries if e.item == "Milk")
        assert mfg_milk.account == "Stock In Hand"
        assert mfg_milk.debit == Decimal("0")
        assert mfg_milk.credit == Decimal("1000")
        mfg_powder = next(e for e in mfg_res.entries if e.item == "Milk Powder")
        assert mfg_powder.account == "Stock In Hand"
        assert mfg_powder.debit == Decimal("1000")
        assert mfg_powder.credit == Decimal("0")

        # 3. DN-02
        dn2_res = vouchers["DN-02"]
        assert dn2_res.is_balanced
        assert dn2_res.total_debit == Decimal("1000")
        assert dn2_res.total_credit == Decimal("1000")
        dn2_accounts = {e.account: (e.debit, e.credit) for e in dn2_res.entries}
        assert dn2_accounts["Cost of Goods Sold"] == (Decimal("1000"), Decimal("0"))
        assert dn2_accounts["Stock In Hand"] == (Decimal("0"), Decimal("1000"))

        # Net summary validation
        net_map = gl_summary.net_by_account()
        assert net_map["Cost of Goods Sold"] == Decimal("1000")  # Dr 1000
        assert net_map["Stock Received But Not Billed"] == Decimal("-1000")  # Cr 1000
        assert net_map["Stock In Hand"] == Decimal("0")  # Net 0

        # Totals
        assert gl_summary.total_debit == Decimal("3000")
        assert gl_summary.total_credit == Decimal("3000")

    def test_unchanged_entries_produce_no_gl_entries(self) -> None:
        """DN-01 and PR-02 have no value difference, so no GL entries are produced."""
        ledger, engine = build_task_scenario()
        from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))
        res = engine.repost("Milk", "Stores", from_dt, early_stop=True)

        gl_summary = generate_gl_entries(res)
        voucher_nos = [e.voucher_no for e in gl_summary.entries]
        assert "DN-01" not in voucher_nos
        assert "PR-02" not in voucher_nos

    def test_empty_repost_result_produces_empty_summary(self) -> None:
        """An empty RepostResult yields empty GL summary with 0 debit and credit."""
        empty_res = RepostResult(value_changed=(), re_evaluated_only=())
        summary = generate_gl_entries(empty_res)
        assert len(summary.entries) == 0
        assert summary.total_debit == Decimal("0")
        assert summary.total_credit == Decimal("0")
        assert summary.is_balanced

    def test_receipt_negative_delta_accounting(self) -> None:
        """When purchase receipt rate decreases, Stock In Hand is credited."""
        item = ValueChangedItem(
            voucher_no="PR-NEG",
            old_value=Decimal("5000"),
            new_value=Decimal("4000"),
            delta=Decimal("-1000"),
            voucher_type="Purchase Receipt",
            item="Widget",
            warehouse="Stores",
        )
        res = RepostResult(value_changed=(item,), re_evaluated_only=())
        summary = generate_gl_entries(res)
        assert summary.is_balanced
        entry_map = {e.account: (e.debit, e.credit) for e in summary.entries}
        assert entry_map["Stock Received But Not Billed"] == (
            Decimal("1000"),
            Decimal("0"),
        )
        assert entry_map["Stock In Hand"] == (Decimal("0"), Decimal("1000"))

    def test_issue_positive_delta_accounting(self) -> None:
        """When issue outgoing cost decreases, COGS is credited and SIH debited."""
        # Value change becomes less negative (-5000 -> -4000, delta = +1000)
        item = ValueChangedItem(
            voucher_no="DN-RET",
            old_value=Decimal("-5000"),
            new_value=Decimal("-4000"),
            delta=Decimal("1000"),
            voucher_type="Delivery Note",
            item="Widget",
            warehouse="Stores",
        )
        res = RepostResult(value_changed=(item,), re_evaluated_only=())
        summary = generate_gl_entries(res)
        assert summary.is_balanced
        entry_map = {e.account: (e.debit, e.credit) for e in summary.entries}
        assert entry_map["Stock In Hand"] == (Decimal("1000"), Decimal("0"))
        assert entry_map["Cost of Goods Sold"] == (Decimal("0"), Decimal("1000"))
