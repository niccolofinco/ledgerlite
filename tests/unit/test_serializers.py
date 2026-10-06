import unittest
from datetime import date

from ledgerlite.api import serializers as s
from ledgerlite.domain.errors import ValidationError
from ledgerlite.domain.models import Side


def valid_payload(**overrides):
    payload = {
        "date": "2026-10-01",
        "description": "Sale",
        "postings": [
            {"account_id": "a", "side": "debit", "amount": "10.00"},
            {"account_id": "b", "side": "credit", "amount": "10.00"},
        ],
    }
    payload.update(overrides)
    return payload


class ParseDateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(s.parse_date("2026-02-28", "d"), date(2026, 2, 28))

    def test_invalid(self):
        for value in ["2026-2-28", "20261001", "2026-02-30", "2026-W40-1", "", None, 20261001]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                s.parse_date(value, "d")

    def test_optional(self):
        self.assertIsNone(s.parse_optional_date(None, "d"))
        self.assertEqual(s.parse_optional_date("2026-01-01", "d"), date(2026, 1, 1))


class ParseRequestsTest(unittest.TestCase):
    def test_account_request(self):
        self.assertEqual(
            s.parse_account_request({"code": "CASH", "name": "Cash", "type": "asset"}),
            ("CASH", "Cash", "asset"),
        )

    def test_account_request_errors(self):
        for body in [None, [], "x", {}, {"code": "A", "name": "n"}, {"code": 1, "name": "n", "type": "asset"}]:
            with self.subTest(body=body), self.assertRaises(ValidationError):
                s.parse_account_request(body)

    def test_transaction_request(self):
        tx_date, description, postings = s.parse_transaction_request(valid_payload())
        self.assertEqual((tx_date, description), (date(2026, 10, 1), "Sale"))
        self.assertEqual([(p.account_id, p.side, p.amount) for p in postings],
                         [("a", Side.DEBIT, 1000), ("b", Side.CREDIT, 1000)])

    def test_transaction_request_errors(self):
        bad_payloads = [
            None,
            valid_payload(date="yesterday"),
            valid_payload(date=None),
            valid_payload(description=5),
            valid_payload(postings="nope"),
            valid_payload(postings=["x"]),
            valid_payload(postings=[{"account_id": 1, "side": "debit", "amount": "1.00"}]),
            valid_payload(postings=[{"account_id": "a", "side": "left", "amount": "1.00"}]),
            valid_payload(postings=[{"account_id": "a", "side": "debit", "amount": 1.0}]),
            valid_payload(postings=[{"account_id": "a", "side": "debit"}]),
        ]
        for body in bad_payloads:
            with self.subTest(body=body), self.assertRaises(ValidationError):
                s.parse_transaction_request(body)

    def test_reversal_request(self):
        self.assertEqual(s.parse_reversal_request(None), (None, None))
        self.assertEqual(s.parse_reversal_request({}), (None, None))
        self.assertEqual(
            s.parse_reversal_request({"date": "2026-10-01", "description": "oops"}),
            (date(2026, 10, 1), "oops"),
        )
        with self.assertRaises(ValidationError):
            s.parse_reversal_request({"description": 3})
        with self.assertRaises(ValidationError):
            s.parse_reversal_request([])
