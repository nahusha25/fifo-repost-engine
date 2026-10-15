"""stock_repost — FIFO stock valuation and reposting engine.

Re-exports the core public API so callers can write::

    from stock_repost import FifoQueue, StockLedgerEntry
"""

from stock_repost.fifo import ConsumeResult, FifoQueue, InsufficientStockError
from stock_repost.gl import (
    AccountBalance,
    GLEntry,
    GLSummary,
    VoucherGLResult,
    generate_gl_entries,
)
from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.report import generate_repost_report
from stock_repost.repost import RepostEngine, RepostResult, ValueChangedItem
from stock_repost.scenario import build_task_scenario

__all__ = [
    "AccountBalance",
    "ConsumeResult",
    "FifoQueue",
    "GLEntry",
    "GLSummary",
    "InsufficientStockError",
    "RepostEngine",
    "RepostResult",
    "StockLedger",
    "StockLedgerEntry",
    "ValueChangedItem",
    "VoucherGLResult",
    "build_task_scenario",
    "generate_gl_entries",
    "generate_repost_report",
]

