"""WSGI entry point: ``gunicorn ledgerlite.wsgi:app``."""

from .api import create_app

app = create_app()
