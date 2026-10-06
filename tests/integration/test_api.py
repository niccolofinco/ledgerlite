"""HTTP-level tests: Flask test client + real SQLite repository."""

import unittest
from unittest import mock

from ledgerlite.api import create_app
from ledgerlite.domain.errors import LedgerError
from ledgerlite.repositories import SqliteRepository
from ledgerlite.services import LedgerService

from tests.helpers import NOW, SequentialIds


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        repo = SqliteRepository(":memory:")
        self.addCleanup(repo.close)
        service = LedgerService(repo, clock=lambda: NOW, id_factory=SequentialIds())
        self.client = create_app(service=service).test_client()
        self.cash = self.create_account("CASH", "Cash", "asset")
        self.sales = self.create_account("SALES", "Sales", "income")

    def create_account(self, code, name, kind):
        response = self.client.post("/accounts", json={"code": code, "name": name, "type": kind})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def tx_payload(self, amount="100.00", day="2026-10-01", **overrides):
        payload = {
            "date": day,
            "description": "Sale",
            "postings": [
                {"account_id": self.cash["id"], "side": "debit", "amount": amount},
                {"account_id": self.sales["id"], "side": "credit", "amount": amount},
            ],
        }
        payload.update(overrides)
        return payload

    def post_tx(self, payload=None, **headers):
        return self.client.post("/transactions", json=payload or self.tx_payload(), headers=headers)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(response.get_json()["error"]["code"], code)


class HealthAndErrorsTest(ApiTestCase):
    def test_health(self):
        self.assertEqual(self.client.get("/health").get_json(), {"status": "ok"})

    def test_unknown_route_returns_json_404(self):
        self.assert_error(self.client.get("/nope"), 404, "not_found")

    def test_wrong_method_returns_json_405(self):
        self.assert_error(self.client.delete("/accounts"), 405, "method_not_allowed")

    def test_unmapped_domain_error_becomes_a_500(self):
        service = self.client.application.extensions["ledger_service"]
        with mock.patch.object(service, "list_accounts", side_effect=LedgerError("boom")):
            self.assert_error(self.client.get("/accounts"), 500, "ledger_error")

    def test_empty_and_non_json_bodies_are_rejected(self):
        self.assert_error(self.client.post("/transactions"), 422, "validation_error")
        response = self.client.post("/accounts", data="not json", content_type="text/plain")
        self.assert_error(response, 422, "validation_error")
        response = self.client.post("/accounts", data="{broken", content_type="application/json")
        self.assert_error(response, 422, "validation_error")


class AccountsApiTest(ApiTestCase):
    def test_create_returns_201_location_and_normal_side(self):
        response = self.client.post("/accounts", json={"code": "RENT", "name": "Rent", "type": "expense"})
        body = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.headers["Location"].endswith(f"/accounts/{body['id']}"))
        self.assertEqual((body["normal_side"], body["active"]), ("debit", True))

    def test_get_and_list(self):
        self.assertEqual(self.client.get(f"/accounts/{self.cash['id']}").get_json(), self.cash)
        self.assertEqual([a["code"] for a in self.client.get("/accounts").get_json()], ["CASH", "SALES"])

    def test_errors(self):
        self.assert_error(self.client.post("/accounts", json={"code": "CASH", "name": "x", "type": "asset"}), 409, "conflict")
        self.assert_error(self.client.post("/accounts", json={"code": "bad code", "name": "x", "type": "asset"}), 422, "validation_error")
        self.assert_error(self.client.post("/accounts", json={"code": "X", "name": "x", "type": "wrong"}), 422, "validation_error")
        self.assert_error(self.client.get("/accounts/ghost"), 404, "not_found")

    def test_deactivate(self):
        empty = self.create_account("OLD", "Old", "asset")
        response = self.client.post(f"/accounts/{empty['id']}/deactivate")
        self.assertEqual((response.status_code, response.get_json()["active"]), (200, False))

    def test_cannot_deactivate_account_with_balance(self):
        self.post_tx()
        self.assert_error(self.client.post(f"/accounts/{self.cash['id']}/deactivate"), 409, "conflict")

    def test_cannot_post_to_deactivated_account(self):
        empty = self.create_account("OLD", "Old", "asset")
        self.client.post(f"/accounts/{empty['id']}/deactivate")
        payload = self.tx_payload()
        payload["postings"][0]["account_id"] = empty["id"]
        self.assert_error(self.post_tx(payload), 409, "conflict")


class TransactionsApiTest(ApiTestCase):
    def test_create_and_fetch(self):
        response = self.post_tx()
        body = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(body["postings"][0], {"account_id": self.cash["id"], "side": "debit", "amount": "100.00"})
        self.assertTrue(response.headers["Location"].endswith(f"/transactions/{body['id']}"))
        self.assertEqual(self.client.get(f"/transactions/{body['id']}").get_json(), body)

    def test_validation_errors(self):
        unbalanced = self.tx_payload()
        unbalanced["postings"][1]["amount"] = "99.99"
        cases = {
            "unbalanced": unbalanced,
            "single posting": self.tx_payload(postings=self.tx_payload()["postings"][:1]),
            "future date": self.tx_payload(day="2026-10-06"),
            "bad amount": self.tx_payload(amount="1.999"),
            "zero amount": self.tx_payload(amount="0.00"),
            "negative amount": self.tx_payload(amount="-5.00"),
            "bad date": self.tx_payload(day="05/10/2026"),
        }
        for name, payload in cases.items():
            with self.subTest(name):
                self.assert_error(self.post_tx(payload), 422, "validation_error")
        self.assertEqual(self.client.get("/transactions").get_json(), [])

    def test_unknown_account_is_404(self):
        payload = self.tx_payload()
        payload["postings"][0]["account_id"] = "ghost"
        self.assert_error(self.post_tx(payload), 404, "not_found")

    def test_unknown_transaction_is_404(self):
        self.assert_error(self.client.get("/transactions/ghost"), 404, "not_found")

    def test_idempotency_key(self):
        first = self.post_tx(**{"Idempotency-Key": "abc"})
        replay = self.post_tx(**{"Idempotency-Key": "abc"})
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        self.assertEqual(first.get_json()["id"], replay.get_json()["id"])
        self.assertEqual(len(self.client.get("/transactions").get_json()), 1)

    def test_idempotency_key_reuse_with_other_payload_is_409(self):
        self.post_tx(**{"Idempotency-Key": "abc"})
        self.assert_error(self.post_tx(self.tx_payload(amount="5.00"), **{"Idempotency-Key": "abc"}), 409, "conflict")

    def test_listing_filters_and_pagination(self):
        for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
            self.post_tx(self.tx_payload(day=day))
        listing = lambda query: self.client.get(f"/transactions?{query}").get_json()  # noqa: E731
        self.assertEqual([t["date"] for t in listing("")], ["2026-09-01", "2026-09-02", "2026-09-03"])
        self.assertEqual(len(listing("limit=2")), 2)
        self.assertEqual(listing("limit=2&offset=2")[0]["date"], "2026-09-03")
        self.assertEqual(len(listing("date_from=2026-09-02&date_to=2026-09-02")), 1)
        self.assertEqual(len(listing(f"account_id={self.cash['id']}")), 3)

    def test_listing_errors(self):
        for query, status in [("limit=abc", 422), ("limit=0", 422), ("offset=-1", 422),
                              ("date_from=oops", 422), ("account_id=ghost", 404)]:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/transactions?{query}").status_code, status)


class ReversalApiTest(ApiTestCase):
    def test_reverse_with_empty_body(self):
        original = self.post_tx().get_json()
        response = self.client.post(f"/transactions/{original['id']}/reverse")
        body = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(body["reverses"], original["id"])
        self.assertEqual([p["side"] for p in body["postings"]], ["credit", "debit"])
        balance = self.client.get(f"/accounts/{self.cash['id']}/balance").get_json()
        self.assertEqual(balance["balance"], "0.00")

    def test_reverse_with_body(self):
        original = self.post_tx().get_json()
        response = self.client.post(
            f"/transactions/{original['id']}/reverse", json={"date": "2026-10-02", "description": "Mistake"}
        )
        self.assertEqual((response.get_json()["date"], response.get_json()["description"]), ("2026-10-02", "Mistake"))

    def test_errors(self):
        original = self.post_tx().get_json()
        self.client.post(f"/transactions/{original['id']}/reverse")
        self.assert_error(self.client.post(f"/transactions/{original['id']}/reverse"), 409, "conflict")
        self.assert_error(self.client.post("/transactions/ghost/reverse"), 404, "not_found")
        other = self.post_tx(self.tx_payload(amount="7.00")).get_json()
        self.assert_error(
            self.client.post(f"/transactions/{other['id']}/reverse", json={"date": "2026-09-01"}), 422, "validation_error"
        )


class ReportsApiTest(ApiTestCase):
    def test_balance_statement_and_trial_balance(self):
        self.post_tx(self.tx_payload(amount="100.00", day="2026-09-01"))
        self.post_tx(self.tx_payload(amount="50.50", day="2026-09-10"))

        balance = self.client.get(f"/accounts/{self.cash['id']}/balance").get_json()
        self.assertEqual((balance["balance"], balance["normal_side"], balance["as_of"]), ("150.50", "debit", None))
        earlier = self.client.get(f"/accounts/{self.cash['id']}/balance?as_of=2026-09-05").get_json()
        self.assertEqual((earlier["balance"], earlier["as_of"]), ("100.00", "2026-09-05"))

        statement = self.client.get(f"/accounts/{self.sales['id']}/statement?date_from=2026-09-05").get_json()
        self.assertEqual(statement["opening_balance"], "100.00")
        self.assertEqual(statement["closing_balance"], "150.50")
        self.assertEqual([(line["side"], line["amount"], line["running_balance"]) for line in statement["lines"]],
                         [("credit", "50.50", "150.50")])

        report = self.client.get("/reports/trial-balance").get_json()
        self.assertTrue(report["balanced"])
        self.assertEqual((report["total_debit"], report["total_credit"]), ("150.50", "150.50"))
        self.assertEqual([r["code"] for r in report["rows"]], ["CASH", "SALES"])
        as_of = self.client.get("/reports/trial-balance?as_of=2026-09-05").get_json()
        self.assertEqual(as_of["total_debit"], "100.00")

    def test_report_errors(self):
        self.assert_error(self.client.get("/accounts/ghost/balance"), 404, "not_found")
        self.assert_error(self.client.get(f"/accounts/{self.cash['id']}/balance?as_of=x"), 422, "validation_error")
        self.assert_error(self.client.get("/accounts/ghost/statement"), 404, "not_found")
        self.assert_error(
            self.client.get(f"/accounts/{self.cash['id']}/statement?date_from=2026-02-01&date_to=2026-01-01"),
            422,
            "validation_error",
        )
