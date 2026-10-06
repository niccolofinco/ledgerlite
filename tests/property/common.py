"""Strategies, a reference model and helpers shared by the property-based tests.

The *model* below is deliberately naive (plain lists and sums). Properties
check that the real implementation always agrees with it.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import settings
from hypothesis import strategies as st
from ledgerlite.domain.models import Account, Side
from ledgerlite.repositories import InMemoryRepository
from ledgerlite.services import LedgerService, PostingInput

from tests.helpers import make_service, seed_chart

PROPERTY_SETTINGS = settings(max_examples=100, deadline=None)

BASE_DAY = date(2026, 1, 1)  # every generated date is BASE_DAY + [0, 200] days (< "today")
MAX_DAY_OFFSET = 200
ACCOUNT_COUNT = 6  # size of tests.helpers.CHART_OF_ACCOUNTS
MAX_LEG_AMOUNT = 100_000

Legs = list[tuple[int, int]]  # (account index, signed amount): debit > 0, credit < 0
Operation = tuple[int, Legs]  # (day offset, legs)


# ---- strategies -----------------------------------------------------------
@st.composite
def balanced_legs(draw) -> Legs:
    """1-3 debit legs and 1-3 credit legs whose totals are equal by construction."""
    debits = draw(st.lists(st.integers(1, MAX_LEG_AMOUNT), min_size=1, max_size=3))
    total = sum(debits)
    credit_count = draw(st.integers(1, min(3, total)))
    cuts: list[int] = []
    if credit_count > 1:
        cuts = draw(
            st.lists(
                st.integers(1, total - 1),
                min_size=credit_count - 1,
                max_size=credit_count - 1,
                unique=True,
            )
        )
    points = [0, *sorted(cuts), total]
    credits = [end - start for start, end in zip(points, points[1:])]
    legs: Legs = []
    for amount in debits:
        legs.append((draw(st.integers(0, ACCOUNT_COUNT - 1)), amount))
    for amount in credits:
        legs.append((draw(st.integers(0, ACCOUNT_COUNT - 1)), -amount))
    return legs


def operations(max_size: int = 12):
    return st.lists(st.tuples(st.integers(0, MAX_DAY_OFFSET), balanced_legs()), max_size=max_size)


# ---- helpers --------------------------------------------------------------
def build(repository=None) -> tuple[LedgerService, list[Account], object]:
    repository = repository if repository is not None else InMemoryRepository()
    service = make_service(repository)
    accounts = list(seed_chart(service).values())
    return service, accounts, repository


def to_postings(accounts: list[Account], legs: Legs) -> list[PostingInput]:
    return [
        PostingInput(accounts[index].id, Side.DEBIT if amount > 0 else Side.CREDIT, abs(amount))
        for index, amount in legs
    ]


def day(offset: int) -> date:
    return BASE_DAY + timedelta(days=offset)


def apply(service: LedgerService, accounts: list[Account], ops: list[Operation]) -> None:
    for offset, legs in ops:
        service.record_transaction(day(offset), "generated", to_postings(accounts, legs))


def model_signed_balance(ops: list[Operation], account_index: int, up_to: int | None = None) -> int:
    """Reference implementation of balance(a, d): a plain sum over all legs."""
    return sum(
        amount
        for offset, legs in ops
        if up_to is None or offset <= up_to
        for index, amount in legs
        if index == account_index
    )
