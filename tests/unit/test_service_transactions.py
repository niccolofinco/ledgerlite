import unittest
from datetime import date

from ledgerlite.domain.errors import ConflictError, NotFoundError, ValidationError
from ledgerlite.domain.models import Side
from ledgerlite.services import PostingInput

from tests.helpers import TODAY, credit, debit, make_service, record_simple, seed_chart


class RecordTransactionTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.acc = seed_chart(self.service)

    def test_records_balanced_transaction(self):
        result = record_simple(self.service, self.acc, "CASH", "SALES", 12050)
        self.assertTrue(result.created)
        self.assertEqual(self.service.get_transaction(result.transaction.id), result.transaction)
        self.assertEqual(self.service.get_balance(self.acc["CASH"].id).balance, 12050)

    def test_multi_leg_transaction(self):
        result = self.service.record_transaction(
            TODAY,
            "Sale paid half cash, half bank",
            [debit(self.acc["CASH"], 500), debit(self.acc["BANK"], 500), credit(self.acc["SALES"], 1000)],
        )
        self.assertEqual(len(result.transaction.postings), 3)

    def test_unbalanced_transaction_is_rejected(self):  # T3
        with self.assertRaises(ValidationError):
            self.service.record_transaction(
                TODAY, "bad", [debit(self.acc["CASH"], 100), credit(self.acc["SALES"], 99)]
            )
        self.assertEqual(self.service.list_transactions(), [])

    def test_single_posting_is_rejected(self):  # T1
        with self.assertRaises(ValidationError):
            self.service.record_transaction(TODAY, "bad", [debit(self.acc["CASH"], 100)])

    def test_non_positive_amounts_are_rejected(self):  # T2
        for amount in (0, -100):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.service.record_transaction(
                    TODAY,
                    "bad",
                    [PostingInput(self.acc["CASH"].id, Side.DEBIT, amount), credit(self.acc["SALES"], 100)],
                )

    def test_unknown_account_is_rejected(self):  # T4
        with self.assertRaises(NotFoundError):
            self.service.record_transaction(
                TODAY,
                "bad",
                [PostingInput("ghost", Side.DEBIT, 100), credit(self.acc["SALES"], 100)],
            )

    def test_inactive_account_is_rejected(self):  # T4
        old = self.service.create_account("OLD", "Old", "asset")
        self.service.deactivate_account(old.id)
        with self.assertRaises(ConflictError):
            self.service.record_transaction(TODAY, "bad", [debit(old, 100), credit(self.acc["SALES"], 100)])

    def test_future_date_is_rejected(self):  # T5
        with self.assertRaises(ValidationError):
            record_simple(self.service, self.acc, "CASH", "SALES", 100, day=date(2026, 10, 6))

    def test_today_and_past_dates_are_accepted(self):
        record_simple(self.service, self.acc, "CASH", "SALES", 100, day=TODAY)
        record_simple(self.service, self.acc, "CASH", "SALES", 100, day=date(2020, 1, 1))
        self.assertEqual(len(self.service.list_transactions()), 2)

    def test_same_account_on_both_sides_is_allowed_and_nets_to_zero(self):
        self.service.record_transaction(
            TODAY, "wash", [debit(self.acc["CASH"], 100), credit(self.acc["CASH"], 100)]
        )
        self.assertEqual(self.service.get_balance(self.acc["CASH"].id).balance, 0)


class IdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.acc = seed_chart(self.service)

    def test_replay_returns_original_without_duplicating(self):  # T7
        first = record_simple(self.service, self.acc, "CASH", "SALES", 500, idempotency_key="k1")
        second = record_simple(self.service, self.acc, "CASH", "SALES", 500, idempotency_key="k1")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.transaction.id, second.transaction.id)
        self.assertEqual(len(self.service.list_transactions()), 1)
        self.assertEqual(self.service.get_balance(self.acc["CASH"].id).balance, 500)

    def test_replay_is_insensitive_to_posting_order(self):
        key = "k-order"
        self.service.record_transaction(
            TODAY, "x", [debit(self.acc["CASH"], 5), credit(self.acc["SALES"], 5)], idempotency_key=key
        )
        replay = self.service.record_transaction(
            TODAY, "x", [credit(self.acc["SALES"], 5), debit(self.acc["CASH"], 5)], idempotency_key=key
        )
        self.assertFalse(replay.created)

    def test_same_key_with_different_payload_is_a_conflict(self):  # T7
        record_simple(self.service, self.acc, "CASH", "SALES", 500, idempotency_key="k1")
        with self.assertRaises(ConflictError):
            record_simple(self.service, self.acc, "CASH", "SALES", 501, idempotency_key="k1")

    def test_replay_still_works_after_account_is_closed(self):
        first = record_simple(self.service, self.acc, "CASH", "SALES", 500, idempotency_key="k1")
        self.service.reverse_transaction(first.transaction.id)
        self.service.deactivate_account(self.acc["CASH"].id)
        replay = record_simple(self.service, self.acc, "CASH", "SALES", 500, idempotency_key="k1")
        self.assertFalse(replay.created)

    def test_invalid_keys_are_rejected(self):
        for key in ["", "x" * 129, "line\nbreak"]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                record_simple(self.service, self.acc, "CASH", "SALES", 500, idempotency_key=key)


class ReversalTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.acc = seed_chart(self.service)
        self.original = record_simple(
            self.service, self.acc, "RENT", "BANK", 80000, day=date(2026, 9, 1), description="September rent"
        ).transaction

    def test_reversal_cancels_the_original(self):
        reversal = self.service.reverse_transaction(self.original.id)
        self.assertEqual(reversal.reverses, self.original.id)
        self.assertEqual(reversal.date, TODAY)
        self.assertEqual(reversal.description, "Reversal: September rent")
        for code in ("RENT", "BANK"):
            self.assertEqual(self.service.get_balance(self.acc[code].id).balance, 0)
        self.assertTrue(self.service.get_trial_balance().balanced)

    def test_original_is_untouched(self):  # T6 immutability
        self.service.reverse_transaction(self.original.id)
        self.assertEqual(self.service.get_transaction(self.original.id), self.original)
        self.assertEqual(len(self.service.list_transactions()), 2)

    def test_custom_date_and_description(self):
        reversal = self.service.reverse_transaction(
            self.original.id, on=date(2026, 9, 15), description="Booked twice"
        )
        self.assertEqual((reversal.date, reversal.description), (date(2026, 9, 15), "Booked twice"))

    def test_cannot_reverse_twice(self):  # R2
        self.service.reverse_transaction(self.original.id)
        with self.assertRaises(ConflictError):
            self.service.reverse_transaction(self.original.id)

    def test_cannot_reverse_a_reversal(self):  # R1
        reversal = self.service.reverse_transaction(self.original.id)
        with self.assertRaises(ConflictError):
            self.service.reverse_transaction(reversal.id)

    def test_reversal_cannot_precede_original(self):  # R3
        with self.assertRaises(ValidationError):
            self.service.reverse_transaction(self.original.id, on=date(2026, 8, 31))

    def test_reversal_cannot_be_in_the_future(self):
        with self.assertRaises(ValidationError):
            self.service.reverse_transaction(self.original.id, on=date(2026, 12, 1))

    def test_long_descriptions_are_truncated(self):
        long_tx = record_simple(
            self.service, self.acc, "CASH", "SALES", 100, description="d" * 200
        ).transaction
        self.assertEqual(len(self.service.reverse_transaction(long_tx.id).description), 200)

    def test_unknown_transaction(self):
        with self.assertRaises(NotFoundError):
            self.service.reverse_transaction("missing")

    def test_cannot_reverse_into_an_inactive_account(self):  # R4
        tx = record_simple(self.service, self.acc, "CASH", "CAPITAL", 100).transaction
        record_simple(self.service, self.acc, "CAPITAL", "CASH", 100)  # CASH back to zero
        self.service.deactivate_account(self.acc["CASH"].id)
        with self.assertRaises(ConflictError):
            self.service.reverse_transaction(tx.id)


class ListTransactionsTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.acc = seed_chart(self.service)
        for day, amount in [(date(2026, 9, 3), 100), (date(2026, 9, 1), 200), (date(2026, 9, 2), 300)]:
            record_simple(self.service, self.acc, "CASH", "SALES", amount, day=day)
        record_simple(self.service, self.acc, "RENT", "BANK", 50, day=date(2026, 9, 2))

    def test_ordered_by_date(self):
        dates = [t.date for t in self.service.list_transactions()]
        self.assertEqual(dates, sorted(dates))

    def test_filters(self):
        self.assertEqual(len(self.service.list_transactions(account_id=self.acc["CASH"].id)), 3)
        self.assertEqual(len(self.service.list_transactions(date_from=date(2026, 9, 2))), 3)
        self.assertEqual(len(self.service.list_transactions(date_to=date(2026, 9, 2))), 3)
        self.assertEqual(
            len(self.service.list_transactions(date_from=date(2026, 9, 2), date_to=date(2026, 9, 2))), 2
        )

    def test_pagination(self):
        everything = self.service.list_transactions()
        self.assertEqual(self.service.list_transactions(limit=2), everything[:2])
        self.assertEqual(self.service.list_transactions(limit=2, offset=2), everything[2:])

    def test_invalid_parameters(self):
        for kwargs in [
            dict(limit=0),
            dict(limit=201),
            dict(offset=-1),
            dict(date_from=date(2026, 9, 3), date_to=date(2026, 9, 1)),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.service.list_transactions(**kwargs)

    def test_unknown_account_filter(self):
        with self.assertRaises(NotFoundError):
            self.service.list_transactions(account_id="ghost")
