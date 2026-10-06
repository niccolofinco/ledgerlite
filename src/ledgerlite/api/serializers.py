"""Conversion between JSON payloads and domain objects."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..domain.errors import ValidationError
from ..domain.models import Account, Posting, Side, Transaction
from ..domain.money import format_amount, parse_amount
from ..services import (
    AccountBalance,
    PostingInput,
    Statement,
    TrialBalance,
)

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


# ---- parsing --------------------------------------------------------------
def parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise ValidationError(f"{field} must be a date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field} is not a valid calendar date") from None


def parse_optional_date(value: Any, field: str) -> date | None:
    return None if value is None else parse_date(value, field)


def require_object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    return data


def parse_account_request(data: Any) -> tuple[str, str, str]:
    data = require_object(data)
    for field in ("code", "name", "type"):
        if not isinstance(data.get(field), str):
            raise ValidationError(f"{field} is required and must be a string")
    return data["code"], data["name"], data["type"]


def parse_transaction_request(data: Any) -> tuple[date, str, list[PostingInput]]:
    data = require_object(data)
    tx_date = parse_date(data.get("date"), "date")
    description = data.get("description")
    if not isinstance(description, str):
        raise ValidationError("description is required and must be a string")
    raw_postings = data.get("postings")
    if not isinstance(raw_postings, list):
        raise ValidationError("postings is required and must be a list")
    postings = []
    for index, item in enumerate(raw_postings):
        if not isinstance(item, dict):
            raise ValidationError(f"postings[{index}] must be an object")
        account_id = item.get("account_id")
        if not isinstance(account_id, str):
            raise ValidationError(f"postings[{index}].account_id must be a string")
        try:
            side = Side(item.get("side"))
        except ValueError:
            raise ValidationError(f"postings[{index}].side must be 'debit' or 'credit'") from None
        postings.append(PostingInput(account_id, side, parse_amount(item.get("amount"))))
    return tx_date, description, postings


def parse_reversal_request(data: Any) -> tuple[date | None, str | None]:
    if data is None:
        return None, None
    data = require_object(data)
    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise ValidationError("description must be a string")
    return parse_optional_date(data.get("date"), "date"), description


# ---- rendering ------------------------------------------------------------
def account_to_json(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "code": account.code,
        "name": account.name,
        "type": account.type.value,
        "normal_side": account.normal_side.value,
        "active": account.active,
    }


def _posting_to_json(posting: Posting) -> dict[str, str]:
    return {
        "account_id": posting.account_id,
        "side": posting.side.value,
        "amount": format_amount(abs(posting.amount)),
    }


def transaction_to_json(transaction: Transaction) -> dict[str, Any]:
    return {
        "id": transaction.id,
        "date": transaction.date.isoformat(),
        "description": transaction.description,
        "postings": [_posting_to_json(p) for p in transaction.postings],
        "reverses": transaction.reverses,
        "created_at": transaction.created_at.isoformat(),
    }


def balance_to_json(result: AccountBalance) -> dict[str, Any]:
    return {
        "account_id": result.account.id,
        "code": result.account.code,
        "as_of": result.as_of.isoformat() if result.as_of else None,
        "normal_side": result.account.normal_side.value,
        "balance": format_amount(result.balance),
    }


def statement_to_json(statement: Statement) -> dict[str, Any]:
    return {
        "account_id": statement.account.id,
        "code": statement.account.code,
        "date_from": statement.date_from.isoformat() if statement.date_from else None,
        "date_to": statement.date_to.isoformat() if statement.date_to else None,
        "normal_side": statement.account.normal_side.value,
        "opening_balance": format_amount(statement.opening_balance),
        "lines": [
            {
                "date": line.date.isoformat(),
                "transaction_id": line.transaction_id,
                "description": line.description,
                "side": line.side.value,
                "amount": format_amount(line.amount),
                "running_balance": format_amount(line.running_balance),
            }
            for line in statement.lines
        ],
        "closing_balance": format_amount(statement.closing_balance),
    }


def trial_balance_to_json(report: TrialBalance) -> dict[str, Any]:
    return {
        "as_of": report.as_of.isoformat() if report.as_of else None,
        "rows": [
            {
                "account_id": row.account.id,
                "code": row.account.code,
                "name": row.account.name,
                "type": row.account.type.value,
                "debit": format_amount(row.debit),
                "credit": format_amount(row.credit),
            }
            for row in report.rows
        ],
        "total_debit": format_amount(report.total_debit),
        "total_credit": format_amount(report.total_credit),
        "balanced": report.balanced,
    }
