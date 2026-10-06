import unittest

from ledgerlite.domain.errors import ConflictError, NotFoundError, ValidationError
from ledgerlite.domain.models import AccountType

from tests.helpers import make_service, record_simple, seed_chart


class AccountServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service()

    def test_create_and_get_account(self):
        account = self.service.create_account("CASH", "Cash", AccountType.ASSET)
        self.assertEqual(self.service.get_account(account.id), account)

    def test_type_can_be_given_as_string(self):
        self.assertEqual(self.service.create_account("SALES", "Sales", "income").type, AccountType.INCOME)

    def test_invalid_type_string_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_account("X", "X", "bogus")

    def test_duplicate_code_is_a_conflict(self):  # A1
        self.service.create_account("CASH", "Cash", "asset")
        with self.assertRaises(ConflictError):
            self.service.create_account("CASH", "Another cash", "asset")

    def test_unknown_account_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.get_account("missing")

    def test_accounts_are_listed_by_code(self):
        seed_chart(self.service)
        codes = [a.code for a in self.service.list_accounts()]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(len(codes), 6)

    def test_deactivate_empty_account(self):  # A3
        account = self.service.create_account("OLD", "Old", "asset")
        self.assertFalse(self.service.deactivate_account(account.id).active)
        self.assertFalse(self.service.get_account(account.id).active)

    def test_deactivate_is_idempotent(self):
        account = self.service.create_account("OLD", "Old", "asset")
        self.service.deactivate_account(account.id)
        self.assertFalse(self.service.deactivate_account(account.id).active)

    def test_cannot_deactivate_account_with_balance(self):  # A3
        accounts = seed_chart(self.service)
        record_simple(self.service, accounts, "CASH", "CAPITAL", 1000)
        with self.assertRaises(ConflictError):
            self.service.deactivate_account(accounts["CASH"].id)

    def test_can_deactivate_after_balance_returns_to_zero(self):
        accounts = seed_chart(self.service)
        tx = record_simple(self.service, accounts, "CASH", "CAPITAL", 1000).transaction
        self.service.reverse_transaction(tx.id)
        self.assertFalse(self.service.deactivate_account(accounts["CASH"].id).active)

    def test_deactivate_unknown_account(self):
        with self.assertRaises(NotFoundError):
            self.service.deactivate_account("missing")
