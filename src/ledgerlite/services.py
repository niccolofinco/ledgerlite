"""Application service: orchestrates domain rules and the repository.

Formal pre/post-conditions of each operation are documented in
``docs/specification.md`` (rule identifiers such as T4 or R2 are referenced
in the comments below).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .domain.errors import ConflictError, NotFoundError, ValidationError
from .domain.models import (
    MAX_DESCRIPTION_LENGTH,
    Account,
    AccountType,
    Posting,
    Side,
    Transaction,
)
from .repositories.base import LedgerRepository

MAX_PAGE_SIZE = 200
MAX_IDEMPOTENCY_KEY_LENGTH = 128


# ---- value objects returned by the service -------------------------------
@dataclass(frozen=True)
class PostingInput:
    """A posting as requested by a client: side + strictly positive amount."""

    account_id: str
    side: Side
    amount: int


@dataclass(frozen=True)
class RecordResult:
    transaction: Transaction
    created: bool  # False when an idempotent replay returned the original


@dataclass(frozen=True)
class AccountBalance:
    account: Account
    as_of: date | None
    balance: int  # expressed on the account's normal side


@dataclass(frozen=True)
class StatementLine:
    date: date
    transaction_id: str
    description: str
    side: Side
    amount: int  # strictly positive
    running_balance: int  # on the account's normal side


@dataclass(frozen=True)
class Statement:
    account: Account
    date_from: date | None
    date_to: date | None
    opening_balance: int
    lines: tuple[StatementLine, ...]
    closing_balance: int


@dataclass(frozen=True)
class TrialBalanceRow:
    account: Account
    debit: int
    credit: int


@dataclass(frozen=True)
class TrialBalance:
    as_of: date | None
    rows: tuple[TrialBalanceRow, ...]
    total_debit: int
    total_credit: int

    @property
    def balanced(self) -> bool:
        return self.total_debit == self.total_credit


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LedgerService:
    def __init__(
        self,
        repository: LedgerRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._new_id = id_factory

    def today(self) -> date:
        return self._clock().date()

    # ---- accounts -------------------------------------------------------
    def create_account(self, code: str, name: str, account_type: AccountType | str) -> Account:
        """A1, A2: unique code, valid type."""
        if not isinstance(account_type, AccountType):
            try:
                account_type = AccountType(account_type)
            except ValueError:
                allowed = ", ".join(t.value for t in AccountType)
                raise ValidationError(f"account type must be one of: {allowed}") from None
        account = Account(id=self._new_id(), code=code, name=name, type=account_type)
        self._repo.add_account(account)
        return account

    def get_account(self, account_id: str) -> Account:
        account = self._repo.get_account(account_id)
        if account is None:
            raise NotFoundError(f"account {account_id!r} not found")
        return account

    def list_accounts(self) -> list[Account]:
        return self._repo.list_accounts()

    def deactivate_account(self, account_id: str) -> Account:
        """A3: an account can be deactivated only when its balance is zero."""
        account = self.get_account(account_id)
        if not account.active:
            return account  # idempotent
        if self._repo.balance_of(account.id) != 0:
            raise ConflictError("cannot deactivate an account with a non-zero balance")
        closed = account.deactivated()
        self._repo.update_account(closed)
        return closed

    # ---- transactions ---------------------------------------------------
    def record_transaction(
        self,
        tx_date: date,
        description: str,
        postings: Sequence[PostingInput],
        idempotency_key: str | None = None,
    ) -> RecordResult:
        """T1-T7: record a balanced transaction, optionally idempotently."""
        if idempotency_key is not None:
            self._validate_idempotency_key(idempotency_key)
        candidate = Transaction(  # validates T1, T2, T3
            id=self._new_id(),
            date=tx_date,
            description=description,
            postings=tuple(Posting.from_side(p.account_id, p.side, p.amount) for p in postings),
            created_at=self._clock(),
            idempotency_key=idempotency_key,
        )
        if idempotency_key is not None:
            existing = self._repo.get_transaction_by_idempotency_key(idempotency_key)
            if existing is not None:  # T7
                if existing.fingerprint() != candidate.fingerprint():
                    raise ConflictError("idempotency key already used with a different payload")
                return RecordResult(existing, created=False)
        self._ensure_not_future(candidate.date)  # T5
        self._ensure_accounts_usable(candidate.postings)  # T4
        self._repo.add_transaction(candidate)
        return RecordResult(candidate, created=True)

    def reverse_transaction(
        self,
        transaction_id: str,
        *,
        on: date | None = None,
        description: str | None = None,
    ) -> Transaction:
        """R1-R4: append a transaction that cancels an earlier one."""
        original = self.get_transaction(transaction_id)
        if original.reverses is not None:  # R1
            raise ConflictError("a reversal transaction cannot be reversed")
        if self._repo.get_reversal_of(original.id) is not None:  # R2
            raise ConflictError("transaction has already been reversed")
        reversal_date = on if on is not None else self.today()
        if reversal_date < original.date:  # R3
            raise ValidationError("reversal date cannot precede the original transaction date")
        self._ensure_not_future(reversal_date)
        reversal = Transaction(
            id=self._new_id(),
            date=reversal_date,
            description=description or f"Reversal: {original.description}"[:MAX_DESCRIPTION_LENGTH],
            postings=original.reversed_postings(),
            created_at=self._clock(),
            reverses=original.id,
        )
        self._ensure_accounts_usable(reversal.postings)  # R4
        self._repo.add_transaction(reversal)
        return reversal

    def get_transaction(self, transaction_id: str) -> Transaction:
        transaction = self._repo.get_transaction(transaction_id)
        if transaction is None:
            raise NotFoundError(f"transaction {transaction_id!r} not found")
        return transaction

    def list_transactions(
        self,
        *,
        account_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValidationError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise ValidationError("offset must not be negative")
        self._ensure_range(date_from, date_to)
        if account_id is not None:
            self.get_account(account_id)
        return self._repo.list_transactions(
            account_id=account_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
        )

    # ---- reports --------------------------------------------------------
    def get_balance(self, account_id: str, as_of: date | None = None) -> AccountBalance:
        """B1: balance(a, d) = sum of the postings on `a` dated <= d."""
        account = self.get_account(account_id)
        signed = self._repo.balance_of(account.id, as_of)
        return AccountBalance(account, as_of, account.type.natural_balance(signed))

    def get_statement(
        self,
        account_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Statement:
        """S1: closing = opening + sum(lines); closing == balance(a, date_to)."""
        account = self.get_account(account_id)
        self._ensure_range(date_from, date_to)
        opening_signed = 0
        if date_from is not None and date_from > date.min:
            opening_signed = self._repo.balance_of(account.id, date_from - timedelta(days=1))
        running = opening_signed
        lines: list[StatementLine] = []
        for entry in self._repo.statement_entries(account.id, date_from, date_to):
            running += entry.amount
            lines.append(
                StatementLine(
                    date=entry.date,
                    transaction_id=entry.transaction_id,
                    description=entry.description,
                    side=Side.DEBIT if entry.amount > 0 else Side.CREDIT,
                    amount=abs(entry.amount),
                    running_balance=account.type.natural_balance(running),
                )
            )
        return Statement(
            account=account,
            date_from=date_from,
            date_to=date_to,
            opening_balance=account.type.natural_balance(opening_signed),
            lines=tuple(lines),
            closing_balance=account.type.natural_balance(running),
        )

    def get_trial_balance(self, as_of: date | None = None) -> TrialBalance:
        """B2: total debits == total credits (a consequence of T3)."""
        balances = self._repo.balances(as_of)
        rows = []
        for account in self._repo.list_accounts():
            net = balances.get(account.id, 0)
            rows.append(TrialBalanceRow(account, debit=max(net, 0), credit=max(-net, 0)))
        return TrialBalance(
            as_of=as_of,
            rows=tuple(rows),
            total_debit=sum(r.debit for r in rows),
            total_credit=sum(r.credit for r in rows),
        )

    # ---- internal checks ------------------------------------------------
    def _ensure_not_future(self, day: date) -> None:
        if day > self.today():
            raise ValidationError("transaction date cannot be in the future")

    def _ensure_accounts_usable(self, postings: Sequence[Posting]) -> None:
        for account_id in dict.fromkeys(p.account_id for p in postings):
            account = self._repo.get_account(account_id)
            if account is None:
                raise NotFoundError(f"account {account_id!r} not found")
            if not account.active:
                raise ConflictError(f"account {account.code!r} is inactive")

    @staticmethod
    def _ensure_range(date_from: date | None, date_to: date | None) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValidationError("date_from must not be after date_to")

    @staticmethod
    def _validate_idempotency_key(key: str) -> None:
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH
            or not key.isprintable()
        ):
            raise ValidationError(
                f"idempotency key must be 1-{MAX_IDEMPOTENCY_KEY_LENGTH} printable characters"
            )
