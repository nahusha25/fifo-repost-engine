# ERPNext Architecture Mapping

This document provides a conceptual and architectural mapping between the standalone `stock_repost` engine and ERPNext's stock valuation and reposting subsystem.

## Architectural Comparison

| `stock_repost` Component | ERPNext Equivalent *(verify against ERPNext source)* | Description / Mapping |
|---|---|---|
| `RepostEngine.repost()` / `dirty_ranges` | `Repost Item Valuation` (DocType) *(verify against ERPNext source)* | Queued document representing pending repost tasks with item, warehouse, and timestamp boundaries. |
| Replay loop in `RepostEngine.repost()` | `update_entries_after` in `stock_ledger.py` *(verify against ERPNext source)* | Iterates forward through chronological entries for an `(item, warehouse)` key, re-evaluating valuation rates and balances. |
| `FifoQueue.snapshot()` / `SLE.fifo_snapshot` | `stock_queue` field on `Stock Ledger Entry` *(verify against ERPNext source)* | Persisted FIFO state representation. ERPNext stores serialized JSON `[[qty, rate], ...]` *(verify against ERPNext source)*; `stock_repost` stores immutable tuples of `(qty, rate)` pairs. |
| `SLE.depends_on` | `dependant_sle_voucher_detail_no` *(verify against ERPNext source)* | Links consumption of raw materials to the incoming receipt of finished goods in manufacturing vouchers. |
| `stock_repost.gl.generate_gl_entries()` | `repost_future_sle_and_gle` *(verify against ERPNext source)* | Derives stock value delta per voucher and emits balanced General Ledger Entries for inventory and expense accounts. |
| `ledger.get_min_dirty(item, wh)` | `posting_date` / `posting_time` on `Repost Item Valuation` *(verify against ERPNext source)* | The timestamp boundary indicating from where recalculation and FIFO replay must begin. |
| `ledger.get_max_dirty(item, wh)` | *No direct ERPNext equivalent* | Safety gate in `stock_repost` preventing early-stop optimization from halting before all backdated edits have been evaluated. |

---

## Detailed Component Analysis

### 1. `Repost Item Valuation` vs. `dirty_ranges`
- **ERPNext**: When backdated stock transactions are submitted, cancelled, or amended, ERPNext creates a `Repost Item Valuation` document *(verify against ERPNext source)* in the background. It records `item_code`, `warehouse`, `posting_date`, `posting_time` *(verify against ERPNext source)*, and state tracking flags such as `Queued`, `In Progress`, `Completed`, `Failed` *(verify against ERPNext source)*.
- **`stock_repost`**: The `StockLedger` tracks dirty ranges via `(min_dirty, max_dirty)` timestamps per `(item, warehouse)` key whenever an entry is inserted, replaced, or removed. A repost operation reads `dirty_ranges()` to seed its work queue, ensuring all dirty keys across items and warehouses are processed in a single coordinated run.

### 2. `update_entries_after` vs. Replay Loop
- **ERPNext**: Located in `erpnext/stock/stock_ledger.py` *(verify against ERPNext source)*, `update_entries_after` *(verify against ERPNext source)* queries SLEs matching the item and warehouse where `timestamp >= repost_start_time`, ordered by posting timestamp and creation order. It updates running valuation rates and running balances sequentially.
- **`stock_repost`**: The replay loop in `RepostEngine.repost()` identifies the earliest dirty timestamp, restores the clean `fifo_snapshot` from the immediately preceding entry (or initializes an empty queue if rewinding to the beginning), and replays subsequent entries through the pure `FifoQueue` until either the entries end or early-stop conditions are met.

### 3. `stock_queue` vs. `fifo_snapshot`
- **ERPNext**: In the database schema for `tabStock Ledger Entry` *(verify against ERPNext source)*, the `stock_queue` column *(verify against ERPNext source)* stores a JSON string formatted as `[[qty1, rate1], [qty2, rate2], ...]` *(verify against ERPNext source)*. Each consumption pops or trims from the front of this serialized queue.
- **`stock_repost`**: The pure `FifoQueue` class operates on immutable tuples of `(qty, rate)` pairs (`tuple[tuple[Decimal, Decimal], ...]`). Each `StockLedgerEntry` holds a `fifo_snapshot` containing a snapshot of remaining inventory bins immediately after that entry posted.

### 4. `dependant_sle_voucher_detail_no` vs. `depends_on`
- **ERPNext**: To support multi-level valuation (e.g. Stock Entry of type Manufacture), ERPNext tracks relationships between raw material consumption and finished goods production lines using detail row references like `dependant_sle_voucher_detail_no` *(verify against ERPNext source)*. When raw material costs shift, dependent finished good receipts must be re-evaluated.
- **`stock_repost`**: In `StockLedgerEntry`, dependent entries use the `depends_on` field linking a finished good receipt to its source manufacturing voucher. During replay, `is_dependent_on` identifies these links and queries consumed raw material issues via `get_consumed_issues_for_dependent`:
  1. The finished good receipt's `incoming_rate` is updated to match the total consumed value divided by the finished good qty.
  2. The finished good's key is marked dirty at its posting datetime.
  3. The finished good key is enqueued into the `RepostEngine` work queue to propagate downstream valuation.

### 5. `repost_future_sle_and_gle` vs. `stock_repost.gl`
- **ERPNext**: Valuation changes require adjusting the accounting ledger in `tabGL Entry` *(verify against ERPNext source)*. ERPNext reconciles accounting balance against stock valuation using functions like `repost_future_sle_and_gle` *(verify against ERPNext source)*.
- **`stock_repost`**: `stock_repost.gl.generate_gl_entries()` consumes a `RepostResult`, inspects stock value deltas (`val_after - val_before`) per voucher, and emits balanced double-entry accounting records:
  - **Purchase Receipts**: Dr `Stock In Hand`, Cr `Stock Received But Not Billed`.
  - **Delivery Notes**: Dr `Cost of Goods Sold`, Cr `Stock In Hand`.
  - **Manufacture Entries**: Dr `Stock In Hand` (Finished Good), Cr `Stock In Hand` (Raw Material).

### 6. Dirty Ranges and Early-Stop Safeguards
- **ERPNext**: ERPNext evaluates entries sequentially forward from the target date *(verify against ERPNext source)*.
- **`stock_repost`**: Early stopping in `RepostEngine.repost()` is permitted only when all of the following conditions are simultaneously met:
  1. **Not on the first replayed entry**: At least one entry has been replayed (`i >= 1`).
  2. **Strictly after max dirty**: The entry's posting datetime is strictly greater than the key's maximum dirty datetime (`entry.posting_datetime > max_dirty`).
  3. **Snapshot equal**: The newly calculated FIFO snapshot exactly matches the existing snapshot (`new_entry.fifo_snapshot == entry.fifo_snapshot`).
  4. **Value change equal**: The net value change applied to the running balance matches (`new_entry.value_change == entry.value_change`).
  5. **Balance value equal**: The resulting running balance value matches (`new_entry.balance_value == entry.balance_value`).
  6. **No dependent pending**: There are no pending dependent entries upstream or in the work queue for this item and warehouse (`not work_queue.has_dependent_pending(...)`).
