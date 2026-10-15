"""Command-line interface for the stock repost engine.

Runs the canonical Milk -> Milk Powder manufacturing scenario,
triggers a reposting operation, and outputs the detailed plain-text
report and balanced GL ledger entries.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from stock_repost.gl import generate_gl_entries
from stock_repost.report import generate_repost_report
from stock_repost.scenario import build_task_scenario


def main() -> int:
    """Run the task scenario and print repost report and GL summary.

    Returns:
        Exit code (0 on success).
    """
    ledger, engine = build_task_scenario(initial_repost=True)
    ledger_before = ledger.copy()

    # Apply rate edit: PR-01 incoming_rate from 60 to 80
    from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))

    # Execute reposting with early stop enabled
    result = engine.repost("Milk", "Stores", from_dt, early_stop=True)

    # 1. Print plain-text report
    report_text = generate_repost_report(ledger_before, ledger, result)
    print(report_text)
    print()

    # 2. Print GL summary
    gl_summary = generate_gl_entries(result)
    print("=" * 75)
    print("GENERAL LEDGER (GL) SUMMARY")
    print("=" * 75)

    for v_res in gl_summary.voucher_results:
        print(f"\nVoucher: {v_res.voucher_type} - {v_res.voucher_no}")
        print(f"{'Account':<35} {'Debit':>12} {'Credit':>12}")
        print("-" * 62)
        for e in v_res.entries:
            dr = str(e.debit) if e.debit > 0 else "-"
            cr = str(e.credit) if e.credit > 0 else "-"
            account_display = (
                f"{e.account} ({e.item})"
                if e.account == "Stock In Hand" and e.item
                else e.account
            )
            print(f"{account_display:<35} {dr:>12} {cr:>12}")
        print("-" * 62)
        print(
            f"{'Total':<35} "
            f"{v_res.total_debit!s:>12} {v_res.total_credit!s:>12}"
        )
        print(f"Balanced: {v_res.is_balanced}")

    print("\n" + "=" * 75)
    print("NET GL BALANCES BY ACCOUNT")
    print("=" * 75)
    print(f"{'Account':<35} {'Debit':>12} {'Credit':>12} {'Net':>12}")
    print("-" * 75)
    for acct_bal in gl_summary.account_balances:
        dr = str(acct_bal.debit) if acct_bal.debit > 0 else "-"
        cr = str(acct_bal.credit) if acct_bal.credit > 0 else "-"
        net_str = f"{'+' if acct_bal.net > 0 else ''}{acct_bal.net}"
        print(f"{acct_bal.account:<35} {dr:>12} {cr:>12} {net_str:>12}")
    print("-" * 75)
    print(
        f"{'Grand Total':<35} "
        f"{gl_summary.total_debit!s:>12} {gl_summary.total_credit!s:>12}"
    )
    print(f"Overall Balanced: {gl_summary.is_balanced}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
