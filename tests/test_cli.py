"""Tests for the CLI module (stock_repost.cli).

Written before implementation per the Phase 4 development rules.
Tests command-line entrypoint main() via pytest capsys fixture.
"""

from __future__ import annotations

import pytest

from stock_repost import cli


class TestCLI:
    """Tests for the stock_repost CLI entrypoint."""

    def test_cli_main_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cli.main() executes the scenario and prints report and GL summary."""
        exit_code = cli.main()
        assert exit_code == 0

        captured = capsys.readouterr()
        out = captured.out

        # Check key scenario vouchers
        assert "PR-01" in out
        assert "DN-01" in out
        assert "MFG-01" in out
        assert "DN-02" in out

        # Check GL accounts and balances
        assert "Stock In Hand" in out
        assert "Stock Received But Not Billed" in out
        assert "Cost of Goods Sold" in out

        # Check debits/credits balance
        assert "1000" in out
        assert "3000" in out
