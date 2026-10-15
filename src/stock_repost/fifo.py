"""FIFO (first-in, first-out) queue for stock valuation layers.

The queue stores ``(qty, rate)`` layers and consumes from the oldest
layer first.  All arithmetic uses :class:`~decimal.Decimal` to avoid
floating-point drift.  The queue performs **no I/O**; it is a pure
in-memory data structure.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal


class InsufficientStockError(Exception):
    """Raised when a consume request exceeds the available balance.

    Attributes:
        requested: The quantity the caller asked to consume.
        available: The quantity actually in the queue.
    """

    def __init__(self, requested: Decimal, available: Decimal) -> None:
        """Initialise with the requested and available quantities."""
        self.requested = requested
        self.available = available
        super().__init__(
            f"Cannot consume {requested}; only {available} available"
        )


@dataclass
class _Layer:
    """Internal mutable representation of one FIFO layer.

    Using a dataclass rather than a plain list gives named access and
    type safety while remaining mutable so partial consumption can
    decrement ``qty`` in place.
    """

    qty: Decimal
    rate: Decimal


@dataclass(frozen=True)
class ConsumeResult:
    """Immutable result of a FIFO consume operation.

    Attributes:
        total_value: Total monetary value consumed across all layers.
        avg_rate: Weighted-average rate (``total_value / qty``).
        layers_used: Tuple of ``(qty, rate)`` pairs, one per layer
            touched during the consumption.
    """

    total_value: Decimal
    avg_rate: Decimal
    layers_used: tuple[tuple[Decimal, Decimal], ...]


class FifoQueue:
    """FIFO queue that tracks stock valuation layers.

    Layers are added with :meth:`add` and consumed oldest-first with
    :meth:`consume`.  The queue is a pure data structure and performs
    no I/O.

    Args:
        allow_negative: When *False* (default), :meth:`consume` raises
            :class:`InsufficientStockError` if the requested quantity
            exceeds the balance.  When *True*, the queue permits
            negative stock and tracks the deficit internally.
    """

    __slots__ = ("_allow_negative", "_layers", "_negative_qty")

    def __init__(self, *, allow_negative: bool = False) -> None:
        """Create an empty FIFO queue."""
        self._layers: deque[_Layer] = deque()
        self._allow_negative = allow_negative
        self._negative_qty = Decimal("0")

    # -- mutators --------------------------------------------------------

    def add(self, qty: Decimal, rate: Decimal) -> None:
        """Append a new layer to the back of the queue.

        If the queue carries a negative balance (from a prior
        ``allow_negative`` consumption), the incoming quantity first
        offsets the deficit before a new layer is created.

        Raises:
            ValueError: If *qty* is not positive.
        """
        if qty <= Decimal("0"):
            msg = "qty must be positive"
            raise ValueError(msg)
        if self._negative_qty > Decimal("0"):
            offset = min(qty, self._negative_qty)
            self._negative_qty -= offset
            qty -= offset
        if qty > Decimal("0"):
            self._layers.append(_Layer(qty=qty, rate=rate))

    def consume(self, qty: Decimal) -> ConsumeResult:
        """Remove *qty* units from the oldest layers.

        Returns a :class:`ConsumeResult` describing the value and
        layers consumed.  When ``allow_negative`` is *False* and *qty*
        exceeds the balance, :class:`InsufficientStockError` is raised
        **without** mutating the queue.

        Raises:
            ValueError: If *qty* is not positive.
            InsufficientStockError: If insufficient stock and
                ``allow_negative`` is *False*.
        """
        if qty <= Decimal("0"):
            msg = "qty must be positive"
            raise ValueError(msg)

        available = self.balance_qty
        if qty > available and not self._allow_negative:
            raise InsufficientStockError(requested=qty, available=available)

        remaining = qty
        total_value = Decimal("0")
        layers_used: list[tuple[Decimal, Decimal]] = []

        while remaining > Decimal("0") and self._layers:
            layer = self._layers[0]
            if layer.qty <= remaining:
                total_value += layer.qty * layer.rate
                layers_used.append((layer.qty, layer.rate))
                remaining -= layer.qty
                self._layers.popleft()
            else:
                total_value += remaining * layer.rate
                layers_used.append((remaining, layer.rate))
                layer.qty -= remaining
                remaining = Decimal("0")

        if remaining > Decimal("0"):
            self._negative_qty += remaining

        avg_rate = total_value / qty
        return ConsumeResult(
            total_value=total_value,
            avg_rate=avg_rate,
            layers_used=tuple(layers_used),
        )

    # -- queries ---------------------------------------------------------

    @property
    def balance_qty(self) -> Decimal:
        """Total quantity across all layers, less any negative-stock deficit."""
        layer_total = sum(
            (layer.qty for layer in self._layers), Decimal("0")
        )
        return layer_total - self._negative_qty

    @property
    def balance_value(self) -> Decimal:
        """Total monetary value across all layers (qty * rate per layer)."""
        return sum(
            (layer.qty * layer.rate for layer in self._layers), Decimal("0")
        )

    def snapshot(self) -> tuple[tuple[Decimal, Decimal], ...]:
        """Return the current layers as an immutable tuple of ``(qty, rate)``.

        The result can be stored (e.g. on a
        :class:`~stock_repost.models.StockLedgerEntry`) and later
        restored via :meth:`from_snapshot`.
        """
        return tuple((layer.qty, layer.rate) for layer in self._layers)

    @classmethod
    def from_snapshot(
        cls,
        snap: tuple[tuple[Decimal, Decimal], ...],
        *,
        allow_negative: bool = False,
    ) -> FifoQueue:
        """Reconstruct a :class:`FifoQueue` from a snapshot.

        Args:
            snap: A tuple of ``(qty, rate)`` pairs as returned by
                :meth:`snapshot`.
            allow_negative: Forwarded to the constructor.
        """
        queue = cls(allow_negative=allow_negative)
        for layer_qty, layer_rate in snap:
            queue._layers.append(_Layer(qty=layer_qty, rate=layer_rate))
        return queue
