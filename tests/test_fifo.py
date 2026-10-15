"""Tests for the FIFO queue — written before implementation per project rules."""

from __future__ import annotations

from decimal import Decimal

import pytest

from stock_repost.fifo import FifoQueue, InsufficientStockError

# -- Partial layer consumption -------------------------------------------


class TestPartialLayerConsumption:
    """Consuming less than a full layer leaves a remainder."""

    def test_partial_consume_value(self) -> None:
        """Consuming 4 of 10 @ 100 yields value 400."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        result = q.consume(Decimal("4"))
        assert result.total_value == Decimal("400")
        assert result.avg_rate == Decimal("100")
        assert result.layers_used == ((Decimal("4"), Decimal("100")),)

    def test_partial_consume_remainder(self) -> None:
        """After consuming 4 of 10, balance is 6 @ 100."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        q.consume(Decimal("4"))
        assert q.balance_qty == Decimal("6")
        assert q.balance_value == Decimal("600")


# -- Multi-layer consumption ---------------------------------------------


class TestMultiLayerConsumption:
    """Consuming across multiple FIFO layers."""

    def test_multi_layer_value_and_layers(self) -> None:
        """Consume 12 across [10@100, 5@200] => 10*100 + 2*200 = 1400."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        q.add(Decimal("5"), Decimal("200"))
        result = q.consume(Decimal("12"))
        assert result.total_value == Decimal("1400")
        assert result.layers_used == (
            (Decimal("10"), Decimal("100")),
            (Decimal("2"), Decimal("200")),
        )

    def test_multi_layer_remaining_balance(self) -> None:
        """After consuming 12 of [10@100, 5@200], 3@200 remain."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        q.add(Decimal("5"), Decimal("200"))
        q.consume(Decimal("12"))
        assert q.balance_qty == Decimal("3")
        assert q.balance_value == Decimal("600")


# -- Exact depletion -----------------------------------------------------


class TestExactDepletion:
    """Consuming exactly the full balance empties the queue."""

    def test_exact_depletion(self) -> None:
        """Consume all 10 @ 100 => value 1000, balance 0."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        result = q.consume(Decimal("10"))
        assert result.total_value == Decimal("1000")
        assert result.avg_rate == Decimal("100")
        assert q.balance_qty == Decimal("0")
        assert q.balance_value == Decimal("0")

    def test_exact_depletion_multi_layer(self) -> None:
        """Deplete two layers exactly."""
        q = FifoQueue()
        q.add(Decimal("3"), Decimal("50"))
        q.add(Decimal("7"), Decimal("80"))
        result = q.consume(Decimal("10"))
        assert result.total_value == Decimal("710")  # 3*50 + 7*80
        assert q.balance_qty == Decimal("0")
        assert q.balance_value == Decimal("0")


# -- Insufficient stock --------------------------------------------------


class TestInsufficientStock:
    """Consuming more than the balance raises by default."""

    def test_raises_insufficient_stock(self) -> None:
        """Default allow_negative=False raises InsufficientStockError."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        with pytest.raises(InsufficientStockError):
            q.consume(Decimal("15"))

    def test_queue_unchanged_after_error(self) -> None:
        """Failed consume must not mutate queue state."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        with pytest.raises(InsufficientStockError):
            q.consume(Decimal("15"))
        assert q.balance_qty == Decimal("10")
        assert q.balance_value == Decimal("1000")

    def test_raises_on_empty_queue(self) -> None:
        """Consuming from an empty queue raises."""
        q = FifoQueue()
        with pytest.raises(InsufficientStockError):
            q.consume(Decimal("1"))

    def test_allow_negative_does_not_raise(self) -> None:
        """allow_negative=True lets consumption exceed balance."""
        q = FifoQueue(allow_negative=True)
        q.add(Decimal("10"), Decimal("100"))
        result = q.consume(Decimal("15"))
        assert result.total_value == Decimal("1000")
        assert q.balance_qty == Decimal("-5")


# -- Snapshot round-trip -------------------------------------------------


class TestSnapshotRoundTrip:
    """Snapshot captures queue state and can be fully restored."""

    def test_round_trip_preserves_state(self) -> None:
        """snapshot -> from_snapshot produces identical queue state."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        q.add(Decimal("5"), Decimal("200"))
        q.consume(Decimal("3"))
        snap = q.snapshot()
        restored = FifoQueue.from_snapshot(snap)
        assert restored.balance_qty == q.balance_qty
        assert restored.balance_value == q.balance_value
        assert restored.snapshot() == snap

    def test_snapshot_content(self) -> None:
        """Snapshot is a tuple of (qty, rate) tuples."""
        q = FifoQueue()
        q.add(Decimal("10"), Decimal("100"))
        snap = q.snapshot()
        assert snap == ((Decimal("10"), Decimal("100")),)

    def test_empty_snapshot(self) -> None:
        """Empty queue produces empty snapshot."""
        q = FifoQueue()
        assert q.snapshot() == ()
        restored = FifoQueue.from_snapshot(())
        assert restored.balance_qty == Decimal("0")


# -- Decimal precision ---------------------------------------------------


class TestDecimalPrecision:
    """Verify Decimal arithmetic avoids float-style drift."""

    def test_no_float_drift(self) -> None:
        """0.1 * 0.2 = 0.02 exactly, unlike IEEE-754 floats."""
        q = FifoQueue()
        q.add(Decimal("0.1"), Decimal("0.2"))
        result = q.consume(Decimal("0.1"))
        assert result.total_value == Decimal("0.02")
        assert q.balance_qty == Decimal("0")

    def test_fractional_layer(self) -> None:
        """Consume 1 of 3 @ 33.33 => value 33.33, balance 66.66."""
        q = FifoQueue()
        q.add(Decimal("3"), Decimal("33.33"))
        result = q.consume(Decimal("1"))
        assert result.total_value == Decimal("33.33")
        assert q.balance_qty == Decimal("2")
        assert q.balance_value == Decimal("66.66")

    def test_consume_result_is_frozen(self) -> None:
        """ConsumeResult must be immutable."""
        q = FifoQueue()
        q.add(Decimal("5"), Decimal("10"))
        result = q.consume(Decimal("1"))
        with pytest.raises(AttributeError):
            result.total_value = Decimal("999")  # type: ignore[misc]


# -- Deficit handling and hardening --------------------------------------


class TestDeficitAndHardening:
    """Tests for negative stock deficit tracking and snapshot constraints."""

    def test_default_allow_negative_is_false(self) -> None:
        """FifoQueue() raises InsufficientStockError when consuming over balance."""
        q = FifoQueue()
        q.add(Decimal("5"), Decimal("10"))
        with pytest.raises(InsufficientStockError):
            q.consume(Decimal("10"))

    def test_deficit_is_offset_by_next_add(self) -> None:
        """Consuming 30 from 20 leaves deficit; adding 50 @ 10 offsets deficit."""
        q = FifoQueue(allow_negative=True)
        q.add(Decimal("20"), Decimal("5"))
        q.consume(Decimal("30"))
        assert q.balance_qty == Decimal("-10")

        q.add(Decimal("50"), Decimal("10"))
        assert q.balance_qty == Decimal("40")
        assert q.balance_value == Decimal("400")

    def test_snapshot_raises_when_deficit_exists(self) -> None:
        """snapshot() on a queue with a nonzero deficit raises ValueError."""
        q = FifoQueue(allow_negative=True)
        q.add(Decimal("20"), Decimal("5"))
        q.consume(Decimal("30"))
        msg = "Cannot take snapshot while queue has a negative stock deficit"
        with pytest.raises(ValueError, match=msg):
            q.snapshot()

