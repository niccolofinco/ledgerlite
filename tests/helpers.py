"""Shared helpers for deterministic tests."""

from __future__ import annotations

import itertools
from datetime import date, datetime, timezone

from ledgerlite.domain.models import Account, AccountType, Side
from ledgerlite.repositories.memory import InMemoryRepository
from ledgerlite.services import LedgerService, PostingInput

TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

CHART_OF_ACCOUNTS = [
    ("CASH", "Cash", AccountType.ASSET),
    ("BANK", "Bank account", AccountType.ASSET),
    ("LOAN", "Bank loan", AccountType.LIABILITY),
    ("CAPITAL", "Owner's capital", AccountType.EQUITY),
    ("SALES", "Sales", AccountType.INCOME),
    ("RENT", "Rent expense", AccountType.EXPENSE),
]


class SequentialIds:
    """Deterministic id factory: id-1, id-2, ..."""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = itertools.count(1)

    def __call__(self) -> str:
        return f"{self._prefix}-{next(self._counter)}"


def make_service(repository=None) -> LedgerService:
    return LedgerService(
        repository if repository is not None else InMemoryRepository(),
        clock=lambda: NOW,
        id_factory=SequentialIds(),
    )


def seed_chart(service: LedgerService) -> dict[str, Account]:
    return {code: service.create_account(code, name, kind) for code, name, kind in CHART_OF_ACCOUNTS}


def debit(account: Account, amount: int) -> PostingInput:
    return PostingInput(account.id, Side.DEBIT, amount)


def credit(account: Account, amount: int) -> PostingInput:
    return PostingInput(account.id, Side.CREDIT, amount)


def record_simple(service, accounts, debit_code, credit_code, amount, day=TODAY, **kwargs):
    """Record a two-leg transaction between two accounts identified by code."""
    return service.record_transaction(
        day,
        kwargs.pop("description", f"{debit_code} / {credit_code}"),
        [debit(accounts[debit_code], amount), credit(accounts[credit_code], amount)],
        **kwargs,
    )
