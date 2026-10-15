"""Deterministic regression test reproducing the Hypothesis equivalence failure."""

from datetime import datetime
from decimal import Decimal

from stock_repost.ledger import StockLedger
from stock_repost.models import StockLedgerEntry
from stock_repost.repost import RepostEngine


def full_rebuild(ledger: StockLedger) -> StockLedger:
    """Build a completely fresh ledger replaying every key from scratch.

    Args:
        ledger: The target stock ledger to reconstruct.

    Returns:
        A brand-new StockLedger replayed from inception without early stop.
    """
    clean_entries: list[StockLedgerEntry] = []
    for entry in ledger.all_entries():
        clean_entries.append(
            StockLedgerEntry(
                item=entry.item,
                warehouse=entry.warehouse,
                voucher_type=entry.voucher_type,
                voucher_no=entry.voucher_no,
                posting_datetime=entry.posting_datetime,
                creation_seq=entry.creation_seq,
                qty_change=entry.qty_change,
                incoming_rate=entry.incoming_rate,
                outgoing_value=Decimal("0"),
                value_change=Decimal("0"),
                balance_qty=Decimal("0"),
                balance_value=Decimal("0"),
                fifo_snapshot=(),
                depends_on=entry.depends_on,
            )
        )
    rebuilt = StockLedger(clean_entries)
    engine = RepostEngine(rebuilt)
    items = sorted({e.item for e in clean_entries})
    earliest_dt = min(e.posting_datetime for e in clean_entries)
    for it in items:
        engine.repost(it, "Stores", earliest_dt, early_stop=False)
    return rebuilt


def _build_failing_base_ledger() -> StockLedger:
    """Construct the exact base ledger from the falsifying Hypothesis scenario.

    Returns:
        A StockLedger populated with the 11 transactions before edits.
    """
    base_entries = [
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Purchase Receipt",
            voucher_no="OPEN-RawMaterial1",
            posting_datetime=datetime(2026, 1, 1, 10, 0),
            creation_seq=1,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("10"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("500"),
            balance_qty=Decimal("50"),
            balance_value=Decimal("500"),
            fifo_snapshot=((Decimal("50"), Decimal("10")),),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial2",
            warehouse="Stores",
            voucher_type="Purchase Receipt",
            voucher_no="OPEN-RawMaterial2",
            posting_datetime=datetime(2026, 1, 2, 10, 0),
            creation_seq=2,
            qty_change=Decimal("50"),
            incoming_rate=Decimal("10"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("500"),
            balance_qty=Decimal("50"),
            balance_value=Decimal("500"),
            fifo_snapshot=((Decimal("50"), Decimal("10")),),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-0",
            posting_datetime=datetime(2026, 1, 3, 10, 0),
            creation_seq=3,
            qty_change=Decimal("-1"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("10"),
            value_change=Decimal("-10"),
            balance_qty=Decimal("49"),
            balance_value=Decimal("490"),
            fifo_snapshot=((Decimal("49"), Decimal("10")),),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Purchase Receipt",
            voucher_no="PR-1",
            posting_datetime=datetime(2026, 1, 4, 10, 0),
            creation_seq=4,
            qty_change=Decimal("20"),
            incoming_rate=Decimal("10"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("200"),
            balance_qty=Decimal("69"),
            balance_value=Decimal("690"),
            fifo_snapshot=(
                (Decimal("49"), Decimal("10")),
                (Decimal("20"), Decimal("10")),
            ),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-2",
            posting_datetime=datetime(2026, 1, 5, 10, 0),
            creation_seq=5,
            qty_change=Decimal("-1"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("10"),
            value_change=Decimal("-10"),
            balance_qty=Decimal("68"),
            balance_value=Decimal("680"),
            fifo_snapshot=(
                (Decimal("48"), Decimal("10")),
                (Decimal("20"), Decimal("10")),
            ),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-3",
            posting_datetime=datetime(2026, 1, 6, 10, 0),
            creation_seq=6,
            qty_change=Decimal("-1"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("10"),
            value_change=Decimal("-10"),
            balance_qty=Decimal("67"),
            balance_value=Decimal("670"),
            fifo_snapshot=(
                (Decimal("47"), Decimal("10")),
                (Decimal("20"), Decimal("10")),
            ),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="FinishedGood",
            warehouse="Stores",
            voucher_type="Purchase Receipt",
            voucher_no="PR-4",
            posting_datetime=datetime(2026, 1, 7, 10, 0),
            creation_seq=7,
            qty_change=Decimal("20"),
            incoming_rate=Decimal("20"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("400"),
            balance_qty=Decimal("20"),
            balance_value=Decimal("400"),
            fifo_snapshot=((Decimal("20"), Decimal("20")),),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-5",
            posting_datetime=datetime(2026, 1, 8, 10, 0),
            creation_seq=8,
            qty_change=Decimal("-1"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("10"),
            value_change=Decimal("-10"),
            balance_qty=Decimal("66"),
            balance_value=Decimal("660"),
            fifo_snapshot=(
                (Decimal("46"), Decimal("10")),
                (Decimal("20"), Decimal("10")),
            ),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="FinishedGood",
            warehouse="Stores",
            voucher_type="Delivery Note",
            voucher_no="DN-6",
            posting_datetime=datetime(2026, 1, 9, 10, 0),
            creation_seq=9,
            qty_change=Decimal("-1"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("20"),
            value_change=Decimal("-20"),
            balance_qty=Decimal("19"),
            balance_value=Decimal("380"),
            fifo_snapshot=((Decimal("19"), Decimal("20")),),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="RawMaterial1",
            warehouse="Stores",
            voucher_type="Stock Entry",
            voucher_no="MFG-0",
            posting_datetime=datetime(2026, 1, 10, 10, 0),
            creation_seq=1,
            qty_change=Decimal("-5"),
            incoming_rate=Decimal("0"),
            outgoing_value=Decimal("50"),
            value_change=Decimal("-50"),
            balance_qty=Decimal("61"),
            balance_value=Decimal("610"),
            fifo_snapshot=(
                (Decimal("41"), Decimal("10")),
                (Decimal("20"), Decimal("10")),
            ),
            depends_on=None,
        ),
        StockLedgerEntry(
            item="FinishedGood",
            warehouse="Stores",
            voucher_type="Stock Entry",
            voucher_no="MFG-0",
            posting_datetime=datetime(2026, 1, 10, 10, 0),
            creation_seq=2,
            qty_change=Decimal("2"),
            incoming_rate=Decimal("25"),
            outgoing_value=Decimal("0"),
            value_change=Decimal("50"),
            balance_qty=Decimal("21"),
            balance_value=Decimal("430"),
            fifo_snapshot=(
                (Decimal("19"), Decimal("20")),
                (Decimal("2"), Decimal("25")),
            ),
            depends_on="MFG-0",
        ),
    ]
    return StockLedger(base_entries)


def test_regression_early_stop_dependent_interrupted_by_multi_edit() -> None:
    """Reproduce early_stop equivalence bug found by Hypothesis.

    A rate edit on RawMaterial1 triggers recalculation of dependent MFG-0.
    Simultaneously, a rate edit on FinishedGood (PR-4) causes FinishedGood to
    be enqueued earlier than MFG-0. FinishedGood reaches an early convergence
    point at DN-6 before encountering MFG-0 because max_dirty on FinishedGood
    was not updated when MFG-0 was mutated during RawMaterial1 replay.
    """
    base_ledger = _build_failing_base_ledger()
    print("\n--- BASE LEDGER ENTRIES ---")
    for entry in base_ledger.all_entries():
        print(f"  {entry!r}")

    warehouse = "Stores"
    ledger_early = base_ledger.copy()
    ledger_full = base_ledger.copy()

    dt1 = ledger_early.update_entry(
        "OPEN-RawMaterial1", "RawMaterial1", incoming_rate=Decimal("20")
    )
    dt2 = ledger_early.update_entry(
        "PR-4", "FinishedGood", incoming_rate=Decimal("20")
    )

    ledger_full.update_entry(
        "OPEN-RawMaterial1", "RawMaterial1", incoming_rate=Decimal("20")
    )
    ledger_full.update_entry(
        "PR-4", "FinishedGood", incoming_rate=Decimal("20")
    )

    from_dt = min(dt1, dt2)
    entry_item = "RawMaterial1"

    engine_early = RepostEngine(ledger_early)
    engine_early.repost(entry_item, warehouse, from_dt, early_stop=True)

    engine_full = RepostEngine(ledger_full)
    engine_full.repost(entry_item, warehouse, from_dt, early_stop=False)

    rebuilt = full_rebuild(ledger_early)

    # early_stop=False must match full_rebuild
    assert ledger_full.all_entries() == rebuilt.all_entries()

    # This assertion fails because early_stop=True skips MFG-0 on FinishedGood
    assert ledger_early.all_entries() == ledger_full.all_entries()
    assert ledger_early.all_entries() == rebuilt.all_entries()
