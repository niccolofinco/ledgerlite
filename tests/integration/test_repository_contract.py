"""One behavioural contract, verified against every repository implementation."""

import unittest
from datetime import date

from ledgerlite.domain.errors import ConflictError, NotFoundError
from ledgerlite.domain.models import Account, AccountType, Posting, Transaction
from ledgerlite.repositories import InMemoryRepository, SqliteRepository, StatementEntry

from tests.helpers import NOW


def account(code, kind=AccountType.ASSET, active=True):
    return Account(f"id-{code}", code, code.title(), kind, active)


def transaction(tx_id, day, postings, **kwargs):
    return Transaction(
        id=tx_id,
        date=day,
        description=kwargs.pop("description", f"tx {tx_id}"),
        postings=tuple(Posting(a, n) for a, n in postings),
        created_at=NOW,
        **kwargs,
    )


class RepositoryContract:
    """Mixin: subclasses provide ``make_repository``."""

    def make_repository(self):
        raise NotImplementedError

    def setUp(self):
        self.repo = self.make_repository()
        self.addCleanup(self.repo.close)
        for acc in (account("CASH"), account("SALES", AccountType.INCOME), account("RENT", AccountType.EXPENSE)):
            self.repo.add_account(acc)

    # ---- accounts
    def test_account_roundtrip(self):
        stored = self.repo.get_account("id-CASH")
        self.assertEqual(stored, account("CASH"))
        self.assertEqual(self.repo.get_account_by_code("CASH"), stored)

    def test_missing_account_returns_none(self):
        self.assertIsNone(self.repo.get_account("nope"))
        self.assertIsNone(self.repo.get_account_by_code("NOPE"))

    def test_duplicate_account_code_or_id_conflicts(self):
        with self.assertRaises(ConflictError):
            self.repo.add_account(Account("other-id", "CASH", "Dup", AccountType.ASSET))
        with self.assertRaises(ConflictError):
            self.repo.add_account(Account("id-CASH", "OTHER", "Dup", AccountType.ASSET))

    def test_accounts_are_listed_by_code(self):
        self.assertEqual([a.code for a in self.repo.list_accounts()], ["CASH", "RENT", "SALES"])

    def test_update_account(self):
        self.repo.update_account(account("CASH", active=False))
        self.assertFalse(self.repo.get_account("id-CASH").active)

    def test_update_missing_account(self):
        with self.assertRaises(NotFoundError):
            self.repo.update_account(account("GHOST"))

    # ---- transactions
    def test_transaction_roundtrip_preserves_posting_order(self):
        tx = transaction("t1", date(2026, 9, 1), [("id-CASH", 300), ("id-RENT", 200), ("id-SALES", -500)],
                         idempotency_key="key-1")
        self.repo.add_transaction(tx)
        self.assertEqual(self.repo.get_transaction("t1"), tx)
        self.assertEqual(self.repo.get_transaction_by_idempotency_key("key-1"), tx)

    def test_missing_transaction_returns_none(self):
        self.assertIsNone(self.repo.get_transaction("nope"))
        self.assertIsNone(self.repo.get_transaction_by_idempotency_key("nope"))
        self.assertIsNone(self.repo.get_reversal_of("nope"))

    def test_duplicate_id_and_idempotency_key_conflict(self):
        self.repo.add_transaction(
            transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("id-SALES", -5)], idempotency_key="k")
        )
        with self.assertRaises(ConflictError):
            self.repo.add_transaction(transaction("t1", date(2026, 9, 2), [("id-CASH", 5), ("id-SALES", -5)]))
        with self.assertRaises(ConflictError):
            self.repo.add_transaction(
                transaction("t2", date(2026, 9, 2), [("id-CASH", 5), ("id-SALES", -5)], idempotency_key="k")
            )
        self.assertEqual(len(self.repo.list_transactions()), 1)

    def test_unknown_account_conflicts_and_leaves_no_trace(self):
        bad = transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("ghost", -5)])
        with self.assertRaises(ConflictError):
            self.repo.add_transaction(bad)
        self.assertIsNone(self.repo.get_transaction("t1"))
        self.assertEqual(self.repo.balance_of("id-CASH"), 0)

    def test_reversal_links(self):
        self.repo.add_transaction(transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("id-SALES", -5)]))
        reversal = transaction("t2", date(2026, 9, 2), [("id-CASH", -5), ("id-SALES", 5)], reverses="t1")
        self.repo.add_transaction(reversal)
        self.assertEqual(self.repo.get_reversal_of("t1"), reversal)

    def test_a_transaction_can_be_reversed_only_once(self):
        self.repo.add_transaction(transaction("t1", date(2026, 9, 1), [("id-CASH", 5), ("id-SALES", -5)]))
        self.repo.add_transaction(
            transaction("t2", date(2026, 9, 2), [("id-CASH", -5), ("id-SALES", 5)], reverses="t1")
        )
        with self.assertRaises(ConflictError):
            self.repo.add_transaction(
                transaction("t3", date(2026, 9, 3), [("id-CASH", -5), ("id-SALES", 5)], reverses="t1")
            )

    def test_reversing_a_missing_transaction_conflicts(self):
        with self.assertRaises(ConflictError):
            self.repo.add_transaction(
                transaction("t2", date(2026, 9, 2), [("id-CASH", -5), ("id-SALES", 5)], reverses="ghost")
            )

    # ---- queries
    def fill(self):
        self.repo.add_transaction(transaction("t1", date(2026, 9, 3), [("id-CASH", 100), ("id-SALES", -100)]))
        self.repo.add_transaction(transaction("t2", date(2026, 9, 1), [("id-CASH", 200), ("id-SALES", -200)]))
        self.repo.add_transaction(transaction("t3", date(2026, 9, 3), [("id-RENT", 50), ("id-CASH", -50)]))
        self.repo.add_transaction(transaction("t4", date(2026, 9, 2), [("id-RENT", 10), ("id-SALES", -10)]))

    def test_list_order_is_date_then_insertion(self):
        self.fill()
        self.assertEqual([t.id for t in self.repo.list_transactions()], ["t2", "t4", "t1", "t3"])

    def test_list_filters_and_paging(self):
        self.fill()
        ids = lambda **kw: [t.id for t in self.repo.list_transactions(**kw)]  # noqa: E731
        self.assertEqual(ids(account_id="id-RENT"), ["t4", "t3"])
        self.assertEqual(ids(date_from=date(2026, 9, 2)), ["t4", "t1", "t3"])
        self.assertEqual(ids(date_to=date(2026, 9, 2)), ["t2", "t4"])
        self.assertEqual(ids(date_from=date(2026, 9, 3), date_to=date(2026, 9, 3)), ["t1", "t3"])
        self.assertEqual(ids(account_id="id-CASH", limit=2), ["t2", "t1"])
        self.assertEqual(ids(limit=2, offset=3), ["t3"])
        self.assertEqual(ids(account_id="id-RENT", date_from=date(2026, 9, 3)), ["t3"])

    def test_balances(self):
        self.fill()
        self.assertEqual(self.repo.balance_of("id-CASH"), 250)
        self.assertEqual(self.repo.balance_of("id-CASH", date(2026, 9, 2)), 200)
        self.assertEqual(self.repo.balance_of("id-SALES"), -310)
        self.assertEqual(self.repo.balances(), {"id-CASH": 250, "id-SALES": -310, "id-RENT": 60})
        self.assertEqual(self.repo.balances(date(2026, 9, 1)), {"id-CASH": 200, "id-SALES": -200})
        # the upper bound is inclusive: 2026-09-03 has transactions on CASH, SALES and RENT
        self.assertEqual(self.repo.balance_of("id-CASH", date(2026, 9, 3)), 250)
        self.assertEqual(self.repo.balance_of("id-RENT", date(2026, 9, 2)), 10)
        self.assertEqual(self.repo.balances(date(2026, 9, 3)), self.repo.balances())
        self.assertEqual(self.repo.balances(date(2026, 8, 1)), {})

    def test_balance_of_account_without_postings_is_zero(self):
        self.assertEqual(self.repo.balance_of("id-CASH"), 0)

    def test_statement_entries(self):
        self.fill()
        entries = self.repo.statement_entries("id-CASH")
        self.assertEqual(
            entries,
            [
                StatementEntry(date(2026, 9, 1), "t2", "tx t2", 200),
                StatementEntry(date(2026, 9, 3), "t1", "tx t1", 100),
                StatementEntry(date(2026, 9, 3), "t3", "tx t3", -50),
            ],
        )
        self.assertEqual(
            [e.transaction_id for e in self.repo.statement_entries("id-CASH", date(2026, 9, 2), date(2026, 9, 3))],
            ["t1", "t3"],
        )
        self.assertEqual(self.repo.statement_entries("id-CASH", date_to=date(2026, 8, 1)), [])


class InMemoryRepositoryTest(RepositoryContract, unittest.TestCase):
    def make_repository(self):
        return InMemoryRepository()


class SqliteRepositoryTest(RepositoryContract, unittest.TestCase):
    def make_repository(self):
        return SqliteRepository(":memory:")
