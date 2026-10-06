"""Repository port: what the service layer needs from a storage backend.

Contract shared by every implementation (verified by the contract test-suite
in ``tests/integration/test_repository_contract.py``):

* ``add_account`` / ``add_transaction`` raise :class:`ConflictError` when a
  uniqueness or referential constraint is violated, and leave no partial data.
* ``list_accounts`` is ordered by account code.
* ``list_transactions`` and ``statement_entries`` are ordered by
  (date, insertion order).
* Dates are inclusive on both ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..domain.models import Account, Transaction


@dataclass(frozen=True)
class StatementEntry:
    """A single posting on one account, enriched with its transaction data."""

    date: date
    transaction_id: str
    description: str
    amount: int  # signed: debit - credit


class LedgerRepository(Protocol):
    def add_account(self, account: Account) -> None: ...

    def update_account(self, account: Account) -> None: ...

    def get_account(self, account_id: str) -> Account | None: ...

    def get_account_by_code(self, code: str) -> Account | None: ...

    def list_accounts(self) -> list[Account]: ...

    def add_transaction(self, transaction: Transaction) -> None: ...

    def get_transaction(self, transaction_id: str) -> Transaction | None: ...

    def get_transaction_by_idempotency_key(self, key: str) -> Transaction | None: ...

    def get_reversal_of(self, transaction_id: str) -> Transaction | None: ...

    def list_transactions(
        self,
        *,
        account_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]: ...

    def balance_of(self, account_id: str, up_to: date | None = None) -> int: ...

    def balances(self, up_to: date | None = None) -> dict[str, int]: ...

    def statement_entries(
        self,
        account_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[StatementEntry]: ...

    def close(self) -> None: ...
