# ERPNext Architecture Mapping

This document provides a conceptual and architectural mapping between the standalone `stock_repost` engine and ERPNext's stock valuation and reposting subsystem.

## Architectural Comparison

| `stock_repost` Component | ERPNext Equivalent | Description / Mapping |
|---|---|---|
| `RepostEngine.repost()` / `dirty_ranges` | `Repost Item Valuation` (DocType) | Queued document representing pending repost tasks with item, warehouse, and timestamp boundaries. |
| Replay loop in `_repost_key()` | `update_entries_after` (`stock_ledger.py`) | Iterates forward through chronological entries for an `(item, warehouse)` key, re-evaluating valuation rates and balances. |
| `FIFOQueue.snapshot()` / `SLE.fifo_snapshot` | `stock_queue` field on `Stock Ledger Entry` | Persisted FIFO state representation. ERPNext stores serialized JSON `[[qty, rate], ...]`; `stock_repost` stores immutable tuples of `FifoBin(qty, rate)`. |
| `SLE.outgoing_dependencies` | `dependant_sle_voucher_detail_no` *(verify against ERPNext source)* | Links consumption of raw materials to the incoming receipt of finished goods in manufacturing vouchers. |
| `stock_repost.gl.generate_gl_entries()` | `repost_future_sle_and_gle` *(verify against ERPNext source)* | Derives stock value delta per voucher and emits balanced General Ledger Entries for inventory and expense accounts. |
| `ledger.get_min_dirty(item, wh)` | `posting_date` / `posting_time` on `Repost Item Valuation` | The timestamp boundary indicating from where recalculation and FIFO replay must begin. |
| `ledger.get_max_dirty(item, wh)` | *No direct ERPNext equivalent* | Safety gate in `stock_repost` preventing early-stop optimization from halting before all backdated edits have been evaluated. |

---

## Detailed Component Analysis

### 1. `Repost Item Valuation` vs. `dirty_ranges`
- **ERPNext**: When backdated stock transactions are submitted, cancelled, or amended, ERPNext creates a `Repost Item Valuation` document in the background. It records `item_code`, `warehouse`, `posting_date`, `posting_time`, and state tracking flags (`Queued`, `In Progress`, `Completed`, `Failed`).
- **`stock_repost`**: The `StockLedger` tracks dirty ranges via `(min_dirty, max_dirty)` timestamps per `(item, warehouse)` key whenever an entry is inserted, replaced, or removed. A repost operation reads `dirty_ranges()` to seed its work queue, ensuring all dirty keys across items and warehouses are processed in a single coordinated run.

### 2. `update_entries_after` vs. Replay Loop
- **ERPNext**: Located in `erpnext/stock/stock_ledger.py`, `update_entries_after` queries SLEs matching the item and warehouse where `timestamp >= repost_start_time`, ordered by posting timestamp and creation order. It updates running valuation rates and running balances sequentially.
- **`stock_repost`**: `RepostEngine._repost_key()` identifies the earliest dirty timestamp, restores the clean `fifo_snapshot` from the immediately preceding entry (or initializes an empty queue if rewinding to the beginning), and replays subsequent entries through the pure `FIFOQueue` until either the ledger ends or early-stop conditions are met.

### 3. `stock_queue` vs. `fifo_snapshot`
- **ERPNext**: In the database schema for `tabStock Ledger Entry`, the `stock_queue` column stores a JSON string formatted as `[[qty1, rate1], [qty2, rate2], ...]`. Each consumption pops or trims from the front of this serialized queue.
- **`stock_repost`**: The pure `FIFOQueue` class operates on immutable dataclass instances (`FifoBin(qty=Decimal, rate=Decimal)`). Each `StockLedgerEntry` holds a `fifo_snapshot` containing a snapshot of remaining inventory bins immediately after that entry posted.

### 4. `dependant_sle_voucher_detail_no` vs. Outgoing Dependencies
- **ERPNext**: To support multi-level valuation (e.g. Stock Entry of type Manufacture), ERPNext tracks relationships between raw material consumption and finished goods production lines using detail row references like `dependant_sle_voucher_detail_no` *(verify against ERPNext source across versions v13, v14, and v15)*. When raw material costs shift, dependent finished good receipts must be re-evaluated.
- **`stock_repost`**: A raw material consumption entry explicitly stores `outgoing_dependencies` containing `(dep_item, dep_warehouse, dep_posting_datetime)`. When consumption rate changes during replay:
  1. The finished good receipt's `incoming_rate` is updated to match the total consumption value divided by the finished good qty.
  2. The finished good's key is marked dirty at its posting datetime.
  3. The finished good key is enqueued into the `RepostEngine` work queue to propagate downstream valuation.

### 5. `repost_future_sle_and_gle` vs. `stock_repost.gl`
- **ERPNext**: Valuation changes require adjusting the accounting ledger (`tabGL Entry`). ERPNext reconciles accounting balance against stock valuation using functions like `repost_future_sle_and_gle` or background reposting jobs *(verify exact call hierarchy and naming against ERPNext version in use)*.
- **`stock_repost`**: `stock_repost.gl` consumes a `RepostResult`, inspects stock value deltas (`val_after - val_before`) per voucher, and emits balanced double-entry accounting records:
  - **Purchase Receipts**: Dr `Stock In Hand`, Cr `Stock Received But Not Billed`.
  - **Delivery Notes**: Dr `Cost of Goods Sold`, Cr `Stock In Hand`.
  - **Manufacture Entries**: Dr `Stock In Hand` (Finished Good), Cr `Stock In Hand` (Raw Material).

### 6. Dirty Ranges and Early-Stop Safeguards
- **ERPNext**: ERPNext evaluates entries sequentially forward from the target date. Early stopping has historically been a source of edge-case discrepancies in complex multi-voucher revaluations.
- **`stock_repost`**: Early stopping is mathematically gated:
  1. An entry's output valuation rate must match its previous rate.
  2. An entry's ending balance value must match its previous balance value.
  3. An entry's FIFO snapshot must match its previous snapshot.
  4. **Critical Gate**: The current entry's posting datetime must be **strictly greater than** `max_dirty` for that key, ensuring intermediate edits are not skipped prematurely.
