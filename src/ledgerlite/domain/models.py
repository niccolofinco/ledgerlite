"""Domain entities: accounts, postings and transactions.

Sign convention used throughout the code base: a *signed* amount is
``debit - credit``, so debits are positive and credits are negative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum

from .errors import ValidationError
from .money import MAX_AMOUNT

ACCOUNT_CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,19}")
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 200


class Side(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def sign(self) -> int:
        return 1 if self is Side.DEBIT else -1


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def normal_side(self) -> Side:
        """Side on which the balance of this kind of account normally grows."""
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return Side.DEBIT
        return Side.CREDIT

    def natural_balance(self, signed_balance: int) -> int:
        """Express a signed (debit - credit) balance on the account's normal side."""
        return signed_balance * self.normal_side.sign


@dataclass(frozen=True)
class Account:
    id: str
    code: str
    name: str
    type: AccountType
    active: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValidationError("account id must be a non-empty string")
        if not isinstance(self.code, str) or ACCOUNT_CODE_RE.fullmatch(self.code) is None:
            raise ValidationError(
                "account code must match [A-Z0-9][A-Z0-9._-]{0,19} (uppercase letters, digits, . _ -)"
            )
        name = self.name.strip() if isinstance(self.name, str) else ""
        if not name or len(name) > MAX_NAME_LENGTH:
            raise ValidationError(f"account name must have 1-{MAX_NAME_LENGTH} characters")
        object.__setattr__(self, "name", name)
        if not isinstance(self.type, AccountType):
            raise ValidationError("invalid account type")

    @property
    def normal_side(self) -> Side:
        return self.type.normal_side

    def deactivated(self) -> Account:
        return replace(self, active=False)


@dataclass(frozen=True)
class Posting:
    """One leg of a transaction: a non-zero signed amount on an account."""

    account_id: str
    amount: int

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id:
            raise ValidationError("posting account_id must be a non-empty string")
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise ValidationError("posting amount must be an integer number of minor units")
        if self.amount == 0:
            raise ValidationError("posting amount must not be zero")
        if abs(self.amount) > MAX_AMOUNT:
            raise ValidationError("posting amount exceeds the maximum allowed value")

    @classmethod
    def from_side(cls, account_id: str, side: Side, amount: int) -> Posting:
        """Build a posting from a debit/credit side and a *positive* amount."""
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValidationError("amount must be strictly positive")
        return cls(account_id, side.sign * amount)

    @property
    def side(self) -> Side:
        return Side.DEBIT if self.amount > 0 else Side.CREDIT

    def negated(self) -> Posting:
        return Posting(self.account_id, -self.amount)


@dataclass(frozen=True)
class Transaction:
    """An immutable, balanced set of postings (sum of signed amounts == 0)."""

    id: str
    date: date
    description: str
    postings: tuple[Posting, ...]
    created_at: datetime
    reverses: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValidationError("transaction id must be a non-empty string")
        if not isinstance(self.date, date) or isinstance(self.date, datetime):
            raise ValidationError("transaction date must be a calendar date")
        description = self.description.strip() if isinstance(self.description, str) else ""
        if not description or len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"transaction description must have 1-{MAX_DESCRIPTION_LENGTH} characters"
            )
        object.__setattr__(self, "description", description)
        postings = tuple(self.postings)
        object.__setattr__(self, "postings", postings)
        if len(postings) < 2:
            raise ValidationError("a transaction needs at least two postings")
        total = sum(p.amount for p in postings)
        if total != 0:
            raise ValidationError(f"transaction is not balanced: debits - credits = {total}")

    def fingerprint(self) -> tuple:
        """Content identity used to detect idempotent replays (order-insensitive)."""
        legs = tuple(sorted((p.account_id, p.amount) for p in self.postings))
        return (self.date, self.description, legs)

    def reversed_postings(self) -> tuple[Posting, ...]:
        return tuple(p.negated() for p in self.postings)
