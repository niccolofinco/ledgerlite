"""Invariants of the ledger, checked on randomly generated histories."""

import unittest

from hypothesis import given
from hypothesis import strategies as st
from ledgerlite.domain.errors import ConflictError, ValidationError
from ledgerlite.domain.models import Side
from ledgerlite.repositories import SqliteRepository

from tests.property.common import (
    ACCOUNT_COUNT,
    MAX_DAY_OFFSET,
    PROPERTY_SETTINGS,
    apply,
    balanced_legs,
    build,
    day,
    model_signed_balance,
    operations,
    to_postings,
)


class LedgerInvariants(unittest.TestCase):
    @PROPERTY_SETTINGS
    @given(operations())
    def test_ledger_always_balances(self, ops):  # B2 / T3
        service, accounts, repo = build()
        apply(service, accounts, ops)
        report = service.get_trial_balance()
        self.assertTrue(report.balanced)
        self.assertEqual(report.total_debit, report.total_credit)
        self.assertEqual(sum(repo.balances().values()), 0)

    @PROPERTY_SETTINGS
    @given(operations(), st.integers(0, MAX_DAY_OFFSET))
    def test_balances_agree_with_the_reference_model(self, ops, as_of_offset):  # B1
        service, accounts, _ = build()
        apply(service, accounts, ops)
        for index, account in enumerate(accounts):
            for as_of, up_to in ((None, None), (day(as_of_offset), as_of_offset)):
                expected = account.type.natural_balance(model_signed_balance(ops, index, up_to))
                self.assertEqual(service.get_balance(account.id, as_of).balance, expected)

    @PROPERTY_SETTINGS
    @given(operations(), balanced_legs())
    def test_reversal_restores_every_balance(self, ops, legs):  # R1-R4
        service, accounts, repo = build()
        apply(service, accounts, ops)
        before = repo.balances()
        tx = service.record_transaction(day(0), "to be reversed", to_postings(accounts, legs)).transaction
        service.reverse_transaction(tx.id)
        after = repo.balances()
        for account in accounts:
            self.assertEqual(after.get(account.id, 0), before.get(account.id, 0))
        self.assertTrue(service.get_trial_balance().balanced)

    @PROPERTY_SETTINGS
    @given(operations(), balanced_legs())
    def test_a_transaction_can_never_be_reversed_twice(self, ops, legs):  # R2
        service, accounts, _ = build()
        apply(service, accounts, ops)
        tx = service.record_transaction(day(0), "x", to_postings(accounts, legs)).transaction
        service.reverse_transaction(tx.id)
        with self.assertRaises(ConflictError):
            service.reverse_transaction(tx.id)

    @PROPERTY_SETTINGS
    @given(operations(), balanced_legs(), st.integers(0, 50), st.integers(1, 1000))
    def test_unbalanced_transactions_are_rejected_and_change_nothing(self, ops, legs, index, delta):  # T3
        service, accounts, repo = build()
        apply(service, accounts, ops)
        before_balances = repo.balances()
        before_count = len(service.list_transactions(limit=200))
        broken = list(legs)
        position = index % len(broken)
        account_index, amount = broken[position]
        broken[position] = (account_index, amount + delta if amount > 0 else amount - delta)
        with self.assertRaises(ValidationError):
            service.record_transaction(day(0), "broken", to_postings(accounts, broken))
        self.assertEqual(repo.balances(), before_balances)
        self.assertEqual(len(service.list_transactions(limit=200)), before_count)

    @PROPERTY_SETTINGS
    @given(operations(), balanced_legs(), st.integers(0, 10**6))
    def test_replaying_an_idempotent_request_is_a_no_op(self, ops, legs, key_number):  # T7
        service, accounts, repo = build()
        apply(service, accounts, ops)
        key = f"key-{key_number}"
        first = service.record_transaction(day(1), "x", to_postings(accounts, legs), idempotency_key=key)
        snapshot = (repo.balances(), len(service.list_transactions(limit=200)))
        replay = service.record_transaction(day(1), "x", to_postings(accounts, list(reversed(legs))), idempotency_key=key)
        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(replay.transaction.id, first.transaction.id)
        self.assertEqual((repo.balances(), len(service.list_transactions(limit=200))), snapshot)

    @PROPERTY_SETTINGS
    @given(
        operations(),
        st.integers(0, ACCOUNT_COUNT - 1),
        st.integers(0, MAX_DAY_OFFSET),
        st.integers(0, MAX_DAY_OFFSET),
    )
    def test_statement_is_consistent_with_balances(self, ops, account_index, first, second):  # S1
        service, accounts, _ = build()
        apply(service, accounts, ops)
        account = accounts[account_index]
        start, end = min(first, second), max(first, second)
        statement = service.get_statement(account.id, day(start), day(end))

        signed_sum = sum(line.amount if line.side is Side.DEBIT else -line.amount for line in statement.lines)
        self.assertEqual(
            statement.closing_balance,
            statement.opening_balance + account.type.natural_balance(signed_sum),
        )
        self.assertEqual(statement.closing_balance, service.get_balance(account.id, day(end)).balance)
        if start > 0:
            self.assertEqual(statement.opening_balance, service.get_balance(account.id, day(start - 1)).balance)

    @PROPERTY_SETTINGS
    @given(operations(), st.integers(0, MAX_DAY_OFFSET))
    def test_in_memory_and_sqlite_repositories_are_equivalent(self, ops, as_of_offset):  # differential
        memory_service, memory_accounts, _ = build()
        sqlite_repo = SqliteRepository(":memory:")
        try:
            sqlite_service, sqlite_accounts, _ = build(sqlite_repo)
            apply(memory_service, memory_accounts, ops)
            apply(sqlite_service, sqlite_accounts, ops)

            self.assertEqual(
                memory_service.list_transactions(limit=200), sqlite_service.list_transactions(limit=200)
            )
            self.assertEqual(memory_service.get_trial_balance(), sqlite_service.get_trial_balance())
            as_of = day(as_of_offset)
            self.assertEqual(memory_service.get_trial_balance(as_of), sqlite_service.get_trial_balance(as_of))
            for index in range(ACCOUNT_COUNT):
                self.assertEqual(
                    memory_service.get_statement(memory_accounts[index].id),
                    sqlite_service.get_statement(sqlite_accounts[index].id),
                )
                self.assertEqual(
                    memory_service.get_balance(memory_accounts[index].id, as_of),
                    sqlite_service.get_balance(sqlite_accounts[index].id, as_of),
                )
        finally:
            sqlite_repo.close()
