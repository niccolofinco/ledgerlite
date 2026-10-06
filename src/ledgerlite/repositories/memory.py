"""In-memory repository: used by unit tests and as a reference implementation."""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import replace
from datetime import date

from ..domain.errors import ConflictError, NotFoundError
from ..domain.models import Account, Transaction
from .base import StatementEntry


class InMemoryRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: dict[str, Account] = {}
        self._transactions: list[Transaction] = []  # insertion order
        self._by_id: dict[str, Transaction] = {}
        self._by_key: dict[str, Transaction] = {}
        self._reversal_of: dict[str, Transaction] = {}

    # ---- accounts -------------------------------------------------------
    def add_account(self, account: Account) -> None:
        with self._lock:
            if account.id in self._accounts or any(
                a.code == account.code for a in self._accounts.values()
            ):
                raise ConflictError(f"account {account.code!r} already exists")
            self._accounts[account.id] = account

    def update_account(self, account: Account) -> None:
        with self._lock:
            if account.id not in self._accounts:
                raise NotFoundError(f"account {account.id!r} not found")
            self._accounts[account.id] = replace(account)

    def get_account(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def get_account_by_code(self, code: str) -> Account | None:
        with self._lock:
            return next((a for a in self._accounts.values() if a.code == code), None)

    def list_accounts(self) -> list[Account]:
        with self._lock:
            return sorted(self._accounts.values(), key=lambda a: a.code)

    # ---- transactions ---------------------------------------------------
    def add_transaction(self, transaction: Transaction) -> None:
        with self._lock:
            if transaction.id in self._by_id:
                raise ConflictError(f"transaction {transaction.id!r} already exists")
            key = transaction.idempotency_key
            if key is not None and key in self._by_key:
                raise ConflictError(f"idempotency key {key!r} already used")
            original = transaction.reverses
            if original is not None:
                if original not in self._by_id:
                    raise ConflictError(f"reversed transaction {original!r} does not exist")
                if original in self._reversal_of:
                    raise ConflictError(f"transaction {original!r} is already reversed")
            for posting in transaction.postings:
                if posting.account_id not in self._accounts:
                    raise ConflictError(f"unknown account {posting.account_id!r}")
            self._transactions.append(transaction)
            self._by_id[transaction.id] = transaction
            if key is not None:
                self._by_key[key] = transaction
            if original is not None:
                self._reversal_of[original] = transaction

    def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._by_id.get(transaction_id)

    def get_transaction_by_idempotency_key(self, key: str) -> Transaction | None:
        return self._by_key.get(key)

    def get_reversal_of(self, transaction_id: str) -> Transaction | None:
        return self._reversal_of.get(transaction_id)

    def list_transactions(
        self,
        *,
        account_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]:
        with self._lock:
            selected = [
                t
                for t in self._transactions
                if (date_from is None or t.date >= date_from)
                and (date_to is None or t.date <= date_to)
                and (account_id is None or any(p.account_id == account_id for p in t.postings))
            ]
        selected.sort(key=lambda t: t.date)  # stable: keeps insertion order within a day
        return selected[offset : offset + limit]

    # ---- aggregates -----------------------------------------------------
    def balance_of(self, account_id: str, up_to: date | None = None) -> int:
        with self._lock:
            return sum(
                p.amount
                for t in self._transactions
                if up_to is None or t.date <= up_to
                for p in t.postings
                if p.account_id == account_id
            )

    def balances(self, up_to: date | None = None) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        with self._lock:
            for t in self._transactions:
                if up_to is None or t.date <= up_to:
                    for p in t.postings:
                        totals[p.account_id] += p.amount
        return dict(totals)

    def statement_entries(
        self,
        account_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[StatementEntry]:
        with self._lock:
            ordered = sorted(self._transactions, key=lambda t: t.date)
        return [
            StatementEntry(t.date, t.id, t.description, p.amount)
            for t in ordered
            if (date_from is None or t.date >= date_from) and (date_to is None or t.date <= date_to)
            for p in t.postings
            if p.account_id == account_id
        ]

    def close(self) -> None:
        """Nothing to release."""
