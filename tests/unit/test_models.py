import unittest
from datetime import date, datetime, timezone

from ledgerlite.domain.errors import ValidationError
from ledgerlite.domain.models import Account, AccountType, Posting, Side, Transaction
from ledgerlite.domain.money import MAX_AMOUNT

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def make_tx(postings=None, **overrides):
    values = dict(
        id="t1",
        date=date(2026, 10, 1),
        description="Sale",
        postings=postings if postings is not None else (Posting("a", 500), Posting("b", -500)),
        created_at=NOW,
    )
    values.update(overrides)
    return Transaction(**values)


class AccountTypeTest(unittest.TestCase):
    def test_normal_sides(self):
        expected = {
            AccountType.ASSET: Side.DEBIT,
            AccountType.EXPENSE: Side.DEBIT,
            AccountType.LIABILITY: Side.CREDIT,
            AccountType.EQUITY: Side.CREDIT,
            AccountType.INCOME: Side.CREDIT,
        }
        for kind, side in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(kind.normal_side, side)

    def test_natural_balance(self):
        self.assertEqual(AccountType.ASSET.natural_balance(700), 700)
        self.assertEqual(AccountType.LIABILITY.natural_balance(-700), 700)
        self.assertEqual(AccountType.INCOME.natural_balance(300), -300)


class AccountTest(unittest.TestCase):
    def test_valid_account_strips_name(self):
        account = Account("1", "CASH-01", "  Petty cash ", AccountType.ASSET)
        self.assertEqual(account.name, "Petty cash")
        self.assertTrue(account.active)
        self.assertEqual(account.normal_side, Side.DEBIT)

    def test_invalid_fields(self):
        invalid = [
            dict(id=""),
            dict(id=None),
            dict(code="cash"),
            dict(code=""),
            dict(code="A" * 21),
            dict(code="-A"),
            dict(code=None),
            dict(name="   "),
            dict(name=None),
            dict(name="x" * 101),
            dict(type="asset"),
        ]
        for override in invalid:
            values = dict(id="1", code="CASH", name="Cash", type=AccountType.ASSET) | override
            with self.subTest(override=override), self.assertRaises(ValidationError):
                Account(**values)

    def test_deactivated_returns_new_inactive_copy(self):
        account = Account("1", "CASH", "Cash", AccountType.ASSET)
        closed = account.deactivated()
        self.assertFalse(closed.active)
        self.assertTrue(account.active)


class PostingTest(unittest.TestCase):
    def test_side_follows_sign(self):
        self.assertEqual(Posting("a", 10).side, Side.DEBIT)
        self.assertEqual(Posting("a", -10).side, Side.CREDIT)

    def test_from_side(self):
        self.assertEqual(Posting.from_side("a", Side.DEBIT, 10).amount, 10)
        self.assertEqual(Posting.from_side("a", Side.CREDIT, 10).amount, -10)

    def test_from_side_requires_positive_integer(self):
        for amount in [0, -1, 1.5, True, "5", None]:
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                Posting.from_side("a", Side.DEBIT, amount)

    def test_invalid_postings(self):
        for account_id, amount in [("", 5), (None, 5), ("a", 0), ("a", 1.0), ("a", True),
                                   ("a", MAX_AMOUNT + 1), ("a", -MAX_AMOUNT - 1)]:
            with self.subTest(account_id=account_id, amount=amount), self.assertRaises(ValidationError):
                Posting(account_id, amount)

    def test_negated(self):
        self.assertEqual(Posting("a", 7).negated(), Posting("a", -7))


class TransactionTest(unittest.TestCase):
    def test_valid_transaction_normalises_description_and_postings(self):
        tx = make_tx(postings=[Posting("a", 5), Posting("b", -5)], description="  Sale  ")
        self.assertEqual(tx.description, "Sale")
        self.assertIsInstance(tx.postings, tuple)

    def test_requires_two_postings(self):
        with self.assertRaises(ValidationError):
            make_tx(postings=[Posting("a", 5)])
        with self.assertRaises(ValidationError):
            make_tx(postings=[])

    def test_must_be_balanced(self):
        with self.assertRaises(ValidationError):
            make_tx(postings=[Posting("a", 500), Posting("b", -499)])

    def test_multi_leg_balanced_transaction(self):
        tx = make_tx(postings=[Posting("a", 300), Posting("b", 200), Posting("c", -500)])
        self.assertEqual(sum(p.amount for p in tx.postings), 0)

    def test_invalid_scalar_fields(self):
        invalid = [
            dict(id=""),
            dict(description=""),
            dict(description="x" * 201),
            dict(description=None),
            dict(date="2026-10-01"),
            dict(date=datetime(2026, 10, 1)),
        ]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValidationError):
                make_tx(**override)

    def test_fingerprint_ignores_posting_order(self):
        first = make_tx(postings=[Posting("a", 5), Posting("b", -5)])
        second = make_tx(id="t2", postings=[Posting("b", -5), Posting("a", 5)])
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_fingerprint_detects_changes(self):
        base = make_tx()
        self.assertNotEqual(base.fingerprint(), make_tx(description="Other").fingerprint())
        self.assertNotEqual(base.fingerprint(), make_tx(date=date(2026, 10, 2)).fingerprint())
        self.assertNotEqual(
            base.fingerprint(),
            make_tx(postings=[Posting("a", 600), Posting("b", -600)]).fingerprint(),
        )

    def test_reversed_postings_cancel_the_original(self):
        tx = make_tx(postings=[Posting("a", 300), Posting("b", 200), Posting("c", -500)])
        net = {}
        for p in (*tx.postings, *tx.reversed_postings()):
            net[p.account_id] = net.get(p.account_id, 0) + p.amount
        self.assertEqual(set(net.values()), {0})
