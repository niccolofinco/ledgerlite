import unittest
from datetime import date

from ledgerlite.domain.errors import NotFoundError, ValidationError
from ledgerlite.domain.models import Side

from tests.helpers import make_service, record_simple, seed_chart


class ReportsTest(unittest.TestCase):
    """A small business scenario with hand-computed expected values."""

    def setUp(self):
        self.service = make_service()
        self.acc = seed_chart(self.service)
        s, a = self.service, self.acc
        record_simple(s, a, "BANK", "CAPITAL", 1_000_000, day=date(2026, 9, 1), description="Initial capital")
        record_simple(s, a, "BANK", "LOAN", 500_000, day=date(2026, 9, 5), description="Loan")
        record_simple(s, a, "CASH", "SALES", 120_000, day=date(2026, 9, 10), description="Sale 1")
        record_simple(s, a, "RENT", "BANK", 80_000, day=date(2026, 9, 15), description="Rent")
        record_simple(s, a, "CASH", "SALES", 30_000, day=date(2026, 9, 20), description="Sale 2")

    def balance(self, code, as_of=None):
        return self.service.get_balance(self.acc[code].id, as_of).balance

    def test_current_balances_use_the_normal_side(self):  # B1
        self.assertEqual(self.balance("BANK"), 1_420_000)  # asset: debit-normal
        self.assertEqual(self.balance("LOAN"), 500_000)  # liability: credit-normal
        self.assertEqual(self.balance("SALES"), 150_000)
        self.assertEqual(self.balance("RENT"), 80_000)

    def test_balance_as_of_a_date(self):  # B1
        self.assertEqual(self.balance("BANK", date(2026, 9, 1)), 1_000_000)
        self.assertEqual(self.balance("BANK", date(2026, 9, 14)), 1_500_000)
        self.assertEqual(self.balance("BANK", date(2026, 8, 31)), 0)
        self.assertEqual(self.balance("SALES", date(2026, 9, 10)), 120_000)

    def test_balance_of_unknown_account(self):
        with self.assertRaises(NotFoundError):
            self.service.get_balance("ghost")

    def test_trial_balance_is_always_balanced(self):  # B2
        report = self.service.get_trial_balance()
        self.assertTrue(report.balanced)
        self.assertEqual(report.total_debit, report.total_credit)
        # BANK 1_420_000 + CASH 150_000 + RENT 80_000 (== CAPITAL + LOAN + SALES)
        self.assertEqual(report.total_debit, 1_650_000)
        self.assertEqual([r.account.code for r in report.rows], sorted(r.account.code for r in report.rows))

    def test_trial_balance_columns(self):
        rows = {r.account.code: r for r in self.service.get_trial_balance().rows}
        self.assertEqual((rows["BANK"].debit, rows["BANK"].credit), (1_420_000, 0))
        self.assertEqual((rows["SALES"].debit, rows["SALES"].credit), (0, 150_000))
        self.assertEqual((rows["CASH"].debit, rows["CASH"].credit), (150_000, 0))

    def test_trial_balance_as_of(self):
        report = self.service.get_trial_balance(date(2026, 9, 5))
        self.assertEqual(report.total_debit, 1_500_000)
        self.assertTrue(report.balanced)

    def test_trial_balance_of_empty_ledger(self):
        report = make_service().get_trial_balance()
        self.assertEqual((report.rows, report.total_debit, report.total_credit), ((), 0, 0))
        self.assertTrue(report.balanced)

    def test_accounts_without_postings_appear_with_zero(self):
        self.service.create_account("EMPTY", "Empty", "asset")
        row = next(r for r in self.service.get_trial_balance().rows if r.account.code == "EMPTY")
        self.assertEqual((row.debit, row.credit), (0, 0))


class StatementTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.acc = seed_chart(self.service)
        s, a = self.service, self.acc
        record_simple(s, a, "CASH", "SALES", 1000, day=date(2026, 9, 1), description="Sale A")
        record_simple(s, a, "CASH", "SALES", 2000, day=date(2026, 9, 10), description="Sale B")
        record_simple(s, a, "RENT", "CASH", 700, day=date(2026, 9, 12), description="Rent")
        record_simple(s, a, "CASH", "SALES", 400, day=date(2026, 9, 20), description="Sale C")

    def statement(self, code="CASH", **kwargs):
        return self.service.get_statement(self.acc[code].id, **kwargs)

    def test_full_statement(self):  # S1
        st = self.statement()
        self.assertEqual(st.opening_balance, 0)
        self.assertEqual([line.running_balance for line in st.lines], [1000, 3000, 2300, 2700])
        self.assertEqual([line.side for line in st.lines], [Side.DEBIT, Side.DEBIT, Side.CREDIT, Side.DEBIT])
        self.assertEqual([line.amount for line in st.lines], [1000, 2000, 700, 400])
        self.assertEqual(st.closing_balance, 2700)

    def test_statement_with_range_has_opening_balance(self):  # S1
        st = self.statement(date_from=date(2026, 9, 11), date_to=date(2026, 9, 30))
        self.assertEqual(st.opening_balance, 3000)
        self.assertEqual([line.description for line in st.lines], ["Rent", "Sale C"])
        self.assertEqual(st.closing_balance, 2700)

    def test_closing_balance_matches_balance_at_end_date(self):  # S1
        st = self.statement(date_to=date(2026, 9, 12))
        self.assertEqual(st.closing_balance, self.service.get_balance(self.acc["CASH"].id, date(2026, 9, 12)).balance)

    def test_credit_normal_account_statement(self):
        st = self.statement("SALES")
        self.assertEqual(st.closing_balance, 3400)
        self.assertEqual(st.lines[0].side, Side.CREDIT)

    def test_empty_range(self):
        st = self.statement(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
        self.assertEqual((st.opening_balance, st.lines, st.closing_balance), (0, (), 0))

    def test_date_min_does_not_overflow(self):
        self.assertEqual(self.statement(date_from=date.min).opening_balance, 0)

    def test_invalid_range_and_unknown_account(self):
        with self.assertRaises(ValidationError):
            self.statement(date_from=date(2026, 9, 2), date_to=date(2026, 9, 1))
        with self.assertRaises(NotFoundError):
            self.service.get_statement("ghost")
