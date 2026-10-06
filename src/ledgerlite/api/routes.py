"""REST endpoints. See docs/openapi.yaml for the full contract."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request, url_for

from ..domain.errors import ValidationError
from ..services import LedgerService
from . import serializers as s

bp = Blueprint("ledger", __name__)


def _service() -> LedgerService:
    return current_app.extensions["ledger_service"]


def _json_body(*, required: bool) -> Any:
    if not request.get_data():
        if required:
            raise ValidationError("request body must be a JSON object")
        return None
    body = request.get_json(silent=True)
    if body is None:
        raise ValidationError("request body is not valid JSON (check the Content-Type header)")
    return body


def _int_arg(name: str, default: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValidationError(f"{name} must be an integer") from None


def _date_arg(name: str):
    return s.parse_optional_date(request.args.get(name), name)


@bp.get("/health")
def health():
    return jsonify({"status": "ok"})


# ---- accounts -------------------------------------------------------------
@bp.post("/accounts")
def create_account():
    code, name, account_type = s.parse_account_request(_json_body(required=True))
    account = _service().create_account(code, name, account_type)
    response = jsonify(s.account_to_json(account))
    response.status_code = 201
    response.headers["Location"] = url_for("ledger.get_account", account_id=account.id)
    return response


@bp.get("/accounts")
def list_accounts():
    return jsonify([s.account_to_json(a) for a in _service().list_accounts()])


@bp.get("/accounts/<account_id>")
def get_account(account_id: str):
    return jsonify(s.account_to_json(_service().get_account(account_id)))


@bp.post("/accounts/<account_id>/deactivate")
def deactivate_account(account_id: str):
    return jsonify(s.account_to_json(_service().deactivate_account(account_id)))


@bp.get("/accounts/<account_id>/balance")
def get_balance(account_id: str):
    return jsonify(s.balance_to_json(_service().get_balance(account_id, _date_arg("as_of"))))


@bp.get("/accounts/<account_id>/statement")
def get_statement(account_id: str):
    statement = _service().get_statement(account_id, _date_arg("date_from"), _date_arg("date_to"))
    return jsonify(s.statement_to_json(statement))


# ---- transactions ---------------------------------------------------------
@bp.post("/transactions")
def record_transaction():
    tx_date, description, postings = s.parse_transaction_request(_json_body(required=True))
    result = _service().record_transaction(
        tx_date, description, postings, idempotency_key=request.headers.get("Idempotency-Key")
    )
    response = jsonify(s.transaction_to_json(result.transaction))
    response.status_code = 201 if result.created else 200
    response.headers["Location"] = url_for("ledger.get_transaction", transaction_id=result.transaction.id)
    return response


@bp.get("/transactions")
def list_transactions():
    transactions = _service().list_transactions(
        account_id=request.args.get("account_id"),
        date_from=_date_arg("date_from"),
        date_to=_date_arg("date_to"),
        limit=_int_arg("limit", 50),
        offset=_int_arg("offset", 0),
    )
    return jsonify([s.transaction_to_json(t) for t in transactions])


@bp.get("/transactions/<transaction_id>")
def get_transaction(transaction_id: str):
    return jsonify(s.transaction_to_json(_service().get_transaction(transaction_id)))


@bp.post("/transactions/<transaction_id>/reverse")
def reverse_transaction(transaction_id: str):
    on, description = s.parse_reversal_request(_json_body(required=False))
    reversal = _service().reverse_transaction(transaction_id, on=on, description=description)
    response = jsonify(s.transaction_to_json(reversal))
    response.status_code = 201
    response.headers["Location"] = url_for("ledger.get_transaction", transaction_id=reversal.id)
    return response


# ---- reports --------------------------------------------------------------
@bp.get("/reports/trial-balance")
def trial_balance():
    return jsonify(s.trial_balance_to_json(_service().get_trial_balance(_date_arg("as_of"))))
