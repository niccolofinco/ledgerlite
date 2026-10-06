"""Flask application factory and error handling."""

from __future__ import annotations

import os

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from ..domain.errors import ConflictError, LedgerError, NotFoundError, ValidationError
from ..repositories.sqlite import SqliteRepository
from ..services import LedgerService
from .routes import bp

_STATUS = {ValidationError: 422, NotFoundError: 404, ConflictError: 409}


def create_app(database: str | None = None, *, service: LedgerService | None = None) -> Flask:
    """Build the WSGI app.

    ``database`` is a SQLite path (default: env ``LEDGERLITE_DB`` or an
    in-memory database). A ready-made ``service`` can be injected for tests.
    """
    app = Flask(__name__)
    app.json.sort_keys = False
    if service is None:
        path = database or os.environ.get("LEDGERLITE_DB", ":memory:")
        service = LedgerService(SqliteRepository(path))
    app.extensions["ledger_service"] = service
    app.register_blueprint(bp)

    @app.errorhandler(LedgerError)
    def handle_ledger_error(error: LedgerError):
        status = next((s for cls, s in _STATUS.items() if isinstance(error, cls)), 500)
        return jsonify({"error": {"code": error.code, "message": str(error)}}), status

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        code = (error.name or "error").lower().replace(" ", "_")
        return jsonify({"error": {"code": code, "message": error.description}}), error.code

    return app
