"""Plain-text report generation for stock ledger repost operations.

Formats before/after comparisons of stock ledger entries, marks modified rows,
and displays a summary partition of value-changed versus re-evaluated entries.
"""

from __future__ import annotations

from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostResult


def generate_repost_report(
    ledger_before: StockLedger,
    ledger_after: StockLedger,
    result: RepostResult,
) -> str:
    """Generate a formatted plain-text before/after table and repost summary.

    Args:
        ledger_before: Snapshot of the ledger prior to reposting.
        ledger_after: Recomputed ledger after reposting.
        result: Outcome of the repost operation.

    Returns:
        Formatted multi-line plain text report.
    """
    lines: list[str] = []
    lines.append("=" * 110)
    lines.append("STOCK LEDGER REPOST REPORT")
    lines.append("=" * 110)

    # Build lookup for before entries: (voucher_no, item, creation_seq)
    before_map: dict[tuple[str, str, int], StockLedgerEntry] = {
        (e.voucher_no, e.item, e.creation_seq): e
        for e in ledger_before.all_entries()
    }

    changed_keys = {
        (vc.voucher_no, vc.item) for vc in result.value_changed
    }
    reeval_keys = {
        (e.voucher_no, e.item) for e in result.re_evaluated_only
    }

    # Header
    header = (
        f"{'':2} {'Voucher':<10} {'Item':<13} {'Qty':>6} "
        f"{'Rate (B)':>9} {'Val (B)':>10} {'BalVal (B)':>11} | "
        f"{'Rate (A)':>9} {'Val (A)':>10} {'BalVal (A)':>11}"
    )
    lines.append(header)
    lines.append("-" * 110)

    for entry_after in ledger_after.all_entries():
        key = (entry_after.voucher_no, entry_after.item, entry_after.creation_seq)
        entry_before = before_map.get(key)

        is_value_changed = (entry_after.voucher_no, entry_after.item) in changed_keys
        is_reeval = (entry_after.voucher_no, entry_after.item) in reeval_keys

        if is_value_changed:
            marker = "*"
        elif is_reeval:
            marker = "~"
        else:
            marker = " "

        b_rate = str(entry_before.incoming_rate) if entry_before else "-"
        b_val = str(entry_before.value_change) if entry_before else "-"
        b_bal = str(entry_before.balance_value) if entry_before else "-"

        a_rate = str(entry_after.incoming_rate)
        a_val = str(entry_after.value_change)
        a_bal = str(entry_after.balance_value)

        row = (
            f"{marker:2} {entry_after.voucher_no:<10} {entry_after.item:<13} "
            f"{entry_after.qty_change!s:>6} "
            f"{b_rate:>9} {b_val:>10} {b_bal:>11} | "
            f"{a_rate:>9} {a_val:>10} {a_bal:>11}"
        )
        lines.append(row)

    lines.append("-" * 110)
    lines.append("Legend: [*] Value Changed   [~] Re-evaluated Only   [ ] Unchanged")
    lines.append("")

    # Summary section
    lines.append("=" * 60)
    lines.append("REPOST SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Total Entries Replayed: {result.entries_replayed}")
    lines.append(f"Value Changed Count:    {len(result.value_changed)}")
    lines.append(f"Re-evaluated Count:     {len(result.re_evaluated_only)}")
    lines.append("")

    lines.append("Value Changed Entries:")
    if result.value_changed:
        for vc in result.value_changed:
            sign = "+" if vc.delta > 0 else ""
            lines.append(
                f"  - {vc.voucher_no:<10} ({vc.item:<12}): "
                f"old={vc.old_value:>7}, new={vc.new_value:>7}, delta={sign}{vc.delta}"
            )
    else:
        lines.append("  (None)")

    lines.append("")
    lines.append("Re-evaluated Only Entries:")
    if result.re_evaluated_only:
        for e in result.re_evaluated_only:
            lines.append(
                f"  - {e.voucher_no:<10} ({e.item:<12}): "
                f"bal_qty={e.balance_qty}, bal_val={e.balance_value}"
            )
    else:
        lines.append("  (None)")

    return "\n".join(lines)
