"""Entry point and configuration through the environment (what the container uses)."""

import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock

from ledgerlite.api import create_app


class WsgiEntryPointTest(unittest.TestCase):
    def test_module_exposes_a_working_wsgi_app(self):
        with mock.patch.dict(os.environ, {"LEDGERLITE_DB": ":memory:"}):
            sys.modules.pop("ledgerlite.wsgi", None)
            module = importlib.import_module("ledgerlite.wsgi")
        self.assertEqual(module.app.test_client().get("/health").status_code, 200)

    def test_database_path_is_read_from_the_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "from-env.db")
            with mock.patch.dict(os.environ, {"LEDGERLITE_DB": path}):
                client = create_app().test_client()
                response = client.post("/accounts", json={"code": "CASH", "name": "Cash", "type": "asset"})
            self.assertEqual(response.status_code, 201)
            self.assertTrue(os.path.exists(path))

    def test_explicit_argument_wins_over_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = os.path.join(directory, "explicit.db")
            with mock.patch.dict(os.environ, {"LEDGERLITE_DB": os.path.join(directory, "env.db")}):
                create_app(explicit)
            self.assertTrue(os.path.exists(explicit))
            self.assertFalse(os.path.exists(os.path.join(directory, "env.db")))
