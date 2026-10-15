"""Stock ledger reposting engine.

Recomputes valuation layers and financial balances after backdated
entries, cancellations, or rate adjustments. Performs all calculations
in-memory using Decimal arithmetic and atomic commit semantics.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from stock_repost.fifo import FifoQueue
from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry


@dataclass(frozen=True)
class ValueChangedItem:
    """Record of an entry whose financial value_change changed during repost.

    Supports tuple unpacking as ``(voucher, old, new, delta)``.

    Attributes:
        voucher_no: Source document identifier.
        old_value: Original value_change before reposting.
        new_value: Recomputed value_change after reposting.
        delta: Net financial difference (new_value - old_value).
        voucher_type: Source document type.
        item: Item code / SKU.
        warehouse: Warehouse name.
    """

    voucher_no: str
    old_value: Decimal
    new_value: Decimal
    delta: Decimal
    voucher_type: str = ""
    item: str = ""
    warehouse: str = ""

    @property
    def voucher(self) -> str:
        """Alias for voucher_no."""
        return self.voucher_no

    @property
    def old(self) -> Decimal:
        """Alias for old_value."""
        return self.old_value

    @property
    def new(self) -> Decimal:
        """Alias for new_value."""
        return self.new_value

    def __iter__(self) -> Iterator[Any]:
        """Support unpacking as (voucher, old, new, delta)."""
        yield self.voucher_no
        yield self.old_value
        yield self.new_value
        yield self.delta


@dataclass(frozen=True)
class RepostResult:
    """Outcome of a stock ledger repost operation.

    Attributes:
        value_changed: Entries whose financial value_change changed.
        re_evaluated_only: Entries whose running balances or layer
            snapshots changed, but whose value_change remained identical.
        entries_replayed: Total number of SLEs actually recomputed.
    """

    value_changed: tuple[ValueChangedItem, ...]
    re_evaluated_only: tuple[StockLedgerEntry, ...]
    entries_replayed: int = 0


def is_dependent_on(
    dependent: StockLedgerEntry, issue: StockLedgerEntry
) -> bool:
    """Return True if the dependent SLE depends on the given issue SLE.

    Matches if dependent.depends_on references issue.voucher_no,
    issue.item, or combinations thereof, or if both belong to the same
    manufacturing/transfer voucher where dependent is a receipt and
    issue is a consumption.
    """
    if dependent.qty_change <= Decimal("0") or issue.qty_change >= Decimal("0"):
        return False
    if dependent.depends_on is not None:
        keys = {k.strip() for k in dependent.depends_on.split(",")}
        candidates = {
            issue.voucher_no,
            issue.item,
            f"{issue.voucher_no}:{issue.item}",
            f"{issue.voucher_no}/{issue.item}",
        }
        return bool(keys & candidates)
    return dependent.voucher_no == issue.voucher_no


def get_consumed_issues_for_dependent(
    ledger: StockLedger, dep: StockLedgerEntry
) -> list[StockLedgerEntry]:
    """Find all issue SLEs consumed to produce the given dependent SLE."""
    return [
        entry
        for entry in ledger.all_entries()
        if entry.qty_change < Decimal("0") and is_dependent_on(dep, entry)
    ]


def _is_key_upstream(
    ledger: StockLedger,
    upstream_key: tuple[str, str],
    downstream_key: tuple[str, str],
) -> bool:
    """Return True if upstream_key is upstream of downstream_key."""
    visited: set[tuple[str, str]] = set()
    queue = [upstream_key]

    while queue:
        curr_item, curr_wh = queue.pop(0)
        if (curr_item, curr_wh) in visited:
            continue
        visited.add((curr_item, curr_wh))

        for dep in ledger.all_entries():
            dep_key = (dep.item, dep.warehouse)
            if dep_key == downstream_key:
                for iss in ledger.all_entries():
                    if (
                        iss.item == curr_item
                        and iss.warehouse == curr_wh
                        and is_dependent_on(dep, iss)
                    ):
                        return True
            elif dep_key not in visited:
                for iss in ledger.all_entries():
                    if (
                        iss.item == curr_item
                        and iss.warehouse == curr_wh
                        and is_dependent_on(dep, iss)
                    ):
                        queue.append(dep_key)
                        break
    return False


class _WorkQueue:
    """Min-heap work queue tracking (from_datetime, item, warehouse).

    Duplicates for the same (item, warehouse) key coalesce to the earliest
    datetime. Ensures upstream (raw material) keys are settled before
    downstream (finished good) keys.
    """

    def __init__(self) -> None:
        self._earliest: dict[tuple[str, str], datetime] = {}

    def push(self, dt: datetime, item: str, warehouse: str) -> None:
        """Enqueue or coalesce (item, warehouse) at dt."""
        key = (item, warehouse)
        if key in self._earliest:
            self._earliest[key] = min(self._earliest[key], dt)
        else:
            self._earliest[key] = dt

    def is_empty(self) -> bool:
        """Return True if no work items remain."""
        return len(self._earliest) == 0

    def pop_next(self, ledger: StockLedger) -> tuple[datetime, str, str]:
        """Pop the next key to process.

        Among pending keys with no upstream pending keys in the queue,
        selects the one with the globally earliest from_datetime.
        """
        if not self._earliest:
            msg = "pop from empty work queue"
            raise IndexError(msg)

        pending = list(self._earliest.keys())
        independent_keys: list[tuple[str, str]] = []
        for key in pending:
            has_upstream = False
            for other in pending:
                if other != key and _is_key_upstream(ledger, other, key):
                    has_upstream = True
                    break
            if not has_upstream:
                independent_keys.append(key)

        candidates = independent_keys if independent_keys else pending
        best_key = min(candidates, key=lambda k: self._earliest[k])
        dt = self._earliest.pop(best_key)
        return dt, best_key[0], best_key[1]

    def has_dependent_pending(
        self, ledger: StockLedger, item: str, warehouse: str
    ) -> bool:
        """Return True if any pending key is downstream of (item, warehouse)."""
        current_key = (item, warehouse)
        for pending_key in self._earliest:
            if pending_key != current_key and _is_key_upstream(
                ledger, current_key, pending_key
            ):
                return True
        return False


class RepostEngine:
    """FIFO stock ledger reposting engine.

    Recomputes valuation layers and financial balances after backdated
    entries, cancellations, or rate adjustments. Performs all calculations
    in-memory using Decimal arithmetic and atomic commit semantics.
    """

    def __init__(self, ledger: StockLedger) -> None:
        """Initialise with the target stock ledger."""
        self.ledger = ledger

    def repost(
        self,
        item: str,
        warehouse: str,
        from_datetime: datetime,
        *,
        early_stop: bool = True,
    ) -> RepostResult:
        """Replay stock ledger entries from from_datetime onwards.

        Reconstructs FIFO queues, recomputes issue costs, propagates
        rates to dependent manufacture receipts, and commits atomically
        only upon successful completion.

        Args:
            item: Starting item SKU.
            warehouse: Starting warehouse.
            from_datetime: Starting timestamp from which to replay.
            early_stop: When True, halts replay early if layer snapshots
                and balances remain identical and no dependents are pending.

        Returns:
            A RepostResult detailing changed entries and re-evaluated entries.

        Raises:
            InsufficientStockError: If any issue consumes more stock than
                available in the FIFO queue. Original ledger is unchanged.
        """
        original_entries: dict[
            tuple[str, str, str, datetime, int], StockLedgerEntry
        ] = {
            (
                e.voucher_no,
                e.item,
                e.warehouse,
                e.posting_datetime,
                e.creation_seq,
            ): e
            for e in self.ledger.all_entries()
        }

        min_dirty = self.ledger.get_min_dirty(item, warehouse)
        if min_dirty is not None and min_dirty < from_datetime:
            start_datetime = min_dirty
        else:
            start_datetime = from_datetime

        working_ledger = self.ledger.copy()
        work_queue = _WorkQueue()
        work_queue.push(start_datetime, item, warehouse)
        entries_replayed = 0
        reposted_keys: set[tuple[str, str]] = set()

        while not work_queue.is_empty():
            cur_dt, cur_item, cur_wh = work_queue.pop_next(working_ledger)
            reposted_keys.add((cur_item, cur_wh))

            prev_sle = working_ledger.last_before(cur_item, cur_wh, cur_dt)
            if prev_sle is not None:
                queue = FifoQueue.from_snapshot(
                    prev_sle.fifo_snapshot, allow_negative=False
                )
            else:
                queue = FifoQueue(allow_negative=False)

            entries_to_replay = working_ledger.entries_from(
                cur_item, cur_wh, cur_dt
            )

            for i, entry in enumerate(entries_to_replay):
                if entry.qty_change > Decimal("0"):
                    queue.add(entry.qty_change, entry.incoming_rate)
                    outgoing_value = Decimal("0")
                    value_change = entry.qty_change * entry.incoming_rate
                    bal_qty = queue.balance_qty
                    bal_val = queue.balance_value
                    snapshot = queue.snapshot()
                    new_entry = dataclasses.replace(
                        entry,
                        outgoing_value=outgoing_value,
                        value_change=value_change,
                        balance_qty=bal_qty,
                        balance_value=bal_val,
                        fifo_snapshot=snapshot,
                    )
                elif entry.qty_change < Decimal("0"):
                    consume_qty = -entry.qty_change
                    res = queue.consume(consume_qty)
                    outgoing_value = res.total_value
                    value_change = -outgoing_value
                    incoming_rate = Decimal("0")
                    bal_qty = queue.balance_qty
                    bal_val = queue.balance_value
                    snapshot = queue.snapshot()
                    new_entry = dataclasses.replace(
                        entry,
                        incoming_rate=incoming_rate,
                        outgoing_value=outgoing_value,
                        value_change=value_change,
                        balance_qty=bal_qty,
                        balance_value=bal_val,
                        fifo_snapshot=snapshot,
                    )
                else:
                    new_entry = dataclasses.replace(
                        entry,
                        balance_qty=queue.balance_qty,
                        balance_value=queue.balance_value,
                        fifo_snapshot=queue.snapshot(),
                    )

                working_ledger.replace_entry(entry, new_entry)
                entries_replayed += 1

                if entry.qty_change < Decimal("0"):
                    for dep in working_ledger.all_entries():
                        if is_dependent_on(dep, new_entry):
                            consumed_issues = get_consumed_issues_for_dependent(
                                working_ledger, dep
                            )
                            total_consumed = sum(
                                (iss.outgoing_value for iss in consumed_issues),
                                Decimal("0"),
                            )
                            if dep.qty_change > Decimal("0"):
                                new_rate = total_consumed / dep.qty_change
                                updated_dep = dataclasses.replace(
                                    dep, incoming_rate=new_rate
                                )
                                working_ledger.replace_entry(dep, updated_dep)
                                work_queue.push(
                                    dep.posting_datetime,
                                    dep.item,
                                    dep.warehouse,
                                )

                max_dirty = working_ledger.get_max_dirty(cur_item, cur_wh)
                past_max_dirty = (
                    max_dirty is None or entry.posting_datetime > max_dirty
                )

                if (
                    early_stop
                    and i >= 1
                    and past_max_dirty
                    and new_entry.fifo_snapshot == entry.fifo_snapshot
                    and new_entry.value_change == entry.value_change
                    and new_entry.balance_value == entry.balance_value
                    and not work_queue.has_dependent_pending(
                        working_ledger, cur_item, cur_wh
                    )
                ):
                    break

        self.ledger.replace_all(working_ledger.all_entries())
        for k_item, k_wh in reposted_keys:
            self.ledger.clear_dirty(k_item, k_wh)

        value_changed: list[ValueChangedItem] = []
        re_evaluated_only: list[StockLedgerEntry] = []

        for entry in self.ledger.all_entries():
            key = (
                entry.voucher_no,
                entry.item,
                entry.warehouse,
                entry.posting_datetime,
                entry.creation_seq,
            )
            orig = original_entries.get(key)
            if orig is None:
                value_changed.append(
                    ValueChangedItem(
                        voucher_no=entry.voucher_no,
                        old_value=Decimal("0"),
                        new_value=entry.value_change,
                        delta=entry.value_change,
                        voucher_type=entry.voucher_type,
                        item=entry.item,
                        warehouse=entry.warehouse,
                    )
                )
            elif entry.value_change != orig.value_change:
                value_changed.append(
                    ValueChangedItem(
                        voucher_no=entry.voucher_no,
                        old_value=orig.value_change,
                        new_value=entry.value_change,
                        delta=entry.value_change - orig.value_change,
                        voucher_type=entry.voucher_type,
                        item=entry.item,
                        warehouse=entry.warehouse,
                    )
                )
            elif (
                entry.balance_qty != orig.balance_qty
                or entry.balance_value != orig.balance_value
                or entry.fifo_snapshot != orig.fifo_snapshot
                or entry.incoming_rate != orig.incoming_rate
            ):
                re_evaluated_only.append(entry)

        return RepostResult(
            value_changed=tuple(value_changed),
            re_evaluated_only=tuple(re_evaluated_only),
            entries_replayed=entries_replayed,
        )
