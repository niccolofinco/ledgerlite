"""A realistic scenario run through the real stack, across an application restart."""

import os
import tempfile
import unittest

from ledgerlite.api import create_app


class SmallBusinessScenarioTest(unittest.TestCase):
    def test_month_of_bookkeeping_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ledger.db")
            client = create_app(path).test_client()

            ids = {}
            for code, name, kind in [("BANK", "Bank", "asset"), ("CAPITAL", "Capital", "equity"),
                                     ("SALES", "Sales", "income"), ("RENT", "Rent", "expense")]:
                ids[code] = client.post("/accounts", json={"code": code, "name": name, "type": kind}).get_json()["id"]

            def book(day, debit, credit, amount, description, key=None):
                body = {"date": day, "description": description, "postings": [
                    {"account_id": ids[debit], "side": "debit", "amount": amount},
                    {"account_id": ids[credit], "side": "credit", "amount": amount}]}
                headers = {"Idempotency-Key": key} if key else {}
                return client.post("/transactions", json=body, headers=headers)

            book("2026-09-01", "BANK", "CAPITAL", "10000.00", "Initial capital")
            book("2026-09-05", "BANK", "SALES", "2500.00", "Invoice 1", key="inv-1")
            book("2026-09-05", "BANK", "SALES", "2500.00", "Invoice 1", key="inv-1")  # client retry
            wrong = book("2026-09-30", "RENT", "BANK", "9000.00", "Rent (typo!)").get_json()
            client.post(f"/transactions/{wrong['id']}/reverse", json={"date": "2026-09-30"})
            book("2026-09-30", "RENT", "BANK", "900.00", "Rent")

            restarted = create_app(path).test_client()
            report = restarted.get("/reports/trial-balance?as_of=2026-09-30").get_json()
            rows = {r["code"]: r for r in report["rows"]}

            self.assertTrue(report["balanced"])
            self.assertEqual(rows["BANK"]["debit"], "11600.00")  # 10000 + 2500 - 900
            self.assertEqual(rows["SALES"]["credit"], "2500.00")  # retry did not double count
            self.assertEqual(rows["RENT"]["debit"], "900.00")  # typo fully reversed
            self.assertEqual(rows["CAPITAL"]["credit"], "10000.00")
            self.assertEqual(len(restarted.get("/transactions").get_json()), 5)
