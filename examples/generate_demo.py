"""Generate the static demo HTML page showcasing the FIFO repost scenario.

This script executes the canonical Milk -> Milk Powder scenario, applies the
backdated purchase receipt adjustment, runs the reposting engine, and outputs
a self-contained, responsive HTML file to docs/demo.html.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from stock_repost.gl import generate_gl_entries
from stock_repost.scenario import build_task_scenario

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KEY_INSIGHT = (
    "Under FIFO the 15 Jan Delivery Note value does not change (4,000). "
    "The Manufacture entry and the 25 Jan Milk Powder Delivery Note do. "
    "COGS +1,000."
)

CSS_STYLES = """    :root {
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --banner-bg: #eff6ff;
      --banner-border: #3b82f6;
      --banner-text: #1e3a8a;
      --changed-bg: #fef2f2;
      --changed-border: #f87171;
      --changed-text: #991b1b;
      --reeval-bg: #fefce8;
      --reeval-border: #facc15;
      --reeval-text: #854d0e;
      --table-header-bg: #f1f5f9;
      --table-border: #cbd5e1;
      --badge-unchanged-bg: #e2e8f0;
      --badge-unchanged-text: #475569;
      --badge-changed-bg: #fee2e2;
      --badge-changed-text: #b91c1c;
      --badge-reeval-bg: #fef9c3;
      --badge-reeval-text: #a16207;
      --green: #15803d;
      --red: #b91c1c;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0f172a;
        --card-bg: #1e293b;
        --text: #f8fafc;
        --text-muted: #94a3b8;
        --border: #334155;
        --banner-bg: #172554;
        --banner-border: #3b82f6;
        --banner-text: #bfdbfe;
        --changed-bg: #450a0a33;
        --changed-border: #ef4444;
        --changed-text: #fca5a5;
        --reeval-bg: #42200633;
        --reeval-border: #eab308;
        --reeval-text: #fde047;
        --table-header-bg: #1e293b;
        --table-border: #334155;
        --badge-unchanged-bg: #334155;
        --badge-unchanged-text: #cbd5e1;
        --badge-changed-bg: #7f1d1d;
        --badge-changed-text: #fecaca;
        --badge-reeval-bg: #713f12;
        --badge-reeval-text: #fef08a;
        --green: #4ade80;
        --red: #f87171;
      }
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
      line-height: 1.5;
      padding: 1.5rem;
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
    }
    header {
      margin-bottom: 1.5rem;
    }
    h1 {
      font-size: 1.875rem;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 0.5rem;
    }
    .subtitle {
      color: var(--text-muted);
      font-size: 0.95rem;
    }
    .banner {
      background-color: var(--banner-bg);
      border-left: 4px solid var(--banner-border);
      color: var(--banner-text);
      padding: 1rem 1.25rem;
      border-radius: 0.375rem;
      margin-bottom: 2rem;
      font-size: 1rem;
      font-weight: 500;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1.5rem;
      margin-bottom: 2rem;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }
    .card h2 {
      font-size: 1.25rem;
      font-weight: 600;
      margin-bottom: 1rem;
      color: var(--text);
    }
    .table-container {
      width: 100%;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      border: 1px solid var(--table-border);
      border-radius: 0.375rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.875rem;
      white-space: nowrap;
    }
    th, td {
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--table-border);
    }
    th {
      background-color: var(--table-header-bg);
      color: var(--text);
      font-weight: 600;
      text-align: left;
    }
    .group-header {
      text-align: center;
      border-left: 1px solid var(--table-border);
      border-right: 1px solid var(--table-border);
    }
    tr.row-changed {
      background-color: var(--changed-bg);
      border-left: 3px solid var(--changed-border);
    }
    tr.row-reeval {
      background-color: var(--reeval-bg);
      border-left: 3px solid var(--reeval-border);
    }
    .badge {
      display: inline-block;
      padding: 0.2rem 0.5rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
    }
    .badge-changed {
      background-color: var(--badge-changed-bg);
      color: var(--badge-changed-text);
    }
    .badge-reeval {
      background-color: var(--badge-reeval-bg);
      color: var(--badge-reeval-text);
    }
    .badge-unchanged {
      background-color: var(--badge-unchanged-bg);
      color: var(--badge-unchanged-text);
    }
    .text-right {
      text-align: right;
    }
    .text-center {
      text-align: center;
    }
    .font-bold {
      font-weight: 600;
    }
    .text-green {
      color: var(--green);
    }
    .text-red {
      color: var(--red);
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      margin-top: 1rem;
      font-size: 0.85rem;
      color: var(--text-muted);
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 0.35rem;
    }
    footer {
      margin-top: 2rem;
      color: var(--text-muted);
      font-size: 0.85rem;
      text-align: center;
    }"""


def _format_num(val: Decimal) -> str:
    """Format a Decimal number cleanly for table display.

    Args:
        val: The Decimal value to format.

    Returns:
        String formatted without unnecessary decimal places when integral.
    """
    if val == Decimal("0"):
        return "0"
    if val == val.to_integral():
        return str(int(val))
    return str(val)


def build_demo_html() -> str:
    """Build the complete self-contained HTML demo document.

    Executes the scenario reposting process and renders all before/after
    stock ledger entries and general ledger adjustments.

    Returns:
        A valid HTML5 document string with embedded styles.
    """
    ledger, engine = build_task_scenario(initial_repost=True)
    ledger_before = ledger.copy()

    # Apply rate edit: PR-01 incoming_rate from 60 to 80
    from_dt = ledger.update_entry("PR-01", "Milk", incoming_rate=Decimal("80"))

    # Execute reposting with early stop enabled
    result = engine.repost("Milk", "Stores", from_dt, early_stop=True)
    gl_summary = generate_gl_entries(result)

    entries_before = ledger_before.all_entries()
    entries_after = ledger.all_entries()

    changed_keys = {
        (item.voucher_no, item.item) for item in result.value_changed
    }
    reeval_keys = {
        (entry.voucher_no, entry.item) for entry in result.re_evaluated_only
    }

    # Build rows
    rows_html: list[str] = []
    for b, a in zip(entries_before, entries_after, strict=True):
        key = (a.voucher_no, a.item)
        if key in changed_keys:
            row_class = "row-changed"
            status_badge = '<span class="badge badge-changed">Value Changed</span>'
        elif key in reeval_keys:
            row_class = "row-reeval"
            status_badge = '<span class="badge badge-reeval">Re-evaluated Only</span>'
        else:
            row_class = "row-unchanged"
            status_badge = '<span class="badge badge-unchanged">Unchanged</span>'

        qty_str = _format_num(a.qty_change)
        rate_b = _format_num(b.incoming_rate)
        val_b = _format_num(b.value_change)
        bal_b = _format_num(b.balance_value)

        rate_a = _format_num(a.incoming_rate)
        val_a = _format_num(a.value_change)
        bal_a = _format_num(a.balance_value)

        rows_html.append(f"""          <tr class="{row_class}">
            <td class="cell-voucher"><strong>{a.voucher_no}</strong></td>
            <td>{a.item}</td>
            <td class="text-right">{qty_str}</td>
            <td class="text-right">{rate_b}</td>
            <td class="text-right">{val_b}</td>
            <td class="text-right">{bal_b}</td>
            <td class="text-right font-bold">{rate_a}</td>
            <td class="text-right font-bold">{val_a}</td>
            <td class="text-right font-bold">{bal_a}</td>
            <td class="text-center">{status_badge}</td>
          </tr>""")

    table_rows = "\n".join(rows_html)

    # GL Summary rows
    gl_rows: list[str] = []
    for acct_bal in gl_summary.account_balances:
        dr_str = _format_num(acct_bal.debit) if acct_bal.debit > 0 else "-"
        cr_str = _format_num(acct_bal.credit) if acct_bal.credit > 0 else "-"
        net_val = acct_bal.net
        prefix = "+" if net_val > 0 else ""
        net_str = f"{prefix}{_format_num(net_val)}"
        net_class = (
            "text-green" if net_val > 0 else ("text-red" if net_val < 0 else "")
        )
        gl_rows.append(f"""          <tr>
            <td><strong>{acct_bal.account}</strong></td>
            <td class="text-right">{dr_str}</td>
            <td class="text-right">{cr_str}</td>
            <td class="text-right font-bold {net_class}">{net_str}</td>
          </tr>""")

    gl_table_rows = "\n".join(gl_rows)
    total_dr = _format_num(gl_summary.total_debit)
    total_cr = _format_num(gl_summary.total_credit)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FIFO Repost: before and after</title>
  <style>
{CSS_STYLES}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>FIFO Repost: before and after</h1>
      <p class="subtitle">
        Canonical Milk &rarr; Milk Powder Manufacturing Repost Scenario
      </p>
    </header>

    <div class="banner">
      <strong>Key Insight:</strong> {KEY_INSIGHT}
    </div>

    <section class="card">
      <h2>Stock Ledger Entries (Before vs. After Repost)</h2>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th colspan="3">Transaction</th>
              <th colspan="3" class="group-header">Before Edit (PR-01 @ 60)</th>
              <th colspan="3" class="group-header">After Repost (PR-01 @ 80)</th>
              <th class="text-center">Status</th>
            </tr>
            <tr>
              <th>Voucher</th>
              <th>Item</th>
              <th class="text-right">Qty</th>
              <th class="text-right">Rate</th>
              <th class="text-right">Value</th>
              <th class="text-right">Bal Val</th>
              <th class="text-right font-bold">Rate</th>
              <th class="text-right font-bold">Value</th>
              <th class="text-right font-bold">Bal Val</th>
              <th class="text-center">Classification</th>
            </tr>
          </thead>
          <tbody>
{table_rows}
          </tbody>
        </table>
      </div>

      <div class="legend">
        <div class="legend-item">
          <span class="badge badge-changed">Value Changed</span>
          Row valuation or rate altered
        </div>
        <div class="legend-item">
          <span class="badge badge-reeval">Re-evaluated Only</span>
          Balance value updated, transaction value intact
        </div>
        <div class="legend-item">
          <span class="badge badge-unchanged">Unchanged</span>
          Completely unaffected historical entry
        </div>
      </div>
    </section>

    <section class="card">
      <h2>General Ledger (GL) Impact</h2>
      <p style="color: var(--text-muted); font-size: 0.875rem; margin-bottom: 1rem;">
        Balanced double-entry adjustments derived from inventory valuation differences.
      </p>
      <div class="table-container" style="max-width: 700px;">
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th class="text-right">Debit</th>
              <th class="text-right">Credit</th>
              <th class="text-right">Net</th>
            </tr>
          </thead>
          <tbody>
{gl_table_rows}
            <tr style="border-top: 2px solid var(--border);">
              <td><strong>Grand Total</strong></td>
              <td class="text-right font-bold">{total_dr}</td>
              <td class="text-right font-bold">{total_cr}</td>
              <td class="text-right font-bold text-green">Balanced</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <footer>
      Generated by <code>examples/generate_demo.py</code> &bull; FIFO Repost Engine
    </footer>
  </div>
</body>
</html>
"""


def generate_demo_html(target_path: Path | str | None = None) -> str:
    """Generate demo HTML and save it to the specified target path.

    Args:
        target_path: Optional destination path. If None, writes to
            docs/demo.html relative to project root.

    Returns:
        The generated HTML content string.
    """
    html_content = build_demo_html()
    if target_path is None:
        target_path = PROJECT_ROOT / "docs" / "demo.html"
    dest = Path(target_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html_content, encoding="utf-8")
    return html_content


def main() -> int:
    """CLI entry point for generating the demo HTML page.

    Returns:
        Exit code (0 on success).
    """
    out_file = PROJECT_ROOT / "docs" / "demo.html"
    generate_demo_html(out_file)
    print(f"Generated demo page: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
