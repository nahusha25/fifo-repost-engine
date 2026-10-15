"""General Ledger (GL) delta generation module.

Derives balanced double-entry accounting adjustments from a RepostResult.
Translates inventory revaluation differences into accounting vouchers for:
- Stock receipts (Dr Stock In Hand, Cr Stock Received But Not Billed)
- Stock issues to customers (Dr Cost of Goods Sold, Cr Stock In Hand)
- Manufacturing / Stock Entry transfers (Dr / Cr Stock In Hand per item line)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from stock_repost.repost import RepostResult, ValueChangedItem

STOCK_IN_HAND = "Stock In Hand"
STOCK_RECEIVED_BUT_NOT_BILLED = "Stock Received But Not Billed"
COST_OF_GOODS_SOLD = "Cost of Goods Sold"


@dataclass(frozen=True)
class GLEntry:
    """An individual debit or credit line in the general ledger.

    Attributes:
        account: Account title.
        debit: Debit amount (Decimal >= 0).
        credit: Credit amount (Decimal >= 0).
        voucher_type: Source document voucher type.
        voucher_no: Source document identifier.
        item: Item code associated with the movement.
        remarks: Human-readable explanation.
    """

    account: str
    debit: Decimal
    credit: Decimal
    voucher_type: str
    voucher_no: str
    item: str = ""
    remarks: str = ""


@dataclass(frozen=True)
class VoucherGLResult:
    """Collection of GL entries generated for a single voucher.

    Attributes:
        voucher_type: Type of source voucher.
        voucher_no: Identifier of source voucher.
        entries: All individual debit/credit lines.
    """

    voucher_type: str
    voucher_no: str
    entries: tuple[GLEntry, ...]

    @property
    def total_debit(self) -> Decimal:
        """Return the sum of all debit entries for this voucher."""
        return sum((e.debit for e in self.entries), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        """Return the sum of all credit entries for this voucher."""
        return sum((e.credit for e in self.entries), Decimal("0"))

    @property
    def is_balanced(self) -> bool:
        """Return True if total debits equal total credits."""
        return self.total_debit == self.total_credit


@dataclass(frozen=True)
class AccountBalance:
    """Debit and credit totals for a single GL account.

    Attributes:
        account: Account title.
        debit: Cumulative debit amount.
        credit: Cumulative credit amount.
    """

    account: str
    debit: Decimal
    credit: Decimal

    @property
    def net(self) -> Decimal:
        """Return the net balance (debit - credit). Positive indicates debit balance."""
        return self.debit - self.credit


@dataclass(frozen=True)
class GLSummary:
    """Overall accounting summary of all GL postings from a repost operation.

    Attributes:
        voucher_results: GL outcomes grouped by voucher.
        entries: Flat tuple of all generated GL entries.
        account_balances: Per-account debit/credit totals.
    """

    voucher_results: tuple[VoucherGLResult, ...]
    entries: tuple[GLEntry, ...]
    account_balances: tuple[AccountBalance, ...]

    @property
    def total_debit(self) -> Decimal:
        """Return the grand total of all debits across all vouchers."""
        return sum((e.debit for e in self.entries), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        """Return the grand total of all credits across all vouchers."""
        return sum((e.credit for e in self.entries), Decimal("0"))

    @property
    def is_balanced(self) -> bool:
        """Return True if total debits equal total credits across all entries."""
        return (
            self.total_debit == self.total_credit
            and all(v.is_balanced for v in self.voucher_results)
        )

    def net_by_account(self) -> dict[str, Decimal]:
        """Return a mapping of account name to net balance (debit - credit)."""
        return {b.account: b.net for b in self.account_balances}


def generate_gl_entries(result: RepostResult) -> GLSummary:
    """Derive balanced GL entries from a RepostResult.

    Processes every voucher in ``result.value_changed``. Unchanged vouchers
    produce no GL entries. Vouchers with zero net financial difference are omitted.

    Rules applied:
    - Stock receipts (e.g. Purchase Receipt):
        - Increase (+delta): Dr Stock In Hand, Cr Stock Received But Not Billed
        - Decrease (-delta): Dr Stock Received But Not Billed, Cr Stock In Hand
    - Stock issues to customers (e.g. Delivery Note):
        - Cost increase (-delta in SLE): Dr Cost of Goods Sold, Cr Stock In Hand
        - Cost decrease (+delta in SLE): Dr Stock In Hand, Cr Cost of Goods Sold
    - Manufacturing transfers (e.g. Stock Entry):
        - Raw material consumption (-delta in SLE): Cr Stock In Hand
        - Finished good production (+delta in SLE): Dr Stock In Hand

    Args:
        result: Outcome of a stock ledger repost operation.

    Returns:
        A :class:`GLSummary` containing balanced voucher entries and net balances.
    """
    vouchers: dict[tuple[str, str], list[ValueChangedItem]] = defaultdict(list)
    for vc in result.value_changed:
        if vc.delta != Decimal("0"):
            vouchers[(vc.voucher_type, vc.voucher_no)].append(vc)

    voucher_results: list[VoucherGLResult] = []
    all_entries: list[GLEntry] = []

    for (v_type, v_no), items in vouchers.items():
        v_entries: list[GLEntry] = []

        if v_type == "Purchase Receipt":
            for item in items:
                delta = item.delta
                if delta > Decimal("0"):
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=delta,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"Stock valuation adjustment for {item.item}",
                        )
                    )
                    v_entries.append(
                        GLEntry(
                            account=STOCK_RECEIVED_BUT_NOT_BILLED,
                            debit=Decimal("0"),
                            credit=delta,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=(
                                f"Stock received clearing adjustment for {item.item}"
                            ),
                        )
                    )
                elif delta < Decimal("0"):
                    pos_delta = -delta
                    v_entries.append(
                        GLEntry(
                            account=STOCK_RECEIVED_BUT_NOT_BILLED,
                            debit=pos_delta,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=(
                                f"Stock received clearing reduction for {item.item}"
                            ),
                        )
                    )
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=Decimal("0"),
                            credit=pos_delta,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"Stock valuation reduction for {item.item}",
                        )
                    )

        elif v_type == "Delivery Note":
            for item in items:
                # SLE issue stores negative value_change:
                # delta = new_value - old_value.
                # If cost increases: new_value is more negative, delta < 0.
                if item.delta < Decimal("0"):
                    cost_increase = -item.delta
                    v_entries.append(
                        GLEntry(
                            account=COST_OF_GOODS_SOLD,
                            debit=cost_increase,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"COGS adjustment for {item.item}",
                        )
                    )
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=Decimal("0"),
                            credit=cost_increase,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"Stock relief adjustment for {item.item}",
                        )
                    )
                elif item.delta > Decimal("0"):
                    cost_decrease = item.delta
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=cost_decrease,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"Stock reinstatement for {item.item}",
                        )
                    )
                    v_entries.append(
                        GLEntry(
                            account=COST_OF_GOODS_SOLD,
                            debit=Decimal("0"),
                            credit=cost_decrease,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"COGS reduction for {item.item}",
                        )
                    )

        elif v_type == "Stock Entry":
            for item in items:
                # Raw material consumed: negative value_change -> delta < 0
                if item.delta < Decimal("0"):
                    rm_cost = -item.delta
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=Decimal("0"),
                            credit=rm_cost,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=f"Raw material consumed adjustment for {item.item}",
                        )
                    )
                elif item.delta > Decimal("0"):
                    fg_val = item.delta
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=fg_val,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                            remarks=(
                                f"Finished goods produced adjustment for {item.item}"
                            ),
                        )
                    )
        else:
            # Generic fallback
            for item in items:
                if item.delta > Decimal("0"):
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=item.delta,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                        )
                    )
                    v_entries.append(
                        GLEntry(
                            account=STOCK_RECEIVED_BUT_NOT_BILLED,
                            debit=Decimal("0"),
                            credit=item.delta,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                        )
                    )
                elif item.delta < Decimal("0"):
                    pos = -item.delta
                    v_entries.append(
                        GLEntry(
                            account=COST_OF_GOODS_SOLD,
                            debit=pos,
                            credit=Decimal("0"),
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                        )
                    )
                    v_entries.append(
                        GLEntry(
                            account=STOCK_IN_HAND,
                            debit=Decimal("0"),
                            credit=pos,
                            voucher_type=v_type,
                            voucher_no=v_no,
                            item=item.item,
                        )
                    )

        if v_entries:
            voucher_results.append(
                VoucherGLResult(
                    voucher_type=v_type,
                    voucher_no=v_no,
                    entries=tuple(v_entries),
                )
            )
            all_entries.extend(v_entries)

    # Compute per-account balances
    account_debits: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    account_credits: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for e in all_entries:
        account_debits[e.account] += e.debit
        account_credits[e.account] += e.credit

    all_accounts = sorted(set(account_debits.keys()) | set(account_credits.keys()))
    balances = tuple(
        AccountBalance(
            account=acct,
            debit=account_debits[acct],
            credit=account_credits[acct],
        )
        for acct in all_accounts
    )

    return GLSummary(
        voucher_results=tuple(voucher_results),
        entries=tuple(all_entries),
        account_balances=balances,
    )
