"""stock_repost — FIFO stock valuation and reposting engine.

Re-exports the core public API so callers can write::

    from stock_repost import FifoQueue, StockLedgerEntry
"""

from stock_repost.fifo import ConsumeResult, FifoQueue, InsufficientStockError
from stock_repost.models import StockLedgerEntry

__all__ = [
    "ConsumeResult",
    "FifoQueue",
    "InsufficientStockError",
    "StockLedgerEntry",
]
