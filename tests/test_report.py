"""Tests for the plain-text repost reporting module (stock_repost.report).

Written before implementation per the Phase 4 development rules.
Tests before/after table generation, changed row indicators, and summary
partition reporting.
"""

from __future__ import annotations

from decimal import Decimal

from stock_repost.report import generate_repost_report
from stock_repost.scenario import build_task_scenario


class TestRepostReport:
    """Unit tests for generate_repost_report."""

    def test_task_scenario_report_content(self) -> None:
        """Report contains before/after table, changed markers, and summary."""
        ledger, engine = build_task_scenario()
        ledger_before = ledger.copy()

        from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))
        res = engine.repost("Milk", "Stores", from_dt, early_stop=True)

        report_text = generate_repost_report(ledger_before, ledger, res)
        assert isinstance(report_text, str)

        # Must mention all key vouchers
        for v in ("OPEN-01", "PR-01", "DN-01", "PR-02", "MFG-01", "DN-02"):
            assert v in report_text

        # Must mention items
        assert "Milk" in report_text
        assert "Milk Powder" in report_text

        # Must mention changed rows vs re-evaluated rows in summary
        assert "Value Changed" in report_text or "value_changed" in report_text
        assert "Re-evaluated" in report_text or "re_evaluated_only" in report_text
        assert "PR-01" in report_text
        assert "DN-02" in report_text

        # Must mark changed rows (e.g. with '*' or '[*]')
        assert "*" in report_text
