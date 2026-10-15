"""In-memory stock ledger storing StockLedgerEntry instances.

Entries are maintained in deterministic order sorted by
``(posting_datetime, creation_seq)``. This module performs no I/O.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

from stock_repost.models import StockLedgerEntry


class StockLedger:
    """In-memory store of stock ledger entries.

    Maintains entries in a deterministic timeline ordered by
    ``(posting_datetime, creation_seq)``. Provides query methods for
    repost engines to retrieve historical layer snapshots and entries
    requiring replay, as well as mutators for backdated inserts,
    cancellations, and rate adjustments.
    """

    def __init__(
        self, entries: Iterable[StockLedgerEntry] | None = None
    ) -> None:
        """Initialise the ledger with an optional collection of entries."""
        if entries is not None:
            self._entries: list[StockLedgerEntry] = sorted(
                entries,
                key=lambda e: (e.posting_datetime, e.creation_seq),
            )
        else:
            self._entries = []

    def __len__(self) -> int:
        """Return the number of entries in the ledger."""
        return len(self._entries)

    def __iter__(self) -> Iterator[StockLedgerEntry]:
        """Iterate over all entries in chronological order."""
        return iter(self._entries)

    def __getitem__(self, index: int) -> StockLedgerEntry:
        """Retrieve an entry by its index."""
        return self._entries[index]

    def all_entries(self) -> tuple[StockLedgerEntry, ...]:
        """Return an immutable snapshot of all entries in the ledger."""
        return tuple(self._entries)

    def last_before(
        self, item: str, warehouse: str, dt: datetime
    ) -> StockLedgerEntry | None:
        """Find the last entry strictly before the given datetime.

        Args:
            item: Item SKU code.
            warehouse: Target warehouse.
            dt: Strict upper bound for posting_datetime.

        Returns:
            The latest matching entry with ``posting_datetime < dt``, or
            ``None`` if no such entry exists.
        """
        for entry in reversed(self._entries):
            if (
                entry.item == item
                and entry.warehouse == warehouse
                and entry.posting_datetime < dt
            ):
                return entry
        return None

    def entries_from(
        self, item: str, warehouse: str, dt: datetime
    ) -> list[StockLedgerEntry]:
        """Return all entries at or after the given datetime in order.

        Args:
            item: Item SKU code.
            warehouse: Target warehouse.
            dt: Lower bound (inclusive) for posting_datetime.

        Returns:
            A list of matching entries with ``posting_datetime >= dt``,
            ordered chronologically.
        """
        return [
            entry
            for entry in self._entries
            if entry.item == item
            and entry.warehouse == warehouse
            and entry.posting_datetime >= dt
        ]

    def update_entry(
        self,
        voucher_no: str,
        item: str,
        warehouse: str | None = None,
        **changes: Any,
    ) -> datetime:
        """Apply field edits to an entry and return the repost start datetime.

        Args:
            voucher_no: Voucher identifier of the entry to modify.
            item: Item code of the entry.
            warehouse: Optional warehouse filter to disambiguate entries.
            **changes: Field overrides applied via :func:`dataclasses.replace`.

        Returns:
            The earliest datetime affected by the edit (minimum of the
            original and new ``posting_datetime``).

        Raises:
            KeyError: If no matching entry is found.
        """
        for i, entry in enumerate(self._entries):
            if entry.voucher_no == voucher_no and entry.item == item:
                if warehouse is not None and entry.warehouse != warehouse:
                    continue
                new_entry = dataclasses.replace(entry, **changes)
                self._entries[i] = new_entry
                self._entries.sort(
                    key=lambda e: (e.posting_datetime, e.creation_seq)
                )
                return min(entry.posting_datetime, new_entry.posting_datetime)

        msg = f"No entry found with voucher_no={voucher_no!r} and item={item!r}"
        raise KeyError(msg)

    def insert_entry(self, entry: StockLedgerEntry) -> datetime:
        """Insert a new entry and return its posting datetime.

        Maintains the deterministic timeline sort order.

        Args:
            entry: The new stock ledger entry to insert.

        Returns:
            The ``posting_datetime`` of the inserted entry.
        """
        self._entries.append(entry)
        self._entries.sort(key=lambda e: (e.posting_datetime, e.creation_seq))
        return entry.posting_datetime

    def remove_entry(
        self,
        voucher_no: str,
        item: str,
        warehouse: str | None = None,
    ) -> datetime:
        """Remove a matching entry and return its posting datetime.

        Used for voucher cancellation and backdated deletions.

        Args:
            voucher_no: Voucher identifier.
            item: Item code.
            warehouse: Optional warehouse filter.

        Returns:
            The ``posting_datetime`` of the removed entry.

        Raises:
            KeyError: If no matching entry is found.
        """
        for i, entry in enumerate(self._entries):
            if entry.voucher_no == voucher_no and entry.item == item:
                if warehouse is not None and entry.warehouse != warehouse:
                    continue
                removed = self._entries.pop(i)
                return removed.posting_datetime

        msg = f"No entry found with voucher_no={voucher_no!r} and item={item!r}"
        raise KeyError(msg)

    def replace_entry(
        self, old_entry: StockLedgerEntry, new_entry: StockLedgerEntry
    ) -> None:
        """Replace an existing entry in-place with its updated version.

        Args:
            old_entry: The exact entry currently stored in the ledger.
            new_entry: The newly recomputed entry replacing it.

        Raises:
            ValueError: If ``old_entry`` is not present in the ledger.
        """
        for i, entry in enumerate(self._entries):
            if entry == old_entry:
                self._entries[i] = new_entry
                return
        msg = f"Cannot find old_entry {old_entry.voucher_no} to replace"
        raise ValueError(msg)

    def replace_all(self, entries: Iterable[StockLedgerEntry]) -> None:
        """Replace all ledger entries with a new set of entries.

        Args:
            entries: The new entries to populate into the ledger.
        """
        self._entries = sorted(
            entries,
            key=lambda e: (e.posting_datetime, e.creation_seq),
        )

    def copy(self) -> StockLedger:
        """Return a copy of this ledger."""
        return StockLedger(self._entries)
